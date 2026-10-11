#!/usr/bin/env python3
"""Stands in for `caam` (Coding Agent Account Manager) in the tests. The accounts live in the JSON file $FAKE_CAAM:
{"codex": {"active": "a", "profiles": {"a": {"p": "50", "w": "10", "st": "ok", "plan": "plus", "isolated": false}}}}
p / w are the 5-hour and weekly percentages ("-" = no reading). Every call is appended to $FAKE_LOG.
Supports: ls <tool> [--json], limits <tool>, activate <tool> <name>, profile ls <tool>, exec <tool> <name> -- args.
"""
import json
import os
import sys

ARGS = sys.argv[1:]
STATE = os.environ["FAKE_CAAM"]


def load():
    return json.load(open(STATE))


def record(**kw):
    with open(os.environ.get("FAKE_LOG", "/tmp/fake-engine.jsonl"), "a") as f:
        f.write(json.dumps({"tool": "caam", "kind": "caam", "args": ARGS, **kw}) + "\n")


def main():
    record()
    s = load()
    cmd = ARGS[0] if ARGS else ""
    if cmd == "ls":
        t = s.get(ARGS[1], {})
        if "--json" in ARGS:
            print(json.dumps({"profiles": [{"tool": ARGS[1], "name": n, "active": n == t.get("active"),
                                            "identity": {"plan_type": p.get("plan", "plus")}}
                                           for n, p in t.get("profiles", {}).items()]}))
            return
        print("PROFILE  EMAIL  PLAN  STATUS")
        for n, p in t.get("profiles", {}).items():
            print(f"{'●' if n == t.get('active') else ' '} {n}  {n}@example.test  {p.get('plan', 'plus')}  ok")
    elif cmd == "limits":
        t = s.get(ARGS[1], {})
        print("Rate Limit Usage")
        print("PROFILE  SCORE  PRIMARY  SECONDARY  RESETS IN  BURN/HR  DEPLETES  STATUS")
        for n, p in t.get("profiles", {}).items():
            pp, ww = p.get("p", "-"), p.get("w", "-")
            print(f"{ARGS[1]}/{n}  90  {pp}{'%' if pp != '-' else ''}  {ww}{'%' if ww != '-' else ''}  4h  -  -  {p.get('st', 'ok')}")
    elif cmd == "activate":
        s[ARGS[1]]["active"] = ARGS[2]
        json.dump(s, open(STATE, "w"))
    elif cmd == "profile" and ARGS[1:2] == ["ls"]:
        names = [n for n, p in s.get(ARGS[2], {}).get("profiles", {}).items() if p.get("isolated")]
        print("\n".join(names) if names else f"No isolated profiles for {ARGS[2]}")
    elif cmd == "exec":
        rest = ARGS[ARGS.index("--") + 1:] if "--" in ARGS else []
        os.execvpe(ARGS[1], [ARGS[1]] + rest, {**os.environ, "CAAM_PROFILE": ARGS[2]})
    else:
        sys.exit(f"fake caam: unsupported {ARGS}")


main()
