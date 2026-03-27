#!/bin/bash
# ─────────────────────────────────────────────────────────────
# deploy.sh — rsync local code to EC2 and restart the service
#
# Works out of the box for anyone with repo access:
#   - In GitHub Actions: secrets are injected automatically
#   - Locally: export the 3 env vars below, then run ./deploy.sh
#
# Required env vars (stored as GitHub Secrets):
#   EC2_HOST      – public IP or hostname
#   EC2_SSH_KEY   – PEM private key, base64-encoded
#   EC2_USER      – SSH user (default: ec2-user)
# ─────────────────────────────────────────────────────────────
set -euo pipefail

# ── Configuration ──────────────────────────────────────────
EC2_USER="${EC2_USER:-ec2-user}"
EC2_HOST="${EC2_HOST:?ERROR: EC2_HOST is not set. Export it or check GitHub Secrets.}"
REMOTE_DIR="/opt/zenyt/repo/zenyt-webflow-manager2"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Resolve SSH key from env (no local files) ─────────────
if [ -z "${EC2_SSH_KEY:-}" ]; then
    echo "ERROR: EC2_SSH_KEY is not set."
    echo "  For local use:  export EC2_SSH_KEY=\$(base64 < your-key.pem)"
    echo "  In CI:          it's injected from GitHub Secrets automatically."
    exit 1
fi

TMPKEY=$(mktemp /tmp/zenyt-deploy-key.XXXXXX)
trap 'rm -f "$TMPKEY"' EXIT
echo "$EC2_SSH_KEY" | base64 --decode > "$TMPKEY"
chmod 400 "$TMPKEY"

SSH_OPTS="-i $TMPKEY -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"

echo "🚀 Deploying zenyt-webflow-manager2 to $EC2_HOST..."

# ── Rsync code (excludes JSON data, secrets, caches) ──────
rsync -avz --delete \
    --exclude '*.json' \
    --exclude '*.pem' \
    --exclude '.env' \
    --exclude '__pycache__/' \
    --exclude 'venv/' \
    --exclude '.git/' \
    --exclude 'ec2-user-data.sh' \
    --exclude '.DS_Store' \
    -e "ssh $SSH_OPTS" \
    "$SCRIPT_DIR/" \
    "${EC2_USER}@${EC2_HOST}:${REMOTE_DIR}/"

echo "✅ Code synced."

# ── Restart the service ───────────────────────────────────
# shellcheck disable=SC2029
ssh $SSH_OPTS "${EC2_USER}@${EC2_HOST}" "sudo systemctl restart zenyt-app"

echo "✅ Service restarted."
echo ""
echo "🎉 Deploy complete → http://www.zenyt-experiments.com"
