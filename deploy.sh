#!/usr/bin/env bash
# Deploy travel-points to the VPS using GitHub as the source of truth.
# Usage: ./deploy.sh [branch]
#
# Reads VPS target from .deploy-env (gitignored) -- see VPS_MIGRATION_PLAN.md.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load local deploy config (gitignored) -- must define SERVER and REMOTE.
if [ -f "$SCRIPT_DIR/.deploy-env" ]; then
  set -a
  . "$SCRIPT_DIR/.deploy-env"
  set +a
else
  echo "ERROR: .deploy-env not found at $SCRIPT_DIR/.deploy-env" >&2
  echo "Create it with:" >&2
  echo "  SERVER=root@your-vps-host" >&2
  echo "  REMOTE=/opt/travel-points" >&2
  exit 1
fi

: "${SERVER:?SERVER must be set in .deploy-env}"
: "${REMOTE:?REMOTE must be set in .deploy-env}"

BRANCH="${1:-$(git rev-parse --abbrev-ref HEAD)}"
REPO_URL="${REPO_URL:-https://github.com/norangio/travel-points.git}"

if [[ ! "$BRANCH" =~ ^[A-Za-z0-9._/-]+$ ]]; then
  echo "Invalid branch name: $BRANCH" >&2
  exit 1
fi

if [[ "${SKIP_PUSH:-0}" != "1" ]]; then
  echo "-> Pushing $BRANCH to GitHub..."
  git push origin "$BRANCH"
fi

echo "-> Pulling latest code on VPS and deploying..."
ssh "$SERVER" "
set -euo pipefail
if [ ! -d \"$REMOTE/.git\" ]; then
  echo '-> Bootstrapping git repo at $REMOTE'
  mkdir -p \"$REMOTE\"
  git -C \"$REMOTE\" init
fi
if git -C \"$REMOTE\" remote get-url origin >/dev/null 2>&1; then
  git -C \"$REMOTE\" remote set-url origin \"$REPO_URL\"
else
  git -C \"$REMOTE\" remote add origin \"$REPO_URL\"
fi
git config --global --add safe.directory \"$REMOTE\"
git -C \"$REMOTE\" fetch origin \"$BRANCH\"
git -C \"$REMOTE\" clean -fd -e config.yaml -e .env -e state/
if git -C \"$REMOTE\" show-ref --verify --quiet \"refs/heads/$BRANCH\"; then
  git -C \"$REMOTE\" checkout \"$BRANCH\"
else
  git -C \"$REMOTE\" checkout -b \"$BRANCH\" \"origin/$BRANCH\"
fi
git -C \"$REMOTE\" reset --hard \"origin/$BRANCH\"
bash \"$REMOTE/deploy/server-deploy.sh\" \"$BRANCH\"
"

echo "✓ Deploy complete"
