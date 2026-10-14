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

# Join the tailnet in userspace mode (TAILSCALE=on in fly.toml). The bot reaches Home Assistant through Tailscale's
# local SOCKS5 proxy on localhost:1055; MagicDNS names resolve through it. Login: TS_AUTHKEY if set; otherwise the
# first start prints a login link to /data/tailscale/login.log (open it in a browser to approve). The node's identity
# is kept on the volume, so later restarts reconnect without logging in again.
if [ "${TAILSCALE:-}" = "on" ] || [ -n "${TS_AUTHKEY:-}" ]; then
  mkdir -p /data/tailscale
  tailscaled --tun=userspace-networking --statedir=/data/tailscale \
    --outbound-http-proxy-listen=localhost:1055 --socks5-server=localhost:1055 >/data/tailscale/tailscaled.log 2>&1 &
  for _ in 1 2 3 4 5 6 7 8 9 10; do [ -S /var/run/tailscale/tailscaled.sock ] && break; sleep 1; done
  if [ -n "${TS_AUTHKEY:-}" ]; then
    tailscale up --authkey="$TS_AUTHKEY" --hostname=str-bot --accept-dns=false || echo "tailscale up failed"
  else
    tailscale up --hostname=str-bot --accept-dns=false >/data/tailscale/login.log 2>&1 &  # waits for approval
  fi
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
