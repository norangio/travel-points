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

echo "-> Installing Python dependencies..."
python3 -m venv venv
venv/bin/pip install -q --upgrade pip
venv/bin/pip install -q .

echo "-> Installing systemd units..."
cp "$APP_DIR/travel-points.service" /etc/systemd/system/
cp "$APP_DIR/travel-points.timer"   /etc/systemd/system/
systemctl daemon-reload

echo "-> Applying timer state..."
if systemctl is-enabled travel-points.timer >/dev/null 2>&1; then
  systemctl restart travel-points.timer
else
  systemctl enable --now travel-points.timer >/dev/null 2>&1 || true
fi

echo "✓ VPS deploy complete"
