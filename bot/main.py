"""Telegram entry point: wires handlers and scheduled jobs (see bot/README.md)."""

import logging
from datetime import timedelta

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

import chat
import config
import doorcodes
import emails
import health
import inbox
import jobs
import ops
from agent import Agent
from data.repo import Repo
from integrations.gcal import Calendar
from integrations.gmail import Inbox
from integrations.homeassistant import HomeAssistant

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
for noisy in ("httpx", "apscheduler"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger("str")


def build_app(cfg: config.Config) -> Application:
    """Wire up the bot without starting it (the smoke test calls this too)."""
    calendar = Calendar(cfg)
    agent = Agent(cfg, calendar, Repo(cfg.repo_dir, push=cfg.git_push))
    app = Application.builder().token(cfg.telegram_token).post_init(ops.on_startup).build()
    inboxes = [Inbox(label, cfg.google_client_id, cfg.google_client_secret, tok) for label, tok in cfg.gmail_tokens.items()]
    ha = HomeAssistant(cfg.ha_url, cfg.ha_token) if cfg.ha_url and cfg.ha_token else None
    app.bot_data.update(cfg=cfg, calendar=calendar, agent=agent, inboxes=inboxes, ha=ha)
    app.add_handler(CommandHandler("start", chat.on_start))
    app.add_handler(CommandHandler("usage", chat.on_usage))
    app.add_handler(CommandHandler("stays", chat.on_stays))
    app.add_handler(CommandHandler("health", health.on_health))
    app.add_handler(CommandHandler("inbox", inbox.on_inbox))
    app.add_handler(CommandHandler("locks", chat.on_locks))
    app.add_handler(CommandHandler("codes", doorcodes.on_codes))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat.on_message))
    app.add_handler(CallbackQueryHandler(emails.on_button, pattern=r"^em\|"))
    app.add_handler(CallbackQueryHandler(inbox.on_button, pattern=r"^ib\|"))
    app.add_handler(CallbackQueryHandler(doorcodes.on_button, pattern=r"^dc\|"))
    app.add_error_handler(ops.on_error)

    q = app.job_queue
    q.run_repeating(ops.heartbeat, interval=ops.HEARTBEAT_EVERY, first=30, name="heartbeat")
    q.run_repeating(jobs.watch, interval=jobs.WATCH_EVERY, first=60, name="booking_watch")
    q.run_repeating(emails.watch, interval=emails.CHECK_EVERY, first=45, name="email_watch")
    q.run_daily(jobs.morning, jobs.MORNING.replace(tzinfo=cfg.tz), name="morning")
    q.run_daily(jobs.hot_tub_check, jobs.HOT_TUB_CHECK.replace(tzinfo=cfg.tz), name="hot_tub_check")
    q.run_repeating(health.check_job, interval=health.CHECK_EVERY, first=300, name="health_check")
    if ha:
        q.run_repeating(doorcodes.job, interval=doorcodes.CHECK_EVERY, first=120, name="door_codes")
    return app


def _file_logging(cfg: config.Config) -> None:
    """Keep 7 days of logs on the Fly volume."""
    from logging.handlers import TimedRotatingFileHandler
    log_dir = cfg.state_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = TimedRotatingFileHandler(log_dir / "bot.log", when="midnight", backupCount=7, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)


def main() -> None:
    cfg = config.load()
    _file_logging(cfg)
    app = build_app(cfg)
    log.info("STR bot starting (model=%s, feeds=%s, inboxes=%s, heartbeat=%s, deps check=%s)", cfg.model,
             sorted(cfg.feeds), sorted(cfg.gmail_tokens), cfg.hc_ping_url is not None, cfg.hc_deps_ping_url is not None)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
