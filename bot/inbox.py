"""Conversation tracking from notification emails: who's waiting for an answer, reminders, and /inbox.

A guest message, inquiry or booking request opens a thread; Leanne's reply (an Airbnb "Host" email), a confirmation
or a cancellation closes it, and so does ✅ Handled. Reminders: inquiries/requests at 20 h and 23 h (Airbnb's
24 h window), other guest messages after 3 h, skipping ones that don't need an answer ("Thanks!").
VRBO doesn't email the host's own replies, so VRBO threads only close with ✅ Handled or a booking/cancellation.
"""

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

import chat

log = logging.getLogger("str")

HOUR = 3600 * 1000  # ms
MESSAGE_REMINDER = 3 * HOUR
DEADLINE_REMINDERS = (20 * HOUR, 23 * HOUR)  # after an inquiry/request arrives
KEEP = 14 * 24 * HOUR  # forget threads with no activity for two weeks
HOST_ROLES = {"Host", "Co-host"}
_NO_REPLY = re.compile(r"^(thanks?( you)?|thank you( so much)?|perfect|great|awesome|sounds good|ok(ay)?|got it|will do"
                       r"|wonderful|amazing|appreciate it)\b", re.I)
_ASKS = re.compile(r"\?|\b(can|could|would|will|is there|are there|do you|does|how|when|where|what|which|please|let me know)\b", re.I)


def needs_reply(text: str) -> bool:
    text = text.strip()
    if "?" in text:
        return True
    if _NO_REPLY.search(text):  # "Thank you, Leanne! We will take good care…"
        return False
    return bool(_ASKS.search(text)) or len(text) >= 25


def thread_key(item: dict) -> str:
    base = item.get("thread_url") or f"{item['platform']}|{item.get('guest') or item.get('dates') or item['subject']}"
    return hashlib.sha1(base.encode()).hexdigest()[:10]


def _path(cfg):
    return cfg.state_dir / "threads.json"


def load(cfg) -> dict:
    p = _path(cfg)
    return json.loads(p.read_text()) if p.exists() else {}


def save(cfg, threads: dict) -> None:
    _path(cfg).write_text(json.dumps(threads, ensure_ascii=False))


def record(threads: dict, item: dict) -> None:
    """Update thread state from one parsed notification email."""
    kind, key = item["kind"], thread_key(item)
    t = threads.get(key)
    if kind in ("confirmed", "canceled", "review"):  # the conversation reached an outcome
        if t:
            t.update(open=False, closed_by=kind, last_activity=item["date"])
        return
    if kind == "message":
        msgs = item.get("messages", [])
        if not msgs:
            return
        if msgs[-1]["role"] in HOST_ROLES:  # the latest word is Leanne's: answered
            if t:
                t.update(open=False, closed_by="reply", last_activity=item["date"])
            return
        last_host = max((i for i, b in enumerate(msgs) if b["role"] in HOST_ROLES), default=-1)
        unanswered = msgs[last_host + 1:]
        text, guest = " ".join(b["text"] for b in unanswered), unanswered[0]["name"]
        if not needs_reply(text) and not (t and t["open"]):
            return  # a thank-you on a quiet thread: nothing to answer
    else:  # inquiry or booking request
        text, guest = item.get("body", ""), item.get("guest") or "Guest"
    still_open = bool(t and t["open"])
    threads[key] = {
        "key": key, "platform": item["platform"], "guest": guest, "dates": item.get("dates") or (t or {}).get("dates"),
        "kind": t["kind"] if still_open else ("inquiry" if kind in ("inquiry", "request") else "message"),
        "since": t["since"] if still_open else item["date"],
        "deadline": t["deadline"] if still_open else (item["date"] + 24 * HOUR if kind in ("inquiry", "request") else None),
        "reminded": t["reminded"] if still_open else [],
        "text": text[:500], "thread_url": item.get("thread_url"), "item_id": item["id"], "open": True,
        "last_activity": item["date"],
    }


def due_reminders(threads: dict, now_ms: int) -> list[tuple[dict, str]]:
    """(thread, reminder label) pairs to send now; marks them as sent."""
    out = []
    for t in threads.values():
        if not t.get("open"):
            continue
        if t.get("deadline"):
            for step in sorted(DEADLINE_REMINDERS, reverse=True):  # the latest due one (e.g. after downtime)
                tag = f"d{step // HOUR}"
                if now_ms - (t["deadline"] - 24 * HOUR) >= step and tag not in t["reminded"]:
                    t["reminded"] += [f"d{s // HOUR}" for s in DEADLINE_REMINDERS if s <= step]
                    out.append((t, f"{(24 * HOUR - step) // HOUR} h left to respond"))
                    break
        elif now_ms - t["since"] >= MESSAGE_REMINDER and "m" not in t["reminded"]:
            t["reminded"].append("m")
            out.append((t, f"waiting {(now_ms - t['since']) // HOUR} h"))
    return out


def prune(threads: dict, now_ms: int) -> None:
    for key in [k for k, t in threads.items() if now_ms - t["last_activity"] > KEEP]:
        del threads[key]


def keyboard(t: dict) -> InlineKeyboardMarkup:
    row = [InlineKeyboardButton("✍️ Draft reply", callback_data=f"em|d|{t['item_id']}"),
           InlineKeyboardButton("✅ Handled", callback_data=f"ib|done|{t['key']}")]
    rows = [row]
    if t.get("thread_url"):
        rows.append([InlineKeyboardButton(f"Open in {t['platform']}", url=t["thread_url"])])
    return InlineKeyboardMarkup(rows)


def reminder_text(t: dict, label: str, draft: str | None = None) -> str:
    suggestion = f"\n\n✍️ Suggested reply:\n{draft[:2500]}" if draft else ""
    vrbo = "\n(VRBO doesn't email Leanne's replies; tap ✅ Handled if she already answered.)" if t["platform"] == "VRBO" else ""
    what = "Inquiry" if t["kind"] == "inquiry" else "Message"
    return (f"⏰ {what} from {t['guest']} unanswered, {label} · {t['platform']}{' · ' + t['dates'] if t.get('dates') else ''}"
            f"\n\"{t['text'][:300]}\"{vrbo}{suggestion}")


async def after_email_scan(ctx: ContextTypes.DEFAULT_TYPE, items: list[dict]) -> None:
    """Called by the email watch after each scan: update threads, send due reminders."""
    cfg = ctx.bot_data["cfg"]
    threads = load(cfg)
    for item in items:
        record(threads, item)
    now_ms = int(datetime.now().timestamp() * 1000)
    reminders = due_reminders(threads, now_ms)
    prune(threads, now_ms)
    save(cfg, threads)
    drafts = {i["id"]: i.get("draft") for i in items}
    stored = json.loads((cfg.state_dir / "emails.json").read_text()).get("items", {}) \
        if (cfg.state_dir / "emails.json").exists() else {}
    for t, label in reminders:
        draft = drafts.get(t["item_id"]) or stored.get(t["item_id"], {}).get("draft")
        if cfg.allowed_chat_id is not None:
            await ctx.bot.send_message(cfg.allowed_chat_id, reminder_text(t, label, draft), reply_markup=keyboard(t))
    if reminders:
        log.info("Inbox: %d reminder(s) sent", len(reminders))


def _age(ms: int, now_ms: int) -> str:
    h = (now_ms - ms) // HOUR
    return f"{h // 24}d" if h >= 48 else f"{h}h"


def inbox_text(threads: dict, now_ms: int, tz) -> str:
    waiting = sorted((t for t in threads.values() if t.get("open")), key=lambda t: t.get("deadline") or t["since"])
    if not waiting:
        return "📥 Inbox: nobody is waiting for an answer."
    lines = [f"📥 Waiting for an answer ({len(waiting)})"]
    for t in waiting:
        due = (f", respond by {datetime.fromtimestamp(t['deadline'] / 1000, tz):%a %-I:%M %p}" if t.get("deadline") else "")
        lines.append(f"- {t['guest']} · {t['platform']}{' · ' + t['dates'] if t.get('dates') else ''} · "
                     f"{_age(t['since'], now_ms)} ago{due}\n  \"{t['text'][:140]}\"")
    return "\n".join(lines)


async def on_inbox(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = ctx.bot_data["cfg"]
    if not chat.is_allowed(cfg, update):
        return
    threads = load(cfg)
    now_ms = int(datetime.now().timestamp() * 1000)
    waiting = [t for t in threads.values() if t.get("open")]
    await update.message.reply_text(inbox_text(threads, now_ms, cfg.tz),
                                    reply_markup=keyboard(waiting[0]) if len(waiting) == 1 else None)
    for t in waiting[:8] if len(waiting) > 1 else []:
        await ctx.bot.send_message(cfg.allowed_chat_id, f"{t['guest']} · {t['platform']}", reply_markup=keyboard(t))


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    cfg = ctx.bot_data["cfg"]
    if q.message.chat.id != cfg.allowed_chat_id:
        return
    _, action, key = q.data.split("|")
    threads = load(cfg)
    t = threads.get(key)
    if t and t.get("open"):
        t.update(open=False, closed_by="handled")
        save(cfg, threads)
    await q.answer("Marked as handled")
    await q.edit_message_text((q.message.text or "") + "\n\n✅ Handled")
