"""Shared fakes: no network, no Claude, no real calendar or Telegram."""

import os
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

BOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BOT))
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")  # the client is built but never called

TZ = ZoneInfo("America/Phoenix")
CHAT = 4242


def ics(*events: tuple[str, str, str, str], prodid="-//Airbnb Inc//Hosting Calendar 1.0//EN") -> str:
    """(uid, start YYYYMMDD, end YYYYMMDD, summary) → iCal text."""
    def desc(uid, summary):  # only reservations carry a link (folded like Airbnb's), blocks have none
        return (f"DESCRIPTION:Reservation URL: https://www.airbnb.com/hosting/reservations/de\r\n"
                f" tails/HM{uid.upper()}\\nPhone Number (Last 4 Digits): 0000\r\n") if summary.startswith("Reserved") else ""
    body = "".join(f"BEGIN:VEVENT\r\nDTSTART;VALUE=DATE:{s}\r\nDTEND;VALUE=DATE:{e}\r\nSUMMARY:{summary}\r\n"
                   f"UID:{uid}\r\n{desc(uid, summary)}END:VEVENT\r\n" for uid, s, e, summary in events)
    return f"BEGIN:VCALENDAR\r\nPRODID:{prodid}\r\nVERSION:2.0\r\n{body}END:VCALENDAR\r\n"


@pytest.fixture
def repo_dir(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "host.md").write_text("# STR Host Assistant\nTest instructions.\n")
    (root / "property.md").write_text("# Property\n- Check-in: 4pm\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def make_config(repo_dir, **over):
    import config
    fields = dict(telegram_token="x", allowed_chat_id=CHAT, model="claude-opus-5-5", repo_dir=repo_dir, tz=TZ,
                  google_client_id="x", google_client_secret="x", google_refresh_token="x",
                  property_calendar_ids=["leomproperty@gmail.com"], feeds={"Airbnb": "a", "VRBO": "v"}, gmail_tokens={},
                  git_push=False, log_content=True, hc_ping_url=None)
    fields.update(over)
    return config.Config(**fields)


@pytest.fixture
def cfg(repo_dir):
    c = make_config(repo_dir)
    c.state_dir.mkdir(exist_ok=True)
    return c
