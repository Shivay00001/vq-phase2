# VisionID — Phase 2 identity infrastructure (prototype slice)

Identity provider for the VisionQuantech U-STACK: one credential store, one token
issuer, one audit trail for all VisionQuantech apps (VisionSSO / VisionAuth Server /
device identity / access manager).

> **Honesty banner:** this is a **local prototype slice**, not production auth.
> It implements real cryptography and real security logic (nothing is stubbed in
> the auth paths), but it is missing the operational controls that make auth
> production-safe. See "What's prototype" below.

## Layout

```
vision-id/
├── README.md            # this file
├── docs/
│   ├── RESEARCH.md      # competitor notes: Auth0/Keycloak/Supabase Auth, OIDC flows, what prod auth needs
│   ├── PRD.md           # problem, four panels, user stories, non-goals, monetization
│   ├── TRD.md           # architecture, stack, RBAC matrix, rate limits, scaling & cost
│   ├── ERD.md           # tables, invariants (rotation, single-use codes, append-only audit)
│   └── API_CONTRACTS.md # endpoints, auth, rate limits, token lifetimes
└── slice/               # working auth server (FastAPI, local-only)
    ├── visionid/        # app: config, crypto, db, ratelimit, rbac, routes
    ├── tests/test_visionid.py
    ├── requirements.txt
    ├── run.sh           # start server on 127.0.0.1:8000
    └── run_tests.sh     # run the suite
```

## Quick start (zero spend, nothing deployed)

```bash
cd ~/workspace/products/vq-phase2/vision-id/slice
~/workspace/venvs/vision-id/bin/pip install -r requirements.txt
./run_tests.sh        # 25 tests, incl. 10 adversarial
./run.sh              # server on http://127.0.0.1:8000
```

Try it:
```bash
# register + login
curl -s -X POST 127.0.0.1:8000/v1/users/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"ada@local","password":"Str0ng!Passw0rd#2026"}'
curl -s -X POST 127.0.0.1:8000/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"ada@local","password":"Str0ng!Passw0rd#2026"}'
# OIDC discovery + JWKS
curl -s 127.0.0.1:8000/.well-known/openid-configuration | head -c 400
```

## What's REAL in the slice (actually implemented, tested)

- **Password storage:** Argon2id (`time_cost=3, memory=64MB`), bcrypt legacy verify + auto-rehash; strict policy (12+ chars, 4 classes, common-password blocklist, email-name check); generic `invalid_credentials` on login (no user enumeration; dummy-hash verify on unknown email).
- **Tokens:** RS256 JWT access (15 min) + ID tokens, published **JWKS** (`/oidc/jwks`) and **OIDC discovery**; opaque 256-bit refresh tokens stored as SHA-256, **rotation on every use**, **reuse-of-superseded-token revokes the whole family** (theft detection, like Auth0).
- **OIDC subset:** `/oidc/authorize` (302 + `state` echo, exact `redirect_uri` match, scope ceiling), `/oidc/token` with `authorization_code` (single-use, 10-min, **PKCE S256 mandatory for public clients**) and `refresh_token` grants (scope can only narrow), `/oidc/userinfo`.
- **Logout/revocation:** refresh family revoked + access-token `jti` blacklisted until expiry.
- **Device identity:** Ed25519 per-device keypairs; enroll → sign challenge → activate (proof of possession, private key never sent); per-user list/revoke; revoking a device kills its sessions (IDOR-checked: users can't touch others' devices).
- **RBAC scopes:** deny-by-default, role→scope map (`admin:audit` etc.), enforced server-side on every route; scope-denied attempts are audit-logged.
- **Brute-force defense:** 5 failures/15 min per (IP,email) → 423 lockout (correct password also rejected while locked); per-IP rate limits with `Retry-After`.
- **Audit log:** append-only table, 15 event types, never stores passwords.
- **Tests:** 25/25 passing, incl. 10 adversarial: forged signature (wrong RSA key), `alg:none`, expired token, auth-code replay (burns the issued session), brute-force lockout, revoked-refresh reuse, scope escalation (user→admin, forged admin scope, widen-on-refresh), bad device signature, cross-user device IDOR, login rate-limit hammering.

## What's PROTOTYPE (do NOT treat as production)

| Area | Slice reality | Production needs |
|---|---|---|
| Signing keys | Generated in-memory at startup, lost on restart | **HSM/KMS**, persisted, automated rotation with `kid` rollover |
| Persistence | Single SQLite file | Postgres + RLS, backups, tested restore |
| Rate limiting | In-process memory (per-replica, lost on restart) | Redis counters shared across replicas |
| Revocation list | In-process memory | Redis set with TTL |
| MFA / passwordless | Not implemented | TOTP + **WebAuthn/passkeys** (roadmap) |
| Breach monitoring | Common-password blocklist only | HIBP k-anonymity check, anomaly detection |
| Federation | Not implemented | SAML, OIDC upstream, SCIM, social logins |
| KYC | Out of scope | Only if a product needs verified identity |
| Compliance | None | SOC 2 / ISO 27001, DPDP Act handling, DPA |
| Device attestation | Keypair only | Android SafetyNet / Apple DeviceCheck |
| Client secrets | Test-only values in `config.py` | Vault-managed, rotation |
| Cookies/CSRF | Bearer-token API only | CSRF tokens if cookie sessions are added |

## Test report

```
25/25 passed (10/10 adversarial) — run via slice/run_tests.sh
```

## Roadmap (from PRD/TRD)

1. MFA (TOTP) + WebAuthn/passkeys → 2. KMS-backed keys + rotation ceremony → 3. Postgres/Redis port (framework stack) → 4. SAML/SCIM + enterprise SSO → 5. Anomaly/breach monitoring → 6. Compliance certifications → 7. Managed "VisionID for clients" offering.
