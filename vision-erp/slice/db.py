"""VisionERP slice — database: schema (Postgres-ready), connections, seed data."""
import hashlib
import json
import os
import sqlite3
import threading

import auth

DB_PATH = os.environ.get("VISIONERP_DB", os.path.join(os.path.dirname(__file__), "data.sqlite"))

SCHEMA_VERSION = 1

# Close-probability defaults per pipeline stage (server-side; overridable 0-100).
STAGE_PROB = {
    "new": 10, "contacted": 25, "qualified": 50, "proposal": 65,
    "negotiation": 80, "won": 100, "lost": 0,
}
DEAL_STAGES = list(STAGE_PROB.keys())
LEAVE_TYPES = ("casual", "sick", "earned")
ROLES = ("admin", "manager", "employee", "support", "partner")

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);

CREATE TABLE IF NOT EXISTS companies (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  company_id INTEGER NOT NULL REFERENCES companies(id),
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('admin','manager','employee','support','partner')),
  full_name TEXT NOT NULL,
  contact_id INTEGER NULL REFERENCES contacts(id),
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS employees (
  id INTEGER PRIMARY KEY,
  company_id INTEGER NOT NULL REFERENCES companies(id),
  user_id INTEGER NULL UNIQUE REFERENCES users(id),
  full_name TEXT NOT NULL,
  email TEXT NOT NULL,
  phone TEXT NOT NULL DEFAULT '',
  department TEXT NOT NULL DEFAULT '',
  designation TEXT NOT NULL DEFAULT '',
  salary_monthly REAL NULL,
  date_of_joining TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS leave_balances (
  id INTEGER PRIMARY KEY,
  company_id INTEGER NOT NULL REFERENCES companies(id),
  employee_id INTEGER NOT NULL REFERENCES employees(id),
  leave_type TEXT NOT NULL CHECK (leave_type IN ('casual','sick','earned')),
  year INTEGER NOT NULL,
  balance_days REAL NOT NULL DEFAULT 0,
  UNIQUE (employee_id, leave_type, year)
);

CREATE TABLE IF NOT EXISTS leave_requests (
  id INTEGER PRIMARY KEY,
  company_id INTEGER NOT NULL REFERENCES companies(id),
  employee_id INTEGER NOT NULL REFERENCES employees(id),
  leave_type TEXT NOT NULL CHECK (leave_type IN ('casual','sick','earned')),
  start_date TEXT NOT NULL,
  end_date TEXT NOT NULL,
  days REAL NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','approved','rejected','cancelled')),
  decided_by INTEGER NULL REFERENCES users(id),
  decided_at TEXT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attendance (
  id INTEGER PRIMARY KEY,
  company_id INTEGER NOT NULL REFERENCES companies(id),
  employee_id INTEGER NOT NULL REFERENCES employees(id),
  check_in TEXT NOT NULL,
  check_out TEXT NULL,
  work_date TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contacts (
  id INTEGER PRIMARY KEY,
  company_id INTEGER NOT NULL REFERENCES companies(id),
  name TEXT NOT NULL,
  email TEXT NOT NULL DEFAULT '',
  phone TEXT NOT NULL DEFAULT '',
  company_name TEXT NOT NULL DEFAULT '',
  owner_user_id INTEGER NULL REFERENCES users(id),
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deals (
  id INTEGER PRIMARY KEY,
  company_id INTEGER NOT NULL REFERENCES companies(id),
  contact_id INTEGER NOT NULL REFERENCES contacts(id),
  title TEXT NOT NULL,
  value REAL NOT NULL CHECK (value >= 0),
  stage TEXT NOT NULL DEFAULT 'new'
    CHECK (stage IN ('new','contacted','qualified','proposal','negotiation','won','lost')),
  probability REAL NOT NULL DEFAULT 10 CHECK (probability >= 0 AND probability <= 100),
  owner_user_id INTEGER NULL REFERENCES users(id),
  expected_close TEXT NULL,
  lost_reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deal_activities (
  id INTEGER PRIMARY KEY,
  company_id INTEGER NOT NULL REFERENCES companies(id),
  deal_id INTEGER NOT NULL REFERENCES deals(id),
  user_id INTEGER NOT NULL REFERENCES users(id),
  kind TEXT NOT NULL CHECK (kind IN ('note','call','email','meeting')),
  body TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS invoices (
  id INTEGER PRIMARY KEY,
  company_id INTEGER NOT NULL REFERENCES companies(id),
  deal_id INTEGER NULL UNIQUE REFERENCES deals(id),
  contact_id INTEGER NOT NULL REFERENCES contacts(id),
  invoice_no TEXT NOT NULL,
  issue_date TEXT NOT NULL,
  due_date TEXT NULL,
  subtotal REAL NOT NULL,
  tax_percent REAL NOT NULL CHECK (tax_percent >= 0 AND tax_percent <= 100),
  tax_amount REAL NOT NULL,
  total REAL NOT NULL CHECK (total >= 0),
  status TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft','sent','paid','cancelled')),
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  UNIQUE (company_id, invoice_no)
);

CREATE TABLE IF NOT EXISTS invoice_items (
  id INTEGER PRIMARY KEY,
  invoice_id INTEGER NOT NULL REFERENCES invoices(id),
  description TEXT NOT NULL,
  qty REAL NOT NULL CHECK (qty > 0),
  unit_price REAL NOT NULL CHECK (unit_price >= 0),
  line_total REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
  id INTEGER PRIMARY KEY,
  company_id INTEGER NOT NULL REFERENCES companies(id),
  subject TEXT NOT NULL,
  body TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open'
    CHECK (status IN ('open','in_progress','resolved','closed')),
  priority TEXT NOT NULL DEFAULT 'medium'
    CHECK (priority IN ('low','medium','high')),
  requester_user_id INTEGER NULL REFERENCES users(id),
  assigned_to INTEGER NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY,
  company_id INTEGER NOT NULL REFERENCES companies(id),
  actor_user_id INTEGER NOT NULL REFERENCES users(id),
  action TEXT NOT NULL,
  entity TEXT NOT NULL,
  entity_id INTEGER NULL,
  detail TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_users_company ON users(company_id);
CREATE INDEX IF NOT EXISTS idx_employees_company ON employees(company_id);
CREATE INDEX IF NOT EXISTS idx_leave_req_emp ON leave_requests(employee_id, status);
CREATE INDEX IF NOT EXISTS idx_attendance_emp ON attendance(employee_id, work_date);
CREATE INDEX IF NOT EXISTS idx_contacts_company ON contacts(company_id);
CREATE INDEX IF NOT EXISTS idx_deals_stage ON deals(company_id, stage);
CREATE INDEX IF NOT EXISTS idx_activities_deal ON deal_activities(deal_id);
CREATE INDEX IF NOT EXISTS idx_invoices_company ON invoices(company_id, status);
CREATE INDEX IF NOT EXISTS idx_tickets_company ON tickets(company_id, status);
CREATE INDEX IF NOT EXISTS idx_audit_company ON audit_log(company_id);
"""

# Postgres-ready notes: INTEGER PRIMARY KEY -> BIGINT GENERATED ALWAYS AS IDENTITY;
# TEXT ISO dates -> DATE/TIMESTAMPTZ; REAL money -> NUMERIC(14,2).
# Production adds: CREATE UNIQUE INDEX uq_attendance_open ON attendance(employee_id) WHERE check_out IS NULL;

_thread = threading.local()


def connect():
    db = getattr(_thread, "db", None)
    if db is None:
        db = sqlite3.connect(DB_PATH, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        _thread.db = db
    return db


def close_thread_db():
    db = getattr(_thread, "db", None)
    if db is not None:
        db.close()
        _thread.db = None


def init_db():
    fresh = not os.path.exists(DB_PATH)
    db = connect()
    db.executescript(SCHEMA)
    row = db.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        db.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        db.commit()
    elif row["version"] != SCHEMA_VERSION:
        raise RuntimeError(f"schema version mismatch: db={row['version']} code={SCHEMA_VERSION}")
    # seed only if no companies exist
    if db.execute("SELECT COUNT(*) c FROM companies").fetchone()["c"] == 0:
        seed(db)
    return fresh


def q(sql, params=()):
    return connect().execute(sql, params)


def qone(sql, params=()):
    return connect().execute(sql, params).fetchone()


def qall(sql, params=()):
    return connect().execute(sql, params).fetchall()


def commit():
    connect().commit()


def rollback():
    connect().rollback()


# ---------------------------------------------------------------- seed data
# (uses auth.hash_password for seed credentials — demo only, never production)


def seed(db):
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    cur = db.cursor()

    def add_user(company_id, username, role, full_name, contact_id=None):
        h = auth.hash_password("demo1234")
        cur.execute(
            "INSERT INTO users (company_id, username, password_hash, role, full_name, contact_id, is_active, created_at)"
            " VALUES (?,?,?,?,?,?,1,?)",
            (company_id, username, h, role, full_name, contact_id, now),
        )
        return cur.lastrowid

    def add_employee(company_id, user_id, full_name, email, dept, desig, salary):
        cur.execute(
            "INSERT INTO employees (company_id, user_id, full_name, email, phone, department, designation,"
            " salary_monthly, date_of_joining, is_active, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (company_id, user_id, full_name, email, "+91-90000-00000", dept, desig,
             salary, "2024-04-01", 1, now),
        )
        eid = cur.lastrowid
        for lt, bal in (("casual", 12), ("sick", 10), ("earned", 15)):
            cur.execute(
                "INSERT INTO leave_balances (company_id, employee_id, leave_type, year, balance_days)"
                " VALUES (?,?,?,?,?)", (company_id, eid, lt, 2026, bal))
        return eid

    cur.execute("INSERT INTO companies (name, created_at) VALUES (?,?)", ("Acme Demo Pvt Ltd", now))
    c1 = cur.lastrowid
    cur.execute("INSERT INTO companies (name, created_at) VALUES (?,?)", ("Rival Corp", now))
    c2 = cur.lastrowid

    # Company 1 users
    u_admin = add_user(c1, "admin", "admin", "Asha Admin")
    u_mgr = add_user(c1, "meera", "manager", "Meera Manager")
    u_arjun = add_user(c1, "arjun", "employee", "Arjun Employee")
    u_kavya = add_user(c1, "kavya", "employee", "Kavya Employee")
    u_sana = add_user(c1, "sana", "support", "Sana Support")
    # Company 2 user (cross-tenant test subject)
    u_vikram = add_user(c2, "vikram", "employee", "Vikram Rival")

    e_admin = add_employee(c1, u_admin, "Asha Admin", "asha@acme.demo", "Admin", "Founder", 150000)
    e_mgr = add_employee(c1, u_mgr, "Meera Manager", "meera@acme.demo", "Sales", "Sales Manager", 90000)
    e_arjun = add_employee(c1, u_arjun, "Arjun Employee", "arjun@acme.demo", "Sales", "Sales Executive", 45000)
    e_kavya = add_employee(c1, u_kavya, "Kavya Employee", "kavya@acme.demo", "HR", "HR Executive", 50000)
    add_employee(c1, u_sana, "Sana Support", "sana@acme.demo", "Support", "Support Agent", 40000)
    add_employee(c2, u_vikram, "Vikram Rival", "vikram@rival.demo", "Sales", "Sales Executive", 45000)

    # Contacts
    contacts = []
    for name, email, cname, owner in [
        ("Rohan Client", "rohan@clientco.demo", "ClientCo", u_arjun),
        ("Priya Prospect", "priya@prospect.demo", "ProspectInc", u_arjun),
        ("Dev Distributor", "dev@distrib.demo", "DistribLLP", u_mgr),
        ("Rival Buyer", "buyer@rival.demo", "RivalBuyer", None),
    ]:
        cid_company = c2 if name == "Rival Buyer" else c1
        cur.execute(
            "INSERT INTO contacts (company_id, name, email, phone, company_name, owner_user_id, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (cid_company, name, email, "+91-91111-11111", cname, owner, now))
        contacts.append(cur.lastrowid)

    # Partner user links to the ClientCo contact (client portal view)
    u_rohan = add_user(c1, "rohan", "partner", "Rohan Client", contact_id=contacts[0])

    # Deals
    def add_deal(company_id, contact_id, title, value, stage, owner, prob=None):
        p = prob if prob is not None else STAGE_PROB[stage]
        cur.execute(
            "INSERT INTO deals (company_id, contact_id, title, value, stage, probability,"
            " owner_user_id, expected_close, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (company_id, contact_id, title, value, stage, p, owner, "2026-10-15", now, now))
        return cur.lastrowid

    d1 = add_deal(c1, contacts[0], "ClientCo website redesign", 85000, "won", u_arjun)
    d2 = add_deal(c1, contacts[1], "ProspectInc SEO retainer", 36000, "proposal", u_arjun)
    d3 = add_deal(c1, contacts[2], "DistribLLP WhatsApp agents", 120000, "negotiation", u_mgr)
    d4 = add_deal(c1, contacts[1], "ProspectInc AI chatbot", 55000, "qualified", u_kavya)
    add_deal(c2, contacts[3], "Rival supply order", 200000, "new", u_vikram)

    cur.execute(
        "INSERT INTO deal_activities (company_id, deal_id, user_id, kind, body, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (c1, d2, u_arjun, "call", "Discovery call done. Sent proposal deck.", now))
    cur.execute(
        "INSERT INTO deal_activities (company_id, deal_id, user_id, kind, body, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (c1, d1, u_mgr, "note", "Deal won — proceed to invoice.", now))

    # A pending leave request from Arjun
    cur.execute(
        "INSERT INTO leave_requests (company_id, employee_id, leave_type, start_date, end_date,"
        " days, reason, status, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (c1, e_arjun, "casual", "2026-10-05", "2026-10-06", 2.0, "Family function", "pending", now))

    # An invoice already issued from the won deal (so UI shows data; idempotency demo)
    cur.execute(
        "INSERT INTO invoices (company_id, deal_id, contact_id, invoice_no, issue_date, due_date,"
        " subtotal, tax_percent, tax_amount, total, status, created_by, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (c1, d1, contacts[0], "INV-1-0001", "2026-09-20", "2026-10-20",
         85000, 18, 15300, 100300, "sent", u_mgr, now))
    inv_id = cur.lastrowid
    cur.execute(
        "INSERT INTO invoice_items (invoice_id, description, qty, unit_price, line_total)"
        " VALUES (?,?,?,?,?)", (inv_id, "Website redesign — milestone 1", 1, 85000, 85000))

    # Tickets
    cur.execute(
        "INSERT INTO tickets (company_id, subject, body, status, priority, requester_user_id,"
        " assigned_to, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (c1, "VPN not connecting", "VPN drops every 10 minutes on office wifi.", "open", "high",
         u_arjun, None, now, now))
    cur.execute(
        "INSERT INTO tickets (company_id, subject, body, status, priority, requester_user_id,"
        " assigned_to, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (c1, "Payslip download broken", "September payslip shows 404.", "in_progress", "medium",
         u_kavya, u_sana, now, now))

    # Audit seed entries
    for action, entity, eid, actor in [
        ("seed.complete", "company", c1, u_admin),
        ("invoice.create", "invoice", inv_id, u_mgr),
    ]:
        cur.execute(
            "INSERT INTO audit_log (company_id, actor_user_id, action, entity, entity_id, detail, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (c1, actor, action, entity, eid, json.dumps({"seed": True}), now))

    db.commit()
