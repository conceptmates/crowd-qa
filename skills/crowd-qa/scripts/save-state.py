#!/usr/bin/env python3
"""Saves every character's state to disk so the run survives a stopped Claude session, a crash or a reboot.

  save-state.py <run>            rebuild state/<id>.json + state/summary.json
  save-state.py <run> ledger <id> <day> <done line>     also append one line to state/ledger.jsonl

state/<id>.json: credentials, what the character made (world/), the businesses they registered for simulated
customers, each day's runner result and judge verdict summary, and where the diary and reports are.
state/summary.json: the `done` map (last day with a verdict, per character) that resume.sh feeds back into a launch.
"""
import json
import os
import re
import sys
import time

run = sys.argv[1]
S = os.path.join(run, "state")
os.makedirs(S, exist_ok=True)
now = time.strftime("%Y-%m-%dT%H:%M:%S")

if len(sys.argv) > 2 and sys.argv[2] == "ledger":
    lane, day, line = sys.argv[3], sys.argv[4], " ".join(sys.argv[5:])
    with open(os.path.join(S, "ledger.jsonl"), "a") as f:
        f.write(json.dumps({"at": now, "lane": lane, "day": int(day), "done": line}) + "\n")

# businesses owners registered for simulated customers (customer.py register)
businesses = {}
bp = os.path.join(run, "world", "businesses.json")
if os.path.exists(bp):
    try:
        businesses = json.load(open(bp))
    except ValueError:
        pass

world = {}
wp = os.path.join(run, "world", "stores.json")
if os.path.exists(wp):
    try:
        world = json.load(open(wp))
    except ValueError:
        pass

lanes = json.load(open(os.path.join(run, "lanes.json")))
done = {}
for l in lanes:
    cid = l["id"]
    ld = os.path.join(run, "lanes", cid)
    card = open(os.path.join(ld, "card.md")).read()
    m = re.search(r"^- \*\*Email\*\*: (\S+)", card, re.M)
    email = m.group(1).rstrip(",") if m else f"{cid}@crowd.test"
    days = {}
    for d in (1, 2, 3):
        rd = os.path.join(ld, f"round{d}")
        if not os.path.isdir(rd):
            continue
        day = {"dir": rd}
        if os.path.exists(os.path.join(rd, "DONE")):
            day["runner"] = open(os.path.join(rd, "DONE")).read().strip()
        vp = os.path.join(rd, "verdict.json")
        if os.path.exists(vp):
            try:
                v = json.load(open(vp))
                day["verdict"] = {"coverage": v.get("coverage_score"), "satisfied": v.get("satisfied"),
                                  "verified": len(v.get("verified_findings", []))}
                done[cid] = max(done.get(cid, 0), d)
            except ValueError:
                day["verdict"] = "unreadable"
        rp = os.path.join(rd, "report.json")
        if os.path.exists(rp):
            try:
                r = json.load(open(rp))
                sc = r.get("scenarios", [])
                day["report"] = {"scenarios": len(sc), "findings": len(r.get("findings", [])),
                                 "by_status": {k: sum(1 for s in sc if s.get("status") == k)
                                               for k in {s.get("status") for s in sc}}}
            except ValueError:
                pass
        days[str(d)] = day
    st = {
        "id": cid, "role": l["role"], "depends_on": l.get("depends_on", []),
        "email": email, "password": (re.search(r"password ([^\s,(]+)", card) or [None, ""])[1],
        "made": world.get(cid), "businesses": {k: v for k, v in businesses.items() if v.get("vars", {}).get("owner") in (cid, email)},
        "days": days, "diary": os.path.join(ld, "memory.md") if os.path.exists(os.path.join(ld, "memory.md")) else None,
        "saved": now,
    }
    tmp = os.path.join(S, f"{cid}.json.tmp")
    json.dump(st, open(tmp, "w"), indent=1)
    os.replace(tmp, os.path.join(S, f"{cid}.json"))

summary = {"saved": now, "done": done, "characters": len(lanes)}
tmp = os.path.join(S, "summary.json.tmp")
json.dump(summary, open(tmp, "w"), indent=1)
os.replace(tmp, os.path.join(S, "summary.json"))
print(f"state saved {now}: {len(lanes)} characters, done={done}")
