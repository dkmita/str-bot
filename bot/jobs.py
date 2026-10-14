"""Scheduled jobs, all code (no Claude): booking watch and the morning turnover note."""

import asyncio
import json
import logging
from datetime import date, datetime, time, timedelta

from telegram.ext import ContextTypes

import chat
import health
from emails import VORTEX_ROUTINE
from integrations import ical

log = logging.getLogger("str")

WATCH_EVERY = timedelta(hours=1)
MORNING = time(7, 0)
HOT_TUB_CHECK = time(18, 0)  # daily; Vortex's Monday report normally arrives between 9am and 3pm


def diff(before: dict[str, dict], after: dict[str, dict], today: str) -> list[str]:
    """Messages for new, cancelled and changed future stays (dicts keyed by uid)."""
    out = []
    for uid, s in after.items():
        if s["check_out"] <= today:
            continue
        old = before.get(uid)
        if old is None:
            out.append("🆕 New booking: " + ical.describe(s))
        elif (old["check_in"], old["check_out"]) != (s["check_in"], s["check_out"]):
            out.append(f"✏️ Dates changed: {ical.describe(s)} (was {old['check_in']} → {old['check_out']})")
    for uid, s in before.items():
        if uid not in after and s["check_out"] > today:
            out.append("❌ Cancelled: " + ical.describe(s))
    return out


def scan(cfg, state: dict) -> tuple[list[str], dict]:
    """One pass over the feeds → (messages, new state). The first pass per platform is a silent baseline;
    a platform whose feed fails, or suddenly shows no future stays, is skipped rather than read as cancellations."""
    today = datetime.now(cfg.tz).date().isoformat()
    messages, entries = [], []
    for platform, url in cfg.feeds.items():
        try:
            got = ical.parse(ical.fetch(url), platform)
        except Exception as e:
            log.warning("Booking watch: %s feed failed: %s", platform, e)
            entries += [s for s in state.get("entries", []) if s["platform"] == platform]
            continue
        stays = {s["uid"]: s for s in got if s["kind"] == "stay"}
        before = {s["uid"]: s for s in state.get("entries", []) if s["platform"] == platform and s["kind"] == "stay"}
        future_before = [s for s in before.values() if s["check_out"] > today]
        if future_before and not any(s["check_out"] > today for s in stays.values()):
            log.warning("Booking watch: %s feed shows no future stays (had %d); ignoring this read",
                        platform, len(future_before))
            entries += list(before.values())
            continue
        if platform in state.get("platforms", []):
            messages += diff(before, stays, today)
        entries += got
    seen = set(state.get("doubles", []))
    doubles = {f"{a['uid']}|{b['uid']}": (a, b) for a, b in ical.double_bookings(entries) if b["check_out"] > today}
    for key, (a, b) in doubles.items():
        if key not in seen:
            messages.insert(0, f"🚨 DOUBLE BOOKING: {ical.describe(a)} overlaps {ical.describe(b)}")
    return messages, {"platforms": sorted(set(state.get("platforms", [])) | set(cfg.feeds)),
                      "entries": entries, "doubles": sorted(doubles)}


async def watch(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = ctx.bot_data["cfg"]
    path = cfg.state_dir / "stays.json"
    state = json.loads(path.read_text()) if path.exists() else {}
    messages, state = await asyncio.to_thread(scan, cfg, state)
    path.write_text(json.dumps(state))
    health.job_ok(ctx.bot_data, "booking_watch")
    log.info("Booking watch: %d stays/blocks, %d change(s)", len(state["entries"]), len(messages))
    if messages and cfg.allowed_chat_id is not None:
        await chat.send(ctx.application, cfg.allowed_chat_id, "\n".join(messages))


def morning_text(entries: list[dict], today: date) -> str | None:
    """Check-outs and check-ins today, and tomorrow's arrivals (so the turnover can be planned)."""
    d0, d1 = today.isoformat(), (today + timedelta(days=1)).isoformat()
    stays = [s for s in entries if s["kind"] == "stay"]
    out = [s for s in stays if s["check_out"] == d0]
    arrive = [s for s in stays if s["check_in"] == d0]
    tomorrow = [s for s in stays if s["check_in"] == d1]
    lines = [f"🏠 Checking out today: {ical.describe(s)}" for s in out]
    lines += [f"🔑 Arriving today: {ical.describe(s)}" for s in arrive]
    if out and arrive:
        lines.append("↔️ Same-day turnover")
    lines += [f"📅 Arriving tomorrow: {ical.describe(s)}" for s in tomorrow]
    return "\n".join(lines) or None


def hot_tub_status(today: date, mails: list[tuple[date, str]], state: dict) -> tuple[str | None, dict]:
    """Vortex services the hot tub every Monday and emails a report (or work order) the same day. Only their emails
    count (the calendar events are hand-made and don't follow reschedules).
    `mails` = (day, subject) of this week's Vortex emails; `state` = {"week", "alerted", "arrived"}.
    Monday: nothing yet → alert. Later: a report arrives after an alert → note it. Friday: nothing all week → alert."""
    week = (today - timedelta(days=today.weekday())).isoformat()
    state = state if state.get("week") == week else {"week": week, "alerted": [], "arrived": False}
    visits = sorted(d for d, subject in mails if not VORTEX_ROUTINE.search(subject))
    if visits:
        if state["alerted"] and not state["arrived"]:
            state["arrived"] = True
            return f"✅ Hot tub: Vortex's report came in after all ({visits[-1]:%a %b %-d}).", state
        return None, state
    if today.weekday() == 0 and "mon" not in state["alerted"]:
        state["alerted"].append("mon")
        return ("🛁 No hot tub service report from Vortex today (Mon). They usually come Mondays and email by 3pm. "
                "I'll tell you if it shows up later this week."), state
    if today.weekday() == 4 and "fri" not in state["alerted"]:
        state["alerted"].append("fri")
        return "🛁 Still no hot tub service from Vortex this week (no report since Monday).", state
    return None, state


async def hot_tub_check(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg, inboxes = ctx.bot_data["cfg"], ctx.bot_data["inboxes"]
    if not inboxes:
        return
    now = datetime.now(cfg.tz)
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)

    def this_weeks_mail() -> list[tuple[date, str]]:
        q = f"from:getskimmer.com after:{int(monday.timestamp())}"
        out = []
        for ib in inboxes:
            for i in ib.recent_ids(q):
                m = ib.get(i)
                out.append((datetime.fromtimestamp(m["date"] / 1000, cfg.tz).date(), m["subject"]))
        return out
    mails = await asyncio.to_thread(this_weeks_mail)
    path = cfg.state_dir / "hot_tub.json"
    text, state = hot_tub_status(now.date(), mails, json.loads(path.read_text()) if path.exists() else {})
    path.write_text(json.dumps(state))
    log.info("Hot tub check: %d Vortex email(s) this week%s", len(mails), "; alert sent" if text else "")
    if text and cfg.allowed_chat_id is not None:
        await chat.send(ctx.application, cfg.allowed_chat_id, text)


async def morning(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = ctx.bot_data["cfg"]
    if cfg.allowed_chat_id is None:
        return
    entries = await asyncio.to_thread(ical.load_all, cfg.feeds)
    text = morning_text(entries, datetime.now(cfg.tz).date())
    log.info("Morning note: %s", "sent" if text else "nothing today")
    if text:
        await chat.send(ctx.application, cfg.allowed_chat_id, text)
