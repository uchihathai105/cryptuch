#!/usr/bin/env bash
# One-time server setup for the Crypto Trading Advisor on a fresh Ubuntu 22.04/24.04 server.
#
#   git clone https://github.com/uchihathai105/cryptuch.git /opt/cryptuch
#   bash /opt/cryptuch/advisor/deploy/setup.sh
#
# What it does: installs Python and Caddy, adds 1 GB swap, creates a locked-down "advisor" user,
# asks you for the dashboard password, runs the app as a service that restarts on failure and on reboot,
# puts HTTPS in front of it, opens only ports 22/80/443, and backs up the database every day.
# Safe to run again: it updates the app and keeps your data.
set -euo pipefail

APP_DIR=/opt/cryptuch
APP=$APP_DIR/advisor
APP_USER=advisor
BRANCH=${BRANCH:-main}
REPO_URL=${REPO_URL:-https://github.com/uchihathai105/cryptuch.git}

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
fail() { printf '\n\033[1;31mSTOP: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || fail "Run this as root (log in as root, or use sudo)."

say "1/9 Checking that this server can reach Binance"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 https://api.binance.com/api/v3/ping || true)
if [ "$code" != "200" ]; then
  code2=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 https://data-api.binance.vision/api/v3/ping || true)
  [ "$code2" = "200" ] || fail "Binance answered HTTP $code (mirror: $code2). Code 451 means this server's country is blocked. Destroy it and create one in another region."
fi
echo "Binance reachable."

say "2/9 Installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip git curl sqlite3 ufw caddy

say "3/9 Swap (1 GB) so a small server never runs out of memory"
if ! swapon --show | grep -q .; then
  fallocate -l 1G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

say "4/9 Getting the code"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch origin "$BRANCH"
  git -C "$APP_DIR" checkout "$BRANCH"
  git -C "$APP_DIR" merge --ff-only "origin/$BRANCH"
else
  git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
[ -f "$APP/app/main.py" ] || fail "advisor/app/main.py is missing in $APP_DIR."
[ -f "$APP/app/auth.py" ] || fail "advisor/app/auth.py is missing: the login code has not been pushed to GitHub yet. Push it, then run this again. (Nothing is exposed to the internet yet.)"

say "5/9 App user and Python packages"
id "$APP_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
sudo -u "$APP_USER" python3 -m venv "$APP/.venv"
sudo -u "$APP_USER" "$APP/.venv/bin/pip" install --quiet --upgrade pip
sudo -u "$APP_USER" "$APP/.venv/bin/pip" install --quiet -r "$APP/requirements.txt"

say "6/9 Dashboard password"
CONF=$APP/config.local.json
if [ -f "$CONF" ] && python3 -c "import json,sys; sys.exit(0 if json.load(open('$CONF')).get('password') else 1)" 2>/dev/null; then
  echo "A password is already set in $CONF (keeping it). Delete that file to set a new one."
else
  while :; do
    read -r -s -p "Choose a dashboard password (at least 12 characters, typing is hidden): " PW; echo
    [ "${#PW}" -ge 12 ] || { echo "Too short, try again."; continue; }
    read -r -s -p "Type it again: " PW2; echo
    [ "$PW" = "$PW2" ] && break || echo "They did not match, try again."
  done
  ADVISOR_PW="$PW" CONF="$CONF" python3 - <<'PY'
import json, os
path = os.environ["CONF"]
try:
    d = json.load(open(path))
except Exception:
    d = {}
d.setdefault("telegram_bot_token", "")
d.setdefault("telegram_chat_id", "")
d["password"] = os.environ["ADVISOR_PW"]
json.dump(d, open(path, "w"), indent=2)
PY
  unset PW PW2
fi
chown "$APP_USER:$APP_USER" "$CONF"
chmod 600 "$CONF"

say "7/9 Service (starts on boot, restarts if it crashes)"
cat > /etc/systemd/system/advisor.service <<UNIT
[Unit]
Description=Crypto Trading Advisor
After=network-online.target
Wants=network-online.target

[Service]
User=$APP_USER
WorkingDirectory=$APP
Environment=ADVISOR_HOST=127.0.0.1
Environment=ADVISOR_PORT=8000
ExecStart=$APP/.venv/bin/python -m app.main
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable advisor >/dev/null
systemctl restart advisor

say "8/9 HTTPS and firewall"
IP=$(curl -4 -fsS --max-time 10 https://api.ipify.org || hostname -I | awk '{print $1}')
DOMAIN=${DOMAIN:-${IP//./-}.sslip.io}     # free name that points at this IP; set DOMAIN=yourname.com to use your own
cat > /etc/caddy/Caddyfile <<CADDY
$DOMAIN {
    encode gzip
    reverse_proxy 127.0.0.1:8000
}
CADDY
systemctl enable caddy >/dev/null
systemctl restart caddy
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

say "9/9 Daily backup and update command"
cat > /etc/cron.daily/advisor-backup <<'CRON'
#!/bin/sh
# keeps the last 14 daily copies of the database
DB=/opt/cryptuch/advisor/data/advisor.db
DIR=/var/backups/advisor
[ -f "$DB" ] || exit 0
mkdir -p "$DIR"
sqlite3 "$DB" ".backup '$DIR/advisor-$(date +%F).db'"
find "$DIR" -name 'advisor-*.db' -mtime +14 -delete
CRON
chmod +x /etc/cron.daily/advisor-backup
cat > /usr/local/bin/advisor-update <<UPD
#!/bin/sh
set -e
git -C $APP_DIR pull --ff-only
$APP/.venv/bin/pip install --quiet -r $APP/requirements.txt
chown -R $APP_USER:$APP_USER $APP_DIR
systemctl restart advisor
echo "Updated and restarted."
UPD
chmod +x /usr/local/bin/advisor-update

sleep 6
if curl -fsS -o /dev/null http://127.0.0.1:8000/login; then
  printf '\n\033[1;32mDone.\033[0m Open  https://%s  and sign in.\n' "$DOMAIN"
  echo "First load takes 20-30 seconds while it downloads candles."
  echo "Update later:  advisor-update      Logs:  journalctl -u advisor -f      Status:  systemctl status advisor"
else
  echo "The app did not answer. Look at:  journalctl -u advisor -n 50 --no-pager"
  exit 1
fi
