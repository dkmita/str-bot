# STR bot (Sedona short-term rental)

Telegram ⇄ this bot ⇄ Claude (claude-opus-5-5), with read-only access to the Airbnb and VRBO reservation feeds,
the "Sedona Home" Google Calendar, and the markdown notes in this repo (`property.md`, `notes/`). Notes the bot
changes are committed to git. Infrastructure copied from `~/workout-coach` (same patterns, separate app).

Today it can:
- `/stays [days]`: upcoming reservations from both platforms, plus any double bookings (no Claude)
- Hourly booking watch: new, changed and cancelled stays, and 🚨 double bookings (no Claude). The first read
  is a silent baseline; a failed feed, or one that suddenly shows no future stays, is skipped instead of being
  read as cancellations.
- Every 5 min: Airbnb/VRBO notification emails → Telegram (no Claude): 💬 guest messages and ⏳ inquiries/requests
  (with the 24 h deadline) get ✍️ Draft reply (Claude, as Leanne, only when tapped) and Open in Airbnb buttons;
  ✅ confirmations, ❌ cancellations and ⭐ reviews are one-liners. Leanne's own replies and routine mail are skipped;
  the first read of an inbox is a silent baseline.
- 7am (Sedona time): check-outs, arrivals, same-day turnovers and tomorrow's arrivals, if any (no Claude)
- Chat: questions about stays, drafts of guest replies from `property.md`, keeping notes up to date
- `/usage [days]`: Claude spend report
- `/health`: runs every check now (see Monitoring)

Not yet: Home Assistant door codes, messaging and pricing on the platforms, cleaner email/WhatsApp (see CLAUDE.md).

## Secrets (Fly: `flyctl secrets set -a dkmita-str-bot ...`; locally: `.env`)

| Secret | Where it comes from |
|---|---|
| `TELEGRAM_BOT_TOKEN` | @BotFather (bot @LeomProperyBot) |
| `TELEGRAM_ALLOWED_CHAT_ID` | my Telegram user ID (same as the coach bot's) |
| `ANTHROPIC_API_KEY` | console.anthropic.com (currently the coach's key) |
| `GOOGLE_CLIENT_ID` / `_SECRET` / `_REFRESH_TOKEN` | the bot's own Desktop OAuth client "str-bot" + d.kmita@gmail.com sign-in (`bot/google_auth.py`): read-only Calendar + Gmail |
| `LEOM_GOOGLE_REFRESH_TOKEN` | same client, signed in as leomproperty@gmail.com (VRBO mail); optional |
| `AIRBNB_ICAL_URL` / `VRBO_ICAL_URL` | each platform's calendar export link (secret URLs) |
| `GIT_SSH_KEY` | private half of the GitHub deploy key (write access) for dkmita/str-bot |
| `HC_PING_URL` | healthchecks.io check "str-bot heartbeat": period 5 min, grace 10 min |
| `HC_DEPS_PING_URL` | healthchecks.io check "str-bot connections": period 30 min, grace 40 min |

## Monitoring

- **Running + Telegram**: heartbeat every 5 min to healthchecks.io (`HC_PING_URL`), sent only if Telegram answers;
  healthchecks.io emails me if pings stop. A crash also pings `/fail` (entrypoint.sh); Fly restarts the machine.
- **Connections and jobs** (`health.py`, every 30 min): Airbnb/VRBO feeds, Google Calendar, each Gmail inbox, Claude
  API (free model lookup), GitHub push (deploy key; unpushed notes over 1 h), disk space, Claude spend over $1.50/day,
  and whether the email/booking watches succeeded recently. Two failures in a row → Telegram alert, then a recovery
  note. Results also go to a second healthchecks.io check (`HC_DEPS_PING_URL`: success, or `/fail` with the list),
  so a broken Telegram still reaches me by email.
- Errors in handlers/jobs → Telegram alert (at most one per 10 min).

## Run locally

```sh
python3 -m venv .venv && .venv/bin/pip install -r bot/requirements.txt -r bot/requirements-dev.txt
set -a; . ./.env; set +a
.venv/bin/python bot/main.py     # stop the Fly machine first: Telegram delivers to one poller at a time
```

## Deploy

`./deploy.sh`: pytest, `bot/smoke_test.py --live`, `flyctl deploy`, then waits for "Application started".

## Code layout

```
bot/
├── main.py            # wiring: handlers and scheduled jobs
├── chat.py            # Telegram plumbing, /start /usage /stays, message → assistant
├── jobs.py            # booking watch, morning turnover note (code only)
├── ops.py             # error alerts, healthchecks.io heartbeat
├── health.py          # connection/job checks, /health
├── agent.py           # Claude loop, conversations (new one after 1 h idle), context pack
├── tools.py           # tool schemas + dispatcher
├── prompts/telegram.md# bot-specific instructions appended to host.md
├── config.py          # settings from environment variables
├── data/              # repo.py (sandboxed .md files + git), usage.py (cost log)
├── emails.py          # notification emails → Telegram cards, Draft reply button
├── integrations/      # ical.py (Airbnb/VRBO feeds, double bookings), gcal.py (Sedona Home calendar),
│                      # gmail.py (read-only inboxes)
├── tests/             # pytest: feeds, booking watch, morning note, prompt/tool snapshots
└── smoke_test.py      # builds the bot with real settings (--live reads the feeds and calendar)
```

Logs (7 days): `flyctl ssh console -a dkmita-str-bot -C "tail -300 /data/str-bot/.state/logs/bot.log"`.
