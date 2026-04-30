#!/usr/bin/env bash
# Weather Prediction Bot — one-shot installer for Debian/Ubuntu VPS.
#
#   curl -fsSL https://raw.githubusercontent.com/<you>/<repo>/main/install.sh | bash
#
# Or run after `git clone`:
#   cd weather-bot && bash install.sh
#
set -euo pipefail

APPDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_NAME="$(whoami)"
SERVICE_NAME="weather-bot"

echo "▶ Installing Weather Prediction Bot to: $APPDIR"
echo "▶ Running as user: $USER_NAME"

# 1. System packages
if command -v apt-get >/dev/null 2>&1; then
  echo "▶ Installing system dependencies (python3, venv, pip)…"
  sudo apt-get update -y
  sudo apt-get install -y python3 python3-venv python3-pip
fi

# 2. Virtualenv + python deps
if [ ! -d "$APPDIR/venv" ]; then
  echo "▶ Creating virtualenv…"
  python3 -m venv "$APPDIR/venv"
fi
echo "▶ Installing Python dependencies…"
"$APPDIR/venv/bin/pip" install --upgrade pip
"$APPDIR/venv/bin/pip" install -r "$APPDIR/requirements.txt"

# 3. .env
if [ ! -f "$APPDIR/.env" ]; then
  echo "▶ Creating .env from .env.example (edit it now if you want to change the bot token)"
  cp "$APPDIR/.env.example" "$APPDIR/.env"
fi

# 4. systemd unit
echo "▶ Installing systemd unit…"
TMP="$(mktemp)"
sed -e "s|__USER__|$USER_NAME|g" \
    -e "s|__APPDIR__|$APPDIR|g" \
    "$APPDIR/weather-bot.service" > "$TMP"
sudo mv "$TMP" "/etc/systemd/system/$SERVICE_NAME.service"
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

echo ""
echo "✅ Installed and started."
echo ""
echo "  status :  sudo systemctl status $SERVICE_NAME"
echo "  logs   :  sudo journalctl -u $SERVICE_NAME -f"
echo "             (or:  tail -f $APPDIR/bot.log)"
echo "  restart:  sudo systemctl restart $SERVICE_NAME"
echo ""
