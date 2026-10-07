#!/usr/bin/env python3
"""Stack check skeleton: copy to <run>/stack/verify.py, fill in this app's calls, set
STACK_VERIFY="python3 <run>/stack/verify.py" in config.env. preflight.sh runs it before every launch.

Make the SAME calls the app makes, end to end, through the same addresses (the tunnel's localhost ports in
remote mode). Prove what the crowd needs, not that a process is up:
  1. health                                   4. the core thing users create goes live (store, page, room)
  2. sign-up gives a session (no email code)   5. what others reach of it answers (a public page, an invite link)
  3. feature flags really are on — read them   6. uploads: presign, PUT, public GET
     back from the API (an env value of "1"
     can read as false)
Each step prints PASS/FAIL; the last line is "<passed>/<total> passed". Exit 0 only when all pass.
examples/demo-run/verify.py in the crowd-qa repo is a worked example for the demo app.
"""
import json, sys, time, urllib.request, urllib.error

API = "http://localhost:3005"
results = []

def call(method, path, body=None, token=None, base=API):
    h = {"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})}
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            t = r.read().decode(errors="ignore"); return r.status, (json.loads(t) if t[:1] in "{[" else t)
    except urllib.error.HTTPError as e:
        t = e.read().decode(errors="ignore")
        try: return e.code, json.loads(t)
        except Exception: return e.code, t[:300]

def step(name, ok, detail=""):
    results.append(ok); print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}"); return ok

s, r = call("GET", "/health"); step("api health", s == 200, str(r)[:80])
# TODO: sign-up, flags read back, the core create flow, what others reach, uploads (see the docstring)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
