#!/bin/sh
# Smoke-test the bot locally, deploy to Fly.io, and confirm it started.
set -eu
cd "$(dirname "$0")"
APP=dkmita-str-bot

set -a; . ./.env; set +a
.venv/bin/python -m pytest bot/tests -q
.venv/bin/python bot/smoke_test.py --live

since=$(date -u +%Y-%m-%dT%H:%M:%S)
flyctl deploy --remote-only -a "$APP"

# A crashed machine stays stopped after a deploy; start it if needed.
machine=$(flyctl machine list -a "$APP" --json | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['id'])")
if flyctl machine status "$machine" -a "$APP" | grep -q "State: stopped"; then
  flyctl machine start "$machine" -a "$APP"
fi

echo "Waiting for the bot to start..."
for _ in $(seq 1 30); do
  # Only look at log lines written after this deploy began (timestamps sort as strings).
  recent=$(flyctl logs -a "$APP" --no-tail 2>&1 | sed 's/\x1b\[[0-9;]*m//g' | awk -v t="$since" '$1 >= t')
  if echo "$recent" | grep -q "Traceback"; then
    echo "$recent"; echo "Bot crashed on startup"; exit 1
  fi
  if echo "$recent" | grep -q "Application started"; then
    echo "$recent" | grep "STR bot starting"; echo "Deployed and running"; exit 0
  fi
  sleep 5
done
echo "Timed out waiting for startup; check: flyctl logs -a $APP"; exit 1
