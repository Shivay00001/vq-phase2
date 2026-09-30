# Competitor Research — ERP / CRM / HRMS Suites
VisionERP/VisionCRM/VisionHRMS — Phase 2 foundation. Researched 2026-09-26.

## Systems studied

### 1. ERPNext (Frappe) — the open-source workhorse
- **License:** GPLv3, zero license cost; managed hosting from ~$5/mo (infra-based, not per-user). 30,000+ companies.
- **Architecture:** Frappe framework — metadata-driven **DocType** system. Every business object (Employee, Leave Application, Salary Slip) is a DocType with schema JSON, server controller, tests, and client scripts. Custom apps sit separate from core → upgrades don't break customizations.
- **Modules (all included by default):** Accounting, HR, CRM, Manufacturing, Inventory, Projects, Assets, POS, Quality.
- **HRMS (v14+ separate product):** `hr/` (leave, attendance, shifts, expense claims, recruitment, onboarding/separation, appraisal, employee lifecycle) and `payroll/` (salary structures, slips, payroll entry, tax slabs, withholding). Cross-cutting via `hooks.py`: `doc_events` (react to accounting/master-data events), `scheduler_events` (auto-attendance, leave allocation/expiry, reminders), `regional_overrides` (India/UAE statutory logic).
- **Patterns to copy:** DocType metadata → one way to define schema/validation/API/permissions for every object; scheduler jobs for leave expiry and auto-attendance; regional override layer for India GST/payroll instead of branching core logic; approval workflows on leave/expense docs; immutable audit trail via document versioning.
- **Weaknesses to beat:** functional-but-unpolished UI; hosting/maintenance burden falls on the customer; manufacturing depth we don't need in month 1.

### 2. Odoo — the modular ecosystem
- **Editions:** Community (100% free, open source) / Enterprise (paid). Modular app architecture; ~30,000 third-party apps; strong partner network.
- **Architecture:** custom Python ORM on PostgreSQL. Each app works standalone but shares one database — a record created in CRM is visible to Accounting without re-entry. Cohesive app shell: search, activities (chatter), configurable kanban/list views.
- **Patterns to copy:** one shared database across modules (no sync glue); deal → quotation → sales order → invoice → payment conversion chain (our "invoice from won deal" slice mirrors this); activities/chatter per record; configurable pipeline stages.
- **Weaknesses to beat:** costs rise steeply with users + apps + hosting; Community lacks key features (MRP, studio); major-version upgrades break custom modules.

### 3. Zoho (CRM / Books / People) — the SME-friendly cloud
- **Model:** cloud-only SaaS, per-user/month; Zoho One bundles ~45 apps at predictable cost. Very user-friendly, minimal training.
- **Patterns to copy:** blueprint/approval workflows in CRM; deluge-style automation (our equivalent: server-side hooks, not client scripts); clean self-service portals for employees/clients; India-first compliance (GST-ready invoicing).
- **Weaknesses to beat:** customization stays inside the Zoho walled garden; cloud-only = no data ownership; manufacturing weak; per-user pricing punishes growth.

### 4. Reference points (secondary)
- **Dolibarr** — progressive module activation keeps first-use simple; good lesson for our phased rollout.
- **ReconForge competitive map** (community analysis) — key lesson across all suites: *match workflow clarity and integration, not suite breadth, in the first release.* Define platform contracts (auth, RBAC, audit, API versioning) before chasing page count.

## What a real suite actually needs (the honest list)
1. **Accounting core with double-entry ledger** — everything (invoices, payroll, expenses) posts to a GL. Our slice has invoices *without* a GL: marked PROTOTYPE.
2. **Workflow engine** — multi-level approvals, state machines with guards (our leave approval slice implements guards; real engine needs versioned workflow definitions).
3. **Scheduler jobs** — leave allocation/expiry, auto-attendance, reminders, payroll runs. Missing in slice → documented gap.
4. **Statutory compliance** — India: GST e-invoicing, PF/ESI/PT/TDS on payroll. The regional-override pattern (ERPNext-style) is the right architecture; slice marks payroll as non-goal.
5. **RBAC at the data layer** — ERPNext uses per-DocType permissions; Odoo uses record rules. Slice enforces in middleware + every query scoped by company/owner (denies by default); Postgres RLS is the production target.
6. **Audit trail** — every admin/support action logged with before/after. Slice implements audit_log for mutating admin/support endpoints.
7. **Idempotency** — invoice-from-deal guarded by unique(deal_id) so retries can't double-bill. Slice implements; production adds idempotency keys on all money endpoints.
8. **Realtime** — Supabase Realtime/websockets for approvals, chatter. Slice: polling only → gap.
9. **Backups, RPO/RTO, restore drills** — non-negotiable before any production claim.
10. **Rate limiting, session rotation, breach-list password checks** — slice has PBKDF2 + token expiry; rotation/rate-limit/Redis are production gaps.

## Strategic takeaways for VisionU-STACK
- Copy ERPNext's **metadata-driven object model** for the real build (one definition → schema, validation, API, permissions, UI form), but ship our slice with hand-written endpoints to prove the workflows are real.
- Copy Odoo's **single shared database + conversion chains** (deal→invoice is our first chain).
- Copy Zoho's **SME-friendly portals** — the four panels (admin/employee/support/partner) are our differentiator for Indian SMBs.
- Pricing position: open-core SaaS. Free tier covering launch scale (Cloudflare Workers + Supabase free tiers), paid for Pro/API seats. Per-user SaaS pricing is what we're *not* doing — seat-based fees punish the exact SMBs we're selling to.
