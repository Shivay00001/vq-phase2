"""Crypto primitives: password hashing, JWT (RS256), PKCE, random tokens, device keys.

Signing keys are generated IN-MEMORY at startup and are TEST-ONLY. There is no
persistence and no rotation ceremony here — that is an explicit production gap
(documented in README.md). Never copy this key handling into production.
"""
import base64
import hashlib
import secrets
import time

import jwt as pyjwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa, ed25519

try:
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError
    _PH = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
    _BACKEND = "argon2id"
except Exception:  # pragma: no cover - fallback only if argon2 missing
    import bcrypt
    _PH = None
    _BACKEND = "bcrypt"

KID = "visionid-test-key-1"  # test-only key id


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def new_token(nbytes: int = 32) -> str:
    """Opaque token secret (URL-safe). Only its SHA-256 is ever stored."""
    return secrets.token_urlsafe(nbytes)


def random_bytes(nbytes: int = 32) -> bytes:
    return secrets.token_bytes(nbytes)


def random_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(16)}"


# ---------------------------------------------------------------- passwords
COMMON_PASSWORDS = frozenset("""
password password1 password12 password123 password1234 password12345
qwerty qwerty123 123456 12345678 123456789 abc123 letmein welcome
admin administrator changeme iloveyou monkey dragon sunshine
visionquantech visionid test1234 testing123 password!
""".split())


def check_password_policy(password: str, email: str) -> list:
    """Return a list of policy violations (empty = acceptable)."""
    reasons = []
    if len(password) < 12:
        reasons.append("at least 12 characters")
    if not any(c.islower() for c in password):
        reasons.append("a lowercase letter")
    if not any(c.isupper() for c in password):
        reasons.append("an uppercase letter")
    if not any(c.isdigit() for c in password):
        reasons.append("a digit")
    if not any(not c.isalnum() for c in password):
        reasons.append("a symbol")
    if password.lower() in COMMON_PASSWORDS:
        reasons.append("not a commonly used password")
    local = (email or "").split("@")[0].lower()
    if local and len(local) >= 3 and local in password.lower():
        reasons.append("must not contain your email name")
    return ["password must contain " + r if not r.startswith(("at least", "not a", "must not"))
            else "password " + r if r.startswith("at least") else r
            for r in reasons]


def hash_password(password: str) -> str:
    if _BACKEND == "argon2id":
        return _PH.hash(password)
    import bcrypt
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


_DUMMY_HASH = None


def _dummy_hash() -> str:
    """A constant hash used for dummy verifies (anti user-enumeration timing)."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(secrets.token_urlsafe(24))
    return _DUMMY_HASH


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        if stored_hash.startswith("$argon2"):
            _PH.verify(stored_hash, password)
            return True
        if stored_hash.startswith("$2b$") or stored_hash.startswith("$2a$"):
            import bcrypt
            return bcrypt.checkpw(password.encode(), stored_hash.encode())
        return False
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def needs_rehash(stored_hash: str) -> bool:
    if _BACKEND == "argon2id":
        if stored_hash.startswith("$argon2"):
            return _PH.check_needs_rehash(stored_hash)
        return True  # legacy bcrypt -> upgrade to argon2id on next login
    return False


# ---------------------------------------------------------------- JWT / JWKS
_RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_RSA_PUBLIC = _RSA_KEY.public_key()
_PRIVATE_PEM = _RSA_KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
)


def _rsa_numbers():
    nums = _RSA_PUBLIC.public_numbers()
    return b64url(nums.n.to_bytes(256, "big")), b64url(nums.e.to_bytes(3, "big"))


def jwks() -> dict:
    n, e = _rsa_numbers()
    return {"keys": [{
        "kty": "RSA", "use": "sig", "alg": "RS256", "kid": KID, "n": n, "e": e,
    }]}


def mint_jwt(claims: dict) -> str:
    headers = {"kid": KID, "alg": "RS256", "typ": "JWT"}
    return pyjwt.encode(claims, _PRIVATE_PEM, algorithm="RS256", headers=headers)


def verify_jwt(token: str, audience=None, verify_aud: bool = True) -> dict:
    """Verify signature (via JWKS key), expiry, issuer. Raises pyjwt exceptions."""
    n, e = _rsa_numbers()
    jwk_dict = {"kty": "RSA", "kid": KID, "n": n, "e": e}
    key = pyjwt.algorithms.RSAAlgorithm.from_jwk(jwk_dict)
    options = {"require": ["exp", "iat", "iss", "sub"]}
    if not verify_aud:
        options["verify_aud"] = False
    return pyjwt.decode(token, key=key, algorithms=["RS256"],
                        issuer=_issuer(), audience=audience, options=options)


def _issuer():
    from . import config
    return config.ISSUER


def now() -> int:
    return int(time.time())


# ---------------------------------------------------------------- PKCE
def pkce_challenge_ok(verifier: str, challenge: str, method: str) -> bool:
    if method != "S256":
        return False
    digest = hashlib.sha256(verifier.encode()).digest()
    return secrets.compare_digest(b64url(digest), challenge)


# ---------------------------------------------------------------- device keys (Ed25519)
def ed25519_pubkey_from_jwk(jwk: dict) -> ed25519.Ed25519PublicKey:
    if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519" or "x" not in jwk:
        raise ValueError("device JWK must be {kty:OKP, crv:Ed25519, x:...}")
    return ed25519.Ed25519PublicKey.from_public_bytes(b64url_decode(jwk["x"]))


def ed25519_jwk_from_pubkey(pub: ed25519.Ed25519PublicKey) -> dict:
    raw = pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return {"kty": "OKP", "crv": "Ed25519", "x": b64url(raw)}


def verify_ed25519_signature(pub: ed25519.Ed25519PublicKey, signature_b64: str, message: bytes) -> bool:
    try:
        pub.verify(b64url_decode(signature_b64), message)
        return True
    except Exception:
        return False
