from datetime import date

from conftest import ics
from integrations import ical


def test_parse_airbnb_feed():
    got = ical.parse(ics(("a1", "20261018", "20261023", "Reserved"), ("a2", "20261101", "20261103", "Airbnb (Not available)")),
                     "Airbnb")
    assert [(s["kind"], s["check_in"], s["nights"], s["code"]) for s in got] == \
        [("stay", "2026-10-18", 5, "HMA1"), ("block", "2026-11-01", 2, None)]
    assert ical.describe(got[0]) == "Airbnb Sun Oct 18 → Fri Oct 23 (5 nights) · HMA1"


def test_double_bookings_only_across_platforms_and_real_overlaps():
    a = ical.parse(ics(("a1", "20261018", "20261023", "Reserved")), "Airbnb")
    v = ical.parse(ics(("v1", "20261022", "20261025", "Reserved - Sam"), ("v2", "20261023", "20261024", "Reserved - Kim"),
                       ("v3", "20261019", "20261020", "Blocked")), "VRBO")
    pairs = ical.double_bookings(a + v)
    assert [(x["uid"], y["uid"]) for x, y in pairs] == [("a1", "v1")]  # v2 starts on checkout day; v3 is a block
