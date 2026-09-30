# VisionID — Technical Requirements Document (TRD)

**Date:** 2026-09-26. Slice stack: Python 3.12, FastAPI, SQLite (parameterized), PyJWT RS256, Argon2id (bcrypt fallback), stdlib tests via FastAPI TestClient.

## 1. Architecture

```
                    ┌─────────────────────────────────────────┐
                    │            VisionID Auth Server          │
                    │  (FastAPI, single process, local-only)   │
┌──────────┐        │                                         │
│  Browser │───────▶│  /v1/users/register  /v1/auth/login     │
│  / SPA   │        │  /v1/auth/refresh    /v1/auth/logout     │
└──────────┘        │  /v1/me  /v1/devices/*  /v1/admin/*      │
                    │                                         │
┌──────────┐        │  OIDC: /oidc/authorize →302→ RP          │
│ Relying  │───────▶│        /oidc/token (code+refresh grants)│
│ Party    │        │        /oidc/userinfo  /oidc/jwks        │
└──────────┘        │        /.well-known/openid-configuration │
                    │                                         │
                    │  SQLite: users, refresh_tokens,         │
                    │  auth_codes, devices, clients, audit_log │
                    └─────────────────────────────────────────┘
```

**Trust boundaries:** the server is the only holder of the RSA signing key and password hashes.
Clients never see refresh-token plaintext after issuance except at the moment of issue/rotation.
Device private keys never leave the device (enrollment sends only the public JWK; slice
verifies possession with a signed challenge).

## 2. Tech stack (slice vs production)

| Layer | Slice (now) | Production target (roadmap) |
|---|---|---|
| Runtime | FastAPI + uvicorn (local) | Cloudflare Workers / Supabase Edge (framework default) |
| DB | SQLite, parameterized queries | Postgres (Supabase) + RLS on every table |
| Rate limit | In-process fixed-window counters | Upstash Redis counters |
| Passwords | Argon2id (argon2-cffi), policy + common-password blocklist | + breach-corpus (HIBP k-anonymity) |
| JWT | RS256, keypair generated at startup (test-only), JWKS published | KMS/HSM-backed keys, automated rotation, `kid` rollover |
| Refresh tokens | Opaque 256-bit, SHA-256 stored, rotation + reuse detection | Same, backed by Redis revocation set |
| Devices | Ed25519 per-device keys, challenge-response enrollment | + attestation (Android SafetyNet / Apple DeviceCheck) |

## 3. Data model

See `docs/ERD.md`. Core invariants enforced in code:
- `refresh_tokens` rows are **never deleted on rotation** — old token marked `superseded`; reuse of a superseded/revoked token revokes the whole **family** (theft detection).
- `auth_codes` are **single-use, 10-minute TTL**; used codes are marked, and re-presentation raises a theft alert + revokes the associated client grant.
- Password hashes: Argon2id (`time_cost=3, memory_cost=65536, parallelism=4`); legacy bcrypt accepted on verify, re-hashed on next login.
- Audit log is **append-only**; no UPDATE/DELETE path exists in code.

## 4. API contracts

See `docs/API_CONTRACTS.md`. Error envelope: `{"error": "<code>", "message": "<generic>"}`
— never leaks "user exists" on login (always `invalid_credentials`), never stack traces.

## 5. Rate limiting (slice)

- Login: 5 failures / 15 min per (client IP, email) → account+IP lockout 15 min; 20 attempts / min per IP global → 429.
- Register: 10 / hour per IP. Token endpoint: 60 / min per IP. All others: 300 / min per IP.
- Responses carry `Retry-After`. Lockout is enforced even with correct credentials (prevents oracle probing).

## 6. RBAC / scopes

Access tokens carry space-delimited `scope`. Enforcement matrix (deny-by-default):

| Scope | Granted to | Allows |
|---|---|---|
| `openid profile email` | any authenticated user | OIDC userinfo, own profile |
| `devices:manage` | user (own devices only) | enroll/list/revoke own devices |
| `support:read`, `support:write` | support role | read auth state, force logout |
| `admin:users`, `admin:audit`, `admin:keys` | admin role | lock users, read audit, rotate keys |

Scope is bound at login/consent; the `/token` endpoint **never widens** scope beyond what was granted (escalation test covers this).

## 7. Scaling plan & cost (framework §3)

| Users | Slice | Production path |
|---|---|---|
| 1k | SQLite file, single process — fine | Supabase free tier; Upstash free Redis |
| 100k | — | Postgres + Redis; stateless API replicas; JWKS cached at edge |
| 1M | — | KMS keys, read replicas, regional edge issuance; est. infra ~$150–400/mo on the framework's free-tier-first stack vs ~$500–2,000/mo equivalent Auth0 Enterprise |

Zero spend in this phase: no deployment, no paid services, test-only keys.

## 8. Security review notes (framework §4, slice applicability)

- [x] No IDOR: device/user reads always filtered by `sub`; admin/support gated by scope
- [x] JWT expiry + refresh rotation + reuse detection; OAuth `state` validated by RP (server echoes it)
- [x] Password policy + common-password blocklist; generic login errors
- [x] Rate limits on register/login/token
- [x] Parameterized SQL everywhere; no string interpolation
- [x] Secrets only in env/test config; never logged; test keys clearly labeled
- [x] Audit log for all auth events
- [ ] (roadmap) breach-list check, MFA, KMS keys, CSRF on cookie flows — N/A or deferred, documented in README gaps
