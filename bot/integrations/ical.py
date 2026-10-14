"""Reservations from the Airbnb and VRBO iCal export feeds (both are all-day events, end date = checkout).

Airbnb: SUMMARY "Reserved" (with the reservation link and the guest's phone last 4 in DESCRIPTION) or
"Airbnb (Not available)" for blocked nights, including nights blocked because the other platform has them.
VRBO: SUMMARY "Reserved - <name>" or "Blocked". Only "Reserved…" events count as stays.
"""

import re
from datetime import date, datetime

import httpx
from icalendar import Calendar

_CODE = re.compile(r"reservations/details/([A-Z0-9]+)")


def fetch(url: str) -> str:
    for attempt in range(3):
        try:
            r = httpx.get(url, timeout=20, follow_redirects=True)
            r.raise_for_status()
            return r.text
        except httpx.TransportError:
            if attempt == 2:
                raise


def _day(value) -> date:
    return value.date() if isinstance(value, datetime) else value


def parse(text: str, platform: str) -> list[dict]:
    """iCal text → stays and blocks: {platform, kind, uid, check_in, check_out, nights, summary, code}."""
    out = []
    for e in Calendar.from_ical(text).walk("VEVENT"):
        start, end = _day(e.decoded("DTSTART")), _day(e.decoded("DTEND"))
        summary = str(e.get("SUMMARY", "")).strip()
        desc = str(e.get("DESCRIPTION", ""))
        code = m[1] if (m := _CODE.search(desc)) else None  # icalendar has already unfolded wrapped lines
        out.append({"platform": platform, "kind": "stay" if summary.lower().startswith("reserved") else "block",
                    "uid": str(e.get("UID", "")), "check_in": start.isoformat(), "check_out": end.isoformat(),
                    "nights": (end - start).days, "summary": summary, "code": code})
    return sorted(out, key=lambda s: s["check_in"])


def load_all(feeds: dict[str, str]) -> list[dict]:
    """Stays and blocks from every configured platform, by check-in date."""
    return sorted((s for platform, url in feeds.items() for s in parse(fetch(url), platform)),
                  key=lambda s: (s["check_in"], s["platform"]))


def double_bookings(entries: list[dict]) -> list[tuple[dict, dict]]:
    """Pairs of stays on different platforms whose nights overlap (checkout day = next check-in is fine)."""
    stays = [s for s in entries if s["kind"] == "stay"]
    return [(a, b) for i, a in enumerate(stays) for b in stays[i + 1:]
            if a["platform"] != b["platform"] and a["check_in"] < b["check_out"] and b["check_in"] < a["check_out"]]


def describe(s: dict) -> str:
    d1, d2 = date.fromisoformat(s["check_in"]), date.fromisoformat(s["check_out"])
    return (f"{s['platform']} {d1:%a %b %-d} → {d2:%a %b %-d} ({s['nights']} night{'s' if s['nights'] != 1 else ''})"
            + (f" · {s['code']}" if s.get("code") else ""))
