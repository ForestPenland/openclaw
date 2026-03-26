#!/usr/bin/env bash
# OpenClaw Gateway — Fargate entrypoint
#
# 1. Sync workspace files from S3 (if WORKSPACE_BUCKET is set)
# 2. Start the OpenClaw gateway
set -euo pipefail

WORKSPACE_DIR="${OPENCLAW_WORKSPACE_DIR:-/home/node/.openclaw/workspace}"
OPENCLAW_STATE_DIR="${HOME}/.openclaw"

# ── S3 workspace sync ────────────────────────────────────────────
if [[ -n "${WORKSPACE_BUCKET:-}" ]]; then
  TENANT_ID="${TENANT_ID:-default-tenant}"
  AGENT_ID="${AGENT_ID:-default-agent}"
  S3_PREFIX="s3://${WORKSPACE_BUCKET}/${TENANT_ID}/${AGENT_ID}/"

  echo "[entrypoint] Syncing workspace from ${S3_PREFIX} -> ${WORKSPACE_DIR}"
  mkdir -p "${WORKSPACE_DIR}"
  aws s3 sync "${S3_PREFIX}" "${WORKSPACE_DIR}" --quiet
  echo "[entrypoint] Workspace sync complete ($(ls -1 "${WORKSPACE_DIR}" | wc -l) files)"

  # ── Restore agent state (sessions, memory) from S3 ──────────
  # Sessions live at ~/.openclaw/agents/<agentId>/sessions/*.jsonl
  # Memory lives at ~/.openclaw/workspace/memory/*.md
  # Restoring these lets the agent resume conversations after restart.
  STATE_S3_PREFIX="s3://${WORKSPACE_BUCKET}/${TENANT_ID}/${AGENT_ID}/_state/"
  echo "[entrypoint] Restoring agent state from ${STATE_S3_PREFIX}"
  mkdir -p "${OPENCLAW_STATE_DIR}/agents"
  aws s3 sync "${STATE_S3_PREFIX}agents/" "${OPENCLAW_STATE_DIR}/agents/" --quiet 2>/dev/null || true
  STATE_COUNT=$(find "${OPENCLAW_STATE_DIR}/agents" -name "*.jsonl" 2>/dev/null | wc -l)
  echo "[entrypoint] Agent state restored (${STATE_COUNT} session files)"
else
  echo "[entrypoint] WORKSPACE_BUCKET not set — skipping S3 sync"
fi

# ── Gateway Configuration Manager ────────────────────────────────
# Read channel secrets from Secrets Manager and write openclaw.json
# with Telegram/Slack credentials before the Gateway starts.
# Falls back to minimal config if Secrets Manager is unreachable.
export OPENCLAW_CONFIG_DIR="${HOME}/.openclaw"
mkdir -p "${OPENCLAW_CONFIG_DIR}"

# Default secret name for Telegram bot token (not tenant-prefixed)
export TELEGRAM_SECRET_NAME="${TELEGRAM_SECRET_NAME:-openclaw/telegram-bot-token}"

echo "[entrypoint] Running Gateway Configuration Manager..."
if node /app/scripts/configure-gateway.mjs; then
  echo "[entrypoint] Gateway config generated successfully"
else
  echo "[entrypoint] Gateway config script failed — writing minimal fallback config"
  cat > "${OPENCLAW_CONFIG_DIR}/openclaw.json" <<'CONF'
{
  "gateway": {
    "controlUi": {
      "dangerouslyAllowHostHeaderOriginFallback": true
    }
  }
}
CONF
fi

echo "[entrypoint] Starting OpenClaw gateway..."

# ── Background state sync (every 5 minutes) ──────────────────────
# Periodically back up sessions and memory to S3 so a container
# restart loses at most 5 minutes of conversation context.
if [[ -n "${WORKSPACE_BUCKET:-}" ]]; then
  (
    while true; do
      sleep 300
      aws s3 sync "${OPENCLAW_STATE_DIR}/agents/" "${STATE_S3_PREFIX}agents/" --quiet 2>/dev/null || true
      aws s3 sync "${WORKSPACE_DIR}/memory/" "${S3_PREFIX}memory/" --quiet 2>/dev/null || true
    done
  ) &
  SYNC_PID=$!

  # Flush state to S3 on SIGTERM (ECS sends this before killing the task)
  cleanup() {
    echo "[entrypoint] SIGTERM received — flushing state to S3..."
    aws s3 sync "${OPENCLAW_STATE_DIR}/agents/" "${STATE_S3_PREFIX}agents/" --quiet 2>/dev/null || true
    aws s3 sync "${WORKSPACE_DIR}/memory/" "${S3_PREFIX}memory/" --quiet 2>/dev/null || true
    echo "[entrypoint] State flushed to S3"
    kill "$SYNC_PID" 2>/dev/null || true
    # Forward SIGTERM to the gateway process
    kill -TERM "$GATEWAY_PID" 2>/dev/null || true
    wait "$GATEWAY_PID" 2>/dev/null || true
    exit 0
  }
  trap cleanup SIGTERM SIGINT
fi

node /app/openclaw.mjs gateway \
  --bind "${OPENCLAW_GATEWAY_BIND:-lan}" \
  --port "${OPENCLAW_GATEWAY_PORT:-18789}" \
  --allow-unconfigured &
GATEWAY_PID=$!
wait "$GATEWAY_PID"
