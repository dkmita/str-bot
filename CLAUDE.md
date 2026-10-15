# str-bot (Claude Code sessions)

Telegram bot that helps me run my short-term rental in Sedona, AZ (listed on Airbnb and VRBO). Sibling of
`~/workout-coach` (same infrastructure patterns; code is copied, not shared, until both settle).

**The bot's instructions live in `host.md`; property facts in `property.md`.** Read both first.

## Working on the bot
- Code in `bot/` (layout in `bot/README.md`). Deploy only with `./deploy.sh` (pytest + smoke test, Fly deploy,
  waits for a clean start). Fly app `dkmita-str-bot` (dfw, 256 MB, volume `str_data`, repo clone at /data/str-bot).
- The bot's system prompt = `host.md` + `bot/prompts/telegram.md`; the snapshot tests guard it and the tool
  schemas. If a change is intentional, delete `bot/tests/snapshots/*` and re-run to record new ones.
- The bot commits `.md` changes from Fly: `git pull --rebase` before editing here.
- Logs (7 days): `flyctl ssh console -a dkmita-str-bot -C "tail -300 /data/str-bot/.state/logs/bot.log"`.
  Claude cost per call: `.state/usage.jsonl` (also `/usage` in Telegram).

## Data sources
- Reservations: the Airbnb and VRBO iCal export feeds (`AIRBNB_ICAL_URL`, `VRBO_ICAL_URL`; secret URLs, keep them
  out of the repo). Fresher than Google's imported copy of the Airbnb feed, which can lag by up to a day.
- Gmail (read-only, the bot's own token): Airbnb notification mail goes to d.kmita@gmail.com; VRBO's to
  leomproperty@gmail.com (a second token, `LEOM_GOOGLE_REFRESH_TOKEN`). Guest messages can be answered by replying
  to Airbnb's email (Reply-To …@reply.airbnb.com): a possible Send path later (needs gmail.send + approve buttons).
- Google Calendar (personal account d.kmita@gmail.com, read-only here): "Sedona Home" (`leomproperty@gmail.com`:
  hot tub maintenance etc.). From Claude Code use `gog ... -a d.kmita@gmail.com --client personal`.
- Not connected yet: Home Assistant (door codes), sending guest messages, pricing (no public host APIs;
  likely via a channel manager such as Hospitable/OwnerRez/Hostaway or PriceLabs), cleaner email/WhatsApp.

## Later
- Drop the d.kmita@gmail.com inbox: Airbnb sends inquiries/requests/new bookings only there today (Oct 2026
  check: 2 inquiries/requests + 1 booking in 30 days that leomproperty didn't get). Forward from:airbnb.com to
  leomproperty (Gmail filter) or change Airbnb's notification email, then use only the leomproperty token
  (it reads the Sedona Home calendar too) and remove the str-bot grant from d.kmita's Google account.

- Home Assistant (door codes), paused 2026-10-14 (user having VPN trouble): plan = Tailscale in userspace mode on
  the Fly machine (`TS_AUTHKEY`: reusable + ephemeral key from login.tailscale.com/admin/settings/keys), `HA_URL`
  = HA's tailnet name:8123, `HA_TOKEN` = long-lived token of a dedicated non-admin HA user. Read-only lock status
  first, then per-stay codes behind Approve. Tailscale isn't installed on the laptop (stale CLI; the 100.96.x
  address there is Cloudflare WARP).

## Rules for any new capability
- Anything that reaches guests, the listings or prices is proposed with approve buttons first and applied by code
  after I tap Approve. Door codes: the bot may reserve guest slots automatically, but only with a random placeholder
  PIN; the real code comes from me (✏️ Set PIN) or Home Assistant. Never touch permanent codes (no date range).
- Keep guest personal data minimal in logs and in the repo (no full phone numbers or emails in .md files).
