# STR bot (Sedona short-term rental)

Telegram ⇄ this bot ⇄ Claude (claude-opus-5-5), with read-only access to the Airbnb and VRBO reservation feeds,
the "Sedona Home" Google Calendar, and the markdown notes in this repo (`property.md`, `notes/`). Notes the bot
changes are committed to git. Infrastructure copied from `~/workout-coach` (same patterns, separate app).

Telegram messages go out **only when I need to act**; everything else is logged. Today it:
- Watches Airbnb/VRBO notification emails every 5 min (no Claude to sort them): 💬 guest messages that need an answer
  and ⏳ inquiries/booking requests (24 h deadline) come with a suggested reply in Leanne's voice (~9¢ each), ✍️ Redraft
  and Open-in-app buttons. Thank-yous, Leanne's own replies, confirmations, cancellations and reviews are silent.
- Reminds me about unanswered guests (`inbox.py`): inquiries/requests at 20 h and 23 h, messages after 3 h.
- 🚨 Double bookings across Airbnb and VRBO (hourly, from the iCal feeds). New/changed/cancelled stays are silent.
- Guest door codes (`doorcodes.py`): reserves a Keymaster slot on the garage lock a week ahead (arrival 10:00 AM →
  checkout noon, random placeholder PIN; planned: guest's phone last-4), checks it synced 15 min after it goes live,
  resets it an hour after checkout, follows moved/cancelled stays. Messages only for ❗ a code not on the lock, no free
  slot, or a failed write. Permanent codes (Owners, Cleaners, ...) are never touched.
- Hot tub (Vortex via Skimmer): 🛁 no Monday service report by 6pm, or none all week by Friday; Vortex "Repair
  Needed" work orders. Routine reports and service updates are silent. (Report readings are pre-service.)
- Health (`health.py`, every 30 min): 🩺 a connection or job failing twice in a row, or Claude spend over $1.50/day
  (no message when it recovers). Bot errors → ⚠️ (at most one per 10 min).
- On request: `/stays`, `/inbox`, `/locks`, `/codes`, `/health`, `/usage`, and chat (~3¢/message): questions about
  stays, guest drafts, keeping `property.md`'s Guest Q&A current.

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
| `TS_AUTHKEY` | Tailscale auth key (reusable + ephemeral): login.tailscale.com/admin/settings/keys |
| `HA_URL` / `HA_TOKEN` | Home Assistant's tailnet address (e.g. `http://homeassistant:8123`) and a long-lived token of a dedicated non-admin HA user |
| `HC_PING_URL` | healthchecks.io check "str-bot heartbeat": period 5 min, grace 10 min |
| `HC_DEPS_PING_URL` | optional, not used (connection alerts go to Telegram) |

## Monitoring

- **Running + Telegram**: heartbeat every 5 min to healthchecks.io (`HC_PING_URL`), sent only if Telegram answers;
  healthchecks.io emails me if pings stop. A crash also pings `/fail` (entrypoint.sh); Fly restarts the machine.
- **Connections and jobs** (`health.py`, every 30 min): Airbnb/VRBO feeds, Google Calendar, each Gmail inbox, Claude
  API (free model lookup), GitHub push (deploy key; unpushed notes over 1 h), disk space, Claude spend over $1.50/day,
  and whether the email/booking watches succeeded recently. Two failures in a row → Telegram alert, then a recovery
  note. (Optional `HC_DEPS_PING_URL` also reports to healthchecks.io; not used: Telegram alerts are enough, and the
  heartbeat already catches a Telegram outage.)
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
├── doorcodes.py       # guest codes on the garage lock: reserve, verify, clean up, /codes
├── agent.py           # Claude loop, conversations (new one after 1 h idle), context pack
├── tools.py           # tool schemas + dispatcher
├── prompts/telegram.md# bot-specific instructions appended to host.md
├── config.py          # settings from environment variables
├── data/              # repo.py (sandboxed .md files + git), usage.py (cost log)
├── emails.py          # notification emails → Telegram cards, Draft reply button
├── inbox.py           # conversation threads, unanswered reminders, /inbox
├── integrations/      # ical.py (Airbnb/VRBO feeds, double bookings), gcal.py (Sedona Home calendar),
│                      # gmail.py (read-only inboxes)
├── tests/             # pytest: feeds, booking watch, morning note, prompt/tool snapshots
└── smoke_test.py      # builds the bot with real settings (--live reads the feeds and calendar)
```

Logs (7 days): `flyctl ssh console -a dkmita-str-bot -C "tail -300 /data/str-bot/.state/logs/bot.log"`.
