#!/usr/bin/env bash
# Run on the VPS to deploy the latest travel-points code from GitHub.
# Usage: bash /opt/travel-points/deploy/server-deploy.sh [branch]
set -euo pipefail

APP_DIR="/opt/travel-points"
BRANCH="${1:-main}"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "This script must run as root." >&2
  exit 1
fi

if [[ ! "$BRANCH" =~ ^[A-Za-z0-9._/-]+$ ]]; then
  echo "Invalid branch name: $BRANCH" >&2
  exit 1
fi

echo "-> Deploying travel-points branch: $BRANCH"
cd "$APP_DIR"

timer_was_installed=0
timer_was_enabled=0
timer_was_active=0
timer_unit_state="$(systemctl list-unit-files travel-points.timer --no-legend 2>/dev/null | awk '{print $2; exit}' || true)"
if [[ -n "$timer_unit_state" ]]; then
  timer_was_installed=1
  if systemctl is-enabled --quiet travel-points.timer; then
    timer_was_enabled=1
  fi
  if systemctl is-active --quiet travel-points.timer; then
    timer_was_active=1
  fi
fi

echo "-> Installing Python dependencies..."
python3 -m venv venv
venv/bin/pip install -q --upgrade pip
venv/bin/pip install -q .

echo "-> Installing systemd units..."
cp "$APP_DIR/travel-points.service" /etc/systemd/system/
cp "$APP_DIR/travel-points.timer"   /etc/systemd/system/
systemctl daemon-reload

echo "-> Applying timer state..."
if [[ "$timer_was_installed" == "0" ]]; then
  systemctl enable --now travel-points.timer >/dev/null 2>&1
elif [[ "$timer_was_enabled" == "1" && "$timer_was_active" == "1" ]]; then
  systemctl restart travel-points.timer
elif [[ "$timer_was_enabled" == "1" ]]; then
  systemctl enable travel-points.timer >/dev/null 2>&1
  echo "-> Timer is enabled but stopped; leaving it stopped"
else
  systemctl disable --now travel-points.timer >/dev/null 2>&1 || true
  echo "-> Timer is disabled; leaving it disabled"
fi

echo "✓ VPS deploy complete"
