# VisionID — API Contracts

**Base:** `http://localhost:8000` (slice). All JSON unless noted. Auth: `Authorization: Bearer <access_token>` (JWT RS256) except where stated. Error envelope: `{"error": "<code>", "message": "<human, non-leaking>"}`.

## 1. User & session API (`/v1`)

### POST /v1/users/register
- **Body:** `{"email": "a@b.c", "password": "..."}`
- **Policy:** ≥12 chars, upper+lower+digit+symbol, not in common-password blocklist, must not contain the email local-part.
- **201:** `{"id": "usr_...", "email": "a@b.c"}` · **422:** `{"error":"weak_password","reasons":[...]}` · **429** rate-limited.

### POST /v1/auth/login
- **Body:** `{"email","password","device_id"?}`
- **200:** `{"access_token","id_token"?,"refresh_token","token_type":"Bearer","expires_in":900,"scopes":"..."}` (id_token only when `scope` includes `openid` — request via `{"scope":"openid profile email"}`).
- **401:** `{"error":"invalid_credentials"}` — identical for unknown email vs wrong password (no enumeration). **423/429** when locked/rate-limited, with `Retry-After`.
- Rate limit: 5 failures/15 min per (IP,email); 20/min per IP.

### POST /v1/auth/refresh
- **Body:** `{"refresh_token":"..."}`
- **200:** new token set (rotation; old token superseded). **401** `invalid_grant` on unknown/revoked/reused token; reuse of a superseded token revokes the whole family (returns 401 + audit event).

### POST /v1/auth/logout
- **Auth:** Bearer access token **or** body `{"refresh_token"}`.
- Revokes the presented refresh token (and its family) + blacklists the access-token `jti` until expiry. **200** `{"revoked": true}`.

### GET /v1/me
- **Auth:** Bearer. **200:** `{"id","email","roles","devices": n}`.

### Devices — POST /v1/devices/enroll
1. **Auth** Bearer. **Body:** `{"device_name","platform","public_key_jwk":{...Ed25519...}}` → **201** `{"device_id":"dev_...","status":"pending","challenge":"<base64url 32B>"}`.
2. Client signs challenge with the device private key; **POST /v1/devices/{id}/activate** `{"signature":"<b64url>"}` → verifies with stored JWK → **200** `{"status":"active"}`. (Proof of possession; private key never sent.)
- **GET /v1/devices** → own device list. **POST /v1/devices/{id}/revoke** → **200**; revokes device + its refresh-token families.

### Admin — GET /v1/admin/audit?limit=50
- **Scope required:** `admin:audit` (roles `admin`). **200:** audit rows. **403** `insufficient_scope` otherwise (also audit-logged as `scope_denied`).

## 2. OIDC subset (`/oidc`)

### GET /oidc/authorize?client_id&redirect_uri&response_type=code&scope&state&code_challenge&code_challenge_method=S256
- **Auth:** Bearer access token of the end user (the "login session" for the slice).
- Validates client, exact `redirect_uri` match, scope ⊆ client `allowed_scopes`; stores PKCE challenge.
- **302** to `{redirect_uri}?code=<single-use>&state=<echoed>`. Errors redirect with `?error=...&state=...` per OIDC.

### POST /oidc/token (application/x-www-form-urlencoded)
- **grant_type=authorization_code:** `code`, `redirect_uri`, `client_id` (+ `client_secret` if confidential, else `code_verifier` for PKCE).
  Code must be unused and unexpired; PKCE verifier SHA-256 must match challenge; then code marked used.
  **200:** `{"access_token","id_token","refresh_token","token_type":"Bearer","expires_in":900}`.
- **grant_type=refresh_token:** `refresh_token`, `client_id` (+secret if confidential). Same rotation semantics as `/v1/auth/refresh`; scope cannot widen.
- Errors: `{"error":"invalid_grant"|"invalid_client"|"invalid_request"}`.

### GET /oidc/userinfo
- **Auth:** Bearer access token with `openid` scope. **200:** `{"sub","email","email_verified":false,"auth_time":...}`.

### GET /oidc/jwks — `{"keys":[{kty:"RSA",kid,n,e,use:"sig",alg:"RS256"}]}`
### GET /.well-known/openid-configuration — issuer, endpoints, supported scopes/grants/methods.

## 3. Rate limits (summary)

| Endpoint | Limit |
|---|---|
| POST /v1/users/register | 10/hour/IP |
| POST /v1/auth/login | 5 fails/15min per (IP,email); 20/min/IP |
| POST /oidc/token | 60/min/IP |
| everything else | 300/min/IP |

## 4. Token lifetimes (slice)

Access 15 min · ID 15 min · Auth code 10 min (single-use) · Refresh 30 days sliding · Device challenge 10 min.
