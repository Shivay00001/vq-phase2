"""Configuration. Everything here is TEST-ONLY. Production must inject real secrets
via environment and use KMS/HSM-backed signing keys (see README production gaps)."""
import os

ISSUER = os.environ.get("VISIONID_ISSUER", "https://visionid.local")
API_AUDIENCE = "visionid-api"

ACCESS_TOKEN_TTL = 15 * 60          # 15 minutes
ID_TOKEN_TTL = 15 * 60              # 15 minutes
AUTH_CODE_TTL = 10 * 60             # 10 minutes, single use
REFRESH_TOKEN_TTL = 30 * 24 * 3600  # 30 days, sliding on rotation
DEVICE_CHALLENGE_TTL = 10 * 60      # 10 minutes

# Test-only seed clients. Never use these values outside local testing.
TEST_PUBLIC_CLIENT_ID = "visionid-demo-spa"
TEST_CONFIDENTIAL_CLIENT_ID = "visionid-demo-web"
TEST_CONFIDENTIAL_CLIENT_SECRET = "test-secret-local-only-do-not-use-in-prod"

DB_PATH = os.environ.get("VISIONID_DB", os.path.join(os.path.dirname(__file__), "..", "visionid.db"))
