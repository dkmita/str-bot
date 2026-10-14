"""Health checks: alert after two failures in a row, recovery note, stale jobs, spend budget."""

import asyncio
import json
import types
from datetime import datetime, timedelta

import health
from conftest import CHAT


class Bot:
    def __init__(self):
        self.sent, self.up = [], True

    async def get_me(self, **kw):
        if not self.up:
            raise OSError("telegram down")
        return types.SimpleNamespace(username="LeomProperyBot")

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append(text)


def app_for(cfg, checks):
    app = types.SimpleNamespace(bot=Bot(), bot_data={"cfg": cfg, "inboxes": [object()], "agent": None, "calendar": None,
                                                     "started": datetime.now().astimezone() - timedelta(hours=1)})
    health.job_ok(app.bot_data, "email_watch")
    health.job_ok(app.bot_data, "booking_watch")
    return app, types.SimpleNamespace(application=app, bot_data=app.bot_data)


def test_alert_after_two_failures_then_recovery(cfg, monkeypatch):
    state = {"ok": True}

    def gmail():
        if not state["ok"]:
            raise RuntimeError("invalid_grant")
        return "leomproperty@gmail.com"
    monkeypatch.setattr(health, "_checks", lambda app: {"Gmail leomproperty": gmail})
    app, ctx = app_for(cfg, None)
    asyncio.run(health.check_job(ctx))
    state["ok"] = False
    asyncio.run(health.check_job(ctx))
    assert app.bot.sent == []  # one failure: could be a blip
    asyncio.run(health.check_job(ctx))
    assert app.bot.sent[-1].startswith("🩺 Health check\n❌ Gmail leomproperty: RuntimeError: invalid_grant")
    asyncio.run(health.check_job(ctx))
    assert len(app.bot.sent) == 1  # not repeated while it stays broken
    state["ok"] = True
    asyncio.run(health.check_job(ctx))
    assert app.bot.sent[-1] == "🩺 Health check\n✅ Gmail leomproperty is OK again"


def test_stale_job_and_spend(cfg, monkeypatch):
    app, _ = app_for(cfg, None)
    app.bot_data["last_ok"]["email_watch"] = datetime.now().astimezone() - timedelta(hours=2)
    problems = health._jobs(app, app.bot_data["started"])
    assert problems["email_watch job"] == "last succeeded 120 min ago" and problems["booking_watch job"] is None
    today = datetime.now(cfg.tz).isoformat()
    (cfg.state_dir / "usage.jsonl").write_text(json.dumps({"ts": today, "cost_usd": 3.5}) + "\n")
    try:
        health._spend(cfg)
        assert False, "should be over budget"
    except RuntimeError as e:
        assert "$3.50 today" in str(e)


def test_health_command_lists_everything(cfg, monkeypatch):
    monkeypatch.setattr(health, "_checks", lambda app: {"Claude API": lambda: "claude-opus-5-5"})
    app, _ = app_for(cfg, None)
    sent = []

    async def send(a, chat_id, text, reply_markup=None):
        sent.append(text)
    monkeypatch.setattr(health.chat, "send", send)
    upd = types.SimpleNamespace(effective_chat=types.SimpleNamespace(id=CHAT))
    asyncio.run(health.on_health(upd, types.SimpleNamespace(bot_data=app.bot_data, application=app)))
    assert "✅ Telegram: @LeomProperyBot" in sent[0] and "✅ Claude API: claude-opus-5-5" in sent[0]
    assert "Heartbeat to healthchecks.io: not set up" in sent[0]
