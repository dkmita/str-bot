"""Airbnb notification emails → Telegram cards (layouts copied from real emails, names changed)."""

import pytest

import emails
from conftest import TZ

MESSAGE = """%opentrack%
https://www.airbnb.com/?c=.pi80.pk

RESERVATION FOR LUXURY HILLTOP HOME WITH 360° RED ROCK VIEWS, OCT 9 – 16

For your protection and safety, always communicate through Airbnb
[https://www.airbnb.com/help/article/209?c=.pi80].

   DANA
   
   Booker
   
   What kind of coffee machine is at the house? Can we
   possibly check in hour early?

   SAM
   
   Booker
   
   Also, is there a grill?

Reply
[https://www.airbnb.com/hosting/thread/123456789?thread_type=home_booking&inbox_type=host&c=.pi80]

You can also respond by replying directly to this email.

GUESTS

4 adults
"""

HOST_REPLY = MESSAGE.replace("DANA", "LEANNE").replace("SAM", "LEANNE").replace("Booker", "Host")

INQUIRY = """%opentrack%

RESPOND TO RICK’S INQUIRY

Rick

[https://www.airbnb.com/hosting/thread/987654321?thread_type=home_booking&c=.pi80]

Identity verified · 2 reviews

Miami, FL

Hi. Any deals for just a couple celebrating 20 year
anniversary.

Will only use master bedroom

Pre-approve / DeclinePre-approve / Decline
[https://www.airbnb.com/hosting/thread/987654321]

GUESTS

2 adults
"""

T0 = 1791554400000  # 2026-10-09 14:00 UTC = 7:00 AM in Sedona (epoch ms)


def email(subject, text, sender="Airbnb <express@airbnb.com>"):
    return {"id": "m1", "from": sender, "subject": subject, "date": T0, "text": text}


def test_guest_message_digest():
    item = emails.parse(email("RE: Reservation for Luxury Hilltop Home With 360° Red Rock Views, Oct 9 – 16", MESSAGE))
    assert item["kind"] == "message" and item["dates"] == "Oct 9 – 16" and item["guests"] == "4 adults"
    assert [(b["name"], b["text"]) for b in item["messages"]] == [
        ("Dana", "What kind of coffee machine is at the house? Can we possibly check in hour early?"),
        ("Sam", "Also, is there a grill?")]
    text, kb = emails.card(item, TZ)
    assert text.startswith("💬 Airbnb message · Oct 9 – 16 · 4 adults\nDana: \"What kind of coffee")
    assert kb.inline_keyboard[0][0].callback_data == "em|d|m1"
    assert kb.inline_keyboard[0][1].url == "https://www.airbnb.com/hosting/thread/123456789"


def test_leannes_own_reply_is_skipped():
    item = emails.parse(email("RE: Reservation for Luxury Hilltop Home With 360° Red Rock Views, Oct 9 – 16", HOST_REPLY))
    assert item["kind"] == "message" and emails.card(item, TZ) is None


def test_inquiry_with_deadline():
    item = emails.parse(email("Inquiry for Luxury Hilltop Home With 360° Red Rock Views for Nov 11 – 15, 2026", INQUIRY,
                              "Airbnb <automated@airbnb.com>"))
    assert (item["kind"], item["guest"], item["guests"]) == ("inquiry", "Rick", "2 adults")
    assert item["body"] == "Hi. Any deals for just a couple celebrating 20 year anniversary. Will only use master bedroom"
    text, _ = emails.card(item, TZ)
    assert text.endswith("Respond by Sat 7:00 AM")  # 24 h after the email, Sedona time
    assert "draft" in emails.draft_prompt(item).lower() and "Rick: Hi. Any deals" in emails.draft_prompt(item)


@pytest.mark.parametrize("subject", ["Reservation confirmed - Pat Lee arrives Oct 9", "Brady left a 5-star review!",
                                     "Canceled: Reservation HMKMHAKMCZ for Oct 15 – 18, 2026"])
def test_no_action_needed_no_card(subject):
    item = emails.parse(email(subject, "", "Airbnb <automated@airbnb.com>"))
    assert item is not None and emails.card(item, TZ) is None  # parsed (closes threads), not shown


def test_routine_and_unrelated_mail_is_skipped():
    assert emails.parse(email("Account activity: New login from Chrome using macOS", "", "Airbnb <automated@airbnb.com>")) is None
    assert emails.parse(email("Write a review for Drew, who checked out today", "", "Airbnb <automated@airbnb.com>")) is None
    assert emails.parse(email("Your order", "", "shop@example.com")) is None
    assert emails.parse(email("Turn short gaps into bookings", "", "Vrbo <vrbo@mk.vrbo.com>")) is None


VRBO_INQUIRY = """Hello, Curt M is interested in your property.

Dates:
        Jul 4 - Jul 8, 2026, 4 nights
Guests:
        2 adults, 0 children
Traveler Name:
        Curt M

Further info

I appreciate no pets for guests, but have there been any pets in the unit
recently? :)

Respond to this Inquiry

https://www.vrbo.com/supply/interaction?interactionId=bdb9e0db-706c&propertyId=1&utm_campaign=X
"""


def test_vrbo_formats():
    sender = "Curt M <sender@messages.homeaway.com>"
    item = emails.parse(email("Inquiry from Curt M: Jul 4 - Jul 8, 2026 - Vrbo #5114807", VRBO_INQUIRY, sender))
    assert (item["kind"], item["guest"], item["dates"], item["guests"]) == ("inquiry", "Curt M", "Jul 4 - Jul 8, 2026", "2 adults")
    assert item["body"] == "I appreciate no pets for guests, but have there been any pets in the unit recently? :)"
    text, kb = emails.card(item, TZ)
    assert text.startswith("⏳ Inquiry: Curt M · Jul 4 - Jul 8, 2026 · 2 adults\n\"I appreciate")
    assert kb.inline_keyboard[0][1].text == "Open in VRBO"
    assert kb.inline_keyboard[0][1].url == "https://www.vrbo.com/supply/interaction?interactionId=bdb9e0db-706c"
    booked = emails.parse(email("Instant Booking from Kate B: Dec 30, 2026 - Jan 2, 2027 - Vrbo #5114807",
                                "Guests:\n        4 adults, 2 children\n", sender))
    assert booked["kind"] == "confirmed" and emails.card(booked, TZ) is None
    canceled = emails.parse(email("Booking canceled by traveler: Dec 30, 2026 - Jan 2, 2027 (Property ID 5114807) "
                                  "Reservation HA-30FWLZ", "", sender))
    assert canceled["kind"] == "canceled" and emails.card(canceled, TZ) is None  # parsed (closes threads), not shown
    review = emails.parse(email("Martin reviewed their stay (September 23, 2026-September 27, 2026) at your property # 1",
                                "", "Vrbo <noreply@reviews.homeaway.com>"))
    assert review["kind"] == "review" and emails.card(review, TZ) is None


class FakeInbox:
    label = "d.kmita"

    def __init__(self):
        self.mail = {}

    def recent_ids(self):
        return list(reversed(self.mail))  # newest first, like Gmail

    def get(self, msg_id):
        return self.mail[msg_id]


def test_scan_baseline_then_new_mail(cfg):
    inbox = FakeInbox()
    inbox.mail["old"] = email("Brady left a 5-star review!", "") | {"id": "old"}
    state = {"seen": {}, "items": {}}
    assert emails.scan(cfg, [inbox], state) == []  # baseline: old mail isn't replayed
    inbox.mail["new"] = email("Dana left a 4-star review!", "") | {"id": "new"}
    got = emails.scan(cfg, [inbox], state)
    assert [i["id"] for i in got] == ["new"] and emails.scan(cfg, [inbox], state) == []
    # Airbnb mails both hosts: the same notification in the second inbox is reported once
    other = FakeInbox()
    other.label = "leomproperty"
    other.mail["old"] = inbox.mail["old"]
    assert emails.scan(cfg, [inbox, other], state) == []  # baseline for the new inbox
    inbox.mail["n2"] = email("Sam left a 5-star review!", "") | {"id": "n2"}
    other.mail["x2"] = email("Sam left a 5-star review!", "") | {"id": "x2", "date": T0 + 60_000}
    assert [i["id"] for i in emails.scan(cfg, [inbox, other], state)] == ["n2"]


def test_wants_draft():
    q = emails.parse(email("RE: Reservation for Luxury Hilltop Home With 360° Red Rock Views, Oct 9 – 16", MESSAGE))
    assert emails.wants_draft(q) and emails.card(q, TZ)[1].inline_keyboard[0][0].text == "✍️ Redraft"
    thanks = q | {"messages": [{"name": "Dana", "role": "Booker", "text": "Perfect! Thanks"}]}
    assert not emails.wants_draft(thanks) and emails.card(thanks, TZ) is None  # nothing to answer: no message
    answered = q | {"messages": q["messages"] + [{"name": "Leanne", "role": "Host", "text": "Yes!"}]}
    assert not emails.wants_draft(answered)
    assert emails.wants_draft({"kind": "inquiry"}) and not emails.wants_draft({"kind": "review"})


REPAIR = """96
 SKIMMER | Work Order Email
 Hi Dominik,
We noticed an item on your system that needs attention or repair.
Vortex Pools & Spas
 temp reading 108
 temp set point 99
 DATE COMPLETED
 September 14, 2026 at 9:03 AM
 LOCATION
 1 Example Trail"""


def test_vortex_emails():
    sender = '"Vortex Pools & Spas" <messages@appmail.getskimmer.com>'
    assert emails.card(emails.parse(email("Repair Needed", REPAIR, sender)), TZ) == \
        ("🛁 Hot tub (Vortex): Repair Needed\ntemp reading 108 · temp set point 99", None)
    assert emails.card(emails.parse(email("Service Update", "chemicals balanced", sender)), TZ) is None  # routine
    report = emails.parse(email("Pool/Spa Service Report for 10/12/2026", "", sender))
    assert report["kind"] == "service_report" and emails.card(report, TZ) is None
    assert emails.parse(email("Invoice 2939 is ready!", "", sender)) is None
