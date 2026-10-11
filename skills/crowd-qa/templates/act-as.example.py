#!/usr/bin/env python3
"""act-as.py <command> [args]: the other side of a scenario, done through the product's own API, so a character
never waits on the square for someone who is not running (an admin who approves, staff who scan, a seller who
replies). Every call prints the API's status and answer; testers quote it as evidence, then check what they see
as themselves.

Copy to <run>/stack/act-as.py, replace the endpoints and accounts below with the product's, list the commands in
context.md, and run each once in the stack check. Keep it to actions a real person in that role could take.

  scan <code>                              staff scan a ticket at the door
  pending                                  list items waiting for an admin
  approve <id> | reject <id> <reason>      an admin decides on one
  coupon-off <CODE> | coupon-on <CODE>     the seller switches a coupon off or on
"""
import json
import sys
import urllib.error
import urllib.request

API = "http://localhost:8080/api"                     # through the tunnel when the stack is remote
AS = {                                                # role -> (email, password): seeded accounts, never a character's
    "staff": ("staff@example.test", "password"),
    "admin": ("admin@example.test", "password"),
    "seller": ("seller@example.test", "password"),
}


def call(method, path, body=None, token=None):
    h = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(API + path, data=json.dumps(body).encode() if body is not None else None,
                                 method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def token(role):
    email, pw = AS[role]
    _, r = call("POST", "/auth/login", {"email": email, "password": pw})
    d = r.get("data") or r
    return d.get("token") or d.get("access_token")


def show(status, answer):
    print(status, json.dumps(answer, ensure_ascii=False)[:1500])


def main(a):
    if not a or a[0] in ("-h", "--help"):
        print(__doc__)
        return
    c = a[0]
    if c == "scan":
        show(*call("POST", "/scan", {"code": a[1]}, token("staff")))
    elif c == "pending":
        show(*call("GET", "/admin/requests?status=pending", token=token("admin")))
    elif c == "approve":
        show(*call("POST", f"/admin/requests/{a[1]}/approve", {}, token("admin")))
    elif c == "reject":
        show(*call("POST", f"/admin/requests/{a[1]}/reject", {"reason": " ".join(a[2:]) or "Rejected"}, token("admin")))
    elif c in ("coupon-off", "coupon-on"):
        t = token("seller")
        _, r = call("GET", "/seller/coupons?per_page=100", token=t)
        rows = r.get("data") or []
        hit = [x for x in rows if str(x.get("code", "")).upper() == a[1].upper()]
        if not hit:
            sys.exit(f"no coupon {a[1]}")
        show(*call("PUT", f"/seller/coupons/{hit[0]['id']}", {"is_active": c == "coupon-on"}, t))
    else:
        print("unknown command", c)
        print(__doc__)
        sys.exit(2)


main(sys.argv[1:])
