"""VisionERP slice — API tests incl. adversarial cases.

Run from slice/:  python3 -m unittest discover -s tests -v
Spins up the real HTTP server against a temp SQLite DB.
"""
import json
import os
import re
import sys
import tempfile
import threading
import unittest
import urllib.request
import urllib.error

os.environ["VISIONERP_DB"] = os.path.join(tempfile.mkdtemp(prefix="vwerp-test-"), "t.sqlite")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402


def call(method, path, token=None, body=None, raw=None):
    url = f"http://127.0.0.1:{PORT}{path}"
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, method=method)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as r:
            txt = r.read().decode()
            return r.status, (json.loads(txt) if txt else None)
    except urllib.error.HTTPError as e:
        txt = e.read().decode()
        try:
            return e.code, json.loads(txt)
        except Exception:
            return e.code, {"raw": txt}


def login(username, password="demo1234"):
    s, b = call("POST", "/api/v1/auth/login",
                body={"username": username, "password": password})
    assert s == 200, f"login {username}: {s} {b}"
    return b["token"]


PORT = None


class APITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global PORT
        import db
        db.init_db()
        srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        PORT = srv.server_address[1]
        cls._srv = srv
        cls._t = threading.Thread(target=srv.serve_forever, daemon=True)
        cls._t.start()
        cls.T = {u: login(u) for u in
                 ("admin", "meera", "arjun", "kavya", "sana", "rohan", "vikram")}

    @classmethod
    def tearDownClass(cls):
        cls._srv.shutdown()

    # ---------------------------------------------------------- auth
    def test_01_login_ok(self):
        s, b = call("POST", "/api/v1/auth/login",
                    body={"username": "arjun", "password": "demo1234"})
        self.assertEqual(s, 200)
        self.assertEqual(len(b["token"]), 64)
        self.assertEqual(b["user"]["role"], "employee")

    def test_02_login_wrong_password_uniform(self):
        s, b = call("POST", "/api/v1/auth/login",
                    body={"username": "arjun", "password": "nope-nope"})
        self.assertEqual(s, 401)
        self.assertEqual(b["error"], "auth_required")

    def test_03_login_unknown_user_same_message(self):
        s, b = call("POST", "/api/v1/auth/login",
                    body={"username": "ghost-user-xyz", "password": "whatever123"})
        self.assertEqual(s, 401)
        self.assertEqual(b["message"], "invalid credentials")  # no oracle

    def test_04_no_token_rejected(self):
        s, _ = call("GET", "/api/v1/deals")
        self.assertEqual(s, 401)

    def test_05_bad_token_rejected(self):
        s, _ = call("GET", "/api/v1/deals", token="deadbeef" * 8)
        self.assertEqual(s, 401)

    def test_06_logout_invalidates(self):
        tok = login("kavya")
        s, _ = call("POST", "/api/v1/auth/logout", token=tok)
        self.assertEqual(s, 200)
        s, _ = call("GET", "/api/v1/auth/me", token=tok)
        self.assertEqual(s, 401)

    # ---------------------------------------------------------- RBAC
    def test_10_employee_cannot_list_users(self):
        s, b = call("GET", "/api/v1/users", token=self.T["arjun"])
        self.assertEqual(s, 403)

    def test_11_employee_cannot_create_user(self):
        s, _ = call("POST", "/api/v1/users", token=self.T["arjun"],
                    body={"username": "hax", "password": "password123",
                          "role": "admin", "full_name": "Hax"})
        self.assertEqual(s, 403)

    def test_12_manager_cannot_create_user(self):
        s, _ = call("POST", "/api/v1/users", token=self.T["meera"],
                    body={"username": "hax2", "password": "password123",
                          "role": "employee", "full_name": "Hax2"})
        self.assertEqual(s, 403)

    def test_13_support_cannot_create_deal(self):
        s, _ = call("POST", "/api/v1/deals", token=self.T["sana"],
                    body={"contact_id": 1, "title": "x", "value": 1})
        self.assertEqual(s, 403)

    def test_14_support_user_lookup_is_limited(self):
        s, b = call("GET", "/api/v1/users/3", token=self.T["sana"])
        self.assertEqual(s, 200)
        self.assertNotIn("email", b)
        self.assertNotIn("username", b)
        self.assertIn("full_name", b)
        self.assertIn("department", b)

    def test_15_partner_cannot_create_deal(self):
        s, _ = call("POST", "/api/v1/deals", token=self.T["rohan"],
                    body={"contact_id": 1, "title": "x", "value": 1})
        self.assertEqual(s, 403)

    def test_16_partner_cannot_log_activity(self):
        s, _ = call("POST", "/api/v1/deals/1/activities", token=self.T["rohan"],
                    body={"kind": "note", "body": "hi"})
        self.assertIn(s, (403, 404))

    def test_17_employee_cannot_approve_leave(self):
        s, _ = call("POST", "/api/v1/leave/requests/1/approve",
                    token=self.T["kavya"], body={})
        self.assertEqual(s, 403)

    def test_18_employee_cannot_delete_contact(self):
        s, _ = call("DELETE", "/api/v1/contacts/1", token=self.T["arjun"])
        self.assertEqual(s, 403)

    def test_19_employee_cannot_read_audit(self):
        s, _ = call("GET", "/api/v1/audit", token=self.T["arjun"])
        self.assertEqual(s, 403)

    def test_1a_admin_can_create_user_and_audit_logged(self):
        s, b = call("POST", "/api/v1/users", token=self.T["admin"],
                    body={"username": "newbie1", "password": "password123",
                          "role": "employee", "full_name": "New Bie"})
        self.assertEqual(s, 201)
        self.assertNotIn("password_hash", b)
        s, a = call("GET", "/api/v1/audit?per_page=5", token=self.T["admin"])
        self.assertTrue(any(x["action"] == "user.create" for x in a["items"]))

    def test_1b_admin_cannot_demote_self(self):
        s, b = call("PATCH", "/api/v1/users/1", token=self.T["admin"],
                    body={"role": "employee"})
        self.assertEqual(s, 403)

    # ---------------------------------------------------------- CRM
    def test_20_deal_probability_auto_from_stage(self):
        s, b = call("POST", "/api/v1/contacts", token=self.T["arjun"],
                    body={"name": "Test Contact", "email": "t@t.demo"})
        cid = b["id"]
        s, d = call("POST", "/api/v1/deals", token=self.T["arjun"],
                    body={"contact_id": cid, "title": "Stage test", "value": 50000,
                          "stage": "negotiation"})
        self.assertEqual(s, 201)
        self.assertEqual(d["probability"], 80)
        # stage change re-maps probability
        s, d = call("PATCH", f"/api/v1/deals/{d['id']}", token=self.T["arjun"],
                    body={"stage": "proposal"})
        self.assertEqual(d["probability"], 65)

    def test_21_deal_scoping_employee_vs_manager(self):
        s, mine = call("GET", "/api/v1/deals?per_page=100", token=self.T["kavya"])
        self.assertTrue(all(d["owner_user_id"] == 4 for d in mine["items"]),
                        "employee must see only own deals")
        s, alld = call("GET", "/api/v1/deals?per_page=100", token=self.T["meera"])
        owners = {d["owner_user_id"] for d in alld["items"]}
        self.assertGreater(len(owners), 1, "manager sees team-wide deals")

    def test_22_deal_validation(self):
        s, _ = call("POST", "/api/v1/deals", token=self.T["arjun"],
                    body={"contact_id": 1, "title": "bad", "value": -5})
        self.assertEqual(s, 400)
        s, _ = call("POST", "/api/v1/deals", token=self.T["arjun"],
                    body={"contact_id": 1, "title": "bad", "value": 5, "stage": "nope"})
        self.assertEqual(s, 400)
        s, d = call("POST", "/api/v1/deals", token=self.T["arjun"],
                    body={"contact_id": 1, "title": "lost test", "value": 5})
        s, _ = call("PATCH", f"/api/v1/deals/{d['id']}", token=self.T["arjun"],
                    body={"stage": "lost"})
        self.assertEqual(s, 400)  # lost_reason required

    def test_23_employee_cannot_patch_others_deal(self):
        s, mine = call("GET", "/api/v1/deals?per_page=100", token=self.T["arjun"])
        other = [d for d in mine["items"]][0]
        s, _ = call("PATCH", f"/api/v1/deals/{other['id']}", token=self.T["kavya"],
                    body={"title": "hijacked"})
        self.assertEqual(s, 403)

    def test_24_activity_logging(self):
        s, b = call("POST", "/api/v1/deals/2/activities", token=self.T["arjun"],
                    body={"kind": "call", "body": "Follow-up call done."})
        self.assertEqual(s, 201)
        s, d = call("GET", "/api/v1/deals/2", token=self.T["arjun"])
        self.assertTrue(any(a["body"] == "Follow-up call done." for a in d["activities"]))

    # ---------------------------------------------------------- HRMS leave
    def _apply(self, token, start, end, ltype="casual"):
        return call("POST", "/api/v1/leave/requests", token=token,
                    body={"leave_type": ltype, "start_date": start,
                          "end_date": end, "reason": "test"})

    def test_30_leave_apply_days_computed(self):
        s, b = self._apply(self.T["kavya"], "2026-11-10", "2026-11-12")
        self.assertEqual(s, 201)
        self.assertEqual(b["days"], 3.0)
        self.assertEqual(b["status"], "pending")

    def test_31_leave_overlap_rejected(self):
        s, _ = self._apply(self.T["arjun"], "2026-10-06", "2026-10-07")  # overlaps seeded 10-05..06
        self.assertEqual(s, 409)

    def test_32_leave_insufficient_balance(self):
        s, _ = self._apply(self.T["kavya"], "2026-11-01", "2026-11-30", "casual")  # 30 > 12
        self.assertEqual(s, 409)

    def test_33_leave_approve_decrements_balance(self):
        s, b = self._apply(self.T["kavya"], "2026-12-01", "2026-12-02")
        rid = b["id"]
        s, before = call("GET", "/api/v1/leave/balances", token=self.T["kavya"])
        bal0 = next(x["balance_days"] for x in before["balances"] if x["leave_type"] == "casual")
        s, b = call("POST", f"/api/v1/leave/requests/{rid}/approve",
                    token=self.T["meera"], body={})
        self.assertEqual(s, 200)
        self.assertEqual(b["status"], "approved")
        s, after = call("GET", "/api/v1/leave/balances", token=self.T["kavya"])
        bal1 = next(x["balance_days"] for x in after["balances"] if x["leave_type"] == "casual")
        self.assertAlmostEqual(bal0 - bal1, 2.0)

    def test_34_double_approve_blocked(self):
        s, b = self._apply(self.T["arjun"], "2026-11-20", "2026-11-21")
        rid = b["id"]
        s, _ = call("POST", f"/api/v1/leave/requests/{rid}/approve",
                    token=self.T["meera"], body={})
        self.assertEqual(s, 200)
        s, b = call("POST", f"/api/v1/leave/requests/{rid}/approve",
                    token=self.T["admin"], body={})
        self.assertEqual(s, 409)  # already approved — no double decrement
        self.assertIn("already", b["message"])

    def test_35_self_approval_blocked(self):
        s, b = self._apply(self.T["meera"], "2026-11-25", "2026-11-25")  # manager applies
        rid = b["id"]
        s, _ = call("POST", f"/api/v1/leave/requests/{rid}/approve",
                    token=self.T["meera"], body={})
        self.assertEqual(s, 403)
        # admin can still approve it
        s, _ = call("POST", f"/api/v1/leave/requests/{rid}/approve",
                    token=self.T["admin"], body={})
        self.assertEqual(s, 200)

    def test_36_reject_then_approve_blocked(self):
        s, b = self._apply(self.T["kavya"], "2026-12-10", "2026-12-11")
        rid = b["id"]
        s, _ = call("POST", f"/api/v1/leave/requests/{rid}/reject",
                    token=self.T["meera"], body={"reason": "nope"})
        self.assertEqual(s, 200)
        s, _ = call("POST", f"/api/v1/leave/requests/{rid}/approve",
                    token=self.T["meera"], body={})
        self.assertEqual(s, 409)

    # ---------------------------------------------------------- attendance
    def test_40_checkin_checkout_flow(self):
        s, b = call("POST", "/api/v1/attendance/check-in", token=self.T["kavya"], body={})
        self.assertEqual(s, 201)
        s, _ = call("POST", "/api/v1/attendance/check-in", token=self.T["kavya"], body={})
        self.assertEqual(s, 409)  # double check-in blocked
        s, b = call("POST", "/api/v1/attendance/check-out", token=self.T["kavya"], body={})
        self.assertEqual(s, 200)
        self.assertIsNotNone(b["check_out"])
        s, _ = call("POST", "/api/v1/attendance/check-out", token=self.T["kavya"], body={})
        self.assertEqual(s, 409)  # nothing open

    # ---------------------------------------------------------- invoices
    def test_50_invoice_from_won_deal(self):
        # make a fresh won deal
        s, c = call("POST", "/api/v1/contacts", token=self.T["meera"],
                    body={"name": "Invoice Test Co"})
        s, d = call("POST", "/api/v1/deals", token=self.T["meera"],
                    body={"contact_id": c["id"], "title": "Invoice deal", "value": 10000,
                          "stage": "won"})
        s, inv = call("POST", f"/api/v1/invoices/from-deal/{d['id']}", token=self.T["meera"],
                      body={"items": [{"description": "Work", "qty": 2, "unit_price": 5000}],
                            "tax_percent": 18, "total": 999999999})
        self.assertEqual(s, 201)
        # server computes totals; client-supplied total ignored
        self.assertEqual(inv["subtotal"], 10000)
        self.assertEqual(inv["tax_amount"], 1800)
        self.assertEqual(inv["total"], 11800)
        self.assertTrue(inv["invoice_no"].startswith("INV-1-"))

    def test_51_invoice_idempotent_per_deal(self):
        s, c = call("POST", "/api/v1/contacts", token=self.T["meera"],
                    body={"name": "Idem Co"})
        s, d = call("POST", "/api/v1/deals", token=self.T["meera"],
                    body={"contact_id": c["id"], "title": "Idem deal", "value": 1000,
                          "stage": "won"})
        s, _ = call("POST", f"/api/v1/invoices/from-deal/{d['id']}", token=self.T["meera"],
                    body={"items": [{"description": "x", "qty": 1, "unit_price": 1000}]})
        self.assertEqual(s, 201)
        s, b = call("POST", f"/api/v1/invoices/from-deal/{d['id']}", token=self.T["meera"],
                    body={"items": [{"description": "x", "qty": 1, "unit_price": 1000}]})
        self.assertEqual(s, 409)  # second invoice blocked

    def test_52_invoice_from_non_won_deal_rejected(self):
        s, c = call("POST", "/api/v1/contacts", token=self.T["meera"],
                    body={"name": "NotWon Co"})
        s, d = call("POST", "/api/v1/deals", token=self.T["meera"],
                    body={"contact_id": c["id"], "title": "Not won", "value": 1000,
                          "stage": "proposal"})
        s, _ = call("POST", f"/api/v1/invoices/from-deal/{d['id']}", token=self.T["meera"],
                    body={"items": [{"description": "x", "qty": 1, "unit_price": 1000}]})
        self.assertEqual(s, 409)

    def test_53_negative_amounts_rejected(self):
        s, c = call("POST", "/api/v1/contacts", token=self.T["meera"],
                    body={"name": "Neg Co"})
        s, d = call("POST", "/api/v1/deals", token=self.T["meera"],
                    body={"contact_id": c["id"], "title": "Neg deal", "value": 1000,
                          "stage": "won"})
        for items in ([{"description": "x", "qty": 1, "unit_price": -50}],
                      [{"description": "x", "qty": -2, "unit_price": 50}],
                      [{"description": "x", "qty": 0, "unit_price": 50}]):
            s, _ = call("POST", f"/api/v1/invoices/from-deal/{d['id']}", token=self.T["meera"],
                        body={"items": items})
            self.assertEqual(s, 400, f"items={items}")
        s, _ = call("POST", f"/api/v1/invoices/from-deal/{d['id']}", token=self.T["meera"],
                    body={"items": [{"description": "x", "qty": 1, "unit_price": 10}],
                          "tax_percent": 150})
        self.assertEqual(s, 400)

    def test_54_invoice_status_transitions(self):
        s, inv = call("GET", "/api/v1/invoices?per_page=1", token=self.T["meera"])
        iid = inv["items"][0]["id"]
        # force to draft first via direct check — use seeded sent invoice: sent->draft invalid
        s, _ = call("POST", f"/api/v1/invoices/{iid}/status", token=self.T["meera"],
                    body={"status": "draft"})
        # depends on current status; assert invalid backward move rejected OR valid move works
        s, cur = call("GET", f"/api/v1/invoices/{iid}", token=self.T["meera"])
        if cur["status"] == "draft":
            s, _ = call("POST", f"/api/v1/invoices/{iid}/status", token=self.T["meera"],
                        body={"status": "paid"})
            self.assertEqual(s, 409)  # draft -> paid skips sent
            s, b = call("POST", f"/api/v1/invoices/{iid}/status", token=self.T["meera"],
                        body={"status": "sent"})
            self.assertEqual(s, 200)
        elif cur["status"] == "sent":
            s, b = call("POST", f"/api/v1/invoices/{iid}/status", token=self.T["meera"],
                        body={"status": "paid"})
            self.assertEqual(s, 200)
            s, _ = call("POST", f"/api/v1/invoices/{iid}/status", token=self.T["meera"],
                        body={"status": "cancelled"})
            self.assertEqual(s, 409)  # paid is terminal

    def test_55_employee_cannot_create_invoice(self):
        s, _ = call("POST", "/api/v1/invoices/from-deal/1", token=self.T["arjun"],
                    body={"items": [{"description": "x", "qty": 1, "unit_price": 1}]})
        self.assertEqual(s, 403)

    # ---------------------------------------------------------- adversarial
    def test_60_sqli_in_name_is_literal(self):
        payload = "Robert'); DROP TABLE contacts;--"
        s, b = call("POST", "/api/v1/contacts", token=self.T["arjun"],
                    body={"name": payload, "email": "r@r.demo"})
        self.assertEqual(s, 201)
        self.assertEqual(b["name"], payload)  # stored literally
        s, b = call("GET", "/api/v1/contacts?per_page=100", token=self.T["admin"])
        self.assertGreater(b["total"], 0)  # table still exists

    def test_61_sqli_in_search_is_escaped(self):
        s, b = call("GET", "/api/v1/contacts?q=%27%20OR%20%271%27%3D%271&per_page=100",
                    token=self.T["arjun"])
        self.assertEqual(s, 200)
        # must not dump every contact — literal search for the payload string
        self.assertEqual(b["total"], 0)

    def test_62_sqli_in_login_is_neutralized(self):
        s, b = call("POST", "/api/v1/auth/login",
                    body={"username": "admin' --", "password": "anything123"})
        self.assertEqual(s, 401)

    def test_63_xss_payload_roundtrips_raw_ui_escapes(self):
        payload = "<script>alert('xss')</script><img src=x onerror=alert(1)>"
        s, b = call("POST", "/api/v1/deals/2/activities", token=self.T["arjun"],
                    body={"kind": "note", "body": payload})
        self.assertEqual(s, 201)
        self.assertEqual(b["body"], payload)  # API stores raw; UI renders via textContent
        # and the static JS contains no innerHTML usage on user data (comments stripped)
        js = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "static", "app.js")).read()
        code = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
        self.assertNotRegex(code, r"\.innerHTML\s*=")

    def test_64_cross_tenant_deal_blocked(self):
        # vikram is company 2; deal 1 belongs to company 1
        s, b = call("GET", "/api/v1/deals/1", token=self.T["vikram"])
        self.assertEqual(s, 404)  # 404, not 403 — no tenant enumeration

    def test_65_cross_tenant_contact_list_isolated(self):
        s, b = call("GET", "/api/v1/contacts?per_page=100", token=self.T["vikram"])
        self.assertEqual(s, 200)
        self.assertTrue(all(c["company_id"] == 2 for c in b["items"]))

    def test_66_cross_tenant_invoice_blocked(self):
        s, _ = call("GET", "/api/v1/invoices/1", token=self.T["vikram"])
        self.assertEqual(s, 404)

    def test_67_partner_sees_only_own_invoices(self):
        s, b = call("GET", "/api/v1/invoices?per_page=100", token=self.T["rohan"])
        self.assertEqual(s, 200)
        self.assertTrue(all(i["contact_id"] == 1 for i in b["items"]),
                        "partner sees only own company's invoices")
        s, _ = call("GET", "/api/v1/invoices/1", token=self.T["rohan"])
        self.assertEqual(s, 200)  # invoice 1 is for contact 1 — allowed

    def test_68_partner_cannot_create_user(self):
        s, _ = call("POST", "/api/v1/users", token=self.T["rohan"],
                    body={"username": "pz", "password": "password123",
                          "role": "admin", "full_name": "PZ"})
        self.assertEqual(s, 403)

    def test_69_oversize_body_rejected(self):
        s, _ = call("POST", "/api/v1/contacts", token=self.T["arjun"],
                    raw=b'{"name":"' + b"x" * 2_000_000 + b'"}')
        self.assertEqual(s, 413)

    def test_6a_invalid_json_rejected(self):
        s, _ = call("POST", "/api/v1/contacts", token=self.T["arjun"],
                    raw=b'{"name": not json}')
        self.assertEqual(s, 400)

    def test_6b_type_confusion_rejected(self):
        s, _ = call("POST", "/api/v1/deals", token=self.T["arjun"],
                    body={"contact_id": "1; DROP TABLE deals", "title": "x", "value": 1})
        self.assertEqual(s, 400)

    def test_6c_support_cannot_see_salary(self):
        # support has no employee.read_all; /employees/me would 404 for sana (no link) — assert no salary leak path
        s, _ = call("GET", "/api/v1/employees", token=self.T["sana"])
        self.assertEqual(s, 403)

    def test_6d_employee_salary_redacted(self):
        s, b = call("GET", "/api/v1/employees/me", token=self.T["arjun"])
        self.assertEqual(s, 200)
        self.assertNotIn("salary_monthly", b)

    # ---------------------------------------------------------- tickets
    def test_70_ticket_flow(self):
        s, t = call("POST", "/api/v1/tickets", token=self.T["arjun"],
                    body={"subject": "Test ticket", "body": "details here"})
        self.assertEqual(s, 201)
        s, _ = call("POST", f"/api/v1/tickets/{t['id']}/status", token=self.T["arjun"],
                    body={"status": "resolved"})
        self.assertEqual(s, 403)  # employee cannot triage
        s, b = call("POST", f"/api/v1/tickets/{t['id']}/status", token=self.T["sana"],
                    body={"status": "in_progress"})
        self.assertEqual(s, 200)
        self.assertEqual(b["status"], "in_progress")


if __name__ == "__main__":
    unittest.main(verbosity=2)
