#!/bin/bash
# Run the VisionID slice test suite (functional + adversarial).
set -euo pipefail
cd "$(dirname "$0")"
unset no_proxy NO_PROXY
rm -f visionid.db
exec ~/workspace/venvs/vision-id/bin/python -m tests.test_visionid
