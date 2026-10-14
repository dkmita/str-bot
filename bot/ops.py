"""Operations: error alerts, healthchecks.io heartbeat, startup (copied from ~/workout-coach/bot/ops.py)."""

import logging
import traceback
from datetime import datetime, timedelta

import httpx
from telegram.error import Conflict, NetworkError
from telegram.ext import Application, ContextTypes


log = logging.getLogger("str")

HEARTBEAT_EVERY = timedelta(minutes=5)

ERROR_ALERT_GAP = timedelta(minutes=10)  # at most one Telegram error alert per 10 min

async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Errors in handlers and scheduled jobs: log, and tell me on Telegram (rate-limited)."""
    cfg = ctx.bot_data["cfg"]
    now = datetime.now(cfg.tz)
    # Telegram connection blips (and the brief overlap during restarts) fix themselves: the library
    # retries. Only alert if they keep happening for 10+ minutes; healthchecks.io covers real outages.
    if isinstance(ctx.error, (NetworkError, Conflict)):
        first = ctx.bot_data.setdefault("network_errors_since", now)
        ctx.bot_data["last_network_error"] = now
        log.warning("Telegram connection problem (retrying): %s", ctx.error)
        if now - first < timedelta(minutes=10):
            return
    else:
        log.error("Unhandled error", exc_info=ctx.error)
    last = ctx.bot_data.get("last_error_alert")
    if cfg.allowed_chat_id is None or (last and now - last < ERROR_ALERT_GAP):
        return
    ctx.bot_data["last_error_alert"] = now
    where = getattr(getattr(ctx, "job", None), "name", None) or "a message"
    detail = "".join(traceback.format_exception_only(ctx.error)).strip()[:300]
    try:
        await ctx.bot.send_message(cfg.allowed_chat_id, f"⚠️ STR bot error in {where}:\n{detail}\n\n"
                                                        "Details: flyctl logs -a dkmita-str-bot")
    except Exception:
        log.exception("Couldn't send the error alert")

async def ping(ctx_or_app, suffix: str = "") -> None:
    """Heartbeat to healthchecks.io. If these stop, healthchecks.io alerts me."""
    app = getattr(ctx_or_app, "application", ctx_or_app)
    url = app.bot_data["cfg"].hc_ping_url
    if not url:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.get(url + suffix)
    except httpx.HTTPError as e:  # a missed ping is exactly what healthchecks.io watches for
        log.warning("Heartbeat ping failed: %s", e)

async def heartbeat(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    # Connection is healthy again if no network error for 5 min: reset the "errors since" clock.
    last = ctx.bot_data.get("last_network_error")
    if last and datetime.now(ctx.bot_data["cfg"].tz) - last > HEARTBEAT_EVERY:
        ctx.bot_data.pop("network_errors_since", None)
        ctx.bot_data.pop("last_network_error", None)
    # Only report healthy if Telegram itself answers; otherwise skip the ping so healthchecks.io alerts me.
    try:
        await ctx.bot.get_me(read_timeout=15)
    except Exception as e:
        log.warning("Heartbeat: Telegram unreachable (%s); not pinging healthchecks.io", e)
        return
    await ping(ctx)

async def on_startup(app: Application) -> None:
    await ping(app, "/start")
