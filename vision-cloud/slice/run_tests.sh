#!/usr/bin/env bash
# VisionCloud v0 slice — test runner.
# Starts the server on a temp data dir (small object cap for fast adversarial
# tests), runs pytest, then stops the server. Zero external services.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${PY:-$HOME/workspace/vq-venv/bin/python}"

PORT="${VC_TEST_PORT:-8471}"
DATA="$(mktemp -d "${TMPDIR:-$HOME/workspace}/.vctest.XXXXXX")"
export VC_TEST_BASE="http://127.0.0.1:${PORT}"
export VC_TEST_DATA="$DATA"
export VC_TEST_ADMIN_SECRET="test-admin-secret-do-not-use-in-prod"
export VC_ADMIN_SECRET="$VC_TEST_ADMIN_SECRET"
export VC_MAX_OBJECT_BYTES="$((8*1024*1024))"   # 8 MB cap keeps oversized tests fast

"$PY" "$HERE/vc_server.py" --port "$PORT" --data "$DATA" >/tmp/vc-test-server.log 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null; rm -rf "$DATA"' EXIT

for i in $(seq 1 50); do
  curl -sf "$VC_TEST_BASE/health" >/dev/null 2>&1 && break
  sleep 0.2
done

"$PY" -m pytest "$HERE/tests/test_visioncloud.py" -q "$@"
