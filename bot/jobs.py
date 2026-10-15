"""Scheduled jobs, all code (no Claude): booking watch (double bookings) and the hot tub check.

Messages go out only when I need to act."""

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
HOT_TUB_CHECK = time(18, 0)  # daily; Vortex's Monday report normally arrives between 9am and 3pm


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


def hot_tub_status(today: date, mails: list[tuple[date, str]], state: dict) -> tuple[str | None, dict]:
    """Vortex services the hot tub every Monday and emails a report (or work order) the same day. Only their emails
    count (the calendar events are hand-made and don't follow reschedules).
    `mails` = (day, subject) of this week's Vortex emails; `state` = {"week", "alerted", "arrived"}.
    Monday: nothing yet → alert. Friday: still nothing all week → alert. (A late report needs no message.)"""
    week = (today - timedelta(days=today.weekday())).isoformat()
    state = state if state.get("week") == week else {"week": week, "alerted": [], "arrived": False}
    visits = sorted(d for d, subject in mails if not VORTEX_ROUTINE.search(subject))
    if visits:
        state["arrived"] = True  # late report after an alert: nothing to do, so no message
        return None, state
    if today.weekday() == 0 and "mon" not in state["alerted"]:
        state["alerted"].append("mon")
        return ("🛁 No hot tub service report from Vortex today (Mon). They usually come Mondays and email by 3pm: "
                "check with Vortex if the visit didn't happen."), state
    if today.weekday() == 4 and "fri" not in state["alerted"]:
        state["alerted"].append("fri")
        return "🛁 Still no hot tub service from Vortex this week (no report since Monday): contact Vortex.", state
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


