#!/bin/sh
# Set up SSH with the GitHub deploy key, then clone (or update) the str-bot repo.
set -eu

mkdir -p ~/.ssh
printf '%s\n' "$GIT_SSH_KEY" > ~/.ssh/id_ed25519
chmod 600 ~/.ssh/id_ed25519
ssh-keyscan -t ed25519 github.com >> ~/.ssh/known_hosts 2>/dev/null

if [ ! -d "$REPO_DIR/.git" ]; then
  git clone -q "$GIT_REPO_URL" "$REPO_DIR"
else
  git -C "$REPO_DIR" pull -q --rebase
fi
git -C "$REPO_DIR" config user.name "STR Bot"
git -C "$REPO_DIR" config user.email "d.kmita@gmail.com"

# Join the tailnet (only if TS_AUTHKEY is set) in userspace mode. The bot reaches Home Assistant through
# Tailscale's local HTTP proxy on localhost:1055 (HA_PROXY); MagicDNS names resolve through it.
if [ -n "${TS_AUTHKEY:-}" ]; then
  mkdir -p /data/tailscale
  tailscaled --tun=userspace-networking --statedir=/data/tailscale \
    --outbound-http-proxy-listen=localhost:1055 --socks5-server=localhost:1055 >/data/tailscale/tailscaled.log 2>&1 &
  for _ in 1 2 3 4 5 6 7 8 9 10; do [ -S /var/run/tailscale/tailscaled.sock ] && break; sleep 1; done
  tailscale up --authkey="$TS_AUTHKEY" --hostname=str-bot --accept-dns=false || echo "tailscale up failed; Home Assistant will be unreachable"
fi

# Run the bot; if it exits with an error, tell healthchecks.io right away (Fly then restarts it).
# The bot runs as a child (not exec'd), so forward Fly's stop signal as SIGTERM
# (background jobs start with SIGINT ignored; the bot handles SIGTERM the same way).
"$@" &
pid=$!
trap 'kill -TERM "$pid" 2>/dev/null' INT TERM
while :; do
  status=0
  wait "$pid" || status=$?
  kill -0 "$pid" 2>/dev/null || break   # still running: wait was interrupted by a signal
done
if [ "$status" -ne 0 ] && [ -n "${HC_PING_URL:-}" ]; then
  python -c "import sys, urllib.request; urllib.request.urlopen(sys.argv[1] + '/fail', timeout=10)" "$HC_PING_URL" || true
fi
exit "$status"
