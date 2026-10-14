"""Airbnb/VRBO notification emails → short Telegram messages, sorted by code (no Claude).

Guest messages that need an answer, inquiries and booking requests get a suggested reply right away (Claude, as
Leanne; ~9¢ each, thank-yous skipped), sent as a reply to the card, plus ✍️ Redraft and "Open in Airbnb" buttons. Leanne's own replies (role "Host") and routine mail are skipped. The first read of an
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
import inbox

log = logging.getLogger("str")

CHECK_EVERY = timedelta(minutes=5)
ROLES = {"Booker", "Guest", "Co-traveler", "Host", "Co-host"}
HOST_ROLES = {"Host", "Co-host"}

SUBJECTS = [  # Airbnb: (kind, pattern) — first match wins
    ("message", re.compile(r"^RE: (?:Reservation|Pre-approval|Inquiry|Request) for (?P<listing>.+?), (?P<dates>.+)$")),
    ("inquiry", re.compile(r"^Inquiry for (?P<listing>.+?) for (?P<dates>.+)$")),
    ("request", re.compile(r"^Pending: Reservation Request")),
    ("confirmed", re.compile(r"^Reservation confirmed - (?P<guest>.+?) arrives (?P<arrives>.+)$")),
    ("canceled", re.compile(r"^Canceled: Reservation (?P<code>\w+) for (?P<dates>.+)$")),
    ("review", re.compile(r"^(?P<guest>.+?) left a (?P<stars>\d)-star review")),
]
VRBO_SUBJECTS = [  # from messages.homeaway.com / reviews.homeaway.com; everything else from VRBO is marketing
    ("confirmed", re.compile(r"^(?:Reservation|Instant Booking) from (?P<guest>.+?): (?P<dates>.+?) - Vrbo #")),
    ("inquiry", re.compile(r"^Inquiry from (?P<guest>.+?): (?P<dates>.+?) - Vrbo #")),
    ("message", re.compile(r"^Message from (?P<guest>.+?): (?P<dates>.+?) - Vrbo #")),
    ("canceled", re.compile(r"^Booking canceled by traveler: (?P<dates>.+?) \(Property ID \d+\) Reservation (?P<code>\S+)")),
    ("review", re.compile(r"^(?P<guest>\S+) reviewed their stay")),
]


# ---------- parsing (pure) ----------

def _platform(sender: str) -> str | None:
    s = sender.lower()
    if "getskimmer.com" in s:
        return "Vortex"  # hot tub service (Vortex Pools & Spas, via the Skimmer app)
    return "Airbnb" if "airbnb.com" in s else "VRBO" if ("vrbo.com" in s or "homeaway.com" in s) else None


VORTEX_ROUTINE = re.compile(r"invoice|payment|billing|portal|updating our", re.I)  # not a service visit


def _vortex_notes(text: str) -> str:
    """The technician's notes in a Skimmer work-order email (between the greeting and DATE COMPLETED)."""
    body = re.split(r"DATE (?:COMPLETED|OF SERVICE)", text)[0]
    body = body.split("needs attention or repair.", 1)[-1].split("Service Update", 1)[-1]
    lines = [l.strip() for l in body.splitlines()]
    lines = [l for l in lines if l and l not in ("Vortex Pools & Spas", "SKIMMER | Work Order Email")
             and not l.startswith(("Hi ", "We noticed")) and not l.isdigit()]
    return " · ".join(lines)[:500]


def _thread_url(text: str) -> str | None:
    if m := re.search(r"https://www\.airbnb\.com/hosting/thread/(\d+)", text):
        return f"https://www.airbnb.com/hosting/thread/{m[1]}"
    if m := re.search(r"https://www\.vrbo\.com/supply/interaction\?interactionId=([\w-]+)", text):
        return f"https://www.vrbo.com/supply/interaction?interactionId={m[1]}"
    return None


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
    m = re.search(r"(?:GUESTS|Guests:)\s*\n\s*(.+)", text)
    return m[1].strip().removesuffix(", 0 children") if m else None


def _vrbo_body(text: str) -> str:
    """The traveler's own words in a VRBO inquiry/message ("Further info" … "Respond to this …")."""
    m = re.search(r"Further info\s*\n(.*?)\n\s*Respond to this", text, re.S)
    return " ".join(m[1].split()) if m else ""


def parse(email: dict) -> dict | None:
    """Notification email → {kind, platform, ...} or None for mail that needn't reach Telegram."""
    platform = _platform(email["from"])
    if not platform:
        return None
    subject = email["subject"].strip()
    base = {"id": email["id"], "platform": platform, "subject": subject, "date": email["date"],
            "thread_url": _thread_url(email["text"])}
    if platform == "Vortex":
        if "Service Report" in subject:
            return base | {"kind": "service_report"}  # routine: checked by jobs.hot_tub_check, not shown
        if VORTEX_ROUTINE.search(subject):
            return None
        return base | {"kind": "work_order", "notes": _vortex_notes(email["text"])}
    if platform == "VRBO":
        for kind, pattern in VRBO_SUBJECTS:
            if m := pattern.search(subject):
                body = _vrbo_body(email["text"])
                extra = {"messages": [{"name": m["guest"], "role": "Guest", "text": body}] if body else []} \
                    if kind == "message" else {"body": body} if kind == "inquiry" else {}
                return base | {"kind": kind, **m.groupdict(), "guests": _guests(email["text"]), **extra}
        return None  # marketing, tips, "review your guest" reminders
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


def wants_draft(item: dict) -> bool:
    """Draft automatically for inquiries/requests and guest messages that need an answer."""
    if item["kind"] in ("inquiry", "request"):
        return True
    msgs = item.get("messages", []) if item["kind"] == "message" else []
    if not msgs or msgs[-1]["role"] in HOST_ROLES:
        return False
    last_host = max((i for i, b in enumerate(msgs) if b["role"] in HOST_ROLES), default=-1)
    return inbox.needs_reply(" ".join(b["text"] for b in msgs[last_host + 1:]))


def card(item: dict, tz) -> tuple[str, InlineKeyboardMarkup | None] | None:
    """Telegram text and buttons for a parsed email; None to skip (e.g. Leanne's own reply)."""
    kind, dates = item["kind"], item.get("dates", "")
    if kind == "service_report":
        return None
    if kind == "work_order":
        return f"🛁 Hot tub (Vortex): {item['subject']}" + (f"\n{item['notes']}" if item.get("notes") else ""), None
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
        when = f"arrives {item['arrives']}" if item.get("arrives") else item["dates"]
        return f"✅ New booking on {item['platform']}: {item['guest']} · {when}{guests}", None
    elif kind == "canceled":
        return f"❌ Canceled on {item['platform']}: {item['code']} ({item['dates']})", None
    else:  # review
        stars = f" a {item['stars']}-star review" if item.get("stars") else " a review"
        return f"⭐ {item['guest']} left{stars} on {item['platform']}", None
    rows = [[InlineKeyboardButton("✍️ Redraft" if wants_draft(item) else "✍️ Draft reply", callback_data=f"em|d|{item['id']}")]]
    if item.get("thread_url"):
        rows[0].append(InlineKeyboardButton(f"Open in {item['platform']}", url=item["thread_url"]))
    return text, InlineKeyboardMarkup(rows)


# ---------- the watcher ----------

def _load(cfg) -> dict:
    p = cfg.state_dir / "emails.json"
    return {"seen": {}, "items": {}} | (json.loads(p.read_text()) if p.exists() else {})


def _save(cfg, state: dict) -> None:
    state["items"] = dict(list(state["items"].items())[-100:])  # enough for Draft buttons on recent mail
    (cfg.state_dir / "emails.json").write_text(json.dumps(state, ensure_ascii=False))


DUPLICATE_WINDOW = 30 * 60 * 1000  # ms: Airbnb mails both hosts; the same subject within 30 min is one event


def scan(cfg, inboxes, state: dict) -> list[dict]:
    """New notification emails across inboxes, oldest first. First read per inbox = silent baseline;
    the same notification arriving in both inboxes is reported once."""
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
    recent = state.setdefault("recent", [])  # [platform|subject, date ms] of reported notifications
    out = []
    for item in sorted(new, key=lambda it: it["date"]):
        key = f"{item['platform']}|{item['subject']}"
        if any(k == key and abs(d - item["date"]) < DUPLICATE_WINDOW for k, d in recent):
            continue
        recent.append([key, item["date"]])
        out.append(item)
    state["recent"] = recent[-200:]
    return out


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
            msg = await ctx.bot.send_message(cfg.allowed_chat_id, shown[0], reply_markup=shown[1])
            sent += 1
            if wants_draft(item):
                item["draft"] = await _draft(ctx, item, reply_to=msg.message_id)
    _save(cfg, state)
    await inbox.after_email_scan(ctx, items)
    import health
    health.job_ok(ctx.bot_data, "email_watch")
    log.info("Email watch: %d new notification email(s), %d sent", len(items), sent)


def draft_prompt(item: dict, voice: str = "") -> str:
    """The Draft reply request. `voice` = notes/leanne-voice.md (inlined: saves a tool call per draft)."""
    if item["kind"] == "message":
        convo = "\n".join(f"{b['name']} ({b['role']}): {b['text']}" for b in item["messages"])
        about = f"a guest message on {item['platform']} (stay {item.get('dates')}, {item.get('guests') or 'guests ?'})"
    else:
        convo = f"{item.get('guest') or 'Guest'}: {item.get('body') or '(message not in the email)'}"
        about = (f"an {item['platform']} {'inquiry' if item['kind'] == 'inquiry' else 'booking request'} "
                 f"for {item.get('dates')} ({item.get('guests') or 'guests ?'})")
    style = f"\n\nHow Leanne writes (notes/leanne-voice.md):\n{voice.strip()}" if voice.strip() else ""
    return (f"(Automated draft request) Draft Leanne's reply to {about}. Use the Q&A in property.md for facts "
            f"(read it) and list_stays for dates. Give me only the reply text, ready to paste, then one line on anything "
            f"you weren't sure about or that's missing from property.md.{style}\n\n{convo}")


async def _draft(ctx: ContextTypes.DEFAULT_TYPE, item: dict, reply_to: int | None = None) -> str | None:
    """Claude drafts Leanne's reply and it's sent to the chat (as a reply to the card if given). Never raises."""
    cfg = ctx.bot_data["cfg"]
    voice_file = cfg.repo_dir / "notes" / "leanne-voice.md"
    voice = voice_file.read_text() if voice_file.exists() else ""
    try:
        draft = await ctx.bot_data["agent"].respond(draft_prompt(item, voice), source="draft")
    except Exception:
        log.exception("Drafting a reply failed")
        return None
    await ctx.bot.send_message(cfg.allowed_chat_id, "✍️ Suggested reply:\n\n" + draft[:3900],
                               reply_to_message_id=reply_to)
    return draft


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
        draft = await _draft(ctx, item, reply_to=q.message.message_id)
    finally:
        typing.cancel()
    if draft:
        state = _load(cfg)
        if msg_id in state["items"]:
            state["items"][msg_id]["draft"] = draft
            _save(cfg, state)
