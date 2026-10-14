"""The assistant: Claude plus reservation, calendar and notes tools, with short conversations.

Adapted from ~/workout-coach/bot/coach/agent.py: a new conversation starts after an hour without messages
and opens with a small context pack (upcoming stays) plus the last few messages."""

import asyncio
import json
import logging
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import anthropic

import tools
from config import Config
from data import usage
from data.repo import Repo
from integrations import ical
from integrations.gcal import Calendar

log = logging.getLogger(__name__)

CLIP = 300  # max characters of message/tool content per log line
SESSION_IDLE = timedelta(hours=1)
CARRY_OVER = 6

BOT_ADDENDUM = (Path(__file__).parent / "prompts" / "telegram.md").read_text()


class Agent:
    def __init__(self, cfg: Config, calendar: Calendar, repo: Repo):
        self.cfg = cfg
        self.calendar = calendar
        self.repo = repo
        self.client = anthropic.AsyncAnthropic()
        self.system = (cfg.repo_dir / "host.md").read_text() + BOT_ADDENDUM
        self.tools = tools.TOOLS
        self._lock = asyncio.Lock()
        cfg.state_dir.mkdir(exist_ok=True)

    def today(self) -> date:
        return datetime.now(self.cfg.tz).date()

    # --- conversation sessions ---

    def _open_session(self, now: datetime) -> tuple[Path, list[dict], list[dict] | None]:
        """Return (file, messages, previous messages if this is a brand-new session)."""
        state_path = self.cfg.state_dir / "session.json"
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        current = self.cfg.state_dir / state["file"] if state.get("file") else None
        last = datetime.fromisoformat(state["last_activity"]) if state.get("last_activity") else None
        if current and current.exists() and last and now - last < SESSION_IDLE:
            return current, json.loads(current.read_text()), None
        previous = json.loads(current.read_text()) if current and current.exists() else []
        return self.cfg.state_dir / f"conversation-{now:%Y-%m-%d-%H%M%S}.json", [], previous

    def _close_turn(self, path: Path, messages: list[dict], now: datetime) -> None:
        path.write_text(json.dumps(messages))
        (self.cfg.state_dir / "session.json").write_text(json.dumps({"file": path.name, "last_activity": now.isoformat()}))

    @staticmethod
    def _recent_text(messages: list[dict], n: int = CARRY_OVER) -> str:
        lines = []
        for m in messages:
            content = m["content"]
            text = content if isinstance(content, str) else \
                "\n".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
            text = text.split("NEW MESSAGE:\n")[-1].strip()
            if text:
                lines.append(("Me: " if m["role"] == "user" else "Assistant: ") + text)
        return "\n\n".join(lines[-n:])

    def _context_pack(self, now: datetime) -> str:
        parts = [f"CONTEXT PACK (snapshot at {now:%a %b %-d %-I:%M %p})"]
        try:
            entries = ical.load_all(self.cfg.feeds)
            soon = [s for s in entries if s["kind"] == "stay" and s["check_out"] >= now.date().isoformat()
                    and s["check_in"] <= (now.date() + timedelta(days=21)).isoformat()]
            parts.append("Stays, next 3 weeks:\n" + ("\n".join("- " + ical.describe(s) for s in soon) or "(none)"))
            if doubles := ical.double_bookings(entries):
                parts.append("⚠️ DOUBLE BOOKINGS:\n" + "\n".join(f"- {ical.describe(a)} overlaps {ical.describe(b)}"
                                                                 for a, b in doubles))
        except Exception as e:  # the pack is an optimization; list_stays still works (or reports the error)
            log.warning("Context pack: feeds unavailable: %s", e)
        parts.append("Read property.md before answering guest questions.")
        return "\n\n".join(parts)

    # --- tools ---

    def _clip(self, value) -> str:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        if not self.cfg.log_content:
            return f"<{len(text)} chars>"
        text = text.replace("\n", " ⏎ ")
        return text if len(text) <= CLIP else text[:CLIP] + f"… (+{len(text) - CLIP} chars)"

    async def _tool_result(self, block) -> dict:
        start = time.monotonic()
        log.info("Tool call %s %s", block.name, self._clip(block.input))
        try:
            content = await asyncio.to_thread(tools.run, self, block.name, block.input)
            log.info("Tool done %s in %.1fs → %s", block.name, time.monotonic() - start, self._clip(content))
            return {"type": "tool_result", "tool_use_id": block.id, "content": content}
        except Exception as e:  # report tool failures back to Claude instead of crashing
            log.warning("Tool %s failed: %s: %s", block.name, type(e).__name__, e)
            return {"type": "tool_result", "tool_use_id": block.id, "content": f"Error: {e}", "is_error": True}

    # --- main entry point ---

    async def respond(self, text: str, effort: str = "low", source: str = "chat") -> str:
        async with self._lock:
            now = datetime.now(self.cfg.tz)
            path, messages, previous = self._open_session(now)
            stamped = f"[{now:%a %Y-%m-%d %H:%M %Z}] {text}"
            if previous is not None:
                recent = self._recent_text(previous)
                pack = await asyncio.to_thread(self._context_pack, now)
                stamped = (pack + (f"\n\nRECENT MESSAGES (from the previous conversation):\n{recent}" if recent else "")
                           + f"\n\nNEW MESSAGE:\n{stamped}")
                log.info("New conversation %s (context pack %d chars)", path.name, len(pack))
            log.info("Turn start (history: %d messages) ← %s", len(messages), self._clip(text))
            messages.append({"role": "user", "content": stamped})
            try:
                reply = await self._loop(messages, effort, source, text)
            finally:
                self._close_turn(path, messages, datetime.now(self.cfg.tz))
            await asyncio.to_thread(self.repo.commit, f"Assistant update {now:%Y-%m-%d %H:%M}")
            return reply

    async def _loop(self, messages: list[dict], effort: str, source: str, preview: str) -> str:
        call, turn_id = 0, uuid.uuid4().hex[:8]
        while True:
            call += 1
            start = time.monotonic()
            response = await self.client.beta.messages.create(
                model=self.cfg.model,
                max_tokens=16000,
                system=self.system,
                tools=self.tools,
                messages=messages,
                thinking={"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "drop_block"}},
                output_config={"effort": effort},
                cache_control={"type": "ephemeral", "ttl": "1h"},
                betas=["server-side-fallback-2026-07-01", "thinking-binding-controls-2026-08-01"],
                fallbacks="default",
            )
            u = response.usage
            log.info("Claude call %d: %.1fs, stop=%s, tokens in=%d cache_read=%d cache_write=%d out=%d",
                     call, time.monotonic() - start, response.stop_reason, u.input_tokens,
                     u.cache_read_input_tokens or 0, u.cache_creation_input_tokens or 0, u.output_tokens)
            usage.record(self.cfg.state_dir / "usage.jsonl", ts=datetime.now(self.cfg.tz), source=source,
                         turn_id=turn_id, call=call, model=response.model, effort=effort, usage=u,
                         stop_reason=response.stop_reason,
                         tools=[b.name for b in response.content if b.type in ("tool_use", "server_tool_use")],
                         preview=preview if call == 1 else None)
            messages.append({"role": "assistant",
                             "content": [b.model_dump(mode="json", exclude_none=True) for b in response.content]})
            text = "".join(b.text for b in response.content if b.type == "text").strip()
            match response.stop_reason:
                case "tool_use":
                    uses = [b for b in response.content if b.type == "tool_use"]
                    messages.append({"role": "user", "content": list(await asyncio.gather(*map(self._tool_result, uses)))})
                case "pause_turn":
                    continue
                case "refusal":
                    return text or "Sorry, I can't help with that one."
                case "max_tokens":
                    return (text + "\n\n(cut off; ask me to continue)").strip()
                case _:
                    return text or "(no reply)"
