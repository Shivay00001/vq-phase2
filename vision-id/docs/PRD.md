# VisionID — Product Requirements Document (PRD)

**Product:** VisionID — identity & access infrastructure for the VisionQuantech U-STACK
(identity provider behind VisionSSO, VisionAuth Server, device identity, access manager).
**Phase:** Phase 2 foundation. **Status:** prototype slice (see README "What's real vs prototype").
**Date:** 2026-09-26. **Author:** Nerc (agent) for Shivay / VisionQuantech.

## 1. Problem

Every VisionQuantech app (EnterChat, YOU.PDF, client products) currently needs its own login,
password storage, and session logic. That means N implementations of the N hardest security
surface to get right. VisionID centralizes identity once: one credential store, one token
issuer, one audit trail, one revocation point — so product teams never touch passwords or
hand-roll JWT validation.

## 2. Users (framework four panels)

| Panel | Who | Needs |
|---|---|---|
| **Admin** (platform owner) | Shivay / VisionQuantech ops | User lookup, lock/unlock accounts, revoke sessions/devices, rotate signing keys, read audit log, manage OIDC clients & scopes. All actions audit-logged. |
| **User** (end customer) | People using VisionQuantech apps | Register, log in, MFA, manage own devices, view active sessions, revoke own refresh tokens, consent to OIDC scopes. |
| **Support** | Customer support staff | Read user auth state (locked? devices?), force logout, issue step-up verification. **No** key rotation, **no** PII export, every action ticket-linked. Support sessions expire faster. |
| **Partner** (relying party) | External app developers / client businesses | Register an OIDC client, get `client_id`/`client_secret`, configure redirect URIs, read own client's token stats. Cannot see other partners' clients or users. |

## 3. User stories (slice scope)

1. As a **user**, I can register with an email + strong password; weak passwords are rejected with specific guidance.
2. As a **user**, I can log in and receive a short-lived access token + rotating refresh token; wrong credentials never reveal whether the email exists.
3. As a **user**, I can log in from a new phone, enroll it as a trusted device (per-device key), and later revoke it from my device list.
4. As a **user**, I can log out; my refresh token stops working immediately.
5. As a **relying-party developer**, I can run the standard OIDC authorization-code + PKCE flow against VisionID (`/authorize` → `/token` → `/userinfo`) and validate tokens via published JWKS.
6. As **ops**, I can see every auth event (logins, failures, lockouts, token reuse, revocations) in an append-only audit log.
7. As an **attacker**, I cannot: forge tokens with another key, replay authorization codes, brute-force passwords, reuse revoked refresh tokens, or escalate scopes. (Adversarial stories — covered by tests.)

## 4. Success metrics (for the slice)

- All functional + adversarial tests pass (target: ≥ 30 tests, 100% pass).
- Login p99 < 200 ms locally (Argon2id dominates; acceptable).
- Zero critical findings from the framework §4 security checklist items applicable to a local prototype.

## 5. Non-goals (explicitly out of the slice)

- MFA/TOTP, WebAuthn/passkeys, SMS/email OTP (roadmap).
- SAML, SCIM, enterprise SSO, social login federation.
- KYC / identity verification, breach-corpus API checks.
- HSM/KMS-backed keys, multi-region, production persistence (Postgres/Redis), certifications (SOC 2, ISO 27001).

## 6. Monetization (per framework: state it)

VisionID is platform infrastructure, not a direct revenue product. Indirect monetization:
free for VisionQuantech's own apps; future "VisionID for clients" as a managed-IdP offering
to agency clients (per-MAU pricing undercutting Auth0's ~$0.07/MAU overage), plus SSO/SCIM as
a paid B2B add-on. Nothing is sold or billed in this phase.

## 7. Open questions for Shivay

1. Should VisionID accounts be shared across all VisionQuantech consumer apps (one identity), or per-app isolated?
2. Is phone-number identity (India-first, OTP login) a Phase 2 or Phase 3 requirement?
3. Any compliance target driving the roadmap (e.g. DPDP Act 2023 handling for Indian user data)?
