# PRD — VisionERP / VisionCRM / VisionHRMS (Phase 2 Suite)
Version 0.1 — slice scope only. Status: **PROTOTYPE**. Date: 2026-09-26.

## 1. Problem
Indian SMBs (10–200 people) run sales in WhatsApp/spreadsheets, HR on paper registers, and billing in Tally or manual invoices. ERPNext/Odoo are powerful but need technical staff to host and maintain; Zoho is easy but per-user pricing + walled garden. None gives a zero-cost on-ramp that grows into a full suite.

## 2. Vision
A full multi-user ERP/CRM/HRMS suite inside VisionQuantech's U-STACK: one shared database, four panels (admin, employee, support, partner), open-core pricing, India-first compliance (GST, PF/ESI/TDS) — without requiring the customer to run servers.

## 3. Users (per panel)
| Panel | User | Core jobs |
|---|---|---|
| Admin | Platform/tenant owner | manage users & roles, see all data, approve escalations, audit log, analytics |
| Employee (user) | staff, sales reps | apply leave, check-in/out, manage own contacts/deals, log activities |
| Support | helpdesk staff | triage tickets, respond/resolve, look up users (limited fields, no salary data) |
| Partner | client/vendor contact | view own company's deals & invoices (read-mostly portal) |
| Manager (role inside admin/employee surfaces) | team lead | approve team leave, see team pipeline, create invoices |

## 4. User stories (slice scope)
1. As a sales rep, I can create a contact, move a deal through stages (new → contacted → qualified → proposal → negotiation → won/lost), log calls/emails against it, and see close-probability-weighted pipeline value.
2. As a manager, I can convert a **won** deal into an invoice (one invoice per deal; retries don't double-bill) and mark it sent/paid.
3. As an employee, I can apply for leave with a type and date range; the system rejects overlaps and insufficient balance; my manager approves/rejects; I can't approve my own request.
4. As an employee, I can check in in the morning and check out in the evening; the system blocks a second open check-in.
5. As an admin, I can create users, change roles, and see an audit log of who did what.
6. As a support agent, I can work a ticket queue and look up a user without seeing salary fields.
7. As a partner (client contact), I can see my company's invoices and deals — nothing else.

## 5. Success metrics (slice)
- 100% of endpoints enforce server-side RBAC (adversarial tests green).
- Double-approval of the same leave is impossible (state-machine guard tested).
- Negative invoice amounts and SQL-injection payloads are rejected/neutralized (tests green).
- Cross-tenant read returns 404 for any user outside the tenant (tests green).
- Demo runs locally with zero dependencies beyond Python 3.12 stdlib.

## 6. Non-goals (slice) — explicitly NOT claimed
- General-ledger accounting, inventory, payroll/statutory compliance (PF/ESI/TDS/GST e-invoice), purchase/manufacturing, email/SMS notifications, realtime, file uploads, password reset, SSO/OAuth, migrations tooling, backups. See README "production gaps".

## 7. Monetization (full suite, later)
Free tier (core CRM/HRMS/invoicing up to 25 seats) → Pro seats (workflow engine, payroll, audit export, API) → API/whitelabel for partners. No per-transaction fees.

## 8. Acceptance criteria
- `docs/PRD.md, TRD.md, ERD.md, API_CONTRACTS.md` exist and match the built slice.
- `slice/` runs with `python3 server.py` (or `run.sh`), seeds demo data, passes `python3 -m unittest` incl. adversarial tests.
- README states what is real vs prototype and lists production gaps honestly.
