#!/bin/bash
# Run the VisionID slice auth server locally (TEST-ONLY keys/credentials).
# Usage: ./run.sh   -> http://127.0.0.1:8000
set -euo pipefail
cd "$(dirname "$0")"
unset no_proxy NO_PROXY
export VISIONID_DB="${VISIONID_DB:-$PWD/visionid.db}"
exec ~/workspace/venvs/vision-id/bin/uvicorn visionid.app:app \
  --host 127.0.0.1 --port 8000
