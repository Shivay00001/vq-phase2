# VisionERP / VisionCRM / VisionHRMS — Phase 2 slice (v0.1)

**Status: WORKING PROTOTYPE — not production.** This is the Phase-2 foundation slice:
real API + real RBAC + real database workflows, deliberately small, honestly labeled.

## What this is

A runnable slice of the VisionQuantech U-STACK business suite: CRM (contacts, deal
pipeline, activities), HRMS (employees, leave with approval flow, attendance check-in),
invoicing (create-from-won-deal), support tickets, and four UI panels
(admin, employee self-service, support desk, partner/client portal) — all functional
against the real API with seeded demo data.

Per `../SOFTWARE_BUILD_FRAMEWORK.md`: research first (`docs/RESEARCH.md`), then
PRD → TRD → ERD → API contracts → build → adversarial tests. All docs are in `docs/`.

## Run it (zero dependencies — Python 3.12 stdlib only)

```bash
cd slice
./run.sh                 # → http://127.0.0.1:8471/
python3 -m unittest discover -s tests   # 52 tests, incl. adversarial
```

Demo logins (password for **all**: `demo1234` — seed only, never real):

| Username | Role | Panel |
|---|---|---|
| `admin` | admin | `/admin.html` — full console |
| `meera` | manager | `/admin.html` — no user mgmt / audit |
| `arjun`, `kavya` | employee | `/employee.html` — self-service |
| `sana` | support | `/support.html` — tickets + limited user lookup |
| `rohan` | partner | `/partner.html` — own company's deals/invoices |
| `vikram` | employee | other tenant (Rival Corp) — for cross-tenant tests |

## What's REAL in this slice

- **Server-side RBAC on every endpoint** — deny-by-default capability matrix
  (`rbac.py`); roles admin/manager/employee/support/partner. UI hiding is cosmetic only.
- **Multi-tenant isolation** — every row carries `company_id`; every query is scoped;
  cross-tenant reads return 404 (no tenant enumeration). Seeded second tenant proves it.
- **Leave workflow with real guards** — overlap detection, balance checks, approver ≠
  requester, only `pending` transitions (double-approval → 409), balance decremented
  transactionally on approve.
- **Invoice-from-won-deal** — allowed only from `won` stage; `UNIQUE(deal_id)` makes it
  idempotent (retries can't double-bill); totals/tax computed server-side; negative
  amounts, zero qty, tax > 100 rejected.
- **Attendance** — one open check-in per employee enforced; check-out requires an open record.
- **Audit log** — user changes, leave decisions, invoice actions, ticket triage all recorded.
- **Input validation everywhere** — types, ranges, enums, dates, email format;
  parameterized queries only; LIKE wildcards escaped; 1 MB body cap; no `eval`;
  UI renders via `textContent` (no `innerHTML` of user data — asserted in tests).
- **52 automated tests, all passing**, ~35 of them adversarial (see below).

## What's PROTOTYPE (do not mistake for production)

- **SQLite file DB**, single process. Schema is Postgres-ready (see `docs/TRD.md` §9
  for the migration path: identity columns, NUMERIC money, RLS policies, Alembic).
- **No general ledger** — invoices don't post double-entry journals. A real ERP needs
  `journal_entries`/`journal_lines` before any accounting claim.
- **No payroll / statutory compliance** — no salary runs, no PF/ESI/TDS, no GST e-invoice.
  Leave *balances are seeded*; there's no monthly accrual scheduler.
- **No scheduler jobs** — no leave expiry, auto-attendance, or reminder crons.
- **No rate limiting, no refresh rotation, no MFA, no breach-list password check.**
  Sessions are DB tokens with 12h expiry (4h for admin/support).
- **No password reset, no email/SMS, no file uploads, no realtime** (UI polls on tab switch).
- **No backups / RPO-RTO / restore drills.** No migrations tooling beyond `schema_version`.
- **Cookie auth path exists but UI uses Bearer** — CSRF tokens needed before cookie auth
  is used in production.
- Single-origin `127.0.0.1` demo; no TLS, no security headers beyond nosniff,
  no CORS policy needed yet (same origin).

## Production gaps checklist (before any real deployment)

- [ ] Postgres + Row-Level Security on every table (policies mirror `rbac.py` grants)
- [ ] Redis: sessions, rate limits (auth 10/min/IP, money endpoints), idempotency keys
- [ ] Double-entry GL; invoice/payment/expense postings; trial balance
- [ ] Payroll engine + India statutory (PF/ESI/PT/TDS) via regional-override layer
- [ ] GST e-invoicing; invoice numbering per financial year
- [ ] Scheduler (leave accrual/expiry, auto-attendance, reminders)
- [ ] Password reset, MFA, breach-list check, session rotation
- [ ] Rate limiting, WAF, security headers, CORS lockdown, audit export
- [ ] Backups tested with restore drills; documented RPO/RTO; rollback plan
- [ ] Realtime (approvals, ticket updates) instead of polling
- [ ] Load test notes; pagination already in place on all lists

## Test report

`slice/tests/test_api.py` — **52 tests, all passing** (run: `python3 -m unittest discover -s tests`).

Adversarial coverage (~35 tests):
- **RBAC bypass attempts (14):** employee/manager/partner/support hitting admin-only
  endpoints (`/users`, `/audit`, user create, contact delete, invoice create, deal
  create, leave approve, ticket triage) → all 403; employee patching another's deal → 403;
  admin self-demotion blocked.
- **Workflow abuse (8):** double-approve same leave → 409 (no double balance decrement);
  self-approval → 403; approve-after-reject → 409; overlapping leave → 409;
  insufficient balance → 409; double check-in → 409; duplicate invoice per deal → 409;
  negative/zero amounts, tax > 100 → 400.
- **Injection & isolation (11):** SQLi strings stored literally (tables intact);
  `' OR '1'='1` in search escaped → 0 rows; SQLi in login → 401; XSS payload round-trips
  raw and UI never uses `innerHTML` (asserted); cross-tenant deal/contact/invoice reads
  → 404; partner sees only own company's invoices; oversize body → 413; malformed
  JSON → 400; type-confusion (`contact_id: "1; DROP TABLE deals"`) → 400.
- **Privacy (2):** support user-lookup returns limited fields (no email/username);
  employee `/employees/me` redacts salary.

## File map

```
vision-erp/
  README.md                  ← you are here
  docs/
    RESEARCH.md              competitor research (ERPNext/Odoo/Zoho)
    PRD.md                   problem, users, stories, non-goals
    TRD.md                   architecture, RBAC, scaling, Postgres path
    ERD.md                   tables, indexes, honest limits
    API_CONTRACTS.md          endpoint-by-endpoint contract
  slice/
    server.py                HTTP API (stdlib only) — routing, validation, handlers
    db.py                    schema (Postgres-ready) + seed data
    auth.py                  PBKDF2 hashing, session tokens
    rbac.py                  deny-by-default capability matrix
    run.sh                   start the demo server
    static/                  index.html (login), admin/employee/support/partner panels,
                             app.js (XSS-safe client), style.css
    tests/test_api.py        52 tests incl. adversarial
```

## Cost at scale (production, per framework)

1k users: $0 (Cloudflare Workers + Supabase + Upstash free tiers).
100k: ~$50–150/mo. 1M: ~$500–2k/mo. Seat pricing flat by design.
