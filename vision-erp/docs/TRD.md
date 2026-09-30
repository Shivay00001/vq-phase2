# TRD — VisionERP / VisionCRM / VisionHRMS slice (v0.1)
Status: **PROTOTYPE** — architecture doc for the real suite; the slice implements a deliberately small subset. Date: 2026-09-26.

## 1. Architecture (slice)

```
┌──────────────┐      ┌───────────────────────────────┐      ┌──────────┐
│  Static UI   │ HTTP │  Python stdlib backend        │ SQL  │ SQLite   │
│ 4 panels +   ├─────►│  server.py (routing+handlers) │─────►│ slice/   │
│ login (vanilla│ JSON │  auth.py (pbkdf2, sessions)   │      │ data.    │
│ JS, fetch)   │      │  rbac.py (deny-by-default     │      │ sqlite   │
└──────────────┘      │  grants, tenant scoping)      │      └──────────┘
                      │  db.py (schema, seed, queries)│
                      └───────────────────────────────┘
```

**Why stdlib-only:** zero dependencies = zero supply-chain risk, runs anywhere with Python 3.12, no venv needed. The *production* stack per framework is Cloudflare Workers + Supabase Postgres + RLS; the slice is a workflow/RBAC proof, not the deployment target.

## 2. Auth & sessions
- PBKDF2-HMAC-SHA256, 200k iterations, per-user 16-byte salt, format `pbkdf2:sha256:200000$salt$hex`.
- Session token: 32 random bytes (secrets.token_hex), stored in `sessions` with 12h expiry (admin/support: 4h — shorter privileged sessions per framework §3).
- Transport: `Authorization: Bearer <token>`; server also accepts `vq_session` cookie (UI uses Bearer; cookie path reserved for SSR). Logout deletes the token.
- Password rules: min 8 chars. Breach-list check, rotation, MFA: **production gaps**.

## 3. RBAC (deny-by-default; enforced in middleware + every query)
Roles: `admin`, `manager`, `employee`, `support`, `partner`.

| Capability | admin | manager | employee | support | partner |
|---|---|---|---|---|---|
| user create / role change / deactivate | ✓ | ✗ | ✗ | ✗ | ✗ |
| view audit log | ✓ | ✗ | ✗ | ✗ (own actions only) | ✗ |
| employees: full incl. salary | ✓ | ✓ (no salary edit? sees salary) | own only, no salary | ✗ (limited lookup: name/dept/designation) | ✗ |
| leave: approve/reject team | ✓ | ✓ (not own) | apply own / cancel own pending | ✗ | ✗ |
| attendance: view all | ✓ | ✓ | own | ✗ | ✗ |
| CRM contacts/deals: all | ✓ | ✓ team-wide | own-owned only | ✗ | ✗ |
| activities: log | ✓ | ✓ | on own deals | ✗ | ✗ |
| invoices: create from won deal, change status | ✓ | ✓ | ✗ (view own deals' invoices) | ✗ | ✗ |
| invoices: view (partner portal) | ✓ | ✓ | ✗ | ✗ | own company's only |
| tickets: manage all | ✓ | ✗ | open own / view own | ✓ triage all | ✗ |

Tenant scoping: every query ANDs `company_id = <requester's company>`; cross-tenant lookups return **404** (not 403) to avoid tenant enumeration. Partner users are linked to a `contact` (via `users.contact_id`) and see only deals/invoices for that contact's company.

## 4. Business logic (server-side, tested)
- **Deal→invoice:** allowed only when `stage='won'` and no invoice exists for the deal (`UNIQUE(deal_id)`); items copied from request; totals computed server-side; tax 0–100; negative qty/price rejected (400).
- **Leave:** days computed inclusive server-side; overlap check vs approved+pending; balance check; approver must differ from requester; only `pending` transitions (second approve → 409); on approve, balance decremented (in one transaction).
- **Attendance:** one open check-in per employee; check-out must be ≥ check-in; `work_date` derived from check-in server-side.
- **Tickets:** employees open on self; support/admin triage; status transitions validated.
- **Audit:** mutating admin/support/sensitive actions append to `audit_log` with actor, entity, JSON detail (never passwords).

## 5. Validation & security posture (slice)
- Every input validated server-side (types, ranges, enums, date formats); parameterized queries everywhere; `LIKE` search escapes `%`/`_`/`\`.
- No `eval`, no `exec` on user data, no pickle. UI renders via `textContent`/`createElement` only — no `innerHTML` with user data.
- Error responses: generic 4xx messages, no stack traces, no "user exists" oracle on login (uniform "invalid credentials").
- Rate limiting, CSRF (cookie path), CORS lockdown, security headers, WAF: **production gaps** (documented; single-origin local demo).

## 6. Scaling plan (production, not slice)
- 1k users: Supabase Postgres + RLS, Cloudflare Workers, Upstash Redis (sessions/rate limits). Free tiers cover it.
- 100k: read replicas, Redis cache on hot lists, queue PDF/email generation, pagination everywhere (already in slice lists).
- 1M: sharding by `company_id`, regional data residency, async workers for payroll runs.
- Cost guardrails: per-tenant spend caps, 70% quota alerts (framework §5).

## 7. API design rules
- Base `/api/v1`; JSON only; pagination `?page=&per_page=` (default 20, max 100); errors `{ "error": "<code>", "message": "<human>" }`.
- Money endpoints idempotent via natural keys (`UNIQUE(deal_id)` on invoices).
- Write operations return the created/updated resource; DELETE returns 204.

## 8. Cost estimate (production, monthly)
- 1k users: $0 (Cloudflare Workers 100k req/day free, Supabase free tier, Upstash free).
- 100k users: ~$50–150 (Workers paid, Supabase Pro, Redis).
- 1M users: ~$500–2k + infra engineering. Seat pricing kept flat by design (anti-Zoho positioning).

## 9. Postgres migration path (slice schema is Postgres-ready)
- Types: `INTEGER PRIMARY KEY` → `BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY`; `TEXT`→`TEXT`; `REAL`→`NUMERIC(14,2)` for money; dates `TEXT` (ISO) → `DATE`/`TIMESTAMPTZ`.
- Add `CREATE POLICY` RLS per table keyed on `auth.jwt() ->> 'company_id'` + role claim; our middleware grants map 1:1 to policies.
- Replace the partial-index comment in ERD with real `CREATE UNIQUE INDEX ... WHERE check_out IS NULL`.
- Use Alembic (or Supabase migrations) — slice uses `db.py` DDL + version table stub (`schema_version` row, currently `1`).
- Session store → Redis/Upstash; `sessions` table dropped.
