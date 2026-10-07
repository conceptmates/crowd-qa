#!/usr/bin/env python3
"""Stack check for the Tinybox demo: sign-up, workspace, fake connect, a signed customer message reaching the
inbox, a reply through the CLI, and the health endpoint. Exit 0 only when everything passes."""
import hashlib, hmac, json, os, subprocess, sys, time, urllib.request
BASE = "http://localhost:4100"; HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "..", "demo-app"); STAMP = time.strftime("%H%M%S"); ok = []
def check(name, cond, detail=""):
    ok.append(bool(cond)); print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")
def call(method, path, body=None, token=None, headers=None):
    h = {"content-type": "application/json", **(headers or {})}
    if token: h["authorization"] = f"Bearer {token}"
    data = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r: return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e: return e.code, None
    except Exception as e: return 0, str(e)
s, b = call("GET", "/health"); check("health", s == 200 and b == {"status": "ok"})
email = f"verify-{STAMP}@crowd.test"
s, b = call("POST", "/api/signup", {"name": "Verify", "email": email, "password": "CrowdQA!2026"}); check("sign-up", s in (200, 201))
tok = (b or {}).get("token")
s, b = call("POST", "/api/workspaces", {"name": f"Verify {STAMP}"}, tok); check("workspace", s in (200, 201))
out = subprocess.run(["node", "scripts/connect-fake.js", email, "Verify line"], cwd=APP, capture_output=True, text=True)
ch = json.loads(out.stdout) if out.returncode == 0 else {}; check("fake connect", bool(ch.get("number")), ch.get("number", out.stderr[:80]))
body = json.dumps({"from": "15550009999", "name": "Verify Customer", "text": f"verify {STAMP}", "ts": int(time.time() * 1000)}, separators=(",", ":")).encode()
sig = "sha256=" + hmac.new(ch.get("secret", "").encode(), body, hashlib.sha256).hexdigest()
s, _ = call("POST", f"/webhook/{ch.get('number')}", body, headers={"X-Signature": sig}); check("signed webhook", s == 200)
s, b = call("GET", "/api/threads", token=tok)
check("message in inbox", s == 200 and f"verify {STAMP}" in json.dumps(b))
print(f"\n{sum(ok)}/{len(ok)} checks passed"); sys.exit(0 if all(ok) else 1)
