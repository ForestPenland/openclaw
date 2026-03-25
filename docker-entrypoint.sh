#!/usr/bin/env bash
# OpenClaw Gateway — Fargate entrypoint
#
# 1. Sync workspace files from S3 (if WORKSPACE_BUCKET is set)
# 2. Start the OpenClaw gateway
set -euo pipefail

WORKSPACE_DIR="${OPENCLAW_WORKSPACE_DIR:-/home/node/.openclaw/workspace}"

# ── S3 workspace sync ────────────────────────────────────────────
if [[ -n "${WORKSPACE_BUCKET:-}" ]]; then
  TENANT_ID="${TENANT_ID:-default-tenant}"
  AGENT_ID="${AGENT_ID:-default-agent}"
  S3_PREFIX="s3://${WORKSPACE_BUCKET}/${TENANT_ID}/${AGENT_ID}/"

  echo "[entrypoint] Syncing workspace from ${S3_PREFIX} -> ${WORKSPACE_DIR}"
  mkdir -p "${WORKSPACE_DIR}"
  aws s3 sync "${S3_PREFIX}" "${WORKSPACE_DIR}" --quiet
  echo "[entrypoint] Workspace sync complete ($(ls -1 "${WORKSPACE_DIR}" | wc -l) files)"
else
  echo "[entrypoint] WORKSPACE_BUCKET not set — skipping S3 sync"
fi

# ── Start gateway ────────────────────────────────────────────────
# When binding to lan (0.0.0.0), OpenClaw requires explicit origin
# config for the Control UI. Write a minimal config to allow it.
OPENCLAW_CONFIG_DIR="${HOME}/.openclaw"
mkdir -p "${OPENCLAW_CONFIG_DIR}"
if [ ! -f "${OPENCLAW_CONFIG_DIR}/openclaw.json" ]; then
  cat > "${OPENCLAW_CONFIG_DIR}/openclaw.json" <<'CONF'
{
  "gateway": {
    "controlUi": {
      "dangerouslyAllowHostHeaderOriginFallback": true
    }
  }
}
CONF
  echo "[entrypoint] Created minimal gateway config"
fi

echo "[entrypoint] Starting OpenClaw gateway..."
exec node /app/openclaw.mjs gateway \
  --bind "${OPENCLAW_GATEWAY_BIND:-lan}" \
  --port "${OPENCLAW_GATEWAY_PORT:-18789}" \
  --allow-unconfigured
