"""Guest door codes: reserve a week ahead with a placeholder, verify on the lock, clean up after checkout."""

from datetime import datetime

import doorcodes as dc
from conftest import TZ

P = "garage_lock_code_slot_"


class FakeHA:
    """Keymaster slots in memory; records every write so we can check permanent codes are never touched."""

    def __init__(self, slots):
        self.s, self.writes = {}, []
        for n, (name, pin, use_range, start, end, sync, active) in slots.items():
            self.s.update({f"text.{P}{n}_name": name, f"text.{P}{n}_pin": pin,
                           f"switch.{P}{n}_enabled": "on", f"switch.{P}{n}_use_date_range_limits": "on" if use_range else "off",
                           f"datetime.{P}{n}_date_range_start": start, f"datetime.{P}{n}_date_range_end": end,
                           f"sensor.{P}{n}_sync_status": sync, f"binary_sensor.{P}{n}_active": "on" if active else "off"})

    def states(self):
        return [{"entity_id": k, "state": v} for k, v in self.s.items()]

    def state(self, eid):
        return {"state": self.s.get(eid)}

    def call(self, domain, service, eid, **data):
        n = int(eid.split("_code_slot_")[1].split("_")[0])
        self.writes.append(n)
        if service == "set_value" and domain == "text":
            self.s[eid] = data["value"]
        elif service == "set_value" and domain == "datetime":
            self.s[eid] = datetime.strptime(data["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ).isoformat()
        elif service == "turn_on":
            self.s[eid] = "on"
        elif service == "press":  # Keymaster's slot reset
            for f in ("name", "pin"):
                self.s[f"text.{P}{n}_{f}"] = ""
            self.s[f"switch.{P}{n}_use_date_range_limits"] = "off"
            self.s[f"sensor.{P}{n}_sync_status"] = "Disconnected"
            self.s[f"binary_sensor.{P}{n}_active"] = "off"

    def lock(self, n, synced=True):  # what Keymaster does when the window opens
        self.s[f"sensor.{P}{n}_sync_status"] = "Synced" if synced else "Disconnected"
        self.s[f"binary_sensor.{P}{n}_active"] = "on" if synced else "off"


def stay(uid, ci, co):
    return {"uid": uid, "kind": "stay", "platform": "Airbnb", "check_in": ci, "check_out": co,
            "nights": 2, "code": "HM" + uid.upper(), "summary": "Reserved"}


def at(*a):
    return datetime(*a, tzinfo=TZ)


def run(state, stays, ha, now):
    return dc.step(state, stays, dc.read_slots(ha.states()), now, TZ, ha, lambda s: {"c": "Maya Ruiz"}.get(s["uid"]))


def test_full_lifecycle():
    ha = FakeHA({
        1: ("Owners", "1111", False, "unknown", "unknown", "Synced", True),
        2: ("Cleaners", "2222", False, "unknown", "unknown", "Synced", True),
        3: ("Oct 9", "3333", True, "2026-10-09T16:00:00+00:00", "2026-10-16T19:00:00+00:00", "Synced", True),
        4: ("Sep23", "4444", True, "2026-09-23T20:00:00+00:00", "2026-09-29T20:00:00+00:00", "Disconnected", False),
        5: ("Oct 16 Amy", "7309", True, "2026-10-16T17:00:00+00:00", "2026-10-18T19:00:00+00:00", "Disconnected", False),
        6: ("Verde Solar", "6666", False, "unknown", "unknown", "Synced", True),
        7: ("", "", False, "unknown", "unknown", "Disconnected", False),
        8: ("", "", False, "unknown", "unknown", "Disconnected", False),
    })
    stays = [stay("a", "2026-10-09", "2026-10-16"), stay("b", "2026-10-16", "2026-10-18"),
             stay("c", "2026-10-18", "2026-10-23"), stay("d", "2026-10-24", "2026-10-30")]
    state = {"codes": {}, "awaiting": None}

    msgs = run(state, stays, ha, at(2026, 10, 15, 12))  # deploy day
    assert [m for m, _ in msgs][0].startswith("🔑 Reserved garage slot 7 for Maya Ruiz (Airbnb Sun Oct 18")
    assert len(msgs) == 1 and msgs[0][1] == "c"  # adoptions are silent; Oct 24 is more than a week out
    assert state["codes"]["a"]["status"] == "verified" and state["codes"]["b"]["guest"] == "Amy"
    assert ha.s[f"text.{P}7_name"] == "Oct 18 Maya" and len(ha.s[f"text.{P}7_pin"]) == 6
    assert ha.s[f"datetime.{P}7_date_range_start"].startswith("2026-10-18T10:00")
    assert run(state, stays, ha, at(2026, 10, 15, 12, 15)) == []  # nothing new

    ha.lock(5)
    msgs = [m for m, _ in run(state, stays, ha, at(2026, 10, 16, 10, 20))]
    assert msgs == ["✅ Amy's code is on the garage lock (slot 5)."]  # no placeholder reminders

    msgs = [m for m, _ in run(state, stays, ha, at(2026, 10, 16, 13, 5))]
    assert msgs == ["🧹 Removed the guest's garage code (slot 3) after checkout."]
    assert ha.s[f"text.{P}3_pin"] == ""

    msgs = run(state, stays, ha, at(2026, 10, 17, 12))  # Oct 24 is now within a week: slot 3 (emptied) is reused
    assert msgs[0][0].startswith("🔑 Reserved garage slot 3 for the Airbnb guest")
    d_slot = state["codes"]["d"]["slot"]

    stays2 = [s for s in stays if s["uid"] != "d"]
    stays2[2] = stay("c", "2026-10-18", "2026-10-22")  # Maya leaves a day early
    assert run(state, stays2, ha, at(2026, 10, 17, 13)) == []  # moved and cancelled stays: handled silently
    assert ha.s[f"datetime.{P}7_date_range_end"].startswith("2026-10-22T12:00")
    assert ha.s[f"text.{P}{d_slot}_pin"] == ""
    assert not {1, 2, 6} & set(ha.writes)  # permanent codes never touched


def test_failed_write_is_reported_not_retried_into_a_second_slot():
    class Broken(FakeHA):
        def call(self, domain, service, eid, **data):
            if service == "set_value" and eid.endswith("_pin"):
                raise OSError("HA down")
            super().call(domain, service, eid, **data)
    ha = Broken({7: ("", "", False, "unknown", "unknown", "Disconnected", False)})
    state = {"codes": {}, "awaiting": None}
    msgs = run(state, [stay("c", "2026-10-18", "2026-10-23")], ha, at(2026, 10, 15, 12))
    assert msgs[0][0].startswith("⚠️ Couldn't reserve a garage code for the 2026-10-18 stay (slot 7): OSError")
    assert state["codes"] == {} and ha.s[f"text.{P}7_name"] == ""  # half-written slot undone


def test_codes_text():
    state = {"codes": {"b": {"slot": 5, "name": "Oct 16 Amy", "guest": "Amy", "status": "set", "placeholder": None,
                             "start": "2026-10-16T10:00:00-07:00", "end": "2026-10-18T12:00:00-07:00"}}}
    assert dc.codes_text(state) == "🔑 Guest codes (garage)\n- Oct 16 Amy · slot 5 · Fri Oct 16 10:00 AM → Sun 12:00 PM · scheduled"
