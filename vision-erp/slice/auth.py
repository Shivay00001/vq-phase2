"""Auth: PBKDF2 password hashing, session tokens."""
import hashlib
import hmac
import secrets
import datetime

ITERATIONS = 200_000
# Privileged roles get shorter sessions (framework §3: support/admin expire faster).
SESSION_HOURS = {"admin": 4, "support": 4, "manager": 12, "employee": 12, "partner": 12}


def hash_password(password: str) -> str:
    if not isinstance(password, str) or len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode(), ITERATIONS)
    return f"pbkdf2:sha256:{ITERATIONS}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    # stored format: pbkdf2:sha256:<iters>$<salt_hex>$<dk_hex>
    try:
        head, salt, hexdk = stored.split("$")
        _pfx, algo, iters_s = head.split(":")
        if _pfx != "pbkdf2" or algo != "sha256":
            return False
        iters = int(iters_s)
    except Exception:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode(), iters)
    return hmac.compare_digest(dk.hex(), hexdk)


def new_token() -> str:
    return secrets.token_hex(32)


def session_expiry(role: str) -> str:
    hours = SESSION_HOURS.get(role, 12)
    exp = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=hours)
    return exp.isoformat(timespec="seconds")


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
