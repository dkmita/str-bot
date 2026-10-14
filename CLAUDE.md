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
- Google Calendar (personal account d.kmita@gmail.com, read-only here): "Sedona Home" (`leomproperty@gmail.com`:
  hot tub maintenance etc.). From Claude Code use `gog ... -a d.kmita@gmail.com --client personal`.
- Not connected yet: Home Assistant (door codes), Airbnb/VRBO messaging and pricing (no public host APIs;
  likely via a channel manager such as Hospitable/OwnerRez/Hostaway or PriceLabs), cleaner email/WhatsApp.

## Rules for any new capability
- Anything that reaches guests, the listings, prices or the door lock is proposed with approve buttons first
  and applied by code after I tap Approve (same pattern as the coach's proposals).
- Keep guest personal data minimal in logs and in the repo (no full phone numbers or emails in .md files).
