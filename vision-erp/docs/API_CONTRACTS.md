# API CONTRACTS — VisionERP slice v0.1
Base: `/api/v1` · JSON request/response · Auth: `Authorization: Bearer <token>` (login/logout exempt).
Errors: `{"error": "<code>", "message": "<human-readable>"}` with codes: `auth_required`, `forbidden`, `not_found`, `validation`, `conflict`, `rate_limited`(future).
Lists: `{"items": [...], "page": 1, "per_page": 20, "total": N}`.

## Auth
| Method | Path | Roles | Body | Returns |
|---|---|---|---|---|
| POST | `/auth/login` | — | `{"username","password"}` | `{"token","expires_at","user":{"id","username","role","full_name","company_id"}}` |
| POST | `/auth/logout` | any | — | 204 |
| GET | `/auth/me` | any | — | current user (no hash) |

## Admin — users & audit
| Method | Path | Roles | Notes |
|---|---|---|---|
| GET | `/users` | admin | paginated; password hashes never returned |
| POST | `/users` | admin | `{"username","password"(≥8),"role","full_name","company_id"}` → 201; duplicate username → 409; role must be valid |
| PATCH | `/users/{id}` | admin | `{"role","full_name","is_active"}`; cannot deactivate/demote self; audited |
| GET | `/users/{id}` | admin, support(limited fields) | support sees only `id,full_name,department,designation` — no email/phone/salary linkage |
| GET | `/audit` | admin | paginated audit log |

## CRM
| Method | Path | Roles | Notes |
|---|---|---|---|
| GET/POST | `/contacts` | admin,manager,employee | POST: `{"name*","email","phone","company_name"}`; employees see own-owned + unowned? **no** — own-owned only; manager/admin all |
| GET/PATCH/DELETE | `/contacts/{id}` | per above | DELETE admin only; PATCH owner or above |
| GET/POST | `/deals` | admin,manager,employee | POST: `{"contact_id*","title*","value*≥0","stage?","owner_user_id?","expected_close?"}`; probability auto from stage map unless overridden 0–100 |
| GET/PATCH | `/deals/{id}` | per above | PATCH: stage/value/probability/owner; moving to `won`/`lost` sets updated_at; `lost_reason` required when stage=lost |
| GET/POST | `/deals/{id}/activities` | admin,manager,employee | POST `{"kind*":"note\|call\|email\|meeting","body*"}` |
| GET | `/deals/pipeline` | admin,manager | `{"stages":[{stage,count,total_value,weighted_value}]}` weighted = Σ value×prob/100 |

## HRMS
| Method | Path | Roles | Notes |
|---|---|---|---|
| GET/POST | `/employees` | admin,manager (+employee: own record via `/employees/me`) | POST admin only; salary visible to admin+manager only |
| GET | `/employees/me` | employee | own record, salary redacted |
| GET | `/leave/balances` | employee(+): own; manager/admin: `?employee_id=` | |
| GET/POST | `/leave/requests` | any employee-role | POST: `{"leave_type*","start_date*","end_date*","reason"}`; days computed; overlap+balance enforced; employee sees own, manager/admin all |
| POST | `/leave/requests/{id}/approve` | manager,admin | approver≠requester; only `pending` → 200 else 409; decrements balance in transaction; audited |
| POST | `/leave/requests/{id}/reject` | manager,admin | `{"reason?"}`; audited |
| POST | `/leave/requests/{id}/cancel` | requester,admin | only `pending` |
| POST | `/attendance/check-in` | employee,manager,admin | `{"note?"}`; one open record enforced → 409 if open exists |
| POST | `/attendance/check-out` | employee,manager,admin | closes open record; 409 if none open |
| GET | `/attendance` | any | own; manager/admin `?employee_id=` |

## Invoicing
| Method | Path | Roles | Notes |
|---|---|---|---|
| GET | `/invoices` | admin,manager,employee(partner: own company) | employee sees invoices of own deals; partner sees own company's |
| POST | `/invoices/from-deal/{deal_id}` | admin,manager | deal must be `won`; `UNIQUE(deal_id)` → second call 409 (idempotent); body `{"items":[{"description","qty>0","unit_price≥0"}],"tax_percent? 0–100,"due_date?"}`; totals server-computed; audited |
| GET | `/invoices/{id}` | per GET /invoices scoping | includes items |
| POST | `/invoices/{id}/status` | admin,manager | `{"status":"sent\|paid\|cancelled"}`; transitions: draft→sent→paid, any→cancelled(except paid); audited |

## Support tickets
| Method | Path | Roles | Notes |
|---|---|---|---|
| GET/POST | `/tickets` | any | employees: own; support/admin: all; POST `{"subject*","body*","priority?"}` |
| POST | `/tickets/{id}/status` | support,admin | `{"status":"open\|in_progress\|resolved\|closed"}` |
| POST | `/tickets/{id}/assign` | support,admin | `{"assigned_to"}` (must be support/admin user) |

## Rate limits (production)
Auth endpoints 10/min/IP; money endpoints 60/min/user; reads 600/min/user. Slice: **not implemented** — documented gap.

## Versioning
URL versioning (`/api/v1`); breaking changes bump to `/api/v2` with 6-month overlap.
