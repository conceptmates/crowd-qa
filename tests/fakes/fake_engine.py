#!/usr/bin/env python3
"""Stands in for `claude`, `codex`, `gh`, `git` and `agent-browser` in the tests (symlinked under those names).
No model is called and nothing leaves the machine. Every call is appended to $FAKE_LOG as one JSON line.

Model steps (prompts that start with "[crowd-qa step=... out=...]") write a canned answer to the out path.
Tester runs (prompts with "## Your assignment") write report.json and screenshots for today's scenarios, driven by
$RUN/fake-tester.json: {"<lane>": {"findings": [{"title", "severity", "shots": "ok|none|missing"}],
"budget_first": true, "limit_first": true, "sleep": 2, "die": true}}.
"""
import json
import os
import re
import subprocess
import sys
import time
import uuid

NAME = os.path.basename(sys.argv[0])
ARGS = sys.argv[1:]
LOG = os.environ.get("FAKE_LOG", "/tmp/fake-engine.jsonl")


def record(kind, **kw):
    with open(LOG, "a") as f:
        f.write(json.dumps({"tool": NAME, "kind": kind, "args": ARGS, "lane": os.environ.get("LANE"),
                            "day": os.environ.get("ROUND"), "t": time.time(), **kw}) + "\n")


def once(marker):
    """True the first time a marker is seen for this lane-day."""
    p = os.path.join(os.environ.get("OUT", "/tmp"), f".fake-{marker}")
    if os.path.exists(p):
        return False
    open(p, "w").close()
    return True


PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c6360000002000100e221bc330000000049454e44ae426082")


def tester(prompt):
    run, lane, day, out = os.environ["RUN"], os.environ["LANE"], int(os.environ["ROUND"]), os.environ["OUT"]
    conf = json.load(open(os.path.join(run, "fake-tester.json"))).get(lane, {}) if os.path.exists(
        os.path.join(run, "fake-tester.json")) else {}
    record("tester", engine=NAME, prompt_bytes=len(prompt))
    if conf.get("sleep"):
        time.sleep(conf["sleep"])
    if conf.get("die") and once("die"):
        # kill the runner: our parent is `timeout`, its parent is run-tester.sh
        runner = subprocess.run(["ps", "-o", "ppid=", "-p", str(os.getppid())], capture_output=True, text=True).stdout.strip()
        os.kill(int(runner), 9)
        sys.exit(9)
    if NAME == "codex" and conf.get("limit_first") and once("limit"):
        print("ERROR: You've hit your usage limit. Try again later.")
        sys.exit(1)
    m = re.search(r"^## Day \d+ scenarios\n(.*?)(?=^## (?!Day |Exhaustive)|\Z)", prompt, re.M | re.S)
    ids = re.findall(r"^### (S\d+)", m.group(1), re.M) if m else []
    status = conf.get("status", {})
    os.makedirs(os.path.join(out, "shots"), exist_ok=True)
    rp = os.path.join(out, "report.json")
    rep = json.load(open(rp)) if os.path.exists(rp) else {"character": lane, "day": day, "scenarios": [], "findings": []}
    for s in ids:
        open(os.path.join(out, "shots", f"{s}-end.png"), "wb").write(PNG)
    rep["scenarios"] = [{"id": s, "status": status.get(s, "pass"), "evidence": [f"shots/{s}-end.png"]} for s in ids]
    if conf.get("budget_first") and once("budget"):
        json.dump(rep, open(rp, "w"))
        print("Error: Exceeded USD budget (5)")
        sys.exit(1)
    rep["findings"] = []
    for i, f in enumerate(conf.get("findings", []), 1):
        shot = f"shots/F{i}-d{day}.png"
        if f.get("shots", "ok") == "ok":
            open(os.path.join(out, shot), "wb").write(PNG)
        rep["findings"].append({"id": f"F{i}", "title": f["title"], "type": "bug", "severity": f.get("severity", "medium"),
                                "route": f.get("route", "/inbox"), "voice": f"{lane} says it broke", "steps": ["open", "click"],
                                "expected": "works", "actual": "broke", "mechanism": f.get("mechanism", ""),
                                "screenshots": [] if f.get("shots") == "none" else [shot]})
    json.dump(rep, open(rp, "w"))
    print("Done. 2-line summary.")


def model_step(prompt):
    m = re.match(r"\[crowd-qa step=(\S+) out=(\S+)\]", prompt)
    step, out = m.group(1), m.group(2)
    model = ARGS[ARGS.index("--model") + 1] if "--model" in ARGS else ""
    record("step", step=step, model=model)
    if os.environ.get("FAKE_JUDGE_SLEEP") and step.startswith("judge:"):
        time.sleep(float(os.environ["FAKE_JUDGE_SLEEP"]))
    if step == "product-map":
        open(out, "w").write("# Product map\n" + "x" * 600 + "\n")
    elif step.startswith("plan:"):
        cid = step.split(":", 1)[1]
        if cid.startswith("noplan"):
            open(out, "w").write("I could not write this plan.\n")
        else:
            days = int(re.search(r"of (\d+) simulated days", prompt).group(1))
            body = "".join(f"## Day {d}\n" + "".join(f"### S{(d - 1) * 3 + i} — step {i}  [failure]\nSteps: x\n" for i in (1, 2, 3))
                           for d in range(1, days + 1))
            open(out, "w").write(body + "## Exhaustive sweep\n- every screen\n")
    elif step.startswith("judge:"):
        ids = re.search(r"Check only these findings: ([^.]*)\.", prompt).group(1).split(", ")
        pack = open(re.search(r"Read (\S+/judge-pack\.md)", prompt).group(1)).read()
        fs = []
        for i in ids:
            t = re.search(rf"^### {i}: (.*)$", pack, re.M).group(1)
            fs.append({"id": i, "verdict": "rejected" if "REJECTME" in t else "verified", "reason": "checked",
                       "title": t, "severity": "high" if "HIGH" in t else "medium", "repo": "app",
                       "mechanism": "src/app.js:10 confirmed" if "CAUSE" in t else "not confirmed", "voice": "it broke"})
        json.dump({"in_character": 80, "notes": "fine", "findings": fs}, open(out, "w"))
    elif step == "dedupe":
        json.dump({"same": []}, open(out, "w"))
    elif step.startswith("verify:"):
        title = re.search(r'"title": "([^"]*)"', prompt).group(1)
        if "REFUSE" in title:
            ans = {"verdict": "refuse", "reason": "evidence does not show it"}
        elif "REPAIR" in title and out.endswith("-1.json"):
            shot = re.search(r"look in (\S+/shots/)\)", prompt).group(1)
            ans = {"verdict": "repair", "reason": "wrong screenshot", "repair_screenshots": [os.path.join(shot, "S1-end.png")]}
        else:
            ans = {"verdict": "file", "reason": "holds up", "likely_cause": "src/app.js:10"}
        json.dump(ans, open(out, "w"))
    elif step == "report":
        open(out, "w").write("# Launch report\nVerdict: fine.\n")
    print(json.dumps({"total_cost_usd": 0.01, "num_turns": 2, "result": "DONE"}))


def main():
    if NAME == "agent-browser":
        return
    if NAME == "gh":
        record("gh")
        if ARGS[:2] == ["issue", "create"]:
            n = len([l for l in open(LOG) if '"create"' in l])
            body = open(ARGS[ARGS.index("--body-file") + 1]).read()
            record("gh-body", body=body, title=ARGS[ARGS.index("--title") + 1])
            print(f"https://github.com/owner/app/issues/{100 + n}")
        elif ARGS[:2] == ["issue", "list"]:
            print("[]")
        return
    if NAME == "git":
        record("git")
        if ARGS and ARGS[0] in ("clone",):
            sys.exit(1)
        if ARGS and ARGS[0] == "push":
            return
        real = next(p for p in ("/usr/bin/git", "/usr/local/bin/git", "/opt/homebrew/bin/git") if os.path.exists(p))
        os.execv(real, [real] + ARGS)
    if NAME == "codex":
        print(f"session id: {uuid.uuid4()}")
        if ARGS[:2] == ["exec", "resume"]:
            return tester(open(os.path.join(os.environ["OUT"], "prompt.md")).read())
        return tester(sys.stdin.read())
    # claude
    if "--version" in ARGS:
        print("fake-claude 1.0")
        return
    prompt = sys.stdin.read() if not sys.stdin.isatty() else ""
    if prompt.startswith("[crowd-qa step="):
        return model_step(prompt)
    if not prompt.strip():  # --resume with a message argument
        prompt = open(os.path.join(os.environ["OUT"], "prompt.md")).read()
    return tester(prompt)


main()
