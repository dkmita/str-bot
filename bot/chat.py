"""Telegram plumbing: access check, commands, and message → assistant turn."""

import asyncio
import logging
from datetime import date, datetime, timedelta

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, ContextTypes

from data import usage
from integrations import ical

log = logging.getLogger("str")

TELEGRAM_LIMIT = 4000


async def send(app: Application, chat_id: int, text: str, reply_markup=None) -> None:
    chunks = [text[i:i + TELEGRAM_LIMIT] for i in range(0, len(text), TELEGRAM_LIMIT)] or [""]
    for k, chunk in enumerate(chunks):
        await app.bot.send_message(chat_id, chunk, reply_markup=reply_markup if k == len(chunks) - 1 else None)


def is_allowed(cfg, update: Update) -> bool:
    return cfg.allowed_chat_id is not None and update.effective_chat.id == cfg.allowed_chat_id


async def keep_typing(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    while True:
        await ctx.bot.send_chat_action(chat_id, ChatAction.TYPING)
        await asyncio.sleep(4)


async def on_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = ctx.bot_data["cfg"]
    if cfg.allowed_chat_id is None:
        await update.message.reply_text(f"Your chat ID is {update.effective_chat.id}. "
                                        "Set TELEGRAM_ALLOWED_CHAT_ID to this value and restart the bot.")
    elif is_allowed(cfg, update):
        await update.message.reply_text("STR assistant is online. Try /stays, or ask me about upcoming guests.")


async def on_usage(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = ctx.bot_data["cfg"]
    if not is_allowed(cfg, update):
        return
    days = int(ctx.args[0]) if ctx.args and ctx.args[0].isdigit() else 7
    await send(ctx.application, update.effective_chat.id, usage.report(cfg.state_dir / "usage.jsonl", datetime.now(cfg.tz), days))


def stays_text(entries: list[dict], today: date, days: int = 60) -> str:
    end = (today + timedelta(days=days)).isoformat()
    stays = [s for s in entries if s["kind"] == "stay" and s["check_out"] > today.isoformat() and s["check_in"] < end]
    lines = [f"🗓 Stays, next {days} days ({len(stays)})"] + ["- " + ical.describe(s) for s in stays]
    lines += [f"🚨 DOUBLE BOOKING: {ical.describe(a)} overlaps {ical.describe(b)}"
              for a, b in ical.double_bookings(entries) if b["check_out"] > today.isoformat()]
    return "\n".join(lines)


async def on_stays(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/stays [days]: upcoming reservations from both platforms (no Claude call)."""
    cfg = ctx.bot_data["cfg"]
    if not is_allowed(cfg, update):
        return
    days = int(ctx.args[0]) if ctx.args and ctx.args[0].isdigit() else 60
    entries = await asyncio.to_thread(ical.load_all, cfg.feeds)
    await send(ctx.application, update.effective_chat.id, stays_text(entries, datetime.now(cfg.tz).date(), days))


async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = ctx.bot_data["cfg"]
    if not is_allowed(cfg, update):
        log.warning("Ignoring message from chat %s", update.effective_chat.id)
        return
    agent = ctx.bot_data["agent"]
    typing = asyncio.create_task(keep_typing(ctx, update.effective_chat.id))
    try:
        reply = await agent.respond(update.message.text)
    except Exception:
        log.exception("Assistant failed")
        reply = "Something went wrong on my end. Try again in a minute."
    finally:
        typing.cancel()
    await send(ctx.application, update.effective_chat.id, reply)
    log.info("Reply sent (%d chars)", len(reply))
