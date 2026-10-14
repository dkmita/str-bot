"""Pre-deploy check: build the bot with real settings and verify it's wired up, without sending anything.

Usage (from the repo root, with .env loaded):
    .venv/bin/python bot/smoke_test.py          # offline checks
    .venv/bin/python bot/smoke_test.py --live   # also reads the iCal feeds and Google Calendar
"""

import sys
from datetime import datetime, timedelta

import config
from data import usage
from main import build_app


def check(ok: bool, message: str) -> None:
    print(("  ok  " if ok else "  FAIL ") + message)
    if not ok:
        sys.exit(1)


cfg = config.load()
app = build_app(cfg)
agent = app.bot_data["agent"]
print("Smoke test")

names = [t["name"] for t in agent.tools]
check(len(names) == len(set(names)), f"{len(names)} tools, no duplicate names")
for tool in agent.tools:
    schema = tool["input_schema"]
    missing = set(schema.get("required", [])) - set(schema.get("properties", {}))
    check(not missing, f"{tool['name']}: required fields exist {sorted(missing) or ''}")
source = open(__file__.replace("smoke_test.py", "tools.py")).read()
unhandled = [n for n in names if f'case "{n}"' not in source]
check(not unhandled, f"every tool has a handler {unhandled or ''}")
check(agent.system.startswith("# STR Host Assistant") and "Running as a Telegram bot" in agent.system,
      "system prompt = host.md + telegram.md")
jobs = sorted(j.name for j in app.job_queue.jobs())
check({"heartbeat", "booking_watch", "email_watch", "morning", "health_check"} <= set(jobs), f"scheduled jobs: {', '.join(jobs)}")
check(cfg.allowed_chat_id is not None, "TELEGRAM_ALLOWED_CHAT_ID is set")
check(bool(cfg.feeds), f"reservation feeds: {', '.join(sorted(cfg.feeds)) or 'none'}")
check(cfg.model in usage.PRICES, f"price table has {cfg.model}")
commands = {c for h in app.handlers.get(0, []) for c in getattr(h, "commands", ())}
check({"start", "usage", "stays", "health"} <= commands, f"commands registered: {sorted(commands)}")

if "--live" in sys.argv:
    from integrations import ical
    for platform, url in cfg.feeds.items():
        entries = ical.parse(ical.fetch(url), platform)
        check(True, f"{platform} feed: {sum(s['kind'] == 'stay' for s in entries)} stays, "
                    f"{sum(s['kind'] == 'block' for s in entries)} blocks")
    now = datetime.now(cfg.tz)
    events = agent.calendar.list_events(now.isoformat(), (now + timedelta(days=14)).isoformat())
    check(True, f"Google Calendar (Sedona Home): {len(events)} events in the next 2 weeks")
    for inbox in app.bot_data["inboxes"]:
        check(True, f"Gmail {inbox.label}: {len(inbox.recent_ids('from:(airbnb.com OR vrbo.com) newer_than:30d'))} "
                    "platform emails in 30 days")

print("All checks passed")
