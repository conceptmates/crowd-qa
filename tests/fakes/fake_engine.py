#!/usr/bin/env python3
"""Stands in for `claude`, `codex`, `gh`, `git` and `agent-browser` in the tests (symlinked under those names).
No model is called and nothing leaves the machine. Every call is appended to $FAKE_LOG as one JSON line.

Model steps (prompts that start with "[crowd-qa step=... out=...]") write a canned answer to the out path.
Tester runs (prompts with "## Your assignment") write report.json and screenshots for today's scenarios, driven by
$RUN/fake-tester.json: {"<lane>": {"findings": [{"title", "severity", "shots": "ok|none|missing"}],
"budget_first": true, "limit_first": true, "switch_first": true, "sleep": 2, "die": true}}.
Also `ssh` (prints $FAKE_SSH_OUT), and the one-word test calls of preflight and quota-guard: the active caam profile
(from $FAKE_CAAM, or CAAM_PROFILE under `caam exec`) refuses when "<profile>:<model>" is in $FAKE_CODEX_REFUSE, or
when the Claude profile is in $FAKE_CLAUDE_REFUSE.
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
    if conf.get("switch_first") and once("switch"):
        # quota-guard switched the account mid-day and stopped this tester (SIGTERM)
        open(os.path.join(run, ".engine-switched"), "w").close()
        sys.exit(143)
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


def model_step(prompt, resumed=False):
    m = re.match(r"\[crowd-qa step=(\S+) out=(\S+)\]", prompt)
    step, out = m.group(1), m.group(2)
    model = ARGS[ARGS.index("--model") + 1] if "--model" in ARGS else ""
    flag = os.path.join(os.path.dirname(LOG), f".fake-{step.replace(':', '_').replace('/', '_')}")
    if os.environ.get("FAKE_LIMIT_STEP") and step.startswith(os.environ["FAKE_LIMIT_STEP"]) and not os.path.exists(flag + "-limit"):
        open(flag + "-limit", "w").close()
        record("limited", step=step)
        print("API Error: 429 rate limit exceeded")
        sys.exit(1)
    if os.environ.get("FAKE_BAD_JSON_STEP") and step.startswith(os.environ["FAKE_BAD_JSON_STEP"]) and not resumed:
        record("step", step=step, model=model, bad=True)
        open(out, "w").write("not json")
        print(json.dumps({"total_cost_usd": 0.01, "num_turns": 2, "result": "DONE"}))
        return
    if os.environ.get("FAKE_JUDGE_FAIL") and step.startswith("judge:") and not os.path.exists(flag + "-fail2"):
        record("step", step=step, model=model, failed=True)
        open(flag + ("-fail2" if os.path.exists(flag + "-fail1") else "-fail1"), "w").close()
        print("I could not finish.")
        return
    record("step", step=step, model=model, dup_note="may repeat" in prompt)
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
    elif step.startswith("file-plan:"):
        bugs = json.loads(re.search(r"Bugs: (\[.*\])\nAnswer", prompt, re.S).group(1))
        plan = []
        for b in bugs:
            same = next((i for i in b["existing"] if i["title"].lower() == b["title"].lower()), None)
            noted = next((i for i in b["existing"] if any("intended" in c for c in i["comments"])), None)
            if noted:
                plan.append({"key": b["key"], "action": "skip", "target": noted["number"], "reason": "intended",
                             "quote": "This is intended."})
            elif same and same["state"] == "OPEN":
                plan.append({"key": b["key"], "action": "comment", "target": same["number"], "reason": "open", "quote": ""})
            elif same:
                plan.append({"key": b["key"], "action": "regression", "target": same["number"], "reason": "closed fixed",
                             "quote": ""})
            else:
                plan.append({"key": b["key"], "action": "create", "target": None, "reason": "new", "quote": ""})
        json.dump({"plan": plan}, open(out, "w"))
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
    elif step == "group":
        bugs = json.loads(re.search(r"Bugs: (\[.*\])\nAnswer", prompt, re.S).group(1))
        json.dump({"areas": [{"name": "Inbox flows", "tracker": bugs[0]["tracker"], "bugs": [b["key"] for b in bugs],
                              "files": ["src/app.js"]}]}, open(out, "w"))
    elif step.startswith("bout:"):
        if step.endswith(":judge"):
            json.dump({"winner": os.environ.get("FAKE_BOUT_WINNER", "b"), "scores": {"a": 40, "b": 70},
                       "reason": "the guard is there"}, open(out, "w"))
        else:
            json.dump({"text": "point " + step}, open(out, "w"))
    elif step == "report":
        open(out, "w").write("# Launch report\nVerdict: fine.\n")
    print(json.dumps({"total_cost_usd": 0.01, "num_turns": 2, "result": "DONE"}))


def job_agent(prompt):
    """A fix, review or re-check job (prompt from templates/*-brief.md): writes the result the brief asks for.
    $FAKE_REVIEW_CHANGES_ONCE: the first review asks for changes. $FAKE_REBUT: the fixer disputes that comment.
    $FAKE_RECHECK_FAIL_ONCE: the first re-check fails the first bug."""
    role = {"# Fix brief": "fix", "# Review brief": "review", "# Re-check brief": "recheck"}[prompt.split("\n", 1)[0].strip()]
    res = re.search(r"write `([^`]+result\.json)`", prompt, re.I).group(1)
    keys = re.findall(r"- #(\d+) `([0-9a-f]+)` reported by \*\*([^*]+)\*\*", prompt)
    flag = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(res))), ".fake-" + role)
    record("job", role=role, result=res, keys=[k for _, k, _ in keys], cwd=os.getcwd(), prompt=prompt)
    if role == "fix":
        rebut = os.environ.get("FAKE_REBUT") and "Review comments to address" in prompt
        ans = {"branch": re.search(r"Branch: `([^`]+)`", prompt).group(1), "worktree": os.getcwd(),
               "pr": "https://github.com/owner/app/pull/9", "fixed": [k for _, k, _ in keys],
               "changes": {k: f"changed {k}" for _, k, _ in keys}, "cause": {k: "src/app.js:10" for _, k, _ in keys},
               "evidence": {k: {"before": f"https://e/{n}-before.png?raw=true", "after": f"https://e/{n}-after.png?raw=true"}
                            for n, k, _ in keys},
               "retest": {k: "open the inbox and archive one" for _, k, _ in keys},
               "checks": {"cmd": "npm test", "ok": True, "tail": "all green"}, "needs_backend": [], "not_changed": [],
               "rebuttals": [{"comment": 0, "text": "the handler at src/app.js:12 already guards it"}] if rebut else []}
    elif role == "review":
        first = os.environ.get("FAKE_REVIEW_CHANGES_ONCE") and not os.path.exists(flag)
        open(flag, "w").close()
        ans = {"verdict": "changes" if first else "approve", "summary": "looked at the diff",
               "comments": [{"file": "src/app.js", "line": 12, "severity": "blocker", "bug": "", "text": "no guard"}] if first else []}
    else:
        first = os.environ.get("FAKE_RECHECK_FAIL_ONCE") and not os.path.exists(flag)
        open(flag, "w").close()
        ans = {"results": [{"key": k, "issue": int(n), "persona": who, "verdict": "FAIL" if first and i == 0 else "PASS",
                            "note": "still deletes" if first and i == 0 else "works now", "shot": f"https://e/{n}-recheck.png?raw=true",
                            "frontend_fixable": True, "comment": f"https://github.com/owner/app/issues/{n}#issuecomment-2"}
                           for i, (n, k, who) in enumerate(keys)]}
    json.dump(ans, open(res, "w"))
    print(json.dumps({"total_cost_usd": 0.02, "num_turns": 3, "result": "DONE"}))


JOB_HEADS = ("# Fix brief", "# Review brief", "# Re-check brief")


def active_profile(tool):
    if os.environ.get("CAAM_PROFILE"):
        return os.environ["CAAM_PROFILE"]
    try:
        return json.load(open(os.environ["FAKE_CAAM"]))[tool]["active"]
    except (KeyError, OSError, ValueError):
        return ""


def main():
    if NAME == "agent-browser":
        return
    if NAME == "ssh":
        record("ssh")
        print(os.environ.get("FAKE_SSH_OUT", ""))
        return
    if NAME == "codex" and "--version" in ARGS:
        print("fake-codex 1.0")
        return
    if NAME == "codex" and any("single word ok" in a for a in ARGS):
        model = ARGS[ARGS.index("-m") + 1] if "-m" in ARGS else "default"
        prof = active_profile("codex")
        record("probe", profile=prof, model=model)
        if f"{prof}:{model}" in os.environ.get("FAKE_CODEX_REFUSE", "").split(","):
            print(f"ERROR: The '{model}' model is not supported when using Codex with a ChatGPT account.")
            sys.exit(1)
        print("ok")
        return
    if NAME == "claude" and any("single word ok" in a for a in ARGS):
        prof = active_profile("claude")
        record("probe", profile=prof)
        if prof in os.environ.get("FAKE_CLAUDE_REFUSE", "").split(","):
            print("Claude AI usage limit reached")
            sys.exit(1)
        print("ok")
        return
    if NAME == "gh":
        record("gh")
        if ARGS[:2] == ["issue", "create"] and os.environ.get("FAKE_GH_FAIL_ONCE") and not os.path.exists(LOG + ".ghfail"):
            open(LOG + ".ghfail", "w").close()
            print("HTTP 502: server error", file=sys.stderr)
            sys.exit(1)
        if ARGS[:2] == ["issue", "list"] and any("comments" in a for a in ARGS):
            snap = json.load(open(os.environ["FAKE_GH_SNAPSHOT"])) if os.environ.get("FAKE_GH_SNAPSHOT") else []
            if os.environ.get("FAKE_GH_EXISTING"):
                snap.append({"number": 77, "title": os.environ["FAKE_GH_EXISTING"], "state": "OPEN", "body": "",
                             "url": "https://github.com/owner/app/issues/77", "labels": [], "comments": []})
            print(json.dumps(snap))
            return
        if ARGS[:2] == ["issue", "comment"]:
            record("gh-comment", target=ARGS[2], body=open(ARGS[ARGS.index("--body-file") + 1]).read())
            print(f"https://github.com/owner/app/issues/{ARGS[2]}#issuecomment-1")
            return
        if ARGS[:2] == ["issue", "view"] and any("closedByPullRequestsReferences" in a for a in ARGS):
            print(json.dumps({"closedAt": "2026-10-01T00:00:00Z", "closedByPullRequestsReferences": [{"number": 5}]}))
            return
        if ARGS[:2] == ["issue", "list"] and any("in:title" in a for a in ARGS) and os.environ.get("FAKE_GH_EXISTING"):
            print(json.dumps([{"number": 77, "title": os.environ["FAKE_GH_EXISTING"], "url": "https://github.com/owner/app/issues/77"}]))
            return
        if ARGS[:2] == ["issue", "create"]:
            n = len([l for l in open(LOG) if '"create"' in l])
            body = open(ARGS[ARGS.index("--body-file") + 1]).read()
            record("gh-body", body=body, title=ARGS[ARGS.index("--title") + 1])
            print(f"https://github.com/owner/app/issues/{100 + n}")
        elif ARGS[:2] == ["issue", "list"]:
            print("[]")
        elif ARGS[:2] == ["pr", "edit"] and "--body-file" in ARGS:
            record("gh-pr-edit", pr=ARGS[2], body=open(ARGS[ARGS.index("--body-file") + 1]).read())
        return
    if NAME == "git":
        record("git")
        if ARGS and ARGS[0] in ("clone",):
            sys.exit(1)
        if ARGS and ARGS[0] == "push":
            return
        real = next(p for p in ("/usr/bin/git", "/usr/local/bin/git", "/opt/homebrew/bin/git") if os.path.exists(p))
        os.execv(real, [real] + ARGS)
    if NAME == "codex" and ARGS[:1] == ["exec"] and not ARGS[1:2] == ["resume"]:
        prompt = sys.stdin.read()
        if prompt.startswith(JOB_HEADS):
            return job_agent(prompt)
        print(f"session id: {uuid.uuid4()}")
        return tester(prompt)
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
    sessions = os.path.join(os.path.dirname(LOG), ".fake-sessions")
    os.makedirs(sessions, exist_ok=True)
    if "--session-id" in ARGS and prompt.startswith("[crowd-qa step="):
        open(os.path.join(sessions, ARGS[ARGS.index("--session-id") + 1]), "w").write(prompt)
    if "--resume" in ARGS and not prompt.startswith("[crowd-qa step="):
        saved = os.path.join(sessions, ARGS[ARGS.index("--resume") + 1])
        if os.path.exists(saved):            # a model step asked again in the same session
            record("resume-step")
            return model_step(open(saved).read(), resumed=True)
    if prompt.startswith("[crowd-qa step="):
        return model_step(prompt)
    if prompt.startswith(JOB_HEADS):
        return job_agent(prompt)
    if not prompt.strip():  # --resume with a message argument
        prompt = open(os.path.join(os.environ["OUT"], "prompt.md")).read()
    return tester(prompt)


main()
