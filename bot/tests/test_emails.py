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


@pytest.mark.parametrize("subject,expected", [
    ("Reservation confirmed - Pat Lee arrives Oct 9", "✅ Confirmed on Airbnb: Pat Lee arrives Oct 9"),
    ("Canceled: Reservation HMKMHAKMCZ for Oct 15 – 18, 2026", "❌ Canceled on Airbnb: HMKMHAKMCZ (Oct 15 – 18, 2026)"),
    ("Brady left a 5-star review!", "⭐ Brady left a 5-star review"),
])
def test_one_liners(subject, expected):
    assert emails.card(emails.parse(email(subject, "", "Airbnb <automated@airbnb.com>")), TZ) == (expected, None)


def test_routine_and_unrelated_mail_is_skipped():
    assert emails.parse(email("Account activity: New login from Chrome using macOS", "", "Airbnb <automated@airbnb.com>")) is None
    assert emails.parse(email("Write a review for Drew, who checked out today", "", "Airbnb <automated@airbnb.com>")) is None
    assert emails.parse(email("Your order", "", "shop@example.com")) is None
    assert emails.card(emails.parse(email("New booking", "", "VRBO <x@eg.vrbo.com>")), TZ)[0] == "📧 VRBO: New booking"


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
