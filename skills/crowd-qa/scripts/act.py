#!/usr/bin/env python3
"""act.py: fast taps for crowd testers. Plain-word steps in, taps out, with no model call per tap.

  act.py "fill Email = grace@crowd.test" "fill Password = CrowdQA!2026" "tap Create account"
  act.py "tap @e12"                 # a ref from the last output works too
  act.py "scroll down" "tap Continue" "shot /abs/out/shots/S2-3.png"

Steps:  tap|press <words or @ref>   fill <field words> = <text>   type <text>   keyboard dismiss|enter
        scroll down|up|top|bottom   back   wait <ms> | wait text <words>   shot <abs png path>

How: one `agent-device snapshot -i`, then each step is matched to an element on screen by its words
(case-insensitive, punctuation ignored). A text field also matches the label line just above it, since
Flutter fields carry their placeholder ("you@yourbusiness.com") as their own label. A step must share a
word with the element it picks, or it stops. Consecutive fills and the tap that ends them go to the device
in ONE `agent-device batch` request; after a tap the screen is read again before the next step. On a
miss or a tie it stops and prints the candidates so the tester can pass a ref instead.

Ends by printing the screen's interactive elements (unless --quiet), so no extra snapshot call is needed.
Session and device come from the environment (AGENT_DEVICE_SESSION), as for agent-device itself.
"""
import json, re, subprocess, sys

AD = "agent-device"
LINE = re.compile(r'^\s*[-+=]?\s*(@e\d+(?:~s\d+)?)\s+\[([a-z-]+)\](?:\s+\[[a-z-]+\])*\s*(?:"(.*?)"?)?\s*$')
STOP = {"the", "a", "an", "to", "on", "in", "of", "my", "button", "field", "link", "tap", "press", "box", "your"}
TAPPABLE = {"button", "link", "other", "cell", "tab", "switch", "checkbox", "menu-item", "image", "text", "radio"}
FIELDS = {"text-field", "secure-text-field", "editable", "search-field", "text-view", "textbox"}

def run(args, timeout=120):
    p = subprocess.run([AD, *args], capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")

def words(s):
    toks = (t.strip(".-_+") for t in re.findall(r"[a-z0-9@.+_-]+", (s or "").lower()))
    return [w for w in toks if w and w not in STOP]

def snapshot():
    rc, out = run(["snapshot", "-i"])
    nodes = []
    for line in out.splitlines():
        m = LINE.match(line)
        if m: nodes.append({"ref": m.group(1), "role": m.group(2), "label": (m.group(3) or "").strip()})
    return rc, out, nodes

def score(req, node, above=""):
    rw = set(words(req))
    if not rw: return 0.0
    lw = set(words(node["label"])) | set(words(above))
    shared = rw & lw
    if not shared: return 0.0
    s = len(shared) / len(rw)
    if node["label"].lower().strip(" .") == req.lower().strip(" ."): s += 1.0
    return s

def resolve(kind, req, nodes):
    if req.startswith("@e"):
        return next((n for n in nodes if n["ref"] == req or n["ref"].split("~")[0] == req.split("~")[0]), {"ref": req, "role": "?", "label": req}), []
    cands = []
    for i, n in enumerate(nodes):
        if kind == "fill" and n["role"] not in FIELDS: continue
        if kind == "tap" and n["role"] not in TAPPABLE and n["role"] not in FIELDS: continue
        above = ""
        if kind == "fill":  # the nearest text line above the field is its visible label
            for j in range(i - 1, max(-1, i - 4), -1):
                if nodes[j]["role"] == "text": above = nodes[j]["label"]; break
        s = score(req, n, above)
        if s <= 0: continue
        if kind == "tap" and n["role"] in ("button", "link"): s += 0.15
        if kind == "tap" and n["role"] == "text": s -= 0.2
        cands.append((s, n))
    cands.sort(key=lambda x: -x[0])
    if not cands: return None, []
    if len(cands) > 1 and abs(cands[0][0] - cands[1][0]) < 1e-9 and cands[0][1]["label"] != cands[1][1]["label"]:
        return None, [c[1] for c in cands[:4]]
    return cands[0][1], [c[1] for c in cands[1:3]]

def parse(step):
    s = step.strip()
    head, _, rest = s.partition(" ")
    head = head.lower()
    if head in ("tap", "press", "click"): return ("tap", rest.strip(), None)
    if head == "fill":
        field, eq, text = rest.partition("=")
        if not eq: sys.exit(f"fill needs '=': {step}")
        return ("fill", field.strip(), text.strip())
    return (head, rest.strip(), None)

def show(n): return f'{n["ref"]} [{n["role"]}] "{n["label"][:70]}"'

def main():
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    quiet = "--quiet" in sys.argv
    if not argv: sys.exit(__doc__)
    steps = [parse(a) for a in argv]
    i, nodes, last_out = 0, None, ""
    while i < len(steps):
        kind, arg, text = steps[i]
        if kind in ("tap", "fill"):
            if nodes is None:
                rc, last_out, nodes = snapshot()
                if not nodes: print(f"FAIL snapshot returned no elements:\n{last_out[-800:]}"); sys.exit(1)
            batch, done = [], []
            while i < len(steps) and steps[i][0] in ("tap", "fill"):
                kind, arg, text = steps[i]
                node, alts = resolve(kind, arg, nodes)
                if node is None:
                    if batch: break   # run what is resolved, then read the screen again
                    print(f"STOP step {i + 1} '{argv[i]}': " + ("tie between " + "; ".join(show(a) for a in alts) if alts else "nothing on screen shares a word with it"))
                    print("on screen: " + "; ".join(show(n) for n in nodes if n["role"] in TAPPABLE | FIELDS)[:1500])
                    sys.exit(2)
                tgt = {"kind": "ref", "ref": node["ref"]}
                if kind == "fill": batch.append({"command": "fill", "input": {"target": tgt, "text": text}})
                else: batch.append({"command": "press", "input": {"target": tgt, "settle": True}})
                done.append(f"ok  {argv[i]}  ->  {show(node)}" + (f"   (next best: {show(alts[0])})" if alts else ""))
                i += 1
                if kind == "tap": break  # a tap can change the screen
            rc, out = run(["batch", "--steps", json.dumps(batch), "--on-error", "stop"])
            for d in done: print(d)
            if rc != 0: print(f"FAIL batch:\n{out[-1200:]}"); sys.exit(1)
            last_out, nodes = out, None   # refs are stale after a tap; re-read before the next match
        else:
            args = {"type": ["type", arg], "keyboard": ["keyboard", arg or "dismiss"], "scroll": ["scroll", arg or "down", "--settle"],
                    "back": ["back", "--settle"], "shot": ["screenshot", arg]}.get(kind)
            if kind == "wait": args = ["wait", *arg.split(" ", 1)] if arg.startswith("text") else ["wait", arg or "1000"]
            if args is None: sys.exit(f"unknown step: {argv[i]}")
            rc, out = run(args)
            print(("ok  " if rc == 0 else "FAIL ") + argv[i] + ("" if rc == 0 else "\n" + out[-600:]))
            if rc != 0: sys.exit(1)
            nodes = None
            i += 1
    if not quiet:
        rc, out, fin = snapshot()
        print("\nscreen now:")
        for n in fin[:60]:
            print("  " + show(n))

if __name__ == "__main__":
    main()
