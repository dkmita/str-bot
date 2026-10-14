"""Booking watch and morning note (no network: the feeds are faked)."""

from datetime import date

import pytest

import jobs
from conftest import ics
from integrations import ical


@pytest.fixture
def feeds(monkeypatch):
    data = {"a": ics(("a1", "20261018", "20261023", "Reserved")), "v": ics(("v1", "20261101", "20261104", "Reserved - Sam"))}

    def fetch(url):
        if isinstance(data[url], Exception):
            raise data[url]
        return data[url]
    monkeypatch.setattr(ical, "fetch", fetch)
    monkeypatch.setattr(jobs, "datetime", type("D", (), {"now": staticmethod(lambda tz=None: _now)}))
    return data


_now = __import__("datetime").datetime(2026, 10, 15, 9, 0)


def test_watch_baseline_then_changes(cfg, feeds):
    msgs, state = jobs.scan(cfg, {})
    assert msgs == []  # first read is a silent baseline
    feeds["a"] = ics(("a1", "20261018", "20261024", "Reserved"), ("a3", "20261110", "20261112", "Reserved"))
    feeds["v"] = ics()
    msgs, state = jobs.scan(cfg, state)
    assert msgs == ["✏️ Dates changed: Airbnb Sun Oct 18 → Sat Oct 24 (6 nights) · HMA1 (was 2026-10-18 → 2026-10-23)",
                    "🆕 New booking: Airbnb Tue Nov 10 → Thu Nov 12 (2 nights) · HMA3"]
    # VRBO suddenly showing no future stays is treated as a bad read, not as a cancellation
    assert any(s["uid"] == "v1" for s in state["entries"])


def test_watch_cancellation_failed_feed_and_double_booking(cfg, feeds):
    _, state = jobs.scan(cfg, {})
    feeds["v"] = ics(("v1", "20261101", "20261104", "Reserved - Sam"), ("v2", "20261020", "20261022", "Reserved - Kim"))
    feeds["a"] = OSError("timeout")
    msgs, state = jobs.scan(cfg, state)
    assert msgs[0].startswith("🚨 DOUBLE BOOKING: Airbnb Sun Oct 18") and msgs[1].startswith("🆕 New booking: VRBO")
    msgs, state = jobs.scan(cfg, state)
    assert msgs == []  # the double booking is only announced once
    feeds["a"] = ics(("a9", "20261201", "20261203", "Reserved"))
    msgs, _ = jobs.scan(cfg, state)
    assert "❌ Cancelled: Airbnb Sun Oct 18 → Fri Oct 23 (5 nights) · HMA1" in msgs


def test_morning_text():
    entries = ical.parse(ics(("a1", "20261018", "20261023", "Reserved"), ("a2", "20261023", "20261025", "Reserved"),
                             ("a3", "20261024", "20261026", "Reserved")), "Airbnb")
    text = jobs.morning_text(entries, date(2026, 10, 23))
    assert text.splitlines() == ["🏠 Checking out today: Airbnb Sun Oct 18 → Fri Oct 23 (5 nights) · HMA1",
                                 "🔑 Arriving today: Airbnb Fri Oct 23 → Sun Oct 25 (2 nights) · HMA2",
                                 "↔️ Same-day turnover",
                                 "📅 Arriving tomorrow: Airbnb Sat Oct 24 → Mon Oct 26 (2 nights) · HMA3"]
    assert jobs.morning_text(entries, date(2026, 10, 20)) is None


def test_hot_tub_check():
    mon, tue, fri = date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 23)
    text, st = jobs.hot_tub_status(mon, [], {})
    assert text.startswith("🛁 No hot tub service report from Vortex today (Mon)")
    assert jobs.hot_tub_status(mon, [], st)[0] is None                     # once
    text, st = jobs.hot_tub_status(tue, [(tue, "Pool/Spa Service Report for 10/20/2026")], st)
    assert text == "✅ Hot tub: Vortex's report came in after all (Tue Oct 20)."
    assert jobs.hot_tub_status(fri, [(tue, "Pool/Spa Service Report for 10/20/2026")], st)[0] is None
    # on time: silent all week; invoices don't count as a visit
    assert jobs.hot_tub_status(mon, [(mon, "Pool/Spa Service Report for 10/19/2026")], {})[0] is None
    _, st = jobs.hot_tub_status(mon, [(mon, "Invoice 2939 is ready!")], {})
    assert jobs.hot_tub_status(fri, [(mon, "Invoice 2939 is ready!")], st)[0].startswith("🛁 Still no hot tub service")
    # a new week starts fresh
    assert jobs.hot_tub_status(date(2026, 10, 26), [], st)[0].startswith("🛁 No hot tub service report")
