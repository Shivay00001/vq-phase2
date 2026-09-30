"""VisionID slice — auth server. Runnable locally only; test credentials throughout."""
import json
import threading
import time
from urllib.parse import urlencode

import jwt as pyjwt
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import config, crypto, db, ratelimit, rbac

app = FastAPI(title="VisionID (slice)", version="0.1.0-slice")
_bearer = HTTPBearer(auto_error=False)

# Access-token revocation list: jti -> exp. In-memory for the slice
# (production: Redis set with TTL).
_revoked_jti: dict[str, int] = {}
_revoked_lock = threading.Lock()


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _err(code: str, message: str, status: int):
    return JSONResponse({"error": code, "message": message}, status_code=status)


def _rate_limit_or_429(key: str, limit: int, window_s: int, request: Request, event="rate_limited"):
    ok, retry = ratelimit.check(key, limit, window_s)
    if not ok:
        db.audit(event, ip=_client_ip(request), details={"key": key})
        return JSONResponse({"error": "rate_limited",
                             "message": f"Too many requests. Retry in {retry}s."},
                            status_code=429, headers={"Retry-After": str(retry)})
    return None


def _revoke_jti(jti: str, exp: int):
    with _revoked_lock:
        _revoked_jti[jti] = exp


def _jti_revoked(jti: str) -> bool:
    now = crypto.now()
    with _revoked_lock:
        # opportunistic prune
        for k in [k for k, e in _revoked_jti.items() if e < now]:
            del _revoked_jti[k]
        return jti in _revoked_jti


def _mint_access(user: dict, scopes: set, family_id: str | None, aud: str) -> tuple[str, str]:
    jti = crypto.random_id("jti")
    now = crypto.now()
    claims = {
        "iss": config.ISSUER, "sub": user["id"], "aud": aud,
        "exp": now + config.ACCESS_TOKEN_TTL, "iat": now, "jti": jti,
        "scope": " ".join(sorted(scopes)),
        "amr": ["pwd"],
    }
    if family_id:
        claims["sid"] = family_id
    return crypto.mint_jwt(claims), jti


def _mint_id_token(user: dict, aud: str, auth_time: int) -> str:
    now = crypto.now()
    return crypto.mint_jwt({
        "iss": config.ISSUER, "sub": user["id"], "aud": aud,
        "exp": now + config.ID_TOKEN_TTL, "iat": now,
        "auth_time": auth_time, "email": user["email"],
    })


def _issue_session(user: dict, requested_scope: str | None, device_id: str | None,
                   aud: str, auth_time: int | None = None):
    """Create refresh family + access token. Scope is capped by role (never widened)."""
    allowed = rbac.scopes_for_roles(rbac.roles_of(user))
    if requested_scope:
        wanted = set(requested_scope.split())
        if not wanted <= allowed:
            wanted = wanted & allowed  # silently narrow; login never grants extra
    else:
        wanted = set(allowed)
    secret, row = db.create_refresh_token(user["id"], " ".join(sorted(wanted)), device_id)
    access, _jti = _mint_access(user, wanted, row["family_id"], aud)
    out = {"access_token": access, "refresh_token": secret,
           "token_type": "Bearer", "expires_in": config.ACCESS_TOKEN_TTL,
           "scopes": " ".join(sorted(wanted))}
    if "openid" in wanted:
        out["id_token"] = _mint_id_token(user, aud, auth_time or crypto.now())
    return out, row


def _current_token(request: Request,
                   creds: HTTPAuthorizationCredentials | None,
                   audience: str | None = config.API_AUDIENCE,
                   verify_aud: bool = True) -> dict:
    if creds is None or not creds.credentials:
        raise HTTPException(401, "missing bearer token")
    try:
        claims = crypto.verify_jwt(creds.credentials, audience=audience,
                                   verify_aud=verify_aud)
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(401, "token expired")
    except pyjwt.InvalidTokenError:
        raise HTTPException(401, "invalid token")
    if _jti_revoked(claims.get("jti", "")):
        raise HTTPException(401, "token revoked")
    return claims


def _require_scope(claims: dict, scope: str, request: Request, user_id: str | None):
    if not rbac.has_scope(set(claims.get("scope", "").split()), scope):
        db.audit("scope_denied", actor_user_id=user_id or claims.get("sub"),
                 ip=_client_ip(request), details={"required": scope})
        raise HTTPException(403, "insufficient scope")


# ---------------------------------------------------------------- users/auth
@app.post("/v1/users/register")
def register(body: dict, request: Request):
    hit = _rate_limit_or_429(f"register:{_client_ip(request)}", 10, 3600, request)
    if hit:
        return hit
    email = (body.get("email") or "").strip()
    password = body.get("password") or ""
    if "@" not in email or len(email) > 254:
        return _err("invalid_email", "Provide a valid email address.", 422)
    reasons = crypto.check_password_policy(password, email)
    if reasons:
        db.audit("register_rejected_weak_password", ip=_client_ip(request),
                 details={"email": email, "reasons": reasons})
        return JSONResponse({"error": "weak_password", "reasons": reasons}, status_code=422)
    try:
        user = db.create_user(email, crypto.hash_password(password))
    except Exception:
        # UNIQUE violation -> generic conflict (do not confirm/deny beyond this endpoint's purpose)
        return _err("email_taken", "This email is already registered.", 409)
    db.audit("register", actor_user_id=user["id"], ip=_client_ip(request))
    return JSONResponse({"id": user["id"], "email": user["email"]}, status_code=201)


@app.post("/v1/auth/login")
def login(body: dict, request: Request):
    ip = _client_ip(request)
    hit = _rate_limit_or_429(f"login:ip:{ip}", 20, 60, request)
    if hit:
        return hit
    email = (body.get("email") or "").strip()
    password = body.get("password") or ""
    device_id = body.get("device_id")

    allowed, retry = ratelimit.login_allowed(ip, email)
    if not allowed:
        db.audit("account_locked", ip=ip, details={"email": email})
        return JSONResponse({"error": "account_locked",
                             "message": f"Too many failed attempts. Retry in {retry}s."},
                            status_code=423, headers={"Retry-After": str(retry)})

    user = db.get_user_by_email(email)
    # Anti-enumeration: identical code path & generic error for unknown email.
    ok = crypto.verify_password(password, user["password_hash"]) if user else crypto.verify_password(
        crypto.new_token(), crypto._dummy_hash())
    if not user or not ok or user["status"] != "active":
        triggered = ratelimit.record_login_failure(ip, email)
        db.audit("login_failure", actor_user_id=user["id"] if user else None, ip=ip,
                 details={"email": email, "locked": triggered})
        if triggered:
            return JSONResponse({"error": "account_locked",
                                 "message": "Too many failed attempts. Try again later."},
                                status_code=423)
        return _err("invalid_credentials", "Invalid email or password.", 401)

    if crypto.needs_rehash(user["password_hash"]):
        db.update_password_hash(user["id"], crypto.hash_password(password))

    ratelimit.record_login_success(ip, email)
    if device_id:
        dev = db.get_device(device_id)
        if not dev or dev["user_id"] != user["id"] or dev["status"] != "active":
            device_id = None  # unknown/foreign device -> unbound session, not an error
    out, _row = _issue_session(user, body.get("scope"), device_id, config.API_AUDIENCE)
    db.audit("login_success", actor_user_id=user["id"], ip=ip,
             details={"device_id": device_id})
    return out


def _do_refresh(secret: str, request: Request, client_id: str | None = None):
    """Shared rotation logic for /v1/auth/refresh and the OIDC refresh grant."""
    ip = _client_ip(request)
    row = db.get_refresh_by_hash(crypto.sha256_hex(secret)) if secret else None
    if not row or row["expires_at"] < crypto.now():
        db.audit("token_reuse_detected" if row else "invalid_grant",
                 actor_user_id=row["user_id"] if row else None, ip=ip,
                 details={"reason": "unknown_or_expired"})
        return None, _err("invalid_grant", "Invalid or expired refresh token.", 401)
    if row["status"] != "active":
        # Reuse of superseded/revoked token => probable theft: kill the whole family.
        db.revoke_family(row["family_id"])
        db.audit("token_reuse_detected", actor_user_id=row["user_id"], ip=ip,
                 details={"family_id": row["family_id"], "token_id": row["id"]})
        return None, _err("invalid_grant", "Invalid or expired refresh token.", 401)
    user = db.get_user_by_id(row["user_id"])
    if not user or user["status"] != "active":
        return None, _err("invalid_grant", "Invalid or expired refresh token.", 401)
    new_secret, new_row = db.rotate_refresh_token(row)
    scopes = set(row["scopes"].split())
    aud = client_id or config.API_AUDIENCE
    access, _jti = _mint_access(user, scopes, new_row["family_id"], aud)
    db.audit("token_refreshed", actor_user_id=user["id"], ip=ip,
             details={"family_id": row["family_id"]})
    out = {"access_token": access, "refresh_token": new_secret,
           "token_type": "Bearer", "expires_in": config.ACCESS_TOKEN_TTL,
           "scopes": " ".join(sorted(scopes))}
    if "openid" in scopes:
        out["id_token"] = _mint_id_token(user, aud, crypto.now())
    return out, None


@app.post("/v1/auth/refresh")
def refresh(body: dict, request: Request):
    out, err = _do_refresh(body.get("refresh_token") or "", request)
    return out if out else err


@app.post("/v1/auth/logout")
def logout(body: dict, request: Request,
           creds: HTTPAuthorizationCredentials | None = Depends(_bearer)):
    ip = _client_ip(request)
    revoked_any = False
    secret = (body or {}).get("refresh_token")
    user_id = None
    if secret:
        row = db.get_refresh_by_hash(crypto.sha256_hex(secret))
        if row:
            db.revoke_family(row["family_id"])
            user_id = row["user_id"]
            revoked_any = True
    if creds and creds.credentials:
        try:
            # verify_aud=False: logout may present an OIDC token (aud=client_id);
            # we only need jti/exp/sub to revoke.
            claims = crypto.verify_jwt(creds.credentials, verify_aud=False)
            _revoke_jti(claims["jti"], claims["exp"])
            user_id = user_id or claims.get("sub")
            revoked_any = True
        except pyjwt.InvalidTokenError:
            pass
    if user_id:
        db.audit("logout", actor_user_id=user_id, ip=ip)
    return {"revoked": revoked_any}


@app.get("/v1/me")
def me(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(_bearer)):
    claims = _current_token(request, creds)
    user = db.get_user_by_id(claims["sub"])
    if not user or user["status"] != "active":
        raise HTTPException(401, "invalid token")
    return {"id": user["id"], "email": user["email"],
            "roles": rbac.roles_of(user),
            "devices": len([d for d in db.list_devices(user["id"]) if d["status"] == "active"])}


# ---------------------------------------------------------------- devices
@app.post("/v1/devices/enroll")
def device_enroll(body: dict, request: Request,
                  creds: HTTPAuthorizationCredentials | None = Depends(_bearer)):
    claims = _current_token(request, creds)
    _require_scope(claims, "devices:manage", request, claims["sub"])
    name = (body.get("device_name") or "").strip()[:80] or "unnamed device"
    platform = (body.get("platform") or "").strip().lower()[:20] or "unknown"
    jwk = body.get("public_key_jwk") or {}
    try:
        crypto.ed25519_pubkey_from_jwk(jwk)  # validates shape
    except Exception:
        return _err("invalid_device_key", "public_key_jwk must be an Ed25519 OKP JWK.", 422)
    dev = db.create_device(claims["sub"], name, platform, jwk)
    db.audit("device_enroll_started", actor_user_id=claims["sub"],
             ip=_client_ip(request), details={"device_id": dev["id"]})
    return JSONResponse({"device_id": dev["id"], "status": "pending",
                         "challenge": dev["challenge"]}, status_code=201)


@app.post("/v1/devices/{dev_id}/activate")
def device_activate(dev_id: str, body: dict, request: Request,
                    creds: HTTPAuthorizationCredentials | None = Depends(_bearer)):
    claims = _current_token(request, creds)
    _require_scope(claims, "devices:manage", request, claims["sub"])
    dev = db.get_device(dev_id)
    if not dev or dev["user_id"] != claims["sub"]:
        raise HTTPException(404, "device not found")
    if dev["status"] != "pending" or not dev["challenge"] or \
            dev["challenge_expires"] < crypto.now():
        return _err("challenge_expired", "Enrollment challenge expired. Re-enroll.", 410)
    pub = crypto.ed25519_pubkey_from_jwk(json.loads(dev["public_key_jwk"]))
    if not crypto.verify_ed25519_signature(pub, body.get("signature") or "",
                                           dev["challenge"].encode()):
        db.audit("device_enroll_failed", actor_user_id=claims["sub"],
                 ip=_client_ip(request), details={"device_id": dev_id})
        return _err("bad_signature", "Challenge signature invalid.", 401)
    db.activate_device(dev_id)
    db.audit("device_enroll_completed", actor_user_id=claims["sub"],
             ip=_client_ip(request), details={"device_id": dev_id})
    return {"device_id": dev_id, "status": "active"}


@app.get("/v1/devices")
def device_list(request: Request,
                creds: HTTPAuthorizationCredentials | None = Depends(_bearer)):
    claims = _current_token(request, creds)
    _require_scope(claims, "devices:manage", request, claims["sub"])
    return {"devices": [
        {"id": d["id"], "device_name": d["device_name"], "platform": d["platform"],
         "status": d["status"], "enrolled_at": d["enrolled_at"],
         "last_seen_at": d["last_seen_at"]}
        for d in db.list_devices(claims["sub"])]}


@app.post("/v1/devices/{dev_id}/revoke")
def device_revoke(dev_id: str, request: Request,
                  creds: HTTPAuthorizationCredentials | None = Depends(_bearer)):
    claims = _current_token(request, creds)
    _require_scope(claims, "devices:manage", request, claims["sub"])
    dev = db.get_device(dev_id)
    if not dev or dev["user_id"] != claims["sub"]:
        raise HTTPException(404, "device not found")
    db.revoke_device(dev_id)
    db.audit("device_revoked", actor_user_id=claims["sub"],
             ip=_client_ip(request), details={"device_id": dev_id})
    return {"device_id": dev_id, "status": "revoked"}


# ---------------------------------------------------------------- admin
@app.get("/v1/admin/audit")
def admin_audit(limit: int = 50, request: Request = None,
                creds: HTTPAuthorizationCredentials | None = Depends(_bearer)):
    claims = _current_token(request, creds)
    _require_scope(claims, "admin:audit", request, claims["sub"])
    return {"events": db.read_audit(min(limit, 200))}


# ---------------------------------------------------------------- OIDC
def _get_client_or_400(client_id: str):
    client = db.get_client(client_id)
    if not client:
        raise HTTPException(400, "invalid client_id")
    return client


@app.get("/oidc/authorize")
def oidc_authorize(request: Request,
                   client_id: str, redirect_uri: str, response_type: str,
                   scope: str, state: str,
                   code_challenge: str | None = None,
                   code_challenge_method: str | None = None,
                   creds: HTTPAuthorizationCredentials | None = Depends(_bearer)):
    claims = _current_token(request, creds)  # the end-user session
    client = _get_client_or_400(client_id)
    allowed_uris = json.loads(client["redirect_uris"])
    if redirect_uri not in allowed_uris:
        raise HTTPException(400, "redirect_uri not registered")
    if response_type != "code":
        loc = f"{redirect_uri}?{urlencode({'error': 'unsupported_response_type', 'state': state})}"
        return RedirectResponse(loc, status_code=302)
    wanted = set(scope.split())
    if not wanted <= set(client["allowed_scopes"].split()):
        loc = f"{redirect_uri}?{urlencode({'error': 'invalid_scope', 'state': state})}"
        return RedirectResponse(loc, status_code=302)
    if not wanted <= set(claims.get("scope", "").split()):
        loc = f"{redirect_uri}?{urlencode({'error': 'access_denied', 'state': state})}"
        return RedirectResponse(loc, status_code=302)
    if not client["confidential"] and not code_challenge:
        loc = f"{redirect_uri}?{urlencode({'error': 'invalid_request', 'state': state})}"
        return RedirectResponse(loc, status_code=302)
    if code_challenge and code_challenge_method not in (None, "S256"):
        loc = f"{redirect_uri}?{urlencode({'error': 'invalid_request', 'state': state})}"
        return RedirectResponse(loc, status_code=302)
    code = db.create_auth_code(client_id, claims["sub"], redirect_uri,
                               " ".join(sorted(wanted)), code_challenge,
                               code_challenge_method or "S256")
    db.audit("auth_code_issued", actor_user_id=claims["sub"],
             ip=_client_ip(request), details={"client_id": client_id})
    return RedirectResponse(f"{redirect_uri}?{urlencode({'code': code, 'state': state})}",
                            status_code=302)


def _authenticate_client(client: dict, client_secret: str | None) -> bool:
    if not client["confidential"]:
        return True  # public client: PKCE is the authentication
    if not client_secret:
        return False
    return crypto.sha256_hex(client_secret) == (client["client_secret_hash"] or "")


@app.post("/oidc/token")
def oidc_token(request: Request,
               grant_type: str = Form(...),
               code: str | None = Form(None),
               redirect_uri: str | None = Form(None),
               client_id: str | None = Form(None),
               client_secret: str | None = Form(None),
               code_verifier: str | None = Form(None),
               refresh_token: str | None = Form(None),
               scope: str | None = Form(None)):
    hit = _rate_limit_or_429(f"token:{_client_ip(request)}", 60, 60, request)
    if hit:
        return hit
    if not client_id:
        return _err("invalid_client", "client_id required.", 401)
    client = _get_client_or_400(client_id)
    if not _authenticate_client(client, client_secret):
        db.audit("invalid_client", ip=_client_ip(request), details={"client_id": client_id})
        return _err("invalid_client", "Client authentication failed.", 401)

    if grant_type == "authorization_code":
        if not code or not redirect_uri:
            return _err("invalid_request", "code and redirect_uri required.", 400)
        row = db.get_auth_code(code)
        if not row or row["client_id"] != client_id:
            return _err("invalid_grant", "Invalid authorization code.", 400)
        if row["used"]:
            # Replay of a used code: possible theft. Kill tokens issued from it.
            if row["issued_family_id"]:
                db.revoke_family(row["issued_family_id"])
            db.audit("auth_code_reuse_detected", actor_user_id=row["user_id"],
                     ip=_client_ip(request), details={"client_id": client_id})
            return _err("invalid_grant", "Invalid authorization code.", 400)
        if row["expires_at"] < crypto.now() or row["redirect_uri"] != redirect_uri:
            return _err("invalid_grant", "Invalid authorization code.", 400)
        if row["code_challenge"]:
            if not code_verifier or not crypto.pkce_challenge_ok(
                    code_verifier, row["code_challenge"], row["challenge_method"]):
                return _err("invalid_grant", "PKCE verification failed.", 400)
        user = db.get_user_by_id(row["user_id"])
        if not user or user["status"] != "active":
            return _err("invalid_grant", "Invalid authorization code.", 400)
        scopes = set(row["scopes"].split())
        secret, fam = db.create_refresh_token(user["id"], " ".join(sorted(scopes)))
        db.mark_code_used(row["code_hash"], fam["family_id"])
        access, _j = _mint_access(user, scopes, fam["family_id"], client_id)
        db.audit("auth_code_exchanged", actor_user_id=user["id"],
                 ip=_client_ip(request), details={"client_id": client_id})
        out = {"access_token": access, "refresh_token": secret,
               "token_type": "Bearer", "expires_in": config.ACCESS_TOKEN_TTL}
        if "openid" in scopes:
            out["id_token"] = _mint_id_token(user, client_id, row["auth_time"])
        return out

    if grant_type == "refresh_token":
        if not refresh_token:
            return _err("invalid_request", "refresh_token required.", 400)
        out, err = _do_refresh(refresh_token, request, client_id=client_id)
        if err:
            return err
        if scope:  # scope may only narrow, never widen
            granted = set(out["scopes"].split())
            if not set(scope.split()) <= granted:
                return _err("invalid_scope", "Requested scope exceeds granted scope.", 400)
        return out

    return _err("unsupported_grant_type", "Grant type not supported.", 400)


@app.get("/oidc/userinfo")
def oidc_userinfo(request: Request,
                  creds: HTTPAuthorizationCredentials | None = Depends(_bearer)):
    # verify_aud=False: the access token's aud is the RP's client_id, which the
    # userinfo endpoint cannot know up front. Signature + expiry + openid scope
    # are still enforced.
    claims = _current_token(request, creds, audience=None, verify_aud=False)
    _require_scope(claims, "openid", request, claims["sub"])
    user = db.get_user_by_id(claims["sub"])
    if not user:
        raise HTTPException(401, "invalid token")
    return {"sub": user["id"], "email": user["email"],
            "email_verified": False, "auth_time": claims.get("iat")}


@app.get("/oidc/jwks")
def oidc_jwks():
    return crypto.jwks()


@app.get("/.well-known/openid-configuration")
def oidc_discovery():
    base = config.ISSUER
    return {
        "issuer": base,
        "authorization_endpoint": base + "/oidc/authorize",
        "token_endpoint": base + "/oidc/token",
        "userinfo_endpoint": base + "/oidc/userinfo",
        "jwks_uri": base + "/oidc/jwks",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "scopes_supported": ["openid", "profile", "email", "devices:manage"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic", "none"],
        "code_challenge_methods_supported": ["S256"],
        "id_token_signing_alg_values_supported": ["RS256"],
    }
