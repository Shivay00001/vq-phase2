"""VisionERP slice — HTTP API server (Python stdlib only).

Run:  python3 server.py [--port 8471]
Then open http://localhost:8471/  (demo logins on the login page).
"""
import datetime
import json
import os
import re
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import auth
import db
import rbac
from rbac import Forbidden

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
MAX_BODY = 1_000_000  # 1 MB

# ---------------------------------------------------------------- utilities

class ApiError(Exception):
    def __init__(self, code, message, status):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def bad(code, message, status=400):
    raise ApiError(code, message, status)


def row_to_dict(row):
    return dict(row) if row is not None else None


def rows_to_dicts(rows):
    return [dict(r) for r in rows]


def paginate(items_sql, count_sql, params, page, per_page):
    total = db.qone(count_sql, params)["c"]
    rows = db.qall(items_sql + " LIMIT ? OFFSET ?", params + (per_page, (page - 1) * per_page))
    return {"items": rows_to_dicts(rows), "page": page, "per_page": per_page, "total": total}


def get_page(qs):
    try:
        page = max(1, int(qs.get("page", ["1"])[0]))
        per_page = min(100, max(1, int(qs.get("per_page", ["20"])[0])))
    except ValueError:
        bad("validation", "page/per_page must be integers", 400)
    return page, per_page


# ---------------------------------------------------------------- validation

def v_str(v, name, required=True, max_len=500, allow_empty=False):
    if v is None:
        if required:
            bad("validation", f"'{name}' is required", 400)
        return None
    if not isinstance(v, str):
        bad("validation", f"'{name}' must be a string", 400)
    v = v.strip()
    if required and not allow_empty and not v:
        bad("validation", f"'{name}' must not be empty", 400)
    if len(v) > max_len:
        bad("validation", f"'{name}' exceeds {max_len} characters", 400)
    return v


def v_email(v, name="email", required=False):
    v = v_str(v, name, required=required, max_len=254, allow_empty=True)
    if v and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
        bad("validation", f"'{name}' is not a valid email", 400)
    return v or ""


def v_date(v, name, required=True):
    v = v_str(v, name, required=required, max_len=10)
    if v is None:
        return None
    try:
        datetime.date.fromisoformat(v)
    except ValueError:
        bad("validation", f"'{name}' must be YYYY-MM-DD", 400)
    return v


def v_enum(v, name, allowed, required=True):
    v = v_str(v, name, required=required, max_len=50)
    if v is not None and v not in allowed:
        bad("validation", f"'{name}' must be one of {sorted(allowed)}", 400)
    return v


def v_num(v, name, required=True, minimum=None, maximum=None, integer=False):
    if v is None:
        if required:
            bad("validation", f"'{name}' is required", 400)
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        bad("validation", f"'{name}' must be a number", 400)
    if integer and (isinstance(v, float) and not v.is_integer()):
        bad("validation", f"'{name}' must be an integer", 400)
    f = float(v)
    if minimum is not None and f < minimum:
        bad("validation", f"'{name}' must be >= {minimum}", 400)
    if maximum is not None and f > maximum:
        bad("validation", f"'{name}' must be <= {maximum}", 400)
    return int(f) if integer else f


def v_int(v, name, required=True, minimum=None, maximum=None):
    return v_num(v, name, required, minimum, maximum, integer=True)


def like_escape(s):
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---------------------------------------------------------------- request ctx

class Ctx:
    def __init__(self, user):
        self.user = user  # sqlite Row or None
        self.role = user["role"] if user else None
        self.company_id = user["company_id"] if user else None
        self.uid = user["id"] if user else None


def load_session(handler):
    """Return user Row or None. Bearer header first, then vq_session cookie."""
    token = None
    ah = handler.headers.get("Authorization", "")
    if ah.startswith("Bearer "):
        token = ah[7:].strip()
    else:
        cookie = handler.headers.get("Cookie", "")
        m = re.search(r"vq_session=([A-Za-z0-9]+)", cookie)
        if m:
            token = m.group(1)
    if not token or len(token) > 128:
        return None
    row = db.qone(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id"
        " WHERE s.token = ? AND s.expires_at > ? AND u.is_active = 1",
        (token, auth.now_iso()),
    )
    return row


def need_auth(ctx):
    if ctx.user is None:
        bad("auth_required", "authentication required", 401)


def need_cap(ctx, capability):
    need_auth(ctx)
    try:
        rbac.require(ctx.role, capability)
    except Forbidden as e:
        bad("forbidden", str(e), 403)


def audit(ctx, action, entity, entity_id=None, detail=None):
    db.q(
        "INSERT INTO audit_log (company_id, actor_user_id, action, entity, entity_id, detail, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (ctx.company_id, ctx.uid, action, entity, entity_id,
         json.dumps(detail or {}), auth.now_iso()),
    )


def my_employee_id(ctx):
    row = db.qone(
        "SELECT id FROM employees WHERE user_id = ? AND company_id = ?",
        (ctx.uid, ctx.company_id))
    return row["id"] if row else None


def scoped_or_404(table, rid, ctx, extra=""):
    """Fetch a row by id within the requester's company; 404 if absent/other-tenant."""
    row = db.qone(f"SELECT * FROM {table} WHERE id = ? AND company_id = ? {extra}",
                  (rid, ctx.company_id))
    if row is None:
        bad("not_found", f"{table} not found", 404)
    return row


# ---------------------------------------------------------------- handlers

def h_health(ctx, m, qs, body):
    return {"status": "ok", "version": "0.1-slice", "time": auth.now_iso()}


def h_login(ctx, m, qs, body):
    username = v_str(body.get("username"), "username", max_len=100)
    password = body.get("password")
    if not isinstance(password, str):
        bad("validation", "'password' is required", 400)
    user = db.qone("SELECT * FROM users WHERE username = ?", (username,))
    ok = bool(user) and user["is_active"] == 1 and auth.verify_password(password, user["password_hash"])
    if not ok:
        # uniform message: no user-enumeration oracle
        bad("auth_required", "invalid credentials", 401)
    token = auth.new_token()
    db.q("INSERT INTO sessions (token, user_id, expires_at, created_at) VALUES (?,?,?,?)",
         (token, user["id"], auth.session_expiry(user["role"]), auth.now_iso()))
    db.commit()
    return {"token": token, "expires_at": auth.session_expiry(user["role"]),
            "user": public_user(user)}


def public_user(u):
    return {"id": u["id"], "username": u["username"], "role": u["role"],
            "full_name": u["full_name"], "company_id": u["company_id"],
            "contact_id": u["contact_id"], "is_active": u["is_active"]}


def h_logout(ctx, m, qs, body):
    need_auth(ctx)
    token = getattr(ctx, "_token", None)
    if token:
        db.q("DELETE FROM sessions WHERE token = ?", (token,))
        db.commit()
    return {"ok": True}


def h_me(ctx, m, qs, body):
    need_auth(ctx)
    return public_user(ctx.user)


# ---- users (admin)

def h_users_list(ctx, m, qs, body):
    need_cap(ctx, "user.read_full")
    page, per_page = get_page(qs)
    return paginate(
        "SELECT id, username, role, full_name, contact_id, is_active, created_at FROM users"
        " WHERE company_id = ? ORDER BY id",
        "SELECT COUNT(*) c FROM users WHERE company_id = ?",
        (ctx.company_id,), page, per_page)


def h_users_create(ctx, m, qs, body):
    need_cap(ctx, "user.create")
    username = v_str(body.get("username"), "username", max_len=100).lower()
    if not re.fullmatch(r"[a-z0-9._-]{3,60}", username):
        bad("validation", "username: 3-60 chars, letters/digits/._-", 400)
    password = body.get("password")
    role = v_enum(body.get("role"), "role", set(db.ROLES))
    full_name = v_str(body.get("full_name"), "full_name", max_len=200)
    if not isinstance(password, str) or len(password) < 8:
        bad("validation", "password must be at least 8 characters", 400)
    if db.qone("SELECT id FROM users WHERE username = ?", (username,)):
        bad("conflict", "username already exists", 409)
    contact_id = body.get("contact_id")
    if contact_id is not None:
        contact_id = v_int(contact_id, "contact_id", minimum=1)
        scoped_or_404("contacts", contact_id, ctx)  # must be same-tenant
    try:
        ph = auth.hash_password(password)
    except ValueError as e:
        bad("validation", str(e), 400)
    cur = db.q(
        "INSERT INTO users (company_id, username, password_hash, role, full_name, contact_id, is_active, created_at)"
        " VALUES (?,?,?,?,?,?,1,?)",
        (ctx.company_id, username, ph, role, full_name, contact_id, auth.now_iso()))
    db.commit()
    uid = cur.lastrowid
    audit(ctx, "user.create", "user", uid, {"username": username, "role": role})
    db.commit()
    return public_user(db.qone("SELECT * FROM users WHERE id = ?", (uid,)))


def h_users_get(ctx, m, qs, body):
    rid = int(m.group(1))
    if rbac.can(ctx.role, "user.read_full"):
        need_auth(ctx)
        row = scoped_or_404("users", rid, ctx)
        return public_user(row)
    need_cap(ctx, "user.read_limited")  # support
    row = scoped_or_404("users", rid, ctx)
    emp = db.qone("SELECT department, designation FROM employees WHERE user_id = ? AND company_id = ?",
                  (rid, ctx.company_id))
    return {"id": row["id"], "full_name": row["full_name"],
            "department": emp["department"] if emp else "",
            "designation": emp["designation"] if emp else ""}


def h_users_patch(ctx, m, qs, body):
    need_cap(ctx, "user.update")
    rid = int(m.group(1))
    target = scoped_or_404("users", rid, ctx)
    if rid == ctx.uid:
        bad("forbidden", "you cannot change your own role or active status", 403)
    updates, params = [], []
    if "role" in body:
        updates.append("role = ?")
        params.append(v_enum(body.get("role"), "role", set(db.ROLES)))
    if "full_name" in body:
        updates.append("full_name = ?")
        params.append(v_str(body.get("full_name"), "full_name", max_len=200))
    if "is_active" in body:
        ia = body.get("is_active")
        if ia not in (0, 1, True, False):
            bad("validation", "'is_active' must be boolean", 400)
        updates.append("is_active = ?")
        params.append(1 if ia else 0)
    if not updates:
        bad("validation", "nothing to update", 400)
    db.q(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params + [rid])
    audit(ctx, "user.update", "user", rid, {"fields": [u.split(' ')[0] for u in updates]})
    db.commit()
    return public_user(db.qone("SELECT * FROM users WHERE id = ?", (rid,)))


def h_audit_list(ctx, m, qs, body):
    need_cap(ctx, "audit.read")
    page, per_page = get_page(qs)
    return paginate(
        "SELECT a.*, u.username AS actor FROM audit_log a JOIN users u ON u.id = a.actor_user_id"
        " WHERE a.company_id = ? ORDER BY a.id DESC",
        "SELECT COUNT(*) c FROM audit_log WHERE company_id = ?",
        (ctx.company_id,), page, per_page)


# ---- CRM: contacts

def contact_visible_clause(ctx):
    if rbac.can(ctx.role, "contact.read_all"):
        return "", ()
    need_cap(ctx, "contact.read_own")
    return "AND owner_user_id = ?", (ctx.uid,)


def h_contacts_list(ctx, m, qs, body):
    clause, params = contact_visible_clause(ctx)
    name = qs.get("q", [""])[0].strip()
    like = ""
    if name:
        like = "AND name LIKE ? ESCAPE '\\'"
        params = params + (f"%{like_escape(name[:100])}%",)
    page, per_page = get_page(qs)
    return paginate(
        f"SELECT * FROM contacts WHERE company_id = ? {clause} {like} ORDER BY id DESC",
        f"SELECT COUNT(*) c FROM contacts WHERE company_id = ? {clause} {like}",
        (ctx.company_id,) + params, page, per_page)


def h_contacts_create(ctx, m, qs, body):
    need_cap(ctx, "contact.create")
    name = v_str(body.get("name"), "name", max_len=200)
    email = v_email(body.get("email"))
    phone = v_str(body.get("phone"), "phone", required=False, max_len=40, allow_empty=True) or ""
    company_name = v_str(body.get("company_name"), "company_name", required=False, max_len=200, allow_empty=True) or ""
    cur = db.q(
        "INSERT INTO contacts (company_id, name, email, phone, company_name, owner_user_id, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (ctx.company_id, name, email, phone, company_name, ctx.uid, auth.now_iso()))
    db.commit()
    return row_to_dict(db.qone("SELECT * FROM contacts WHERE id = ?", (cur.lastrowid,)))


def h_contacts_get(ctx, m, qs, body):
    rid = int(m.group(1))
    row = scoped_or_404("contacts", rid, ctx)
    if not rbac.can(ctx.role, "contact.read_all") and row["owner_user_id"] != ctx.uid:
        bad("forbidden", "not your contact", 403)
    return row_to_dict(row)


def h_contacts_patch(ctx, m, qs, body):
    rid = int(m.group(1))
    row = scoped_or_404("contacts", rid, ctx)
    if not rbac.can(ctx.role, "contact.read_all") and row["owner_user_id"] != ctx.uid:
        bad("forbidden", "not your contact", 403)
    updates, params = [], []
    for field, fn in (("name", lambda v: v_str(v, "name", max_len=200)),
                      ("email", v_email),
                      ("phone", lambda v: v_str(v, "phone", required=False, max_len=40, allow_empty=True)),
                      ("company_name", lambda v: v_str(v, "company_name", required=False, max_len=200, allow_empty=True))):
        if field in body:
            updates.append(f"{field} = ?")
            params.append(fn(body[field]))
    if "owner_user_id" in body:
        need_cap(ctx, "contact.read_all")  # only manager/admin reassign
        nou = v_int(body["owner_user_id"], "owner_user_id", minimum=1)
        scoped_or_404("users", nou, ctx)
        updates.append("owner_user_id = ?")
        params.append(nou)
    if not updates:
        bad("validation", "nothing to update", 400)
    db.q(f"UPDATE contacts SET {', '.join(updates)} WHERE id = ?", params + [rid])
    db.commit()
    return row_to_dict(db.qone("SELECT * FROM contacts WHERE id = ?", (rid,)))


def h_contacts_delete(ctx, m, qs, body):
    need_cap(ctx, "contact.delete")
    rid = int(m.group(1))
    row = scoped_or_404("contacts", rid, ctx)
    if db.qone("SELECT id FROM deals WHERE contact_id = ? LIMIT 1", (rid,)):
        bad("conflict", "contact has deals; cannot delete", 409)
    db.q("DELETE FROM contacts WHERE id = ?", (rid,))
    audit(ctx, "contact.delete", "contact", rid, {"name": row["name"]})
    db.commit()
    return {"ok": True}


# ---- CRM: deals

def deal_visible_clause(ctx):
    if rbac.can(ctx.role, "deal.read_all"):
        return "", ()
    if ctx.role == "partner":
        # partner sees deals for their linked contact only (read-only)
        contact_id = ctx.user["contact_id"]
        return "AND d.contact_id = ?", (contact_id or -1,)
    need_cap(ctx, "deal.read_own")
    return "AND d.owner_user_id = ?", (ctx.uid,)


def h_deals_list(ctx, m, qs, body):
    if ctx.role == "partner":
        need_auth(ctx)
    clause, params = deal_visible_clause(ctx)
    stage = qs.get("stage", [""])[0]
    if stage:
        v_enum(stage, "stage", set(db.DEAL_STAGES))
        clause += " AND stage = ?"
        params = params + (stage,)
    page, per_page = get_page(qs)
    return paginate(
        f"SELECT d.*, c.name AS contact_name FROM deals d JOIN contacts c ON c.id = d.contact_id"
        f" WHERE d.company_id = ? {clause} ORDER BY d.id DESC",
        f"SELECT COUNT(*) c FROM deals d WHERE d.company_id = ? {clause}",
        (ctx.company_id,) + params, page, per_page)


def h_deals_create(ctx, m, qs, body):
    need_cap(ctx, "deal.create")
    contact_id = v_int(body.get("contact_id"), "contact_id", minimum=1)
    scoped_or_404("contacts", contact_id, ctx)
    title = v_str(body.get("title"), "title", max_len=200)
    value = v_num(body.get("value"), "value", minimum=0, maximum=1e12)
    stage = v_enum(body.get("stage", "new"), "stage", set(db.DEAL_STAGES), required=False) or "new"
    prob = body.get("probability")
    prob = db.STAGE_PROB[stage] if prob is None else v_num(prob, "probability", minimum=0, maximum=100)
    expected_close = v_date(body.get("expected_close"), "expected_close", required=False)
    owner = ctx.uid
    if "owner_user_id" in body:
        need_cap(ctx, "deal.read_all")
        owner = v_int(body["owner_user_id"], "owner_user_id", minimum=1)
        scoped_or_404("users", owner, ctx)
    now = auth.now_iso()
    cur = db.q(
        "INSERT INTO deals (company_id, contact_id, title, value, stage, probability,"
        " owner_user_id, expected_close, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (ctx.company_id, contact_id, title, value, stage, prob, owner, expected_close, now, now))
    db.commit()
    return row_to_dict(db.qone("SELECT * FROM deals WHERE id = ?", (cur.lastrowid,)))


def h_deals_get(ctx, m, qs, body):
    rid = int(m.group(1))
    row = scoped_or_404("deals", rid, ctx)
    enforce_deal_visible(ctx, row)
    d = row_to_dict(row)
    d["contact"] = row_to_dict(db.qone("SELECT id, name, email, company_name FROM contacts WHERE id = ?",
                                       (row["contact_id"],)))
    d["activities"] = rows_to_dicts(db.qall(
        "SELECT a.*, u.full_name AS author FROM deal_activities a JOIN users u ON u.id = a.user_id"
        " WHERE a.deal_id = ? AND a.company_id = ? ORDER BY a.id", (rid, ctx.company_id)))
    inv = db.qone("SELECT id, invoice_no, status, total FROM invoices WHERE deal_id = ?", (rid,))
    d["invoice"] = row_to_dict(inv)
    return d


def enforce_deal_visible(ctx, row):
    if rbac.can(ctx.role, "deal.read_all"):
        return
    if ctx.role == "partner":
        if row["contact_id"] != (ctx.user["contact_id"] or -1):
            bad("not_found", "deals not found", 404)
        return
    need_cap(ctx, "deal.read_own")
    if row["owner_user_id"] != ctx.uid:
        bad("forbidden", "not your deal", 403)


def h_deals_patch(ctx, m, qs, body):
    rid = int(m.group(1))
    row = scoped_or_404("deals", rid, ctx)
    if ctx.role == "partner":
        bad("forbidden", "partners cannot modify deals", 403)
    if not rbac.can(ctx.role, "deal.read_all") and row["owner_user_id"] != ctx.uid:
        bad("forbidden", "not your deal", 403)
    need_cap(ctx, "deal.update")
    updates, params = [], []
    if "title" in body:
        updates.append("title = ?"); params.append(v_str(body["title"], "title", max_len=200))
    if "value" in body:
        updates.append("value = ?"); params.append(v_num(body["value"], "value", minimum=0, maximum=1e12))
    if "stage" in body:
        stage = v_enum(body["stage"], "stage", set(db.DEAL_STAGES))
        updates.append("stage = ?"); params.append(stage)
        if "probability" not in body:
            updates.append("probability = ?"); params.append(db.STAGE_PROB[stage])
        if stage == "lost" and not body.get("lost_reason"):
            bad("validation", "'lost_reason' required when marking lost", 400)
    if "probability" in body:
        updates.append("probability = ?"); params.append(v_num(body["probability"], "probability", minimum=0, maximum=100))
    if "lost_reason" in body:
        updates.append("lost_reason = ?"); params.append(v_str(body["lost_reason"], "lost_reason", required=False, max_len=500, allow_empty=True) or "")
    if "expected_close" in body:
        updates.append("expected_close = ?"); params.append(v_date(body["expected_close"], "expected_close", required=False))
    if "owner_user_id" in body:
        need_cap(ctx, "deal.read_all")
        nou = v_int(body["owner_user_id"], "owner_user_id", minimum=1)
        scoped_or_404("users", nou, ctx)
        updates.append("owner_user_id = ?"); params.append(nou)
    if not updates:
        bad("validation", "nothing to update", 400)
    updates.append("updated_at = ?"); params.append(auth.now_iso())
    db.q(f"UPDATE deals SET {', '.join(updates)} WHERE id = ?", params + [rid])
    db.commit()
    return row_to_dict(db.qone("SELECT * FROM deals WHERE id = ?", (rid,)))


def h_deals_delete(ctx, m, qs, body):
    need_cap(ctx, "deal.delete")
    rid = int(m.group(1))
    row = scoped_or_404("deals", rid, ctx)
    db.q("DELETE FROM deal_activities WHERE deal_id = ?", (rid,))
    db.q("DELETE FROM deals WHERE id = ?", (rid,))
    audit(ctx, "deal.delete", "deal", rid, {"title": row["title"]})
    db.commit()
    return {"ok": True}


def h_pipeline(ctx, m, qs, body):
    need_cap(ctx, "deal.read_all")
    rows = db.qall(
        "SELECT stage, COUNT(*) n, COALESCE(SUM(value),0) tv, COALESCE(SUM(value*probability/100.0),0) wv"
        " FROM deals WHERE company_id = ? GROUP BY stage", (ctx.company_id,))
    by_stage = {r["stage"]: dict(r) for r in rows}
    return {"stages": [
        {"stage": s, "count": by_stage.get(s, {}).get("n", 0),
         "total_value": round(by_stage.get(s, {}).get("tv", 0), 2),
         "weighted_value": round(by_stage.get(s, {}).get("wv", 0), 2)}
        for s in db.DEAL_STAGES]}


def h_activity_create(ctx, m, qs, body):
    rid = int(m.group(1))
    row = scoped_or_404("deals", rid, ctx)
    enforce_deal_visible(ctx, row)
    if ctx.role == "partner":
        bad("forbidden", "partners cannot log activities", 403)
    need_cap(ctx, "activity.create")
    kind = v_enum(body.get("kind"), "kind", {"note", "call", "email", "meeting"})
    text = v_str(body.get("body"), "body", max_len=5000)
    cur = db.q(
        "INSERT INTO deal_activities (company_id, deal_id, user_id, kind, body, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (ctx.company_id, rid, ctx.uid, kind, text, auth.now_iso()))
    db.commit()
    return row_to_dict(db.qone("SELECT * FROM deal_activities WHERE id = ?", (cur.lastrowid,)))

# ---- HRMS

def employee_row_for(ctx, employee_id):
    row = scoped_or_404("employees", employee_id, ctx)
    return row


def h_employees_list(ctx, m, qs, body):
    need_cap(ctx, "employee.read_all")
    page, per_page = get_page(qs)
    dept = qs.get("department", [""])[0].strip()
    clause, params = "", ()
    if dept:
        clause = "AND department = ?"
        params = (dept[:100],)
    items = paginate(
        f"SELECT * FROM employees WHERE company_id = ? {clause} ORDER BY id",
        f"SELECT COUNT(*) c FROM employees WHERE company_id = ? {clause}",
        (ctx.company_id,) + params, page, per_page)
    # salary is PII: only admin+manager see it (both have employee.read_all)
    return items


def redact_salary(ctx, emp_dict):
    if ctx.role not in ("admin", "manager"):
        emp_dict.pop("salary_monthly", None)
    return emp_dict


def h_employees_create(ctx, m, qs, body):
    need_cap(ctx, "employee.create")
    full_name = v_str(body.get("full_name"), "full_name", max_len=200)
    email = v_email(body.get("email"), required=True)
    phone = v_str(body.get("phone"), "phone", required=False, max_len=40, allow_empty=True) or ""
    department = v_str(body.get("department"), "department", required=False, max_len=100, allow_empty=True) or ""
    designation = v_str(body.get("designation"), "designation", required=False, max_len=100, allow_empty=True) or ""
    salary = v_num(body.get("salary_monthly"), "salary_monthly", required=False, minimum=0, maximum=1e9)
    doj = v_date(body.get("date_of_joining"), "date_of_joining", required=False) or "2026-01-01"
    user_id = body.get("user_id")
    if user_id is not None:
        user_id = v_int(user_id, "user_id", minimum=1)
        u = scoped_or_404("users", user_id, ctx)
        if db.qone("SELECT id FROM employees WHERE user_id = ?", (user_id,)):
            bad("conflict", "user already linked to an employee", 409)
    cur = db.q(
        "INSERT INTO employees (company_id, user_id, full_name, email, phone, department, designation,"
        " salary_monthly, date_of_joining, is_active, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (ctx.company_id, user_id, full_name, email, phone, department, designation,
         salary, doj, 1, auth.now_iso()))
    eid = cur.lastrowid
    for lt, bal in (("casual", 12), ("sick", 10), ("earned", 15)):
        db.q("INSERT INTO leave_balances (company_id, employee_id, leave_type, year, balance_days)"
             " VALUES (?,?,?,?,?)", (ctx.company_id, eid, lt, 2026, bal))
    audit(ctx, "employee.create", "employee", eid, {"full_name": full_name})
    db.commit()
    return redact_salary(ctx, row_to_dict(db.qone("SELECT * FROM employees WHERE id = ?", (eid,))))


def h_employees_me(ctx, m, qs, body):
    need_cap(ctx, "employee.read_own")
    eid = my_employee_id(ctx)
    if eid is None:
        bad("not_found", "no employee record linked to this user", 404)
    return redact_salary(ctx, row_to_dict(db.qone("SELECT * FROM employees WHERE id = ?", (eid,))))


def h_leave_balances(ctx, m, qs, body):
    eid = None
    if rbac.can(ctx.role, "leave.read_all") and qs.get("employee_id", [""])[0]:
        eid = v_int(int(qs["employee_id"][0]), "employee_id", minimum=1)
        employee_row_for(ctx, eid)
    else:
        need_cap(ctx, "leave.read_own")
        eid = my_employee_id(ctx)
        if eid is None:
            bad("not_found", "no employee record linked to this user", 404)
    rows = db.qall(
        "SELECT leave_type, year, balance_days FROM leave_balances"
        " WHERE employee_id = ? AND company_id = ? ORDER BY leave_type",
        (eid, ctx.company_id))
    return {"employee_id": eid, "balances": rows_to_dicts(rows)}


def leave_days(start, end):
    d1 = datetime.date.fromisoformat(start)
    d2 = datetime.date.fromisoformat(end)
    if d2 < d1:
        bad("validation", "end_date must be >= start_date", 400)
    return (d2 - d1).days + 1


def h_leave_list(ctx, m, qs, body):
    if rbac.can(ctx.role, "leave.read_all"):
        clause, params = "", ()
    else:
        need_cap(ctx, "leave.read_own")
        eid = my_employee_id(ctx)
        clause, params = "AND l.employee_id = ?", (eid or -1,)
    status = qs.get("status", [""])[0]
    if status:
        v_enum(status, "status", {"pending", "approved", "rejected", "cancelled"})
        clause += " AND l.status = ?"
        params = params + (status,)
    page, per_page = get_page(qs)
    return paginate(
        "SELECT l.*, e.full_name AS employee_name FROM leave_requests l"
        " JOIN employees e ON e.id = l.employee_id"
        f" WHERE l.company_id = ? {clause} ORDER BY l.id DESC",
        f"SELECT COUNT(*) c FROM leave_requests l WHERE l.company_id = ? {clause}",
        (ctx.company_id,) + params, page, per_page)


def h_leave_create(ctx, m, qs, body):
    need_cap(ctx, "leave.apply")
    eid = my_employee_id(ctx)
    if eid is None:
        bad("not_found", "no employee record linked to this user", 404)
    leave_type = v_enum(body.get("leave_type"), "leave_type", set(db.LEAVE_TYPES))
    start = v_date(body.get("start_date"), "start_date")
    end = v_date(body.get("end_date"), "end_date")
    days = float(leave_days(start, end))
    reason = v_str(body.get("reason"), "reason", required=False, max_len=1000, allow_empty=True) or ""
    year = int(start[:4])
    # overlap: any pending/approved request intersecting [start, end]
    clash = db.qone(
        "SELECT id FROM leave_requests WHERE employee_id = ? AND company_id = ?"
        " AND status IN ('pending','approved') AND NOT (end_date < ? OR start_date > ?)",
        (eid, ctx.company_id, start, end))
    if clash:
        bad("conflict", "leave overlaps an existing pending/approved request", 409)
    bal = db.qone(
        "SELECT balance_days FROM leave_balances WHERE employee_id = ? AND leave_type = ? AND year = ?",
        (eid, leave_type, year))
    if bal is None or bal["balance_days"] < days:
        bad("conflict", f"insufficient {leave_type} balance for {days} day(s)", 409)
    cur = db.q(
        "INSERT INTO leave_requests (company_id, employee_id, leave_type, start_date, end_date,"
        " days, reason, status, created_at) VALUES (?,?,?,?,?,?,?,'pending',?)",
        (ctx.company_id, eid, leave_type, start, end, days, reason, auth.now_iso()))
    db.commit()
    return row_to_dict(db.qone("SELECT * FROM leave_requests WHERE id = ?", (cur.lastrowid,)))


def _decide_leave(ctx, m, body, approve):
    need_cap(ctx, "leave.approve")
    rid = int(m.group(1))
    lr = scoped_or_404("leave_requests", rid, ctx)
    if lr["status"] != "pending":
        # idempotency guard: double-approval impossible
        bad("conflict", f"leave request is already '{lr['status']}'", 409)
    emp = db.qone("SELECT user_id FROM employees WHERE id = ?", (lr["employee_id"],))
    if emp and emp["user_id"] == ctx.uid:
        bad("forbidden", "you cannot approve your own leave request", 403)
    now = auth.now_iso()
    if approve:
        year = int(lr["start_date"][:4])
        bal = db.qone(
            "SELECT balance_days FROM leave_balances WHERE employee_id = ? AND leave_type = ? AND year = ?",
            (lr["employee_id"], lr["leave_type"], year))
        if bal is None or bal["balance_days"] < lr["days"]:
            bad("conflict", "insufficient balance to approve", 409)
        db.q("UPDATE leave_balances SET balance_days = balance_days - ?"
             " WHERE employee_id = ? AND leave_type = ? AND year = ?",
             (lr["days"], lr["employee_id"], lr["leave_type"], year))
        db.q("UPDATE leave_requests SET status='approved', decided_by=?, decided_at=? WHERE id = ?",
             (ctx.uid, now, rid))
        audit(ctx, "leave.approve", "leave_request", rid,
              {"employee_id": lr["employee_id"], "days": lr["days"]})
    else:
        reason = v_str(body.get("reason"), "reason", required=False, max_len=1000, allow_empty=True) or ""
        db.q("UPDATE leave_requests SET status='rejected', decided_by=?, decided_at=? WHERE id = ?",
             (ctx.uid, now, rid))
        audit(ctx, "leave.reject", "leave_request", rid, {"reason": reason})
    db.commit()
    return row_to_dict(db.qone("SELECT * FROM leave_requests WHERE id = ?", (rid,)))


def h_leave_approve(ctx, m, qs, body):
    return _decide_leave(ctx, m, body, True)


def h_leave_reject(ctx, m, qs, body):
    return _decide_leave(ctx, m, body, False)


def h_leave_cancel(ctx, m, qs, body):
    rid = int(m.group(1))
    lr = scoped_or_404("leave_requests", rid, ctx)
    emp = db.qone("SELECT user_id FROM employees WHERE id = ?", (lr["employee_id"],))
    is_owner = emp and emp["user_id"] == ctx.uid
    if not is_owner:
        need_cap(ctx, "leave.approve")  # manager/admin may cancel too
    if lr["status"] != "pending":
        bad("conflict", f"only pending requests can be cancelled (is '{lr['status']}')", 409)
    db.q("UPDATE leave_requests SET status='cancelled' WHERE id = ?", (rid,))
    db.commit()
    return row_to_dict(db.qone("SELECT * FROM leave_requests WHERE id = ?", (rid,)))


def h_attendance_checkin(ctx, m, qs, body):
    need_cap(ctx, "attendance.self")
    eid = my_employee_id(ctx)
    if eid is None:
        bad("not_found", "no employee record linked to this user", 404)
    if db.qone("SELECT id FROM attendance WHERE employee_id = ? AND check_out IS NULL", (eid,)):
        bad("conflict", "already checked in — check out first", 409)
    note = v_str(body.get("note"), "note", required=False, max_len=500, allow_empty=True) or ""
    now = auth.now_iso()
    cur = db.q(
        "INSERT INTO attendance (company_id, employee_id, check_in, work_date, note, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (ctx.company_id, eid, now, now[:10], note, now))
    db.commit()
    return row_to_dict(db.qone("SELECT * FROM attendance WHERE id = ?", (cur.lastrowid,)))


def h_attendance_checkout(ctx, m, qs, body):
    need_cap(ctx, "attendance.self")
    eid = my_employee_id(ctx)
    if eid is None:
        bad("not_found", "no employee record linked to this user", 404)
    open_rec = db.qone(
        "SELECT * FROM attendance WHERE employee_id = ? AND check_out IS NULL", (eid,))
    if open_rec is None:
        bad("conflict", "no open check-in to close", 409)
    now = auth.now_iso()
    if now < open_rec["check_in"]:
        bad("validation", "check-out cannot precede check-in", 400)
    db.q("UPDATE attendance SET check_out = ? WHERE id = ?", (now, open_rec["id"]))
    db.commit()
    return row_to_dict(db.qone("SELECT * FROM attendance WHERE id = ?", (open_rec["id"],)))


def h_attendance_list(ctx, m, qs, body):
    if rbac.can(ctx.role, "attendance.read_all") and qs.get("employee_id", [""])[0]:
        eid = v_int(int(qs["employee_id"][0]), "employee_id", minimum=1)
        employee_row_for(ctx, eid)
    else:
        need_cap(ctx, "attendance.self")
        eid = my_employee_id(ctx)
        if eid is None:
            bad("not_found", "no employee record linked to this user", 404)
    page, per_page = get_page(qs)
    return paginate(
        "SELECT * FROM attendance WHERE employee_id = ? AND company_id = ? ORDER BY id DESC",
        "SELECT COUNT(*) c FROM attendance WHERE employee_id = ? AND company_id = ?",
        (eid, ctx.company_id), page, per_page)


# ---- invoicing

def h_invoices_list(ctx, m, qs, body):
    need_cap(ctx, "invoice.read_scoped")
    clause, params = "", ()
    if rbac.can(ctx.role, "invoice.read_all"):
        pass
    elif ctx.role == "partner":
        clause = "AND i.contact_id = ?"
        params = (ctx.user["contact_id"] or -1,)
    else:  # employee: invoices of own deals
        clause = ("AND i.deal_id IN (SELECT id FROM deals WHERE owner_user_id = ?"
                  " AND company_id = ?)")
        params = (ctx.uid, ctx.company_id)
    status = qs.get("status", [""])[0]
    if status:
        v_enum(status, "status", {"draft", "sent", "paid", "cancelled"})
        clause += " AND i.status = ?"
        params = params + (status,)
    page, per_page = get_page(qs)
    return paginate(
        "SELECT i.*, c.name AS contact_name FROM invoices i JOIN contacts c ON c.id = i.contact_id"
        f" WHERE i.company_id = ? {clause} ORDER BY i.id DESC",
        f"SELECT COUNT(*) c FROM invoices i WHERE i.company_id = ? {clause}",
        (ctx.company_id,) + params, page, per_page)


def h_invoice_from_deal(ctx, m, qs, body):
    need_cap(ctx, "invoice.create")
    deal_id = int(m.group(1))
    deal = scoped_or_404("deals", deal_id, ctx)
    if deal["stage"] != "won":
        bad("conflict", "invoice can only be created from a 'won' deal", 409)
    if db.qone("SELECT id FROM invoices WHERE deal_id = ?", (deal_id,)):
        # idempotency: one invoice per deal
        bad("conflict", "an invoice already exists for this deal", 409)
    items = body.get("items")
    if not isinstance(items, list) or not items or len(items) > 100:
        bad("validation", "'items' must be a non-empty list (max 100)", 400)
    lines = []
    subtotal = 0.0
    for it in items:
        if not isinstance(it, dict):
            bad("validation", "each item must be an object", 400)
        desc = v_str(it.get("description"), "items[].description", max_len=500)
        qty = v_num(it.get("qty"), "items[].qty", minimum=0.000001, maximum=1e9)
        price = v_num(it.get("unit_price"), "items[].unit_price", minimum=0, maximum=1e12)
        line = round(qty * price, 2)
        subtotal += line
        lines.append((desc, qty, price, line))
    subtotal = round(subtotal, 2)
    tax_percent = body.get("tax_percent", 18)
    tax_percent = v_num(tax_percent, "tax_percent", minimum=0, maximum=100)
    tax_amount = round(subtotal * tax_percent / 100.0, 2)
    total = round(subtotal + tax_amount, 2)
    if total <= 0:
        bad("validation", "invoice total must be positive", 400)
    due_date = v_date(body.get("due_date"), "due_date", required=False)
    seq = db.qone("SELECT COUNT(*) c FROM invoices WHERE company_id = ?",
                  (ctx.company_id,))["c"] + 1
    invoice_no = f"INV-{ctx.company_id}-{seq:04d}"
    now = auth.now_iso()
    cur = db.q(
        "INSERT INTO invoices (company_id, deal_id, contact_id, invoice_no, issue_date, due_date,"
        " subtotal, tax_percent, tax_amount, total, status, created_by, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,'draft',?,?)",
        (ctx.company_id, deal_id, deal["contact_id"], invoice_no, now[:10], due_date,
         subtotal, tax_percent, tax_amount, total, ctx.uid, now))
    inv_id = cur.lastrowid
    for desc, qty, price, line in lines:
        db.q("INSERT INTO invoice_items (invoice_id, description, qty, unit_price, line_total)"
             " VALUES (?,?,?,?,?)", (inv_id, desc, qty, price, line))
    audit(ctx, "invoice.create", "invoice", inv_id,
          {"invoice_no": invoice_no, "total": total, "deal_id": deal_id})
    db.commit()
    return h_invoice_get(ctx, m, qs, body, _id=inv_id)


def h_invoice_get(ctx, m, qs, body, _id=None):
    need_cap(ctx, "invoice.read_scoped")
    rid = _id or int(m.group(1))
    inv = scoped_or_404("invoices", rid, ctx)
    if not rbac.can(ctx.role, "invoice.read_all"):
        if ctx.role == "partner":
            if inv["contact_id"] != (ctx.user["contact_id"] or -1):
                bad("not_found", "invoices not found", 404)
        else:  # employee
            deal = db.qone("SELECT owner_user_id FROM deals WHERE id = ?", (inv["deal_id"],)) \
                if inv["deal_id"] else None
            if not deal or deal["owner_user_id"] != ctx.uid:
                bad("forbidden", "not your invoice", 403)
    d = row_to_dict(inv)
    d["items"] = rows_to_dicts(
        db.qall("SELECT * FROM invoice_items WHERE invoice_id = ? ORDER BY id", (rid,)))
    return d


_INVOICE_TRANSITIONS = {
    "draft": {"sent", "cancelled"},
    "sent": {"paid", "cancelled"},
    "paid": set(),
    "cancelled": set(),
}


def h_invoice_status(ctx, m, qs, body):
    need_cap(ctx, "invoice.status")
    rid = int(m.group(1))
    inv = scoped_or_404("invoices", rid, ctx)
    new = v_enum(body.get("status"), "status", {"draft", "sent", "paid", "cancelled"})
    if new not in _INVOICE_TRANSITIONS[inv["status"]]:
        bad("conflict", f"cannot move invoice from '{inv['status']}' to '{new}'", 409)
    db.q("UPDATE invoices SET status = ? WHERE id = ?", (new, rid))
    audit(ctx, "invoice.status", "invoice", rid,
          {"from": inv["status"], "to": new})
    db.commit()
    return h_invoice_get(ctx, m, qs, body, _id=rid)


# ---- tickets

def h_tickets_list(ctx, m, qs, body):
    need_auth(ctx)
    if rbac.can(ctx.role, "ticket.triage"):
        clause, params = "", ()
    else:
        need_cap(ctx, "ticket.read_own")
        clause, params = "AND requester_user_id = ?", (ctx.uid,)
    status = qs.get("status", [""])[0]
    if status:
        v_enum(status, "status", {"open", "in_progress", "resolved", "closed"})
        clause += " AND status = ?"
        params = params + (status,)
    page, per_page = get_page(qs)
    return paginate(
        "SELECT t.*, u.full_name AS requester_name FROM tickets t"
        " LEFT JOIN users u ON u.id = t.requester_user_id"
        f" WHERE t.company_id = ? {clause} ORDER BY t.id DESC",
        f"SELECT COUNT(*) c FROM tickets t WHERE t.company_id = ? {clause}",
        (ctx.company_id,) + params, page, per_page)


def h_tickets_create(ctx, m, qs, body):
    need_cap(ctx, "ticket.open")
    subject = v_str(body.get("subject"), "subject", max_len=200)
    text = v_str(body.get("body"), "body", max_len=5000)
    priority = v_enum(body.get("priority", "medium"), "priority",
                      {"low", "medium", "high"}, required=False) or "medium"
    now = auth.now_iso()
    cur = db.q(
        "INSERT INTO tickets (company_id, subject, body, status, priority, requester_user_id,"
        " created_at, updated_at) VALUES (?, ?, ?, 'open', ?, ?, ?, ?)",
        (ctx.company_id, subject, text, priority, ctx.uid, now, now))
    db.commit()
    return row_to_dict(db.qone("SELECT * FROM tickets WHERE id = ?", (cur.lastrowid,)))


def h_ticket_status(ctx, m, qs, body):
    need_cap(ctx, "ticket.triage")
    rid = int(m.group(1))
    t = scoped_or_404("tickets", rid, ctx)
    new = v_enum(body.get("status"), "status", {"open", "in_progress", "resolved", "closed"})
    db.q("UPDATE tickets SET status = ?, updated_at = ? WHERE id = ?",
         (new, auth.now_iso(), rid))
    audit(ctx, "ticket.status", "ticket", rid, {"from": t["status"], "to": new})
    db.commit()
    return row_to_dict(db.qone("SELECT * FROM tickets WHERE id = ?", (rid,)))


def h_ticket_assign(ctx, m, qs, body):
    need_cap(ctx, "ticket.triage")
    rid = int(m.group(1))
    scoped_or_404("tickets", rid, ctx)
    aid = v_int(body.get("assigned_to"), "assigned_to", minimum=1)
    target = scoped_or_404("users", aid, ctx)
    if target["role"] not in ("support", "admin"):
        bad("validation", "tickets can only be assigned to support/admin users", 400)
    db.q("UPDATE tickets SET assigned_to = ?, updated_at = ? WHERE id = ?",
         (aid, auth.now_iso(), rid))
    audit(ctx, "ticket.assign", "ticket", rid, {"assigned_to": aid})
    db.commit()
    return row_to_dict(db.qone("SELECT * FROM tickets WHERE id = ?", (rid,)))

# ---------------------------------------------------------------- routing

ROUTES = [
    ("GET", r"^/api/v1/health$", h_health),
    ("POST", r"^/api/v1/auth/login$", h_login),
    ("POST", r"^/api/v1/auth/logout$", h_logout),
    ("GET", r"^/api/v1/auth/me$", h_me),

    ("GET", r"^/api/v1/users$", h_users_list),
    ("POST", r"^/api/v1/users$", h_users_create),
    ("GET", r"^/api/v1/users/(\d+)$", h_users_get),
    ("PATCH", r"^/api/v1/users/(\d+)$", h_users_patch),
    ("GET", r"^/api/v1/audit$", h_audit_list),

    ("GET", r"^/api/v1/contacts$", h_contacts_list),
    ("POST", r"^/api/v1/contacts$", h_contacts_create),
    ("GET", r"^/api/v1/contacts/(\d+)$", h_contacts_get),
    ("PATCH", r"^/api/v1/contacts/(\d+)$", h_contacts_patch),
    ("DELETE", r"^/api/v1/contacts/(\d+)$", h_contacts_delete),

    ("GET", r"^/api/v1/deals/pipeline$", h_pipeline),
    ("GET", r"^/api/v1/deals$", h_deals_list),
    ("POST", r"^/api/v1/deals$", h_deals_create),
    ("GET", r"^/api/v1/deals/(\d+)$", h_deals_get),
    ("PATCH", r"^/api/v1/deals/(\d+)$", h_deals_patch),
    ("DELETE", r"^/api/v1/deals/(\d+)$", h_deals_delete),
    ("POST", r"^/api/v1/deals/(\d+)/activities$", h_activity_create),

    ("GET", r"^/api/v1/employees$", h_employees_list),
    ("POST", r"^/api/v1/employees$", h_employees_create),
    ("GET", r"^/api/v1/employees/me$", h_employees_me),
    ("GET", r"^/api/v1/leave/balances$", h_leave_balances),
    ("GET", r"^/api/v1/leave/requests$", h_leave_list),
    ("POST", r"^/api/v1/leave/requests$", h_leave_create),
    ("POST", r"^/api/v1/leave/requests/(\d+)/approve$", h_leave_approve),
    ("POST", r"^/api/v1/leave/requests/(\d+)/reject$", h_leave_reject),
    ("POST", r"^/api/v1/leave/requests/(\d+)/cancel$", h_leave_cancel),
    ("POST", r"^/api/v1/attendance/check-in$", h_attendance_checkin),
    ("POST", r"^/api/v1/attendance/check-out$", h_attendance_checkout),
    ("GET", r"^/api/v1/attendance$", h_attendance_list),

    ("GET", r"^/api/v1/invoices$", h_invoices_list),
    ("POST", r"^/api/v1/invoices/from-deal/(\d+)$", h_invoice_from_deal),
    ("GET", r"^/api/v1/invoices/(\d+)$", h_invoice_get),
    ("POST", r"^/api/v1/invoices/(\d+)/status$", h_invoice_status),

    ("GET", r"^/api/v1/tickets$", h_tickets_list),
    ("POST", r"^/api/v1/tickets$", h_tickets_create),
    ("POST", r"^/api/v1/tickets/(\d+)/status$", h_ticket_status),
    ("POST", r"^/api/v1/tickets/(\d+)/assign$", h_ticket_assign),
]
ROUTES = [(m, re.compile(p), h) for m, p, h in ROUTES]

MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".json": "application/json",
        ".png": "image/png", ".svg": "image/svg+xml"}


class Handler(BaseHTTPRequestHandler):
    server_version = "VisionERP-slice/0.1"

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[{auth.now_iso()}] {self.address_string()} {fmt % args}\n")

    # -- helpers -----------------------------------------------------
    def _send_json(self, status, obj, headers=None):
        body = json.dumps(obj, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = self.headers.get("Content-Length")
        if not length:
            return {}
        try:
            n = int(length)
        except ValueError:
            bad("validation", "bad Content-Length", 400)
        if n > MAX_BODY:
            bad("validation", "request body too large", 413)
        raw = self.rfile.read(n)
        if not raw.strip():
            return {}
        try:
            obj = json.loads(raw.decode("utf-8"))
        except Exception:
            bad("validation", "body must be valid JSON", 400)
        if not isinstance(obj, dict):
            bad("validation", "body must be a JSON object", 400)
        return obj

    def _serve_static(self, path):
        if path == "/":
            path = "/index.html"
        # traversal guard: resolve and stay inside STATIC
        target = os.path.normpath(os.path.join(STATIC, path.lstrip("/")))
        if not target.startswith(os.path.abspath(STATIC) + os.sep):
            self._send_json(403, {"error": "forbidden", "message": "forbidden"})
            return
        if not os.path.isfile(target):
            self._send_json(404, {"error": "not_found", "message": "not found"})
            return
        ext = os.path.splitext(target)[1].lower()
        with open(target, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    # -- dispatch ----------------------------------------------------
    def _dispatch(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)
        if path.startswith("/api/v1/"):
            user = load_session(self)
            ctx = Ctx(user)
            # stash raw token for logout
            ah = self.headers.get("Authorization", "")
            ctx._token = ah[7:].strip() if ah.startswith("Bearer ") else None
            for method, pattern, handler in ROUTES:
                if self.command != method:
                    continue
                m = pattern.match(path)
                if m:
                    try:
                        body = self._read_body() if self.command in ("POST", "PATCH", "PUT") else {}
                        result = handler(ctx, m, qs, body)
                        if result is None:
                            self.send_response(204)
                            self.end_headers()
                        else:
                            if self.command == "POST" and not path.rstrip("/").endswith(
                                    ("login", "logout", "check-out",
                                     "approve", "reject", "cancel", "status", "assign")):
                                status = 201  # resource created
                            else:
                                status = 200
                            self._send_json(status, result)
                    except ApiError as e:
                        self._send_json(e.status, {"error": e.code, "message": e.message})
                    except Forbidden as e:
                        self._send_json(403, {"error": "forbidden", "message": str(e)})
                    except Exception:
                        # never leak stack traces
                        self._send_json(500, {"error": "internal",
                                              "message": "internal server error"})
                    finally:
                        db.close_thread_db()
                    return
            self._send_json(404, {"error": "not_found", "message": "unknown endpoint"})
            db.close_thread_db()
            return
        self._serve_static(path)

    def do_GET(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch()

    def do_PATCH(self):
        self._dispatch()

    def do_DELETE(self):
        self._dispatch()

    def do_PUT(self):
        self._send_json(405, {"error": "method_not_allowed", "message": "method not allowed"})


def main():
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8471
    fresh = db.init_db()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"VisionERP slice on http://127.0.0.1:{port}/  (db: {db.DB_PATH}, fresh={fresh})")
    print("Demo logins (password for all: demo1234):")
    print("  admin / meera(manager) / arjun(employee) / kavya(employee) / sana(support) / rohan(partner) / vikram (other tenant)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
