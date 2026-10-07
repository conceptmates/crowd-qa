#!/usr/bin/env python3
"""The town square: one shared feed every character reads and writes, like MiroFish's simulated
platform. Testers call it from the shell during their round; the runner puts a digest in each prompt.

  square.py <run> post  <author> "<text>" [--shot <abs png>] [--screen <name>]
  square.py <run> reply <author> <post-id> me-too|cant-repro|disagree|tip "<text>" [--shot <abs png>]
  square.py <run> digest <reader> [--limit 40]     # what <reader> sees: newest first, own posts marked
  square.py <run> thread <post-id>
"""
import json, os, sys, time, fcntl, argparse

def feed_path(run): return os.path.join(run, "square", "feed.jsonl")

def load(run):
    p = feed_path(run)
    if not os.path.exists(p): return []
    with open(p) as f: return [json.loads(l) for l in f if l.strip()]

def append(run, entry):
    p = feed_path(run)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        n = sum(1 for l in f if l.strip())
        entry["id"] = f"P{n + 1}"
        entry["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        fcntl.flock(f, fcntl.LOCK_UN)
    print(entry["id"])

def english_only(text, run=None):
    """SQUARE_LANG=en in config.env makes the square English only: it refuses Devanagari, Tamil, Telugu,
    Malayalam, Bengali, Gujarati, Gurmukhi, Kannada, Odia and Arabic-script text. Anything else: no check."""
    import re
    lang = ""
    try:
        for line in open(os.path.join(run or "", "config.env")):
            if line.startswith("SQUARE_LANG="): lang = line.split("=", 1)[1].split("#")[0].strip().strip('"')
    except OSError: pass
    if lang == "en" and re.search(r"[\u0600-\u06ff\u0900-\u0dff]", text):
        sys.exit("refused: the square is English only. Write the post in English.")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run"); ap.add_argument("cmd")
    ap.add_argument("rest", nargs="*")
    ap.add_argument("--shot"); ap.add_argument("--screen"); ap.add_argument("--limit", type=int, default=40)
    a = ap.parse_args()
    if a.cmd == "post":
        author, text = a.rest[0], a.rest[1]
        english_only(text, a.run)
        append(a.run, {"kind": "post", "author": author, "text": text, "shot": a.shot, "screen": a.screen})
    elif a.cmd == "reply":
        author, ref, kind, text = a.rest[0], a.rest[1], a.rest[2], a.rest[3]
        english_only(text, a.run)
        if kind not in ("me-too", "cant-repro", "disagree", "tip"): sys.exit("kind must be me-too|cant-repro|disagree|tip")
        append(a.run, {"kind": kind, "author": author, "ref": ref, "text": text, "shot": a.shot})
    elif a.cmd == "digest":
        reader = a.rest[0]
        feed = load(a.run)
        replies = {}
        for e in feed:
            if e.get("ref"): replies.setdefault(e["ref"], []).append(e)
        posts = [e for e in feed if e["kind"] == "post"][::-1][: a.limit]
        if not posts: print("(the square is quiet so far)"); return
        for p in posts:
            mine = " (you)" if p["author"] == reader else ""
            print(f"- {p['id']} {p['author']}{mine} on {p.get('screen') or '?'}: {p['text']}")
            for r in replies.get(p["id"], []):
                print(f"    - {r['id']} {r['author']} [{r['kind']}]: {r['text']}")
    elif a.cmd == "thread":
        ref = a.rest[0]
        for e in load(a.run):
            if e["id"] == ref or e.get("ref") == ref: print(json.dumps(e, ensure_ascii=False))
    else:
        sys.exit(__doc__)

if __name__ == "__main__":
    main()
