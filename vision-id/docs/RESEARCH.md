# VisionID — Competitor & Standards Research

Date: 2026-09-26. Method: web research on managed auth providers, self-hosted IdPs, and the OAuth 2.0 / OIDC standards before designing anything (framework rule: search → think → understand → build).

## 1. Managed / self-hosted identity providers

| Provider | Model | Free tier (Sept 2026 data) | Strengths | Weaknesses / watch-outs |
|---|---|---|---|---|
| **Auth0 (Okta)** | Cloud CIAM | 25,000 MAU | Enterprise-grade: SSO/SAML, fine-grained authz, SOC2/HIPAA, huge SDK catalog, breach detection, anomaly detection | Expensive past free tier (~$0.07/MAU); tier jumps; complex config |
| **Keycloak** | Self-hosted, Apache 2.0, OIDC-certified | ∞ (infra cost only, ~$24/mo droplet) | Full OIDC/SAML, realms, user federation, zero licence cost, data sovereignty | Heavy (Java/Quarkus, ~4 GB RAM min); you own upgrades, patches, backups, theming; steep learning curve |
| **Supabase Auth** | BaaS-bundled | 50,000 MAU | Postgres-native, JWT + refresh rotation + RLS, hours to integrate | B2C-leaning; thin B2B orgs/SSO story; you own the UI |
| **Clerk** | Cloud | 50,000 MRU | Fastest B2C shipping, polished components | Cost scales per-user; React-first; SSO/SCIM thin |
| **WorkOS** | Cloud B2B | 1M MAU AuthKit free | SSO + SCIM done right for B2B | Overkill for pure B2C; per-connection pricing upmarket |
| **FusionAuth** | Self-hosted, source-available | Community free, unlimited users | No Java ops burden like Keycloak | Licence not OSI-approved |
| **Ory (Kratos/Hydra/Keto)** | Self-hosted modular | Open source | Kubernetes-native, best-of-breed components | You assemble and staff the parts |

**What makes them win:** standards compliance (OIDC-certified), refresh-token rotation with reuse detection, short-lived access tokens + JWKS rotation, brute-force/breach defenses, MFA/WebAuthn, audit trails, and enterprise federation (SAML/SCIM). Nobody wins on "custom crypto" — they all use boring standard primitives.

## 2. OAuth 2.0 / OIDC flows (what production auth actually implements)

- **Authorization code flow (RFC 6749 §4.1)** is the only recommended interactive flow. Steps: client redirects user-agent to `/authorize` → user authenticates + consents → server redirects back with short-lived single-use **authorization code** → client exchanges code at `/token` (server-to-server) for access/ID/refresh tokens.
- **PKCE (RFC 7636, recommended for ALL clients per RFC 9700 §2.1.1):** client generates `code_verifier`, sends `code_challenge` (SHA-256) at authorize time, proves possession with the verifier at token time. Defeats authorization-code interception. VisionID requires PKCE for public clients.
- **Refresh token rotation:** each refresh issues a *new* refresh token and invalidates the old one; **reuse of an already-rotated token = theft signal → revoke the whole token family** (Auth0/Keycloak behavior). VisionID implements this.
- **ID token vs access token:** OIDC adds the ID token (JWT with `sub`, `auth_time`) answering *who the user is*; access token answers *what the client may do* (scopes). `/userinfo` returns profile claims for a valid access token. **Implicit flow is legacy and must not be implemented.**
- **JWKS (`/.well-known/jwks.json` + `openid-configuration`):** resource servers validate JWTs using the published public keys (`kid`-keyed); enables key rotation without redeploying clients.

## 3. What production auth needs (beyond the happy path)

Grouped from competitor behavior + framework §4 checklist:

1. **Key management:** signing keys in HSM/KMS, automated rotation, never in repo/env files. *Slice: generated in-memory test keys, rotation stubbed.*
2. **Credential hygiene:** Argon2id/bcrypt, strict password policy, breach-corpus check (e.g. "have i been pwned" k-anonymity), no user-enumeration via error messages.
3. **Brute-force defense:** per-account + per-IP rate limiting, progressive lockout, CAPTCHA step-up.
4. **Token hygiene:** short access-token TTL, rotation + reuse detection, revocation list, `jti` tracking, audience/scope binding.
5. **MFA / passwordless:** TOTP, WebAuthn/passkeys (roadmap for VisionID).
6. **Device identity:** per-device keypairs, enrollment ceremony, per-user device list, revoke-on-compromise, step-up on new device.
7. **Authorization:** RBAC scopes enforced server-side (deny-by-default), never frontend-only; four-panel matrix (admin/user/support/partner).
8. **Observability:** append-only audit log of every auth event; anomaly/breach monitoring.
9. **Compliance:** SOC 2, ISO 27001, GDPR data handling; KYC only where the product needs it.
10. **Federation:** SAML/OIDC enterprise SSO, SCIM provisioning (roadmap).

## 4. What to copy / what to beat

- **Copy:** OIDC discovery + JWKS, PKCE-everywhere, rotation-with-reuse-detection, generic login errors, rate-limited auth endpoints, audit everything.
- **Beat (VisionID's edge):** first-class **device identity** (per-device keys enrolled at login, listed/revoked per user — competitors bolt this on), and a single identity spanning VisionQuantech's U-STACK apps (VisionSSO/VisionAuth as the platform IdP rather than per-app auth).

Sources: pricing/feature tables from bytesbrothers.com (Aug 2026), guptadeepak.com (Sept 2026), osohq.com comparison; flow details from Ping Identity AM 7.2 docs, ZITADEL OIDC guide, abblix.com, and chrisklug/zeekayda-auth OIDC primer (RFC 6749/7636/9700 references).
