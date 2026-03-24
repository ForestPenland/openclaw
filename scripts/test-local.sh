#!/usr/bin/env bash
# Phase 0 — Local OpenClaw verification
# Starts OpenClaw locally and verifies the Gateway responds on ws://127.0.0.1:18789
# Exit 0 on success, exit 1 on failure with descriptive error
#
# Requirements: 19.1, 19.2
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

GATEWAY_HOST="127.0.0.1"
GATEWAY_PORT="${OPENCLAW_GATEWAY_PORT:-18789}"
GATEWAY_URL="ws://${GATEWAY_HOST}:${GATEWAY_PORT}"
STARTUP_TIMEOUT_SECS=60
POLL_INTERVAL_SECS=2

OPENCLAW_PID=""

cleanup() {
  if [[ -n "$OPENCLAW_PID" ]] && kill -0 "$OPENCLAW_PID" 2>/dev/null; then
    echo "==> Stopping OpenClaw (PID $OPENCLAW_PID)"
    kill "$OPENCLAW_PID" 2>/dev/null || true
    wait "$OPENCLAW_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

echo "==> Phase 0: Local OpenClaw verification"
echo "    Gateway target: ${GATEWAY_URL}"

# ── Check prerequisites ──────────────────────────────────────────────
if ! command -v node &>/dev/null; then
  echo "ERROR: node is not installed or not in PATH" >&2
  exit 1
fi

NODE_VERSION="$(node --version)"
echo "    Node.js version: ${NODE_VERSION}"

# ── Start OpenClaw in the background ─────────────────────────────────
echo "==> Starting OpenClaw Gateway..."
OPENCLAW_SKIP_CHANNELS=1 node "$ROOT_DIR/scripts/run-node.mjs" &
OPENCLAW_PID=$!

# ── Wait for the Gateway to become reachable ─────────────────────────
echo "==> Waiting up to ${STARTUP_TIMEOUT_SECS}s for Gateway on port ${GATEWAY_PORT}..."

elapsed=0
while (( elapsed < STARTUP_TIMEOUT_SECS )); do
  # Check the process is still alive
  if ! kill -0 "$OPENCLAW_PID" 2>/dev/null; then
    echo "ERROR: OpenClaw process exited unexpectedly" >&2
    OPENCLAW_PID=""
    exit 1
  fi

  # Try a TCP connection to the gateway port
  if (echo > "/dev/tcp/${GATEWAY_HOST}/${GATEWAY_PORT}") 2>/dev/null; then
    echo "==> Gateway is listening on port ${GATEWAY_PORT} (after ${elapsed}s)"
    break
  fi

  sleep "$POLL_INTERVAL_SECS"
  elapsed=$(( elapsed + POLL_INTERVAL_SECS ))
done

if (( elapsed >= STARTUP_TIMEOUT_SECS )); then
  echo "ERROR: Gateway did not start within ${STARTUP_TIMEOUT_SECS}s" >&2
  exit 1
fi

# ── Verify WebSocket upgrade handshake ───────────────────────────────
echo "==> Verifying WebSocket handshake at ${GATEWAY_URL}..."

# Use curl to attempt a WebSocket upgrade; a 101 or any HTTP response
# from the gateway confirms it is alive and handling connections.
HTTP_CODE=$(curl --silent --output /dev/null --write-out "%{http_code}" \
  --max-time 5 \
  -H "Connection: Upgrade" \
  -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Version: 13" \
  -H "Sec-WebSocket-Key: dGVzdC1sb2NhbA==" \
  "http://${GATEWAY_HOST}:${GATEWAY_PORT}/" 2>/dev/null) || true

if [[ "$HTTP_CODE" == "101" ]]; then
  echo "==> WebSocket upgrade succeeded (HTTP 101)"
elif [[ -n "$HTTP_CODE" && "$HTTP_CODE" != "000" ]]; then
  echo "==> Gateway responded with HTTP ${HTTP_CODE} (service is alive)"
else
  echo "ERROR: Gateway did not respond to WebSocket handshake" >&2
  exit 1
fi

echo ""
echo "==> Phase 0 verification PASSED"
echo "    Gateway is running at ${GATEWAY_URL}"
exit 0
