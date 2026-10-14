"""Airbnb/VRBO notification emails → short Telegram messages, sorted by code (no Claude).

Guest messages and inquiries get a ✍️ Draft reply button (Claude writes it as Leanne, only when tapped) and an
"Open in Airbnb" link. Leanne's own replies (role "Host") and routine mail are skipped. The first read of an
inbox is a silent baseline, so turning this on doesn't replay old mail.
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

import chat

log = logging.getLogger("str")

CHECK_EVERY = timedelta(minutes=5)
ROLES = {"Booker", "Guest", "Co-traveler", "Host", "Co-host"}
HOST_ROLES = {"Host", "Co-host"}

SUBJECTS = [  # (kind, pattern) — first match wins
    ("message", re.compile(r"^RE: (?:Reservation|Pre-approval|Inquiry|Request) for (?P<listing>.+?), (?P<dates>.+)$")),
    ("inquiry", re.compile(r"^Inquiry for (?P<listing>.+?) for (?P<dates>.+)$")),
    ("request", re.compile(r"^Pending: Reservation Request")),
    ("confirmed", re.compile(r"^Reservation confirmed - (?P<guest>.+?) arrives (?P<arrives>.+)$")),
    ("canceled", re.compile(r"^Canceled: Reservation (?P<code>\w+) for (?P<dates>.+)$")),
    ("review", re.compile(r"^(?P<guest>.+?) left a (?P<stars>\d)-star review")),
]


# ---------- parsing (pure) ----------

def _platform(sender: str) -> str | None:
    s = sender.lower()
    return "Airbnb" if "airbnb.com" in s else "VRBO" if ("vrbo.com" in s or "homeaway.com" in s) else None


def _thread_url(text: str) -> str | None:
    m = re.search(r"https://www\.airbnb\.com/hosting/thread/(\d+)", text)
    return f"https://www.airbnb.com/hosting/thread/{m[1]}" if m else None


def guest_messages(text: str) -> list[dict]:
    """The message blocks in an Airbnb message email: [{name, role, text}] (a digest can hold several)."""
    seg = text.split("communicate through Airbnb", 1)[-1]
    seg = seg.split("].", 1)[-1] if "]." in seg[:400] else seg
    lines = [line.strip() for line in seg.splitlines()]
    blocks: list[dict] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line == "Reply":
            break
        nxt = next((l for l in lines[i + 1:] if l), "")
        if re.fullmatch(r"[A-Z][A-Z .'-]*", line) and nxt in ROLES:
            blocks.append({"name": line.title(), "role": nxt, "text": []})
            i = lines.index(nxt, i + 1) + 1
            continue
        if blocks:
            blocks[-1]["text"].append(line)
        i += 1
    for b in blocks:
        b["text"] = re.sub(r"\n{2,}", "\n", "\n".join(b["text"])).strip()
        b["text"] = re.sub(r"(?<!\n)\n(?!\n)", " ", b["text"])  # undo the email's line wrapping
    return [b for b in blocks if b["text"]]


def _inquiry(text: str) -> tuple[str | None, str]:
    name = m[1].title() if (m := re.search(r"RESPOND TO (.+?)[’']S INQUIRY", text)) else None
    seg = text.split("Pre-approve", 1)[0]
    lines = [l.strip() for l in seg.splitlines() if l.strip() and not l.strip().startswith(("http", "["))]
    # after the guest's name, profile lines ("Identity verified · 2 reviews", "Miami, FL"), then the message
    start = next((k for k, l in enumerate(lines) if "review" in l.lower() or "verified" in l.lower()), None)
    body = lines[start + 2:] if start is not None else []
    return name, " ".join(body).strip()


def _guests(text: str) -> str | None:
    m = re.search(r"GUESTS\s*\n\s*(.+)", text)
    return m[1].strip() if m else None


def parse(email: dict) -> dict | None:
    """Notification email → {kind, platform, ...} or None for mail that needn't reach Telegram."""
    platform = _platform(email["from"])
    if not platform:
        return None
    subject = email["subject"].strip()
    base = {"id": email["id"], "platform": platform, "subject": subject, "date": email["date"],
            "thread_url": _thread_url(email["text"])}
    if platform == "VRBO":  # formats not seen yet: pass the subject through
        return base | {"kind": "other"}
    for kind, pattern in SUBJECTS:
        if m := pattern.search(subject):
            return base | {"kind": kind, **m.groupdict(), **_details(kind, email["text"])}
    return None  # account activity, price notices, review reminders, ...


def _details(kind: str, text: str) -> dict:
    if kind == "message":
        blocks = guest_messages(text)
        return {"messages": blocks, "guests": _guests(text)}
    if kind in ("inquiry", "request"):
        name, body = _inquiry(text)
        return {"guest": name, "body": body, "guests": _guests(text)}
    return {}


def card(item: dict, tz) -> tuple[str, InlineKeyboardMarkup | None] | None:
    """Telegram text and buttons for a parsed email; None to skip (e.g. Leanne's own reply)."""
    kind, dates = item["kind"], item.get("dates", "")
    guests = f" · {item['guests']}" if item.get("guests") else ""
    if kind == "message":
        from_guest = [b for b in item["messages"] if b["role"] not in HOST_ROLES]
        if not from_guest:
            return None
        quoted = "\n".join(f"{b['name']}: \"{b['text'][:700]}\"" for b in from_guest)
        text = f"💬 {item['platform']} message · {dates}{guests}\n{quoted}"
    elif kind in ("inquiry", "request"):
        due = datetime.fromtimestamp(item["date"] / 1000, tz) + timedelta(hours=24)
        label = "Inquiry" if kind == "inquiry" else "Booking request"
        what = f"\n\"{item['body'][:700]}\"" if item.get("body") else ""
        text = (f"⏳ {label}: {item.get('guest') or 'guest'} · {dates or item['subject']}{guests}{what}\n"
                f"Respond by {due:%a %-I:%M %p}")
    elif kind == "confirmed":
        return f"✅ Confirmed on {item['platform']}: {item['guest']} arrives {item['arrives']}", None
    elif kind == "canceled":
        return f"❌ Canceled on {item['platform']}: {item['code']} ({item['dates']})", None
    elif kind == "review":
        return f"⭐ {item['guest']} left a {item['stars']}-star review", None
    else:
        return f"📧 {item['platform']}: {item['subject']}", None
    rows = [[InlineKeyboardButton("✍️ Draft reply", callback_data=f"em|d|{item['id']}")]]
    if item.get("thread_url"):
        rows[0].append(InlineKeyboardButton("Open in Airbnb", url=item["thread_url"]))
    return text, InlineKeyboardMarkup(rows)


# ---------- the watcher ----------

def _load(cfg) -> dict:
    p = cfg.state_dir / "emails.json"
    return {"seen": {}, "items": {}} | (json.loads(p.read_text()) if p.exists() else {})


def _save(cfg, state: dict) -> None:
    state["items"] = dict(list(state["items"].items())[-100:])  # enough for Draft buttons on recent mail
    (cfg.state_dir / "emails.json").write_text(json.dumps(state, ensure_ascii=False))


def scan(cfg, inboxes, state: dict) -> list[dict]:
    """New notification emails across inboxes, oldest first. First read per inbox = silent baseline."""
    new = []
    for inbox in inboxes:
        ids = inbox.recent_ids()
        seen = state["seen"].get(inbox.label)
        if seen is None:
            state["seen"][inbox.label] = ids
            log.info("Email watch: baseline for %s (%d emails)", inbox.label, len(ids))
            continue
        fresh = [i for i in ids if i not in seen]
        for msg_id in reversed(fresh):
            if item := parse(inbox.get(msg_id)):
                new.append(item)
        state["seen"][inbox.label] = (fresh + seen)[:500]
    return sorted(new, key=lambda it: it["date"])


async def watch(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg, inboxes = ctx.bot_data["cfg"], ctx.bot_data["inboxes"]
    if not inboxes:
        return
    state = _load(cfg)
    items = await asyncio.to_thread(scan, cfg, inboxes, state)
    sent = 0
    for item in items:
        state["items"][item["id"]] = item
        shown = card(item, cfg.tz)
        if shown and cfg.allowed_chat_id is not None:
            await ctx.bot.send_message(cfg.allowed_chat_id, shown[0], reply_markup=shown[1])
            sent += 1
    _save(cfg, state)
    log.info("Email watch: %d new notification email(s), %d sent", len(items), sent)


def draft_prompt(item: dict) -> str:
    if item["kind"] == "message":
        convo = "\n".join(f"{b['name']} ({b['role']}): {b['text']}" for b in item["messages"])
        about = f"a guest message on {item['platform']} (stay {item.get('dates')}, {item.get('guests') or 'guests ?'})"
    else:
        convo = f"{item.get('guest') or 'Guest'}: {item.get('body') or '(message not in the email)'}"
        about = (f"an {item['platform']} {'inquiry' if item['kind'] == 'inquiry' else 'booking request'} "
                 f"for {item.get('dates')} ({item.get('guests') or 'guests ?'})")
    return (f"(Automated: I tapped Draft reply) Draft Leanne's reply to {about}. Check list_stays and property.md "
            f"for facts. Give me only the reply text, ready to paste, then one line on anything you weren't sure "
            f"about.\n\n{convo}")


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    cfg = ctx.bot_data["cfg"]
    if q.message.chat.id != cfg.allowed_chat_id:
        return
    _, action, msg_id = q.data.split("|")
    item = _load(cfg)["items"].get(msg_id)
    if not item:
        await q.answer("That email is too old for a draft now.", show_alert=True)
        return
    await q.answer("Drafting…")
    typing = asyncio.create_task(chat.keep_typing(ctx, cfg.allowed_chat_id))
    try:
        reply = await ctx.bot_data["agent"].respond(draft_prompt(item), source="draft")
    finally:
        typing.cancel()
    await chat.send(ctx.application, cfg.allowed_chat_id, reply)
