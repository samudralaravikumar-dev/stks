#!/usr/bin/env bash
# One-time setup on a fresh Ubuntu 22.04/24.04 server. Run from the unzipped project folder:
#   sudo bash deploy/setup_server.sh                 -> uses https://<server-ip>.sslip.io (no domain needed)
#   sudo bash deploy/setup_server.sh trade.example.in -> uses your own domain (point its A record here first)
set -euo pipefail
cd "$(dirname "$0")/.."

DOMAIN="${1:-}"
if [ -z "$DOMAIN" ]; then
  IP="$(curl -fsS https://api.ipify.org)"
  DOMAIN="${IP//./-}.sslip.io"
fi

if ! command -v docker >/dev/null 2>&1; then
  echo ">> Installing Docker"
  curl -fsSL https://get.docker.com | sh
fi

mkdir -p data
if [ ! -f .streamlit/secrets.toml ]; then
  cp .streamlit/secrets.toml.example .streamlit/secrets.toml
  echo ">> Created .streamlit/secrets.toml from the example: edit it for SMS / Telegram / broker keys"
fi
echo "DOMAIN=${DOMAIN}" > .env

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  ufw allow 80/tcp && ufw allow 443/tcp
fi

echo ">> Building and starting (first build takes a few minutes)"
docker compose up -d --build

echo
echo "=============================================================="
echo " Portal:  https://${DOMAIN}"
echo " On Android: open it in Chrome > menu > Add to Home screen"
echo " First visit creates the admin account (add your mobile there)"
echo " OTP codes in console mode:  docker compose logs -f app"
echo "=============================================================="
