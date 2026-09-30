"""SQLite persistence. All queries are parameterized — no string interpolation."""
import json
import os
import sqlite3
import threading

from . import config
from . import crypto

_lock = threading.Lock()
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    roles TEXT NOT NULL DEFAULT '["user"]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS refresh_tokens (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    family_id TEXT NOT NULL,
    token_hash TEXT UNIQUE NOT NULL,
    device_id TEXT,
    scopes TEXT NOT NULL,
    issued_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    replaced_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_rt_user ON refresh_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_rt_family ON refresh_tokens(family_id);
CREATE TABLE IF NOT EXISTS auth_codes (
    code_hash TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    scopes TEXT NOT NULL,
    code_challenge TEXT,
    challenge_method TEXT,
    auth_time INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    used INTEGER NOT NULL DEFAULT 0,
    issued_family_id TEXT
);
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    device_name TEXT NOT NULL,
    platform TEXT NOT NULL,
    public_key_jwk TEXT NOT NULL,
    challenge TEXT,
    challenge_expires INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',
    enrolled_at INTEGER NOT NULL,
    last_seen_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_dev_user ON devices(user_id);
CREATE TABLE IF NOT EXISTS clients (
    client_id TEXT PRIMARY KEY,
    client_secret_hash TEXT,
    name TEXT NOT NULL,
    redirect_uris TEXT NOT NULL,
    allowed_scopes TEXT NOT NULL,
    confidential INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    actor_user_id TEXT,
    event TEXT NOT NULL,
    ip TEXT,
    details TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
-- NOTE: no UPDATE/DELETE on audit_log anywhere in this codebase (append-only).
"""


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        with _lock:
            if _conn is None:
                os.makedirs(os.path.dirname(os.path.abspath(config.DB_PATH)), exist_ok=True)
                c = sqlite3.connect(config.DB_PATH, check_same_thread=False)
                c.row_factory = sqlite3.Row
                c.executescript(SCHEMA)
                _conn = c
                _seed_clients(c)
    return _conn


def reset_for_tests():
    """TEST-ONLY: wipe all data and reseed clients."""
    c = get_conn()
    with _lock:
        for t in ("users", "refresh_tokens", "auth_codes", "devices", "audit_log", "clients"):
            c.execute(f"DELETE FROM {t}")
        c.commit()
        _seed_clients(c)


def _seed_clients(c: sqlite3.Connection):
    c.execute(
        "INSERT OR IGNORE INTO clients (client_id, client_secret_hash, name, redirect_uris, allowed_scopes, confidential)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (config.TEST_PUBLIC_CLIENT_ID, None, "VisionID demo SPA (public)",
         json.dumps(["http://localhost:3000/callback"]), "openid profile email", 0))
    c.execute(
        "INSERT OR IGNORE INTO clients (client_id, client_secret_hash, name, redirect_uris, allowed_scopes, confidential)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (config.TEST_CONFIDENTIAL_CLIENT_ID, crypto.sha256_hex(config.TEST_CONFIDENTIAL_CLIENT_SECRET),
         "VisionID demo web (confidential)", json.dumps(["http://localhost:3001/callback"]),
         "openid profile email devices:manage", 1))
    c.commit()


def utcnow_iso() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def audit(event: str, actor_user_id=None, ip=None, details: dict | None = None):
    c = get_conn()
    with _lock:
        c.execute(
            "INSERT INTO audit_log (ts, actor_user_id, event, ip, details) VALUES (?, ?, ?, ?, ?)",
            (crypto.now(), actor_user_id, event, ip,
             json.dumps(details or {})))
        c.commit()


# ---- users
def create_user(email: str, password_hash: str) -> dict:
    c = get_conn()
    uid = crypto.random_id("usr")
    now_iso = utcnow_iso()
    with _lock:
        c.execute(
            "INSERT INTO users (id, email, password_hash, status, roles, created_at, updated_at)"
            " VALUES (?, ?, ?, 'active', ?, ?, ?)",
            (uid, email.lower(), password_hash, json.dumps(["user"]), now_iso, now_iso))
        c.commit()
    return get_user_by_id(uid)


def get_user_by_email(email: str):
    c = get_conn()
    row = c.execute("SELECT * FROM users WHERE email = ?", (email.lower(),)).fetchone()
    return dict(row) if row else None


def get_user_by_id(uid: str):
    c = get_conn()
    row = c.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    return dict(row) if row else None


def update_password_hash(uid: str, new_hash: str):
    c = get_conn()
    with _lock:
        c.execute("UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                  (new_hash, utcnow_iso(), uid))
        c.commit()


def set_roles(uid: str, roles: list):  # TEST/ADMIN-ONLY helper
    c = get_conn()
    with _lock:
        c.execute("UPDATE users SET roles = ?, updated_at = ? WHERE id = ?",
                  (json.dumps(roles), utcnow_iso(), uid))
        c.commit()


# ---- refresh tokens
def create_refresh_token(user_id, scopes, device_id=None) -> tuple[str, dict]:
    secret = crypto.new_token()
    c = get_conn()
    row = {
        "id": crypto.random_id("rt"),
        "user_id": user_id,
        "family_id": crypto.random_id("fam"),
        "token_hash": crypto.sha256_hex(secret),
        "device_id": device_id,
        "scopes": scopes,
        "issued_at": crypto.now(),
        "expires_at": crypto.now() + config.REFRESH_TOKEN_TTL,
        "status": "active",
        "replaced_by": None,
    }
    with _lock:
        c.execute(
            "INSERT INTO refresh_tokens (id, user_id, family_id, token_hash, device_id, scopes,"
            " issued_at, expires_at, status, replaced_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            tuple(row.values()))
        c.commit()
    return secret, row


def get_refresh_by_hash(token_hash: str):
    c = get_conn()
    row = c.execute("SELECT * FROM refresh_tokens WHERE token_hash = ?", (token_hash,)).fetchone()
    return dict(row) if row else None


def rotate_refresh_token(old_row: dict) -> tuple[str, dict]:
    """Supersede old_row, issue child in same family. Returns (new_secret, new_row)."""
    secret = crypto.new_token()
    c = get_conn()
    new_row = {
        "id": crypto.random_id("rt"),
        "user_id": old_row["user_id"],
        "family_id": old_row["family_id"],
        "token_hash": crypto.sha256_hex(secret),
        "device_id": old_row["device_id"],
        "scopes": old_row["scopes"],  # scope NEVER widens on rotation
        "issued_at": crypto.now(),
        "expires_at": crypto.now() + config.REFRESH_TOKEN_TTL,
        "status": "active",
        "replaced_by": None,
    }
    with _lock:
        c.execute(
            "INSERT INTO refresh_tokens (id, user_id, family_id, token_hash, device_id, scopes,"
            " issued_at, expires_at, status, replaced_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            tuple(new_row.values()))
        c.execute("UPDATE refresh_tokens SET status = 'superseded', replaced_by = ? WHERE id = ?",
                  (new_row["id"], old_row["id"]))
        c.commit()
    return secret, new_row


def revoke_family(family_id: str):
    c = get_conn()
    with _lock:
        c.execute("UPDATE refresh_tokens SET status = 'revoked' WHERE family_id = ? AND status != 'revoked'",
                  (family_id,))
        c.commit()


def revoke_user_families(user_id: str):
    c = get_conn()
    with _lock:
        c.execute("UPDATE refresh_tokens SET status = 'revoked' WHERE user_id = ? AND status = 'active'",
                  (user_id,))
        c.commit()


def revoke_device_families(device_id: str):
    c = get_conn()
    with _lock:
        c.execute("UPDATE refresh_tokens SET status = 'revoked' WHERE device_id = ? AND status = 'active'",
                  (device_id,))
        c.commit()


# ---- auth codes
def create_auth_code(client_id, user_id, redirect_uri, scopes, code_challenge, challenge_method) -> str:
    code = crypto.new_token()
    c = get_conn()
    with _lock:
        c.execute(
            "INSERT INTO auth_codes (code_hash, client_id, user_id, redirect_uri, scopes,"
            " code_challenge, challenge_method, auth_time, expires_at, used)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (crypto.sha256_hex(code), client_id, user_id, redirect_uri, scopes,
             code_challenge, challenge_method, crypto.now(), crypto.now() + config.AUTH_CODE_TTL))
        c.commit()
    return code


def get_auth_code(code: str):
    c = get_conn()
    row = c.execute("SELECT * FROM auth_codes WHERE code_hash = ?",
                    (crypto.sha256_hex(code),)).fetchone()
    return dict(row) if row else None


def mark_code_used(code_hash: str, family_id: str):
    c = get_conn()
    with _lock:
        c.execute("UPDATE auth_codes SET used = 1, issued_family_id = ? WHERE code_hash = ?",
                  (family_id, code_hash))
        c.commit()


# ---- devices
def create_device(user_id, device_name, platform, public_key_jwk: dict) -> dict:
    c = get_conn()
    row = {
        "id": crypto.random_id("dev"),
        "user_id": user_id,
        "device_name": device_name,
        "platform": platform,
        "public_key_jwk": json.dumps(public_key_jwk),
        "challenge": crypto.b64url(crypto.random_bytes(32)),
        "challenge_expires": crypto.now() + config.DEVICE_CHALLENGE_TTL,
        "status": "pending",
        "enrolled_at": crypto.now(),
        "last_seen_at": None,
    }
    with _lock:
        c.execute(
            "INSERT INTO devices (id, user_id, device_name, platform, public_key_jwk, challenge,"
            " challenge_expires, status, enrolled_at, last_seen_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", tuple(row.values()))
        c.commit()
    return row


def get_device(dev_id: str):
    c = get_conn()
    row = c.execute("SELECT * FROM devices WHERE id = ?", (dev_id,)).fetchone()
    return dict(row) if row else None


def list_devices(user_id: str) -> list:
    c = get_conn()
    rows = c.execute("SELECT * FROM devices WHERE user_id = ? ORDER BY enrolled_at DESC",
                     (user_id,)).fetchall()
    return [dict(r) for r in rows]


def activate_device(dev_id: str):
    c = get_conn()
    with _lock:
        c.execute("UPDATE devices SET status = 'active', challenge = NULL, challenge_expires = NULL,"
                  " last_seen_at = ? WHERE id = ?", (crypto.now(), dev_id))
        c.commit()


def revoke_device(dev_id: str):
    c = get_conn()
    with _lock:
        c.execute("UPDATE devices SET status = 'revoked' WHERE id = ?", (dev_id,))
        c.commit()
    revoke_device_families(dev_id)


# ---- clients
def get_client(client_id: str):
    c = get_conn()
    row = c.execute("SELECT * FROM clients WHERE client_id = ?", (client_id,)).fetchone()
    return dict(row) if row else None


# ---- audit read
def read_audit(limit: int = 50) -> list:
    c = get_conn()
    rows = c.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]
