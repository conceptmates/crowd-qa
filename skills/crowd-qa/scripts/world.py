#!/usr/bin/env python3
"""Shared world state between characters: what each character made (a workspace, a page, a listing, a channel), so the people who depend on them can find it.

  world.py <run> register <id> --store "<name>" --url <link or id> [--product "<name>"]... [--note "<text>"]
  world.py <run> get <id>
  world.py <run> list
"""
import json, os, sys, fcntl, argparse

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run"); ap.add_argument("cmd"); ap.add_argument("who", nargs="?")
    ap.add_argument("--store"); ap.add_argument("--url"); ap.add_argument("--product", action="append", default=[])
    ap.add_argument("--note")
    a = ap.parse_args()
    p = os.path.join(a.run, "world", "stores.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if not os.path.exists(p): open(p, "w").write("{}")
    with open(p, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        w = json.load(f)
        if a.cmd == "register":
            e = w.get(a.who, {})
            if a.store: e["store"] = a.store
            if a.url: e["url"] = a.url
            if a.product: e["products"] = sorted(set(e.get("products", []) + a.product))
            if a.note: e.setdefault("notes", []).append(a.note)
            w[a.who] = e
            f.seek(0); f.truncate(); json.dump(w, f, indent=1, ensure_ascii=False)
            print(json.dumps(e, ensure_ascii=False))
        elif a.cmd == "get":
            print(json.dumps(w.get(a.who) or {}, ensure_ascii=False))
        elif a.cmd == "list":
            print(json.dumps(w, indent=1, ensure_ascii=False))
        else:
            sys.exit(__doc__)
        fcntl.flock(f, fcntl.LOCK_UN)

if __name__ == "__main__":
    main()
