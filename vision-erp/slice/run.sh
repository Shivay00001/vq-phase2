#!/usr/bin/env bash
# VisionERP slice — run locally. Requires only Python 3.12+ (stdlib only).
# Usage: ./run.sh [--port 8471]
set -euo pipefail
cd "$(dirname "$0")"
PORT="${2:-8471}"
if [ "${1:-}" = "--port" ]; then PORT="${2:-8471}"; fi
echo "Starting VisionERP slice on http://127.0.0.1:${PORT}/ ..."
exec python3 server.py --port "$PORT"
