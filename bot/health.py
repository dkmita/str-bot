"""Health checks: is everything the bot depends on reachable, and are its jobs actually working?

- Every 30 min: each check below. A check failing twice in a row (~1 h) → one Telegram alert, and a note when it
  recovers. The result also goes to a healthchecks.io check (HC_DEPS_PING_URL: success, or /fail with the list),
  which alerts by email even if Telegram itself is the problem.
- The 5-minute heartbeat (ops.heartbeat → HC_PING_URL) covers "the bot is running and Telegram answers".
- /health runs everything now and shows the results.
"""

import asyncio
import json
import logging
import shutil
import subprocess
from datetime import datetime, timedelta

import httpx
from telegram import Update
from telegram.ext import ContextTypes

import chat
from integrations import ical

log = logging.getLogger("str")

CHECK_EVERY = timedelta(minutes=30)
ALERT_AFTER = 2  # consecutive failures
DAILY_BUDGET_USD = 1.5  # above average use; catches runaways
MIN_FREE_MB = 100
# job → longest acceptable time since its last successful run
JOB_MAX_AGE = {"email_watch": timedelta(minutes=30), "booking_watch": timedelta(hours=3)}
STARTUP_GRACE = timedelta(minutes=20)


def job_ok(bot_data: dict, name: str) -> None:
    """Jobs call this after a successful run."""
    bot_data.setdefault("last_ok", {})[name] = datetime.now().astimezone()


def _checks(app) -> dict:
    """name → function returning a short detail string (raises on failure). Run in a thread."""
    cfg, agent = app.bot_data["cfg"], app.bot_data["agent"]
    checks = {}
    for platform, url in cfg.feeds.items():
        def feed(url=url, platform=platform):
            entries = ical.parse(ical.fetch(url), platform)
            return f"{sum(s['kind'] == 'stay' for s in entries)} stays"
        checks[f"{platform} calendar feed"] = feed
    checks["Google Calendar"] = lambda: f"{len(app.bot_data['calendar'].list_events(*_next_day(cfg)))} events tomorrow"
    for inbox in app.bot_data["inboxes"]:
        checks[f"Gmail {inbox.label}"] = lambda inbox=inbox: inbox._svc.users().getProfile(userId="me").execute()["emailAddress"]
    checks["Claude API"] = lambda: _claude(agent)
    if cfg.git_push:
        checks["GitHub (notes push)"] = lambda: _git(cfg)
    checks["Disk"] = lambda: _disk(cfg)
    checks["Claude spend today"] = lambda: _spend(cfg)
    return checks


def _next_day(cfg) -> tuple[str, str]:
    start = datetime.now(cfg.tz) + timedelta(days=1)
    return start.isoformat(), (start + timedelta(days=1)).isoformat()


def _claude(agent) -> str:
    import anthropic
    anthropic.Anthropic().models.retrieve(agent.cfg.model)  # free: checks the key and that the model exists
    return agent.cfg.model


def _git(cfg) -> str:
    git = ["git", "-C", str(cfg.repo_dir)]
    subprocess.run([*git, "ls-remote", "-q", "origin", "HEAD"], check=True, capture_output=True, timeout=30)
    ahead = subprocess.run([*git, "log", "--format=%ct", "@{u}..HEAD"], capture_output=True, text=True).stdout.split()
    if ahead and datetime.now().timestamp() - int(ahead[-1]) > 3600:
        raise RuntimeError(f"{len(ahead)} note commit(s) not pushed for over an hour")
    return "reachable" + (f", {len(ahead)} commit(s) waiting to push" if ahead else "")


def _disk(cfg) -> str:
    free = shutil.disk_usage(cfg.state_dir).free // 2**20
    if free < MIN_FREE_MB:
        raise RuntimeError(f"only {free} MB free on the volume")
    return f"{free} MB free"


def _spend(cfg) -> str:
    path, today = cfg.state_dir / "usage.jsonl", datetime.now(cfg.tz).date().isoformat()
    total = sum(r["cost_usd"] for r in map(json.loads, path.read_text().splitlines()) if r["ts"].startswith(today)) \
        if path.exists() else 0.0
    if total > DAILY_BUDGET_USD:
        raise RuntimeError(f"${total:.2f} today (budget ${DAILY_BUDGET_USD:.0f})")
    return f"${total:.2f}"


def _jobs(app, started) -> dict[str, str | None]:
    """name → problem (None if fine) for the scheduled jobs."""
    now = datetime.now().astimezone()
    last = app.bot_data.get("last_ok", {})
    out = {}
    for name, max_age in JOB_MAX_AGE.items():
        if name == "email_watch" and not app.bot_data["inboxes"]:
            continue
        t = last.get(name)
        if t is None:
            out[f"{name} job"] = None if now - started < STARTUP_GRACE else "hasn't completed since the bot started"
        else:
            out[f"{name} job"] = None if now - t <= max_age else f"last succeeded {int((now - t).total_seconds() // 60)} min ago"
    return out


async def run_all(app) -> dict[str, tuple[bool, str]]:
    """name → (ok, detail). Telegram is checked with getMe; the rest in a thread, each isolated."""
    results = {}
    try:
        me = await app.bot.get_me(read_timeout=15)
        results["Telegram"] = (True, f"@{me.username}")
    except Exception as e:
        results["Telegram"] = (False, str(e)[:150])

    def run(fn):
        try:
            return True, str(fn())
        except Exception as e:
            return False, f"{type(e).__name__}: {str(e)[:150]}"
    checks = _checks(app)
    for name, res in zip(checks, await asyncio.gather(*(asyncio.to_thread(run, f) for f in checks.values()))):
        results[name] = res
    for name, problem in _jobs(app, app.bot_data.setdefault("started", datetime.now().astimezone())).items():
        results[name] = (problem is None, problem or "ok")
    return results


async def check_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    app, cfg = ctx.application, ctx.bot_data["cfg"]
    results = await run_all(app)
    streaks = app.bot_data.setdefault("fail_streaks", {})
    alerts, recovered = [], []
    for name, (ok, detail) in results.items():
        before = streaks.get(name, 0)
        streaks[name] = 0 if ok else before + 1
        if streaks[name] == ALERT_AFTER:
            alerts.append(f"❌ {name}: {detail}")
        elif ok and before >= ALERT_AFTER:
            recovered.append(f"✅ {name} is OK again")
    failing = [f"{n}: {d}" for n, (ok, d) in results.items() if not ok]
    log.info("Health check: %d/%d ok%s", len(results) - len(failing), len(results),
             f"; failing: {'; '.join(failing)}" if failing else "")
    if (alerts or recovered) and cfg.allowed_chat_id is not None and results["Telegram"][0]:
        await chat.send(app, cfg.allowed_chat_id, "🩺 Health check\n" + "\n".join(alerts + recovered)
                        + ("\n\nDetails: /health" if alerts else ""))
    if cfg.hc_deps_ping_url:  # healthchecks.io alerts by email, independent of Telegram
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                if failing:
                    await client.post(cfg.hc_deps_ping_url + "/fail", content="\n".join(failing))
                else:
                    await client.get(cfg.hc_deps_ping_url)
        except httpx.HTTPError as e:
            log.warning("Health check ping failed: %s", e)


async def on_health(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/health: run every check now."""
    cfg = ctx.bot_data["cfg"]
    if not chat.is_allowed(cfg, update):
        return
    results = await run_all(ctx.application)
    lines = [f"{'✅' if ok else '❌'} {name}: {detail}" for name, (ok, detail) in results.items()]
    lines += ["", "Heartbeat to healthchecks.io: " + ("on" if cfg.hc_ping_url else "not set up (HC_PING_URL)"),
              "Connection check to healthchecks.io: " + ("on" if cfg.hc_deps_ping_url else "not set up (HC_DEPS_PING_URL)")]
    await chat.send(ctx.application, update.effective_chat.id, "🩺 Health\n" + "\n".join(lines))
