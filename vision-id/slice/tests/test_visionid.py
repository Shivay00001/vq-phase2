"""VisionID slice tests: functional + ADVERSARIAL. Run with:
    ~/workspace/venvs/vision-id/bin/python -m tests.test_visionid
Each test gets a fresh DB + rate-limiter state."""
import base64
import hashlib
import json
import secrets
import sys
import os
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient
from cryptography.hazmat.primitives.asymmetric import rsa, ed25519
from cryptography.hazmat.primitives import serialization
import jwt as pyjwt

from visionid import app as appmod, config, crypto, db, ratelimit

client = TestClient(appmod.app, raise_server_exceptions=False)

STRONG = "Str0ng!Passw0rd#2026"
EMAIL = "ada@visionid.local"


def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def fresh():
    db.reset_for_tests()
    ratelimit.reset_for_tests()


def register(email=EMAIL, password=STRONG):
    return client.post("/v1/users/register", json={"email": email, "password": password})


def login(email=EMAIL, password=STRONG, **kw):
    return client.post("/v1/auth/login", json={"email": email, "password": password, **kw})


def authz(token):
    return {"Authorization": f"Bearer {token}"}


# ------------------------------------------------------------- functional
def test_register_ok():
    fresh()
    r = register()
    assert r.status_code == 201, r.text
    assert r.json()["email"] == EMAIL


def test_register_duplicate():
    fresh()
    assert register().status_code == 201
    assert register().status_code == 409


def test_weak_passwords_rejected():
    fresh()
    cases = [("u0@x.local", "password123"), ("u1@x.local", "short1!A"),
             ("u2@x.local", "alllowercase!!1"), ("u3@x.local", "ALLUPPERCASE!!1"),
             ("u4@x.local", "NoDigitsHere!!"), ("u5@x.local", "NoSymbols12345"),
             ("ada@x.local", "ada123!ABCxxXYZ")]  # last contains email local-part
    for email, pw in cases:
        r = register(email=email, password=pw)
        assert r.status_code == 422, (pw, r.text)
        assert r.json()["error"] == "weak_password"
        assert r.json()["reasons"], pw


def test_login_ok_and_me():
    fresh()
    register()
    r = login()
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["access_token"] and body["refresh_token"]
    assert "openid" in body["scopes"]  # default scope includes openid -> id_token present
    assert "id_token" in body
    me = client.get("/v1/me", headers=authz(body["access_token"]))
    assert me.status_code == 200
    assert me.json()["email"] == EMAIL


def test_login_no_enumeration():
    fresh()
    register()
    a = login(password="Wr0ng!Passw0rd#x")
    b = login(email="nobody@visionid.local", password="Wr0ng!Passw0rd#x")
    assert a.status_code == 401 and b.status_code == 401
    assert a.json() == b.json(), "login errors must not distinguish unknown email"


def test_refresh_rotation_and_reuse_kills_family():
    fresh()
    register()
    t1 = login().json()["refresh_token"]
    r1 = client.post("/v1/auth/refresh", json={"refresh_token": t1})
    assert r1.status_code == 200
    t2 = r1.json()["refresh_token"]
    assert t2 != t1
    # old token is now superseded -> reuse must fail AND kill the whole family
    r2 = client.post("/v1/auth/refresh", json={"refresh_token": t1})
    assert r2.status_code == 401
    # the rotated (previously valid) token is now dead too -> theft contained
    r3 = client.post("/v1/auth/refresh", json={"refresh_token": t2})
    assert r3.status_code == 401, "family must be revoked after reuse detection"


def test_logout_revokes():
    fresh()
    register()
    tokens = login().json()
    r = client.post("/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]},
                    headers=authz(tokens["access_token"]))
    assert r.json()["revoked"] is True
    assert client.post("/v1/auth/refresh",
                       json={"refresh_token": tokens["refresh_token"]}).status_code == 401
    assert client.get("/v1/me", headers=authz(tokens["access_token"])).status_code == 401


def _pkce_pair():
    verifier = secrets.token_urlsafe(32)
    challenge = b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def _oidc_login_and_code(scope="openid profile email", client_id=config.TEST_PUBLIC_CLIENT_ID,
                         redirect="http://localhost:3000/callback"):
    register()
    tokens = login().json()
    verifier, challenge = _pkce_pair()
    r = client.get("/oidc/authorize", headers=authz(tokens["access_token"]), params={
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code",
        "scope": scope, "state": "xyz-state",
        "code_challenge": challenge, "code_challenge_method": "S256"},
        follow_redirects=False)
    return r, verifier, tokens


def test_oidc_authorization_code_flow_pkce():
    fresh()
    r, verifier, _ = _oidc_login_and_code()
    assert r.status_code == 302, r.text
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["state"] == ["xyz-state"]
    code = q["code"][0]
    t = client.post("/oidc/token", data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": "http://localhost:3000/callback",
        "client_id": config.TEST_PUBLIC_CLIENT_ID, "code_verifier": verifier})
    assert t.status_code == 200, t.text
    body = t.json()
    assert body["access_token"] and body["id_token"] and body["refresh_token"]
    # id_token verifies against JWKS-published key
    claims = crypto.verify_jwt(body["id_token"], audience=config.TEST_PUBLIC_CLIENT_ID)
    assert claims["iss"] == config.ISSUER
    # userinfo
    u = client.get("/oidc/userinfo", headers=authz(body["access_token"]))
    assert u.status_code == 200
    assert u.json()["email"] == EMAIL


def test_oidc_wrong_pkce_verifier_rejected():
    fresh()
    r, _verifier, _ = _oidc_login_and_code()
    code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
    t = client.post("/oidc/token", data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": "http://localhost:3000/callback",
        "client_id": config.TEST_PUBLIC_CLIENT_ID, "code_verifier": "wrong-verifier"})
    assert t.status_code == 400
    assert t.json()["error"] == "invalid_grant"


def test_oidc_bad_redirect_uri():
    fresh()
    register()
    tokens = login().json()
    r = client.get("/oidc/authorize", headers=authz(tokens["access_token"]), params={
        "client_id": config.TEST_PUBLIC_CLIENT_ID,
        "redirect_uri": "https://evil.example/cb", "response_type": "code",
        "scope": "openid", "state": "s", "code_challenge": "x",
        "code_challenge_method": "S256"})
    assert r.status_code == 400


def test_jwks_and_discovery():
    fresh()
    jwks = client.get("/oidc/jwks").json()
    assert jwks["keys"] and jwks["keys"][0]["kty"] == "RSA"
    assert jwks["keys"][0]["kid"] == crypto.KID
    disc = client.get("/.well-known/openid-configuration").json()
    assert disc["issuer"] == config.ISSUER
    assert "authorization_code" in disc["grant_types_supported"]


def _enroll_device(token, name="pixel-9"):
    priv = ed25519.Ed25519PrivateKey.generate()
    pub = priv.public_key()
    jwk = crypto.ed25519_jwk_from_pubkey(pub)
    r = client.post("/v1/devices/enroll", headers=authz(token),
                    json={"device_name": name, "platform": "android",
                          "public_key_jwk": jwk})
    assert r.status_code == 201, r.text
    body = r.json()
    sig = b64url(priv.sign(body["challenge"].encode()))
    a = client.post(f"/v1/devices/{body['device_id']}/activate",
                    headers=authz(token), json={"signature": sig})
    assert a.status_code == 200, a.text
    return body["device_id"], priv


def test_device_enroll_list_revoke():
    fresh()
    register()
    token = login().json()["access_token"]
    dev_id, _priv = _enroll_device(token)
    lst = client.get("/v1/devices", headers=authz(token)).json()["devices"]
    assert any(d["id"] == dev_id and d["status"] == "active" for d in lst)
    rv = client.post(f"/v1/devices/{dev_id}/revoke", headers=authz(token))
    assert rv.json()["status"] == "revoked"
    lst2 = client.get("/v1/devices", headers=authz(token)).json()["devices"]
    assert next(d for d in lst2 if d["id"] == dev_id)["status"] == "revoked"


def test_device_bound_session_dies_on_revoke():
    fresh()
    register()
    token = login().json()["access_token"]
    dev_id, _ = _enroll_device(token, name="laptop")
    sess = login(device_id=dev_id).json()
    assert client.post("/v1/auth/refresh",
                       json={"refresh_token": sess["refresh_token"]}).status_code == 200
    client.post(f"/v1/devices/{dev_id}/revoke", headers=authz(token))
    assert client.post("/v1/auth/refresh",
                       json={"refresh_token": sess["refresh_token"]}).status_code == 401


def test_admin_audit_rbac():
    fresh()
    register()
    token = login().json()["access_token"]
    r = client.get("/v1/admin/audit", headers=authz(token))
    assert r.status_code == 403
    assert r.json()["detail"] == "insufficient scope"
    # promote to admin -> new login carries admin scopes
    user = db.get_user_by_email(EMAIL)
    db.set_roles(user["id"], ["user", "admin"])
    admin_token = login().json()["access_token"]
    r2 = client.get("/v1/admin/audit", headers=authz(admin_token))
    assert r2.status_code == 200, r2.text
    events = {e["event"] for e in r2.json()["events"]}
    assert {"register", "login_success", "scope_denied"} <= events


def test_audit_log_records_auth_events():
    fresh()
    register()
    login(password="Wr0ng!Passw0rd#x")
    login()
    rows = db.read_audit(50)
    events = [r["event"] for r in rows]
    assert "register" in events and "login_failure" in events and "login_success" in events
    assert all("password" not in (r["details"] or "").lower() for r in rows), \
        "audit log must never contain passwords"


# ------------------------------------------------------------- ADVERSARIAL
def test_adv_forged_token_wrong_key():
    """Attacker signs a JWT with their own RSA key (same kid/claims). Must fail."""
    fresh()
    register()
    evil_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    evil_pem = evil_key.private_bytes(serialization.Encoding.PEM,
                                      serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption())
    forged = pyjwt.encode({"iss": config.ISSUER, "sub": "usr_admin",
                           "aud": config.API_AUDIENCE, "exp": crypto.now() + 3600,
                           "iat": crypto.now(), "jti": "jti_evil",
                           "scope": "admin:audit admin:users"},
                          evil_pem, algorithm="RS256",
                          headers={"kid": crypto.KID, "alg": "RS256"})
    r = client.get("/v1/me", headers=authz(forged))
    assert r.status_code == 401, "forged signature must be rejected"


def test_adv_none_algorithm_rejected():
    fresh()
    register()
    none_token = pyjwt.encode({"iss": config.ISSUER, "sub": "usr_x",
                               "aud": config.API_AUDIENCE, "exp": crypto.now() + 3600,
                               "iat": crypto.now(), "jti": "jti_none",
                               "scope": "admin:audit"}, key="", algorithm="none")
    assert client.get("/v1/me", headers=authz(none_token)).status_code == 401


def test_adv_expired_token_rejected():
    fresh()
    register()
    expired = crypto.mint_jwt({"iss": config.ISSUER, "sub": "usr_x",
                               "aud": config.API_AUDIENCE, "exp": crypto.now() - 10,
                               "iat": crypto.now() - 100, "jti": "jti_old",
                               "scope": "openid"})
    assert client.get("/v1/me", headers=authz(expired)).status_code == 401


def test_adv_replay_used_auth_code():
    """Second presentation of an auth code must fail and burn the issued session."""
    fresh()
    r, verifier, _ = _oidc_login_and_code()
    code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
    first = client.post("/oidc/token", data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": "http://localhost:3000/callback",
        "client_id": config.TEST_PUBLIC_CLIENT_ID, "code_verifier": verifier})
    assert first.status_code == 200
    stolen_refresh = first.json()["refresh_token"]
    replay = client.post("/oidc/token", data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": "http://localhost:3000/callback",
        "client_id": config.TEST_PUBLIC_CLIENT_ID, "code_verifier": verifier})
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"
    # theft response: the session minted from the code is revoked too
    assert client.post("/oidc/token", data={
        "grant_type": "refresh_token", "refresh_token": stolen_refresh,
        "client_id": config.TEST_PUBLIC_CLIENT_ID}).status_code == 401
    # audit captured the attack
    assert any(e["event"] == "auth_code_reuse_detected" for e in db.read_audit(50))


def test_adv_brute_force_lockout():
    fresh()
    register()
    for _ in range(4):
        assert login(password="Wr0ng!Passw0rd#x").status_code == 401
    # 5th failure trips the lockout
    assert login(password="Wr0ng!Passw0rd#x").status_code == 423
    # correct password must still be rejected while locked (no oracle)
    locked = login(password=STRONG)
    assert locked.status_code == 423, locked.text
    assert locked.json()["error"] == "account_locked"


def test_adv_revoked_refresh_reuse():
    fresh()
    register()
    tokens = login().json()
    client.post("/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    r = client.post("/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401


def test_adv_scope_escalation_blocked():
    """A plain user token must not reach admin endpoints, and scope can't widen."""
    fresh()
    register()
    token = login().json()["access_token"]
    assert client.get("/v1/admin/audit", headers=authz(token)).status_code == 403
    # forged scope claim inside an attacker-signed token -> signature fails first
    evil_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    evil_pem = evil_key.private_bytes(serialization.Encoding.PEM,
                                      serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption())
    forged = pyjwt.encode({"iss": config.ISSUER, "sub": "usr_x",
                           "aud": config.API_AUDIENCE, "exp": crypto.now() + 3600,
                           "iat": crypto.now(), "jti": "jti_esc",
                           "scope": "openid admin:audit"}, evil_pem,
                          algorithm="RS256", headers={"kid": crypto.KID})
    assert client.get("/v1/admin/audit", headers=authz(forged)).status_code == 401
    # OIDC refresh grant: requesting wider scope than granted -> invalid_scope
    r, verifier, _ = _oidc_login_and_code(scope="openid")
    code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
    t = client.post("/oidc/token", data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": "http://localhost:3000/callback",
        "client_id": config.TEST_PUBLIC_CLIENT_ID, "code_verifier": verifier})
    rt = t.json()["refresh_token"]
    w = client.post("/oidc/token", data={
        "grant_type": "refresh_token", "refresh_token": rt,
        "client_id": config.TEST_PUBLIC_CLIENT_ID, "scope": "openid admin:audit"})
    assert w.status_code == 400
    assert w.json()["error"] == "invalid_scope"


def test_adv_device_activation_bad_signature():
    fresh()
    register()
    token = login().json()["access_token"]
    priv = ed25519.Ed25519PrivateKey.generate()
    jwk = crypto.ed25519_jwk_from_pubkey(priv.public_key())
    r = client.post("/v1/devices/enroll", headers=authz(token),
                    json={"device_name": "d", "platform": "web",
                          "public_key_jwk": jwk})
    dev_id = r.json()["device_id"]
    other = ed25519.Ed25519PrivateKey.generate()  # wrong key signs
    bad_sig = b64url(other.sign(r.json()["challenge"].encode()))
    a = client.post(f"/v1/devices/{dev_id}/activate", headers=authz(token),
                    json={"signature": bad_sig})
    assert a.status_code == 401


def test_adv_cross_user_device_idor():
    """User B must not see/revoke user A's devices (no IDOR)."""
    fresh()
    register(email="a@x.local")
    token_a = login(email="a@x.local").json()["access_token"]
    dev_id, _ = _enroll_device(token_a)
    register(email="b@x.local")
    token_b = login(email="b@x.local").json()["access_token"]
    assert client.post(f"/v1/devices/{dev_id}/revoke",
                       headers=authz(token_b)).status_code == 404
    assert all(d["id"] != dev_id
               for d in client.get("/v1/devices", headers=authz(token_b)).json()["devices"])


def test_adv_rate_limit_login_endpoint():
    fresh()
    register()
    # hammer with wrong passwords from same IP: lockout must engage (423s appear)
    statuses = {login(password="Wr0ng!Passw0rd#x").status_code for _ in range(12)}
    assert 423 in statuses or 429 in statuses


# ------------------------------------------------------------- runner
if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed, failed = 0, []
    adv_passed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
            if "_adv_" in name:
                adv_passed += 1
            print(f"PASS {name}")
        except Exception as e:
            failed.append((name, e))
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    total = len(tests)
    adv_total = sum(1 for n, _ in tests if "_adv_" in n)
    print(f"\n{passed}/{total} passed ({adv_passed}/{adv_total} adversarial)")
    sys.exit(1 if failed else 0)
