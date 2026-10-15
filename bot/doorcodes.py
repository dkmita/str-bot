"""Guest door codes on the garage lock (Home Assistant + Keymaster), from booking to checkout. No Claude.

- A week before check-in: reserve a free guest slot named "Oct 16 Amy", active from arrival day 10:00 AM to checkout
  day noon, with a random placeholder PIN nobody knows (so it's harmless if never replaced). The real code is set by
  me (✏️ Set PIN, or in Home Assistant). Planned: use the last 4 digits of the guest's phone (Airbnb's iCal has them;
  VRBO's booking email has the number) so no manual step is needed.
- 15 min after the window opens: check Keymaster synced the code to the lock. An hour after it ends: reset the slot.
- Messages only when I need to act: a code that didn't reach the lock, no free slot, or a failed write.
- Cancelled or moved stays: the slot is reset or its window moved, without a message (failures are still reported).
Only guest slots are used: empty ones, or ones with date-range limits. Permanent codes (Owners, Cleaners, ...) are
never touched. PINs aren't logged or stored (only a hash of the placeholder, to tell when it's been replaced).
"""

import asyncio
import hashlib
import json
import logging
import re
import secrets
from datetime import date, datetime, time, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

import chat
from integrations import ical

log = logging.getLogger("str")

LOCK = "garage_lock"  # Keymaster's name for the guest lock
SLOTS = range(1, 11)
LEAD = timedelta(days=7)
START, END = time(10, 0), time(12, 0)  # arrival day (after the previous checkout) → checkout day noon
VERIFY_AFTER = timedelta(minutes=15)
CLEANUP_AFTER = timedelta(hours=1)
CHECK_EVERY = timedelta(minutes=15)
EMPTY = ("", "unknown", "unavailable", None)


def _h(pin: str) -> str:
    return hashlib.sha256(f"str-bot|{pin}".encode()).hexdigest()


def window(stay: dict, tz) -> tuple[datetime, datetime]:
    return (datetime.combine(date.fromisoformat(stay["check_in"]), START, tz),
            datetime.combine(date.fromisoformat(stay["check_out"]), END, tz))


def slot_name(stay: dict, guest: str | None) -> str:
    d = date.fromisoformat(stay["check_in"])
    return f"{d:%b} {d.day}" + (f" {guest.split()[0]}" if guest else "")


# ---------- reading the lock's slots ----------

def read_slots(states: list[dict]) -> dict[int, dict]:
    """Keymaster slot n → {name, pin, enabled, use_range, start, end, sync, active} for the guest lock."""
    by_id = {s["entity_id"]: s["state"] for s in states}
    out = {}
    for n in SLOTS:
        p = f"{LOCK}_code_slot_{n}"
        if f"text.{p}_pin" not in by_id:
            continue
        out[n] = {"name": by_id.get(f"text.{p}_name"), "pin": by_id.get(f"text.{p}_pin"),
                  "enabled": by_id.get(f"switch.{p}_enabled") == "on",
                  "use_range": by_id.get(f"switch.{p}_use_date_range_limits") == "on",
                  "start": by_id.get(f"datetime.{p}_date_range_start"), "end": by_id.get(f"datetime.{p}_date_range_end"),
                  "sync": by_id.get(f"sensor.{p}_sync_status"), "active": by_id.get(f"binary_sensor.{p}_active") == "on"}
    return out


def is_guest_slot(s: dict) -> bool:
    """Empty, or limited to a date range. Named codes without a range (Owners, Cleaners, ...) are permanent."""
    return s["use_range"] or (s["name"] in EMPTY and s["pin"] in EMPTY)


def free_slot(slots: dict[int, dict], taken: set[int], now: datetime) -> int | None:
    def expired(s):
        try:
            return s["use_range"] and datetime.fromisoformat(s["end"]) < now - CLEANUP_AFTER
        except (TypeError, ValueError):
            return False
    for want in (lambda s: s["name"] in EMPTY and s["pin"] in EMPTY, expired):  # empty first, then expired guests
        for n, s in sorted(slots.items()):
            if n not in taken and is_guest_slot(s) and want(s):
                return n
    return None


def placeholder(slots: dict[int, dict]) -> str:
    used = {s["pin"] for s in slots.values()}
    while True:
        pin = f"{secrets.randbelow(900000) + 100000}"  # 6 digits: nobody will guess it
        if pin not in used:
            return pin


# ---------- writing ----------

def write_slot(ha, n: int, name: str, pin: str | None, start: datetime, end: datetime) -> None:
    p = f"{LOCK}_code_slot_{n}"
    ha.call("text", "set_value", f"text.{p}_name", value=name)
    if pin is not None:
        ha.call("text", "set_value", f"text.{p}_pin", value=pin)
    ha.call("datetime", "set_value", f"datetime.{p}_date_range_start", datetime=start.strftime("%Y-%m-%d %H:%M:%S"))
    ha.call("datetime", "set_value", f"datetime.{p}_date_range_end", datetime=end.strftime("%Y-%m-%d %H:%M:%S"))
    ha.call("switch", "turn_on", f"switch.{p}_use_date_range_limits")
    ha.call("switch", "turn_on", f"switch.{p}_enabled")


def reset_slot(ha, n: int) -> None:
    ha.call("button", "press", f"button.{LOCK}_code_slot_{n}_reset")


# ---------- state (.state/doorcodes.json) ----------

def load(cfg) -> dict:
    p = cfg.state_dir / "doorcodes.json"
    return {"codes": {}, "awaiting": None} | (json.loads(p.read_text()) if p.exists() else {})


def save(cfg, state: dict) -> None:
    (cfg.state_dir / "doorcodes.json").write_text(json.dumps(state, indent=1))


# ---------- one pass ----------

def attempt(out: list, fn, fail: str, undo=None) -> bool:
    """Run one Home Assistant change; on failure report it (and undo a half-written slot) instead of aborting."""
    try:
        fn()
        return True
    except Exception as e:
        log.warning("Door codes: %s: %s", fail, e)
        if undo:
            try:
                undo()
            except Exception:
                pass
        out.append((f"{fail}: {type(e).__name__}.", None))
        return False


def step(state: dict, stays: list[dict], slots: dict[int, dict], now: datetime, tz, ha, names) -> list[tuple[str, str | None]]:
    """Plan, adopt, update, verify and clean up. Returns (message, uid for a ✏️ Set PIN button or None).
    `names(stay)` → guest name or None (from the booking emails)."""
    out = []
    codes = state["codes"]
    future = {s["uid"]: s for s in stays if s["kind"] == "stay" and s["check_out"] >= now.date().isoformat()}
    taken = {c["slot"] for c in codes.values() if c["status"] != "removed"}

    # adopt guest slots set up by hand (e.g. Amy's) so they get verified and cleaned up too
    for n, s in slots.items():
        if n in taken or not s["use_range"] or s["pin"] in EMPTY:
            continue
        try:
            start = datetime.fromisoformat(s["start"]).astimezone(tz)
            end = datetime.fromisoformat(s["end"]).astimezone(tz)
        except (TypeError, ValueError):
            continue
        if end < now:
            continue
        stay = next((x for x in future.values() if x["check_in"] == start.date().isoformat()), None)
        uid = stay["uid"] if stay else f"slot{n}-{start.date()}"
        if uid in codes and codes[uid]["status"] != "removed":
            continue
        guest = re.sub(r"^[A-Za-z]{3} ?\d{1,2}\s*", "", s["name"] or "").strip() or "the guest"
        on_lock = s["active"] and s["sync"] == "Synced"
        codes[uid] = {"slot": n, "name": s["name"], "guest": guest, "start": start.isoformat(), "end": end.isoformat(),
                      "placeholder": None, "status": "verified" if on_lock else "set",
                      "check_in": start.date().isoformat(),
                      "check_out": stay["check_out"] if stay else end.date().isoformat(), "reminded": []}
        taken.add(n)
        log.info("Door codes: adopted slot %d (%s)", n, s["name"])

    # cancellations and date changes (only for stays we can still see the feed for)
    for uid, c in codes.items():
        if c["status"] == "removed" or uid.startswith("slot"):
            continue
        stay = future.get(uid)
        start = datetime.fromisoformat(c["start"])
        if stay is None and start > now:
            if not attempt(out, lambda: reset_slot(ha, c["slot"]), f"⚠️ Couldn't remove the code in slot {c['slot']}"):
                continue
            c["status"] = "removed"  # silent: I hear about cancellations from the platforms
            log.info("Door codes: stay %s cancelled, slot %d reset", c["name"], c["slot"])
        elif stay is not None and (stay["check_in"], stay["check_out"]) != (c["check_in"], c.get("check_out")):
            new_start, new_end = window(stay, tz)  # the booking's dates changed
            if attempt(out, lambda: write_slot(ha, c["slot"], c["name"], None, new_start, new_end),
                       f"⚠️ Couldn't move {c['guest']}'s garage code to the new dates (slot {c['slot']})"):
                c.update(start=new_start.isoformat(), end=new_end.isoformat(), check_in=stay["check_in"],
                         check_out=stay["check_out"])  # silent, like the cancellation case
                log.info("Door codes: %s moved to %s → %s", c["name"], new_start, new_end)

    # reserve slots a week ahead
    covered = {c.get("check_in") for c in codes.values() if c["status"] != "removed"}
    for uid, stay in sorted(future.items(), key=lambda kv: kv[1]["check_in"]):
        start, end = window(stay, tz)
        if uid in codes or stay["check_in"] in covered or start - now > LEAD or end < now:
            continue
        n = free_slot(slots, taken, now)
        if n is None:
            out.append((f"⚠️ No free guest slot on the garage lock for the {stay['check_in']} stay.", None))
            continue
        guest = names(stay)
        name, pin = slot_name(stay, guest), placeholder(slots)
        taken.add(n)
        if not attempt(out, lambda: write_slot(ha, n, name, pin, start, end),
                       f"⚠️ Couldn't reserve a garage code for the {stay['check_in']} stay (slot {n})",
                       undo=lambda: reset_slot(ha, n)):
            continue
        codes[uid] = {"slot": n, "name": name, "guest": guest or "the guest", "start": start.isoformat(),
                      "end": end.isoformat(), "placeholder": _h(pin), "status": "set", "check_in": stay["check_in"],
                      "check_out": stay["check_out"], "reminded": []}
        log.info("Door codes: reserved slot %d for %s (%s)", n, name, stay["check_in"])  # no action needed

    # verification, cleanup
    for uid, c in codes.items():
        if c["status"] == "removed":
            continue
        s = slots.get(c["slot"], {})
        start, end = datetime.fromisoformat(c["start"]), datetime.fromisoformat(c["end"])
        if c["status"] == "set" and now >= start + VERIFY_AFTER and now < end:
            if s.get("sync") == "Synced" and s.get("active"):
                c["status"] = "verified"  # on the lock: no message needed
                log.info("Door codes: %s verified on the lock (slot %d)", c["name"], c["slot"])
            elif "verify" not in c["reminded"]:
                c["reminded"].append("verify")
                out.append((f"❗ {c['guest']}'s code isn't on the garage lock yet (slot {c['slot']}: "
                            f"{s.get('sync') or 'unknown'}). Check Keymaster / the lock's Z-Wave connection.", None))
        if now >= end + CLEANUP_AFTER:
            if not attempt(out, lambda: reset_slot(ha, c["slot"]), f"⚠️ Couldn't remove {c['guest']}'s code (slot {c['slot']})"):
                continue
            c["status"] = "removed"
            log.info("Door codes: removed %s (slot %d) after checkout", c["name"], c["slot"])
    for uid in [u for u, c in codes.items() if c["status"] == "removed" and
                datetime.fromisoformat(c["end"]) < now - timedelta(days=14)]:
        del codes[uid]
    return out


# ---------- Telegram ----------

def keyboard(uid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("✏️ Set PIN", callback_data=f"dc|pin|{uid[:48]}")]])


def guest_names(inboxes):
    """stay → guest name, from the platforms' booking emails (Airbnb "arrives Oct 16", VRBO "…: Oct 16 - …")."""
    def lookup(stay: dict) -> str | None:
        d = date.fromisoformat(stay["check_in"])
        md = f"{d:%b} {d.day}"
        q = (f'from:airbnb.com subject:"Reservation confirmed" subject:"arrives {md}"' if stay["platform"] == "Airbnb"
             else f'from:homeaway.com subject:"{md}" (subject:"Reservation from" OR subject:"Instant Booking from")')
        for ib in inboxes:
            for i in ib.recent_ids(q + " newer_than:1y")[:1]:
                subject = ib.get(i)["subject"]
                m = re.search(r"confirmed - (.+?) arrives", subject) or re.search(r"from (.+?):", subject)
                if m:
                    return m[1].strip()
        return None
    return lookup


async def job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg, ha = ctx.bot_data["cfg"], ctx.bot_data.get("ha")
    if not ha:
        return

    def run():
        stays = ical.load_all(cfg.feeds)  # raises if a feed fails: then nothing is changed this round
        if not any(s["kind"] == "stay" and s["check_out"] >= datetime.now(cfg.tz).date().isoformat() for s in stays):
            raise RuntimeError("no future stays in the feeds; not acting on an empty read")
        state = load(cfg)
        msgs = step(state, stays, read_slots(ha.states()), datetime.now(cfg.tz), cfg.tz, ha,
                    guest_names(ctx.bot_data["inboxes"]))
        save(cfg, state)
        return msgs
    msgs = await asyncio.to_thread(run)
    import health
    health.job_ok(ctx.bot_data, "door_codes")
    for text, uid in msgs:
        log.info("Door codes: %s", text.splitlines()[0])
        if cfg.allowed_chat_id is not None:
            await ctx.bot.send_message(cfg.allowed_chat_id, text, reply_markup=keyboard(uid) if uid else None)


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    cfg = ctx.bot_data["cfg"]
    if q.message.chat.id != cfg.allowed_chat_id:
        return
    uid = q.data.split("|", 2)[2]
    state = load(cfg)
    match = next((u for u in state["codes"] if u[:48] == uid and state["codes"][u]["status"] != "removed"), None)
    if not match:
        await q.answer("That code slot is gone.", show_alert=True)
        return
    state["awaiting"] = {"uid": match, "until": (datetime.now(cfg.tz) + timedelta(minutes=30)).isoformat()}
    save(cfg, state)
    await q.answer()
    c = state["codes"][match]
    await ctx.bot.send_message(cfg.allowed_chat_id, f"Type the garage code for {c['guest']} ({c['name']}), 4–8 digits.")


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    """After ✏️ Set PIN: a 4–8 digit message is the code. Anything else falls through to the assistant."""
    cfg, ha = ctx.bot_data["cfg"], ctx.bot_data.get("ha")
    state = load(cfg)
    w = state.get("awaiting")
    text = (update.message.text or "").strip()
    if not w or datetime.fromisoformat(w["until"]) < datetime.now(cfg.tz) or not re.fullmatch(r"\d{4,8}", text):
        return False
    c = state["codes"].get(w["uid"])
    state["awaiting"] = None
    if not c or not ha:
        save(cfg, state)
        return False
    try:
        await asyncio.to_thread(ha.call, "text", "set_value", f"text.{LOCK}_code_slot_{c['slot']}_pin", value=text)
        ok = await asyncio.to_thread(lambda: ha.state(f"text.{LOCK}_code_slot_{c['slot']}_pin")["state"] == text)
    except Exception as e:
        log.warning("Setting a guest PIN failed: %s", e)
        ok = False
    c["placeholder"] = None if ok else c.get("placeholder")
    save(cfg, state)
    start = datetime.fromisoformat(c["start"])
    await update.message.reply_text(
        f"✅ Garage code for {c['guest']} saved in slot {c['slot']}. It goes onto the lock {start:%a %b %-d %-I:%M %p}; "
        "I'll confirm then." if ok else "⚠️ Couldn't save the code in Home Assistant. Try again, or set it there.")
    return True


def codes_text(state: dict) -> str:
    live = sorted((c for c in state["codes"].values() if c["status"] != "removed"), key=lambda c: c["start"])
    if not live:
        return "🔑 No guest codes scheduled."
    lines = ["🔑 Guest codes (garage)"]
    for c in live:
        s, e = datetime.fromisoformat(c["start"]), datetime.fromisoformat(c["end"])
        status = {"set": "scheduled", "verified": "on the lock"}.get(c["status"], c["status"])
        lines.append(f"- {c['name']} · slot {c['slot']} · {s:%a %b %-d %-I:%M %p} → {e:%a %-I:%M %p} · {status}"
                     + (" · placeholder PIN" if c.get("placeholder") else ""))
    return "\n".join(lines)


async def on_codes(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = ctx.bot_data["cfg"]
    if chat.is_allowed(cfg, update):
        await update.message.reply_text(codes_text(load(cfg)))
