# ERD — VisionERP slice (v0.1)
All tables carry `company_id` (multi-tenant stub). SQLite types below; Postgres mapping in TRD §9.

```
companies ─┬─ users ─┬─ sessions
            │        ├─ employees ─┬─ leave_balances
            │        │             ├─ leave_requests ──(approver_user_id)── users
            │        │             └─ attendance
            │        ├─ contacts ─┬─ deals ─┬─ deal_activities
            │        │            │         └─ invoices ── invoice_items
            │        │            └─ (partner users link via contacts.email = users.username)
            │        └─ audit_log
            └─ tickets ──(requester/reporter)
```

## Tables

**companies** — id INTEGER PK, name TEXT NOT NULL, created_at TEXT.
**users** — id PK, company_id FK→companies, username TEXT UNIQUE NOT NULL, password_hash TEXT (pbkdf2:sha256$salt$hex), role TEXT CHECK(role IN ('admin','manager','employee','support','partner')), full_name TEXT, contact_id INTEGER NULL FK→contacts (partner linkage), is_active INTEGER 0/1, created_at TEXT.
**sessions** — token TEXT PK (32-byte hex), user_id FK, expires_at TEXT, created_at TEXT.
**employees** — id PK, company_id, user_id FK UNIQUE NULL (unlinked staff rows allowed), full_name, email, phone, department, designation, salary_monthly REAL NULL (PII: admin+manager only), date_of_joining TEXT, is_active INTEGER, created_at.
**leave_balances** — id PK, company_id, employee_id FK, leave_type TEXT CHECK IN ('casual','sick','earned'), year INTEGER, balance_days REAL, UNIQUE(employee_id, leave_type, year).
**leave_requests** — id PK, company_id, employee_id FK, leave_type, start_date TEXT (YYYY-MM-DD), end_date TEXT, days REAL (computed server-side, inclusive), reason TEXT, status TEXT CHECK IN ('pending','approved','rejected','cancelled') DEFAULT 'pending', decided_by INTEGER NULL FK→users, decided_at TEXT NULL, created_at. Guard: only 'pending' rows can transition; approver ≠ requester.
**attendance** — id PK, company_id, employee_id FK, check_in TEXT NOT NULL, check_out TEXT NULL, work_date TEXT (YYYY-MM-DD derived server-side), note TEXT, UNIQUE(employee_id, work_date, check_out) is wrong — enforced in code: at most one row with check_out IS NULL per employee. Partial index (production): `CREATE UNIQUE INDEX ... WHERE check_out IS NULL`.
**contacts** — id PK, company_id, name TEXT, email TEXT, phone TEXT, company_name TEXT, owner_user_id FK→users NULL, created_at.
**deals** — id PK, company_id, contact_id FK, title TEXT, value REAL CHECK(value >= 0), stage TEXT CHECK IN ('new','contacted','qualified','proposal','negotiation','won','lost'), probability REAL (auto-set from stage map; manual override allowed 0–100), owner_user_id FK, expected_close TEXT NULL, lost_reason TEXT NULL, created_at, updated_at. Stage map: new 10, contacted 25, qualified 50, proposal 65, negotiation 80, won 100, lost 0.
**deal_activities** — id PK, company_id, deal_id FK, user_id FK (author), kind TEXT CHECK IN ('note','call','email','meeting'), body TEXT, created_at.
**invoices** — id PK, company_id, deal_id FK UNIQUE NULL (one invoice per deal — idempotency), contact_id FK, invoice_no TEXT UNIQUE per company (format `INV-<company_id>-<seq>`), issue_date TEXT, due_date TEXT, subtotal REAL, tax_percent REAL CHECK 0–100, tax_amount REAL, total REAL, status TEXT CHECK IN ('draft','sent','paid','cancelled') DEFAULT 'draft', created_by FK→users, created_at. Amounts computed server-side; negative rejected.
**invoice_items** — id PK, invoice_id FK, description TEXT, qty REAL CHECK(qty > 0), unit_price REAL CHECK(unit_price >= 0), line_total REAL.
**tickets** — id PK, company_id, subject TEXT, body TEXT, status TEXT CHECK IN ('open','in_progress','resolved','closed') DEFAULT 'open', priority TEXT CHECK IN ('low','medium','high'), requester_user_id FK NULL, assigned_to FK→users NULL, created_at, updated_at. Support + admin manage; employees can open tickets on themselves.
**audit_log** — id PK, company_id, actor_user_id FK, action TEXT (e.g. `user.role_change`, `leave.approve`), entity TEXT, entity_id INTEGER NULL, detail TEXT (JSON, no passwords), created_at. Written for: user create/role change/deactivate, leave approve/reject, invoice create/status change, deal delete (admin only).

## Indexes
- All FK columns; `users(username)` unique; `deals(company_id, stage)`; `leave_requests(employee_id, status)`; `attendance(employee_id, work_date)`; `invoices(company_id, status)`; `tickets(company_id, status)`.

## Notes / honest limits
- No GL: invoices don't post journal entries (production needs `journal_entries` + `journal_lines` double-entry tables).
- No leave *allocation* engine (balances seeded; scheduler would accrue monthly — production job).
- Soft-delete not implemented; deletes are hard with audit entries (production: soft-delete + retention policy).
