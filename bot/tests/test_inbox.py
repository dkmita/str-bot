"""Who's waiting for an answer: threads from notification emails, reminders, /inbox."""

import inbox

H = inbox.HOUR
T0 = 1791554400000


def msg(role_texts, thread="https://www.airbnb.com/hosting/thread/1", t=T0, mid="m1"):
    return {"id": mid, "kind": "message", "platform": "Airbnb", "subject": "RE: Reservation for X, Oct 9 – 16",
            "dates": "Oct 9 – 16", "date": t, "thread_url": thread,
            "messages": [{"name": n, "role": r, "text": x} for n, r, x in role_texts]}


def test_needs_reply():
    assert inbox.needs_reply("Can we check in an hour early?")
    assert inbox.needs_reply("We're arriving with our dog tomorrow and wanted to give you a heads up about it")
    assert not inbox.needs_reply("Perfect! Thanks")
    assert not inbox.needs_reply("Thank you, Leanne! We will take very good care of the house during our stay.")


def test_message_reminder_then_leanne_answers():
    threads = {}
    inbox.record(threads, msg([("Dana", "Booker", "Is there a grill?")]))
    assert inbox.due_reminders(threads, T0 + 2 * H) == []
    (t, label), = inbox.due_reminders(threads, T0 + 3 * H)
    assert (t["guest"], label) == ("Dana", "waiting 3 h")
    assert inbox.due_reminders(threads, T0 + 5 * H) == []  # once
    inbox.record(threads, msg([("Leanne", "Host", "Yes - on the deck!")], t=T0 + 6 * H, mid="m2"))
    assert not any(t["open"] for t in threads.values())


def test_thanks_doesnt_open_a_thread_and_digest_uses_last_word():
    threads = {}
    inbox.record(threads, msg([("Dana", "Booker", "Thanks!")]))
    assert threads == {}
    inbox.record(threads, msg([("Dana", "Booker", "Is there a crib?"), ("Leanne", "Host", "Unfortunately we don't, sorry")]))
    assert threads == {}  # answered within the same digest


def test_inquiry_deadline_reminders_and_booking_closes():
    threads = {}
    inq = {"id": "i1", "kind": "inquiry", "platform": "Airbnb", "subject": "Inquiry for X for Nov 11 – 15, 2026",
           "dates": "Nov 11 – 15, 2026", "date": T0, "thread_url": "https://www.airbnb.com/hosting/thread/9",
           "guest": "Rick", "body": "Any deals for our anniversary?"}
    inbox.record(threads, inq)
    assert inbox.due_reminders(threads, T0 + 19 * H) == []
    assert [l for _, l in inbox.due_reminders(threads, T0 + 20 * H)] == ["4 h left to respond"]
    assert [l for _, l in inbox.due_reminders(threads, T0 + 23 * H)] == ["1 h left to respond"]
    assert inbox.due_reminders(threads, T0 + 23.5 * H) == []
    inbox.record(threads, inq | {"kind": "confirmed", "id": "c1", "date": T0 + 30 * H})
    assert not threads[inbox.thread_key(inq)]["open"]


def test_late_start_skips_to_the_latest_reminder():
    threads = {}
    inbox.record(threads, {"id": "i1", "kind": "inquiry", "platform": "VRBO", "subject": "Inquiry from Curt M",
                           "dates": "Jul 4 - Jul 8, 2026", "date": T0, "thread_url": None, "guest": "Curt M", "body": "Pets?"})
    (t, label), = inbox.due_reminders(threads, T0 + 23 * H)  # bot was down at 20 h: one reminder, not two
    assert label == "1 h left to respond" and "VRBO doesn't email" in inbox.reminder_text(t, label)


def test_inbox_text_and_prune():
    threads = {}
    inbox.record(threads, msg([("Dana", "Booker", "Is there a grill?")]))
    assert "Dana · Airbnb · Oct 9 – 16 · 5h ago" in inbox.inbox_text(threads, T0 + 5 * H, None)
    inbox.prune(threads, T0 + 15 * 24 * H)
    assert threads == {} and inbox.inbox_text(threads, T0, None) == "📥 Inbox: nobody is waiting for an answer."
