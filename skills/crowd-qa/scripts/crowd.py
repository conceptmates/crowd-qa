#!/usr/bin/env python3
"""crowd.py: drives a whole crowd-QA run in code. A model is called only where judgement is needed: the product
map, each character's plan, checking a day's findings, checking a bug before filing, and the launch report.
Everything else (scheduling, waiting, quota pauses, coverage, evidence checks, duplicates, merging, labels,
issue bodies, evidence push, filing) is plain code, so no agent spends tokens waiting on a tester.

usage:
  crowd.py <run> all        plan, run, dedupe, file, report (each step skips work already on disk)
  crowd.py <run> plan|run|dedupe|file|report
  crowd.py <run> status     one screen: every character-day, model spend so far

Run it detached so it survives the orchestrating session: nohup python3 <run>/scripts/crowd.py <run> all \\
  >> <run>/logs/crowd.out 2>&1 &
Everything it decides is on disk, so after a crash or a reboot the same command continues where it stopped.
Settings come from config.env (scripts/init-crowd.sh lists them).
"""
import concurrent.futures as cf
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

RUN = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else sys.exit(__doc__)
CMD = sys.argv[2] if len(sys.argv) > 2 else "all"
SCRIPTS = os.path.join(RUN, "scripts")
LOCK = threading.RLock()
SEV = {"critical": 4, "high": 3, "medium": 2, "low": 1}
STOP = set("the a an and or of to in on for with is it its this that be was are not no when after from at by as "
           "into than then but can cannot does doesn't don't only still shows show".split())


# ---------------------------------------------------------------------------------------------- config and io
def load_config():
    out = subprocess.run(["bash", "-c", f'set -a; . "{RUN}/config.env" >/dev/null 2>&1; env -0'],
                         capture_output=True).stdout.decode()
    return dict(kv.split("=", 1) for kv in out.split("\0") if "=" in kv)


C = load_config()


def cfg(key, default=""):
    v = C.get(key, "")
    return v if v != "" else default


def cfg_int(key, default):
    try:
        return int(float(cfg(key, default)))
    except ValueError:
        return default


DAYS = cfg_int("DAYS", cfg_int("MAX_ROUNDS", 2))
POLL = float(cfg("POLL_S", "20"))


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    os.makedirs(os.path.join(RUN, "logs"), exist_ok=True)
    with LOCK, open(os.path.join(RUN, "logs", "crowd.log"), "a") as f:
        f.write(line + "\n")


def jload(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def jsave(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


def read(path, default=""):
    try:
        with open(path, errors="replace") as f:
            return f.read()
    except OSError:
        return default


def characters():
    return jload(os.path.join(RUN, "lanes.json"), [])


def lane_dir(cid):
    return os.path.join(RUN, "lanes", cid)


def round_dir(cid, d):
    return os.path.join(RUN, "lanes", cid, f"round{d}")


def card_field(cid, name):
    m = re.search(rf"^- \*\*{re.escape(name)}\*\*: *(.+)$", read(os.path.join(lane_dir(cid), "card.md")), re.M)
    return m.group(1).strip() if m else ""


def words(text):
    return {w for w in re.findall(r"[a-z0-9']+", (text or "").lower()) if len(w) > 2 and w not in STOP}


def similar(a, b):
    wa, wb = words(a), words(b)
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


def filed_titles():
    out = []
    for line in read(os.path.join(RUN, "filed.txt")).splitlines():
        m = re.match(r"\s*(\S+)\s+(.*)", line)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


# ------------------------------------------------------------------------------------------------- the model
def llm(step, prompt, model, effort, out_path, required=(), text=False, timeout=5400):
    """One model call through the configured CLI. The model writes its answer to out_path; code checks it.
    Returns the parsed JSON (or the text when text=True), None after two failed attempts."""
    cli = cfg("LLM_CLI", "claude")
    lean = ["--setting-sources", "project", "--strict-mcp-config", "--disable-slash-commands"] \
        if cfg("CLAUDE_LEAN", "1") == "1" and cli == "claude" else []
    what = "the file" if text else "one JSON object" + (f" with the keys {', '.join(required)}" if required else "")
    body = (f"[crowd-qa step={step} out={out_path}]\n{prompt}\n\nWrite {what} to {out_path}"
            f"{'' if text else ', check that it parses'}, then reply DONE. Keep command output short (| head -c 4000).")
    for attempt in (1, 2):
        if os.path.exists(out_path) and not text:
            os.remove(out_path)
        cmd = [cli, "-p", "--dangerously-skip-permissions", "--output-format", "json", *lean]
        if model:
            cmd += ["--model", model]
        if effort:
            cmd += ["--effort", effort]
        t0 = time.time()
        try:
            r = subprocess.run(cmd, input=body, text=True, capture_output=True, cwd=RUN, timeout=timeout)
            meta = jload_str(r.stdout)
        except subprocess.TimeoutExpired:
            meta = {}
        with LOCK, open(os.path.join(RUN, "state", "costs.jsonl"), "a") as f:
            f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "step": step, "model": model or "default",
                                "usd": meta.get("total_cost_usd", 0), "turns": meta.get("num_turns"),
                                "s": round(time.time() - t0)}) + "\n")
        try:
            if text:
                if os.path.getsize(out_path) > 0:
                    return read(out_path)
                raise ValueError("empty file")
            data = jload(out_path)
            if not isinstance(data, dict):
                raise ValueError("no JSON object")
            missing = [k for k in required if k not in data]
            if missing:
                raise ValueError(f"missing keys {missing}")
            return data
        except (OSError, ValueError) as e:
            log(f"{step}: attempt {attempt} left no valid answer ({e})")
            body += f"\n\nYour previous attempt did not leave a valid answer at {out_path} ({e}). Write it now."
    return None


def jload_str(s):
    try:
        return json.loads(s)
    except ValueError:
        return {}


# ---------------------------------------------------------------------------------------------- 1. planning
def day_scenarios(cid, d):
    """Scenario ids planned for one day: the '### S<n>' headings under '## Day <d>'."""
    txt = read(os.path.join(lane_dir(cid), "scenarios.md"))
    m = re.search(rf"^## Day {d}\b.*?(?=^## |\Z)", txt, re.M | re.S)
    return re.findall(r"^### (S\d+)", m.group(0), re.M) if m else []


def plan_ok(cid):
    return all(day_scenarios(cid, d) for d in range(1, DAYS + 1))


def plan():
    os.makedirs(os.path.join(RUN, "state"), exist_ok=True)
    src = cfg("SOURCE_PATHS")
    pm = os.path.join(RUN, "product-map.md")
    if not (os.path.exists(pm) and os.path.getsize(pm) > 500):
        log("plan: writing the product map")
        llm("product-map", f"""Write the product map every scenario planner of this crowd QA run reads instead of the
source. Source: {src}. Run dir: {RUN} (context.md says what the product is and which surfaces the crowd uses).
For each surface in use (web screens, mobile screens, CLI commands, API endpoints): the routes or commands, the
visible labels of buttons, fields and tabs, what each role may do, plan or feature gates, limits and validation
rules, the server-side refusals a user can hit and their wording, and anything scheduled or asynchronous. Cite
file:line for rules. Group by area. Tables and lists, completeness over prose.""",
            cfg("PLANNER_MODEL", "opus"), cfg("MAP_EFFORT", "high"), pm, text=True)
    todo = [c for c in characters() if not plan_ok(c["id"])]
    skipped = set(jload(os.path.join(RUN, "state", "skipped.json"), []))
    cap = cfg_int("SCEN_MAX", 20)

    def one(c):
        cid = c["id"]
        sp = os.path.join(lane_dir(cid), "scenarios.md")
        note = cfg("RESEARCH_NOTES") or "Research on the web how this kind of person uses this kind of app and what they get wrong."
        prompt = f"""You design the scenarios for one character in a crowd QA run of {DAYS} simulated days.
Run dir: {RUN}. Read {lane_dir(cid)}/card.md (who they are, device, role, relationships, wants per day),
{RUN}/product-map.md and {RUN}/context.md, and use their labels and routes in steps. Do not explore the source:
at most 3 targeted lookups (rg -n -m5, or sed -n on 40 lines or less) for a label the map lacks. Intended
behaviour is not a bug: never design a scenario that expects it to be different. {note}
Write {sp} following {RUN}/templates/scenario-format.md, with one "## Day N" section per day (1..{DAYS}), each
scenario headed "### S<n> — <title>  [happy|failure|edge]", at most {cap} scenarios per day, then one
"## Exhaustive sweep" section. Day 1 is their first contact with the product; later days come back to what they
made and to what other characters did to them. At least 60% failure and edge paths, in this person's habits.
For a boundary tester, stay inside the product's own screens, commands and endpoints and describe each check
plainly. Do not open the product."""
        for attempt in (1, 2):
            llm(f"plan:{cid}", prompt, cfg("PLANNER_MODEL", "opus"), cfg("PLANNER_EFFORT", "medium"), sp, text=True)
            if plan_ok(cid):
                big = [d for d in range(1, DAYS + 1) if len(day_scenarios(cid, d)) > cap]
                if big:
                    log(f"plan:{cid}: days {big} have more than {cap} scenarios")
                return cid, True
            prompt += f"\n\nThe file is missing a '## Day N' section with '### S<n>' scenarios for some day. Rewrite {sp} complete."
        return cid, False

    with cf.ThreadPoolExecutor(cfg_int("DESIGN_PARALLEL", 5)) as ex:
        for cid, ok in ex.map(one, todo):
            if ok:
                skipped.discard(cid)
                log(f"plan:{cid}: {sum(len(day_scenarios(cid, d)) for d in range(1, DAYS + 1))} scenarios")
            else:
                skipped.add(cid)
                log(f"plan:{cid}: no usable plan after two attempts; character skipped (see logs/crowd.log)")
    jsave(os.path.join(RUN, "state", "skipped.json"), sorted(skipped))


# ------------------------------------------------------------------------------------------------ 2. running
def done_line(cid, d):
    return read(os.path.join(round_dir(cid, d), "DONE")).strip()


def runner_alive(cid, d):
    r = subprocess.run(["pgrep", "-f", f"run-tester.sh {RUN} {cid} {d}$"], capture_output=True)
    return r.returncode == 0


def coverage(cid, d, rep):
    """Coverage from the plan and the report, in code: ran = pass or fail, out of the scenarios planned that day."""
    planned = day_scenarios(cid, d)
    st = {s.get("id"): s.get("status") for s in rep.get("scenarios", [])}
    ran = [s for s in planned if st.get(s) in ("pass", "fail")]
    blocked = [s for s in planned if st.get(s) == "blocked"]
    missing = [s for s in planned if st.get(s) in (None, "not_run")]
    score = round(100 * len(ran) / len(planned)) if planned else 0
    return score, ran, blocked, missing


def precheck(cid, d, rep):
    """Rejections that need no model: no evidence, evidence files missing, duplicates of filed issues, repeats of
    this character's earlier days, and duplicates inside the report. Returns (to_check, rejected)."""
    rd = round_dir(cid, d)
    earlier = []
    for pd in range(1, d):
        earlier += [f.get("title", "") for f in jload(os.path.join(round_dir(cid, pd), "verdict.json"), {}).get("verified_findings", [])]
    filed = filed_titles()
    keep, rejected, seen = [], [], []
    for f in rep.get("findings", []):
        title = f.get("title", "")
        shots = [p if os.path.isabs(p) else os.path.join(rd, p) for p in f.get("screenshots", [])]
        real = [p for p in shots if os.path.isfile(p) and os.path.getsize(p) > 0]
        why = ""
        if not shots:
            why = "no evidence attached"
        elif not real:
            why = "evidence files missing or empty: " + ", ".join(os.path.basename(p) for p in shots[:3])
        else:
            dup = next((n for n, t in filed if similar(title, t) >= 0.6), None)
            rep_ = next((t for t in earlier if similar(title, t) >= 0.6), None)
            twin = next((t for t in seen if similar(title, t) >= 0.8), None)
            if dup:
                why = f"already filed as {dup}"
            elif rep_:
                why = f"repeat of an earlier day's verified bug: {rep_}"
            elif twin:
                why = f"duplicate of another finding in the same report: {twin}"
        if why:
            rejected.append({"id": f.get("id"), "title": title, "reason": why, "by": "code"})
        else:
            keep.append({**f, "screenshots": real})
            seen.append(title)
    return keep, rejected


def judge(cid, d):
    rd = round_dir(cid, d)
    vp = os.path.join(rd, "verdict.json")
    if os.path.exists(vp):
        return jload(vp)
    rep = jload(os.path.join(rd, "report.json"), {}) or {}
    score, ran, blocked, missing = coverage(cid, d, rep)
    keep, rejected = precheck(cid, d, rep)
    verified, in_char, notes = [], None, ""
    if keep:
        subprocess.run(["python3", os.path.join(SCRIPTS, "judge-pack.py"), RUN, cid, str(d)], capture_output=True)
        out = os.path.join(rd, "judge-answer.json")
        ids = ", ".join(str(f.get("id")) for f in keep)
        ans = llm(f"judge:{cid}:d{d}", f"""You check the findings of character "{cid}", day {d}, in a crowd QA run.
Read {rd}/judge-pack.md (scenarios, findings with downscaled screenshots, square posts, end of the tester log)
and {lane_dir(cid)}/card.md. Check only these findings: {ids}. Source: {cfg('SOURCE_PATHS')}.
For each: open its screenshots and decide whether they show the claim. Open the cited file:line (sed -n, 40 lines
or less) to confirm the cause; if none is cited, at most 3 targeted lookups, else mechanism = "not confirmed".
Reject: evidence that does not show the claim, speculation, intended behaviour (per {RUN}/context.md and the
project's docs), and stack faults (the test environment, not the product: see {RUN}/stack-faults.md and
{RUN}/logs/stack-verify.log; add a new one there as one line: symptom, cause, fix).
Answer: {{"in_character": 0-100 (how believably the tester played this person), "notes": "<one paragraph>",
"findings": [{{"id", "verdict": "verified|rejected", "reason", "title": "<specific user-visible symptom, no
character names>", "severity": "critical|high|medium|low", "repo": "app|backend", "mechanism": "<file:line and
why, or not confirmed>", "voice": "<the tester's in-character line, one or two plain sentences>",
"confirmers": [{{"id", "line", "screenshot": "<absolute path you opened>"}}], "cant_repro": ["..."]}}]}}""",
                  cfg("JUDGE_MODEL", "opus"), cfg("JUDGE_EFFORT", "medium"), out, ("findings",))
        byid = {str(f.get("id")): f for f in keep}
        if ans is None:
            rejected += [{"id": f.get("id"), "title": f.get("title"), "reason": "judge failed twice", "by": "code"} for f in keep]
        else:
            in_char, notes = ans.get("in_character"), ans.get("notes", "")
            answered = set()
            for a in ans.get("findings", []):
                f = byid.get(str(a.get("id")))
                if not f:
                    continue
                answered.add(str(a.get("id")))
                if a.get("verdict") != "verified":
                    rejected.append({"id": f.get("id"), "title": f.get("title"), "reason": a.get("reason", ""), "by": "judge"})
                    continue
                verified.append({
                    "title": a.get("title") or f.get("title"), "type": f.get("type", "bug"),
                    "severity": a.get("severity") or f.get("severity", "medium"), "route": f.get("route", ""),
                    "viewport": f.get("viewport", ""), "steps": f.get("steps", []), "expected": f.get("expected", ""),
                    "actual": f.get("actual", ""), "mechanism": a.get("mechanism") or "not confirmed",
                    "console_errors": f.get("console_errors", []), "screenshots": f["screenshots"],
                    "repo": a.get("repo") or "app", "reporter": cid, "voice": a.get("voice") or f.get("voice", ""),
                    "day": d, "confirmers": a.get("confirmers", []), "cant_repro": a.get("cant_repro", []),
                })
            for k, f in byid.items():
                if k not in answered:
                    rejected.append({"id": f.get("id"), "title": f.get("title"), "reason": "judge gave no verdict", "by": "code"})
    sat = score >= cfg_int("SATISFIED_PCT", 90)
    v = {"satisfied": sat, "coverage_score": score, "in_character": in_char, "verified_findings": verified,
         "rejected": rejected, "scenarios": {"ran": ran, "blocked": blocked, "missing": missing},
         "reason": f"{len(ran)} of {len(day_scenarios(cid, d))} planned scenarios ran; {len(blocked)} blocked, "
                   f"{len(missing)} not run. {len(verified)} verified, {len(rejected)} rejected. {notes}".strip()}
    fb = ["satisfied"] if sat else [f"{i}. Run {s} (it was {'blocked' if s in blocked else 'not run'})."
                                    for i, s in enumerate(blocked + missing, 1)]
    with open(os.path.join(lane_dir(cid), f"feedback-r{d}.md"), "w") as f:
        f.write("\n".join(fb) + "\n")
    jsave(vp, v)
    log(f"judge:{cid}:d{d}: coverage {score}, {len(verified)} verified, {len(rejected)} rejected")
    return v


def engine_of(cid):
    """The engine a character's day starts on: its card's Engine line, else TESTER."""
    return (card_field(cid, "Engine").split() or [cfg("TESTER", "claude")])[0].lower()


def paused(engine):
    """True while QUOTA_PAUSE.<engine> is younger than RETRY_PRIMARY_MIN; an older one is lifted here."""
    qp = os.path.join(RUN, f"QUOTA_PAUSE.{engine}")
    if not os.path.exists(qp):
        return False
    if time.time() - os.path.getmtime(qp) < cfg_int("RETRY_PRIMARY_MIN", 60) * 60:
        return True
    os.replace(qp, os.path.join(RUN, f"QUOTA_PAUSE.{engine}.lifted-{time.strftime('%H%M%S')}"))
    tf = os.path.join(RUN, "TESTER_FALLBACK")
    if f"from={engine} " in read(tf):
        os.remove(tf)
    log(f"quota pause on {engine} lifted: its characters are tried again")
    return False


def run():
    os.makedirs(os.path.join(RUN, "state"), exist_ok=True)
    chars = characters()
    ids = {c["id"] for c in chars}
    skipped = set(jload(os.path.join(RUN, "state", "skipped.json"), []))
    skipped |= {c["id"] for c in chars if not plan_ok(c["id"])}
    st, procs, waits = {}, {}, {}
    for c in chars:
        for d in range(1, DAYS + 1):
            k = (c["id"], d)
            dl = done_line(*k)
            if c["id"] in skipped:
                st[k] = "skipped"
            elif os.path.exists(os.path.join(round_dir(*k), "verdict.json")):
                st[k] = "judged"
            elif dl and "quota" not in dl:
                st[k] = "ran"
            elif runner_alive(*k):
                st[k] = "running"          # a runner from before a restart: wait for it, never start a second
            else:
                if dl:
                    os.replace(os.path.join(round_dir(*k), "DONE"), os.path.join(round_dir(*k), "DONE.stale-quota"))
                st[k] = "pending"
    deps = {c["id"]: [p for p in c.get("depends_on", []) if p in ids] for c in chars}
    cap = cfg_int("HARD_CAP", 4)
    judges = cf.ThreadPoolExecutor(cfg_int("JUDGE_PARALLEL", 4))
    futs = {}
    log(f"run: {len(chars)} characters x {DAYS} days, {sum(v == 'pending' for v in st.values())} character-days to run")
    finished = ("judged", "skipped", "failed")
    last_down_note = 0
    while not all(v in finished for v in st.values()):
        # finished runners: DONE written, or the process died without one
        for k in [k for k, v in st.items() if v == "running"]:
            dl = done_line(*k)
            p = procs.get(k)
            if not dl and ((p and p.poll() is not None) or (not p and not runner_alive(*k))):
                time.sleep(2)
                dl = done_line(*k)
                if not dl:
                    dl = "exit=1 rc=runner-died"
                    with open(os.path.join(round_dir(*k), "DONE"), "w") as f:
                        f.write(dl + "\n")
            if not dl:
                continue
            if "quota" in dl:
                waits[k] = waits.get(k, 0) + 1
                os.replace(os.path.join(round_dir(*k), "DONE"), os.path.join(round_dir(*k), "DONE.stale-quota"))
                st[k] = "pending" if waits[k] <= cfg_int("MAX_QUOTA_WAITS", 8) else "failed"
                log(f"{k[0]} d{k[1]}: stopped at a usage limit (wait {waits[k]})")
            else:
                st[k] = "ran"
                log(f"{k[0]} d{k[1]}: tester finished: {dl}")
        # judges run beside the testers and never hold a slot
        for k in [k for k, v in st.items() if v == "ran"]:
            st[k] = "judging"
            futs[k] = judges.submit(judge, *k)
        for k in [k for k, f in futs.items() if f.done()]:
            try:
                futs.pop(k).result()
                st[k] = "judged"
            except Exception as e:  # a judge crash must not stop the crowd
                log(f"judge {k}: {e}")
                st[k] = "judged"
        down = os.path.exists(os.path.join(RUN, "STACK_DOWN"))
        if down and time.time() - last_down_note > 1800:
            log(f"STACK_DOWN ({read(os.path.join(RUN, 'STACK_DOWN')).strip()}): no character starts until the health checks pass")
            last_down_note = time.time()
        elif not down:
            last_down_note = 0
        running = sum(v == "running" for v in st.values())
        for c in chars:
            for d in range(1, DAYS + 1):
                k = (c["id"], d)
                if st[k] != "pending" or running >= cap or paused(engine_of(c["id"])):
                    continue
                if d > 1 and st[(c["id"], d - 1)] not in finished:
                    continue        # tomorrow starts once yesterday is judged: its prompt carries the verdict
                if any(st[(p, d)] not in ("ran", "judging") + finished for p in deps[c["id"]]):
                    continue        # a dependent waits for its provider's run that day, not for its judge
                rd = round_dir(*k)
                os.makedirs(rd, exist_ok=True)
                procs[k] = subprocess.Popen(["bash", os.path.join(SCRIPTS, "run-tester.sh"), RUN, c["id"], str(d)],
                                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=open(os.path.join(rd, "runner.err"), "a"),
                                            start_new_session=True)
                st[k] = "running"
                running += 1
                log(f"{c['id']} d{d}: started")
        jsave(os.path.join(RUN, "state", "crowd-state.json"), {f"{a}:{b}": v for (a, b), v in st.items()})
        time.sleep(POLL)
    judges.shutdown(wait=True)
    jsave(os.path.join(RUN, "state", "crowd-state.json"), {f"{a}:{b}": v for (a, b), v in st.items()})
    subprocess.run(["python3", os.path.join(SCRIPTS, "save-state.py"), RUN], capture_output=True)
    log("run: every character-day is judged, skipped or failed")


# ------------------------------------------------------------------------------------------------- 3. dedupe
def dedupe():
    found = []
    for f in glob.glob(os.path.join(RUN, "archive", "*", "prior_findings.json")):
        found += jload(f, [])
    for c in characters():
        for d in range(1, DAYS + 1):
            found += jload(os.path.join(round_dir(c["id"], d), "verdict.json"), {}).get("verified_findings", [])
    roles = {c["id"]: c.get("role", "") for c in characters()}
    n = len(found)
    parent = list(range(n))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    ambiguous = []
    for i in range(n):
        for j in range(i + 1, n):
            s = similar(found[i]["title"], found[j]["title"])
            same_route = (found[i].get("route") or "") == (found[j].get("route") or "") != ""
            if s >= 0.6 or (s >= 0.45 and same_route):
                parent[root(j)] = root(i)
            elif s >= 0.35:
                ambiguous.append((i, j))   # the same bug seen on another surface (web and API) has another route
    if ambiguous and cfg("DEDUPE_LLM", "1") == "1":
        out = os.path.join(RUN, "state", "dedupe-answer.json")
        pairs = [{"pair": k, "a": found[i]["title"], "b": found[j]["title"], "route": found[i].get("route"),
                  "a_actual": found[i].get("actual", "")[:300], "b_actual": found[j].get("actual", "")[:300]}
                 for k, (i, j) in enumerate(ambiguous[:200])]
        ans = llm("dedupe", "These pairs of verified crowd-QA findings look alike. For each, say whether they are the "
                  "same bug: the same root cause, even when one was seen in the web app and the other through the API "
                  "or the CLI, or the same symptom on the same screen. Answer "
                  '{"same": [<pair numbers that are the same bug>]}.\n' + json.dumps(pairs, indent=1),
                  cfg("JUDGE_MODEL", "opus"), "low", out, ("same",))
        for k in (ans or {}).get("same", []):
            if isinstance(k, int) and 0 <= k < len(ambiguous):
                i, j = ambiguous[k]
                parent[root(j)] = root(i)
    groups = {}
    for i in range(n):
        groups.setdefault(root(i), []).append(found[i])
    filed = filed_titles()
    issues, dropped = [], 0
    for g in groups.values():
        g.sort(key=lambda f: (f.get("day", 99), -SEV.get(f.get("severity"), 0)))
        p = dict(g[0])
        if any(similar(p["title"], t) >= 0.6 for _, t in filed):
            dropped += 1
            continue
        conf = list(p.get("confirmers", []))
        for o in g[1:]:
            if o.get("reporter") != p.get("reporter") and all(c.get("id") != o.get("reporter") for c in conf):
                conf.append({"id": o.get("reporter"), "line": o.get("voice", ""),
                             "screenshot": (o.get("screenshots") or [""])[0]})
            conf += [c for c in o.get("confirmers", []) if all(c.get("id") != x.get("id") for x in conf)]
        conf = [c for c in conf if c.get("id") != p.get("reporter")]
        p["confirmers"] = conf
        p["severity"] = max((f.get("severity", "low") for f in g), key=lambda s: SEV.get(s, 0))
        p["screenshots"] = list(dict.fromkeys(s for f in g for s in f.get("screenshots", [])))
        p["cant_repro"] = list(dict.fromkeys(x for f in g for x in f.get("cant_repro", [])))
        labels = [p.get("type", "bug"), f"severity:{p['severity']}", cfg("RUN_LABEL", "crowd-qa"),
                  f"persona:{p.get('reporter')}"] + [f"persona:{c['id']}" for c in conf]
        if roles.get(p.get("reporter")):
            labels.append(f"role:{roles[p['reporter']]}")
        p["labels"] = list(dict.fromkeys(labels))
        p["key"] = hashlib.sha1(p["title"].encode()).hexdigest()[:12]
        issues.append(p)
    issues.sort(key=lambda f: (-SEV.get(f["severity"], 0), -len(f["confirmers"])))
    jsave(os.path.join(RUN, "state", "issues.json"), issues)
    log(f"dedupe: {n} verified findings -> {len(issues)} issues ({dropped} already filed)")
    return issues


# --------------------------------------------------------------------------------------------------- 4. filing
def trackers():
    out = {}
    for item in cfg("TRACKERS").split():
        if "=" in item:
            k, v = item.split("=", 1)
            out[k] = v
    return out


def sh(cmd, cwd=None, check=False):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"{' '.join(cmd[:4])}: {r.stderr.strip()[:300]}")
    return r


def push_evidence(key, repo, issues):
    """Copies each issue's evidence to the orphan evidence branch and pushes once. Returns {issue key: [urls]}."""
    branch, label = cfg("EVIDENCE_BRANCH", "qa-evidence"), cfg("RUN_LABEL", "crowd-qa")
    d = os.path.join(RUN, f"evidence-{key}")
    if not os.path.isdir(os.path.join(d, ".git")):
        r = sh(["git", "clone", "-q", "--single-branch", "-b", branch, f"https://github.com/{repo}.git", d])
        if r.returncode:
            shutil.rmtree(d, ignore_errors=True)
            os.makedirs(d)
            sh(["git", "init", "-q"], d, True)
            sh(["git", "checkout", "-q", "--orphan", branch], d, True)
            with open(os.path.join(d, "README.md"), "w") as f:
                f.write("Evidence for crowd-QA issues.\n")
            sh(["git", "add", "README.md"], d, True)
            sh(["git", "commit", "-q", "-m", "qa-evidence"], d, True)
            sh(["git", "remote", "add", "origin", f"https://github.com/{repo}.git"], d, True)
    urls = {}
    for x in issues:
        rel = os.path.join(label, f"issue-{x['key']}")
        os.makedirs(os.path.join(d, rel), exist_ok=True)
        files = [(x.get("reporter"), s) for s in x.get("screenshots", [])] + \
                [(c.get("id"), c.get("screenshot")) for c in x.get("confirmers", []) if c.get("screenshot")]
        urls[x["key"]] = []
        for who, src in files:
            if not (src and os.path.isfile(src)):
                continue
            name = f"{who}-{os.path.basename(src)}"
            shutil.copy(src, os.path.join(d, rel, name))
            urls[x["key"]].append((src, f"https://github.com/{repo}/blob/{branch}/{rel}/{name}?raw=true"))
    sh(["git", "add", label], d, True)
    if sh(["git", "diff", "--cached", "--quiet"], d).returncode:
        sh(["git", "commit", "-q", "-m", f"qa-evidence: {label}"], d, True)
    sh(["git", "push", "-q", "-u", "origin", branch], d, True)
    return urls


def render_body(x, urls):
    cid = x.get("reporter", "")
    who = card_field(cid, "Name") or cid
    bits = [card_field(cid, k) for k in ("Age", "Business", "City")]
    dev = card_field(cid, "Device")
    head = f"> \"{x.get('voice', '').strip()}\"\n> **{who}**" + "".join(f", {b}" for b in bits if b) + (f" · {dev}" if dev else "")
    by_src = dict(urls)
    lines = [head, "", "**Where**", "", f"- Screen: `{x.get('route', '')}`"]
    if x.get("viewport"):
        lines.append(f"- Device: {x['viewport']}")
    lines += [f"- Day: {x.get('day', 1)} of the crowd run", "", "**Steps to reproduce**", ""]
    lines += [f"{i}. {s}" for i, s in enumerate(x.get("steps", []), 1)]
    lines += ["", "**Expected**", "", x.get("expected", ""), "", "**Actual**", "", x.get("actual", "")]
    for c in x.get("cant_repro", []):
        lines.append(f"\n{c}")
    lines += ["", "**Likely cause**", "", x.get("likely_cause") or x.get("mechanism") or "not confirmed"]
    if x.get("confirmers"):
        lines += ["", "**Also hit by**", ""]
        for c in x["confirmers"]:
            name = card_field(c.get("id", ""), "Name") or c.get("id")
            shot = by_src.get(c.get("screenshot"))
            lines.append(f"- **{name}**: \"{c.get('line', '')}\"" + (f" ([screenshot]({shot}))" if shot else ""))
    lines += ["", "**Evidence**", ""]
    lines += [f"![]({u})" for s, u in urls if s in x.get("screenshots", [])] or [f"`{s}`" for s in x.get("screenshots", [])]
    lines += ["", "---", f"Found in the {cfg('RUN_DATE', time.strftime('%Y-%m-%d'))} crowd run: {len(characters())} simulated "
              f"users over {DAYS} day(s). Tested by {cfg('TESTER_LABEL', 'the configured tester')}; evidence and source "
              f"re-checked before filing."]
    return "\n".join(lines) + "\n"


def verify(x, repo, attempt=1):
    """The model checks one merged bug before filing. High severity and unconfirmed causes go to ESCALATE_MODEL."""
    hard = SEV.get(x.get("severity"), 0) >= 3 or "not confirmed" in (x.get("mechanism") or "not confirmed")
    model = cfg("ESCALATE_MODEL", "opus") if hard else cfg("VERIFY_MODEL", "opus")
    effort = cfg("ESCALATE_EFFORT", "medium") if hard else cfg("VERIFY_EFFORT", "medium")
    cands = []
    if repo and not cfg("FILE_CMD"):
        q = " ".join(sorted(words(x["title"]), key=len, reverse=True)[:5])
        r = sh(["gh", "issue", "list", "-R", repo, "--state", "all", "--search", q, "--limit", "10", "--json", "number,title"])
        cands = jload_str(r.stdout) or []
    out = os.path.join(RUN, "state", f"verify-{x['key']}-{attempt}.json")
    return llm(f"verify:{x['key']}", f"""You check one crowd-QA bug before it is filed. Run dir: {RUN}. Source: {cfg('SOURCE_PATHS')}.
Bug: {json.dumps({k: x.get(k) for k in ('title', 'severity', 'route', 'steps', 'expected', 'actual', 'mechanism', 'screenshots', 'confirmers', 'reporter')}, indent=1)}
1. Evidence: open the screenshots (and the confirmers') and confirm they show what the bug claims. If they do not
   but another screenshot from the same scenario does (look in {RUN}/lanes/{x.get('reporter')}/round{x.get('day', 1)}/shots/),
   answer verdict "repair" with those absolute paths in repair_screenshots.
2. Source: open the cited file:line at the current checkout and confirm the cause is still there. If nothing is
   cited, find the code that handles this behaviour and name it, or say "not confirmed".
3. Rule out intended behaviour ({RUN}/context.md and the project's docs), a stack fault ({RUN}/stack-faults.md),
   and an existing issue. Possible existing issues: {json.dumps(cands)}.
Answer {{"verdict": "file|refuse|repair", "reason": "<one sentence>", "likely_cause": "<file:line and why, or not
confirmed>", "duplicate_of": "<issue number or empty>", "repair_screenshots": []}}.""",
               model, effort, out, ("verdict", "reason"))


def file_issues():
    issues = jload(os.path.join(RUN, "state", "issues.json")) or dedupe()
    tr = trackers()
    hook = cfg("FILE_CMD")
    done = jload(os.path.join(RUN, "state", "filed.json"), {})
    refused = jload(os.path.join(RUN, "state", "refused.json"), {})
    if not tr and not hook:
        log("file: no TRACKERS and no FILE_CMD; nothing filed")
        return
    keys = list(tr) or ["app"]
    todo = [x for x in issues if x["key"] not in done and x["key"] not in refused]
    for x in todo:
        x["tracker"] = x.get("repo") if x.get("repo") in tr else keys[0]
    for key in keys:
        mine = [x for x in todo if x["tracker"] == key]
        if not mine:
            continue
        repo = tr.get(key, key)
        urls = {}
        if not hook:
            for lab in sorted({lab for x in mine for lab in x["labels"]}):
                sh(["gh", "label", "create", "--force", "-R", repo, lab])
            urls = push_evidence(key, repo, mine)

        def one(x):
            v = verify(x, repo)
            if v and v.get("verdict") == "repair":
                fix = [p for p in v.get("repair_screenshots", []) if os.path.isfile(p)]
                if fix:
                    x["screenshots"] = fix
                    if not hook:
                        with LOCK:
                            urls.update(push_evidence(key, repo, [x]))
                    v = verify(x, repo, 2)
                    if v and v.get("verdict") == "repair":
                        v = {"verdict": "refuse", "reason": "evidence still does not show the claim after repair"}
            if not v or v.get("verdict") != "file":
                return x, None, (v or {}).get("reason", "verifier failed")
            x["likely_cause"] = v.get("likely_cause")
            body = render_body(x, urls.get(x["key"], []))
            bf = os.path.join(RUN, "state", f"issue-{x['key']}.md")
            with open(bf, "w") as f:
                f.write(body)
            if hook:
                jf = os.path.join(RUN, "state", f"issue-{x['key']}.json")
                jsave(jf, {"title": x["title"], "body": body, "labels": x["labels"], "repo": key, "screenshots": x["screenshots"]})
                r = sh(["bash", "-c", f'{hook} "$1"', "_", jf])
                url = (r.stdout.strip().splitlines() or [""])[-1]
            else:
                r = sh(["gh", "issue", "create", "-R", repo, "--title", x["title"], "--body-file", bf,
                        *sum((["--label", lab] for lab in x["labels"]), [])])
                url = next((w for w in r.stdout.split() if "/issues/" in w), "")
                if url and sh(["gh", "issue", "view", url, "--json", "number"]).returncode:
                    url = ""
            if not url:
                return x, None, f"filing command returned no URL: {r.stderr.strip()[:200]}"
            return x, url, ""

        with cf.ThreadPoolExecutor(cfg_int("FILE_PARALLEL", 6)) as ex:
            for x, url, why in ex.map(one, mine):
                with LOCK:
                    if url:
                        done[x["key"]] = {"url": url, "title": x["title"], "tracker": key}
                        num = re.search(r"/issues/(\d+)", url)
                        with open(os.path.join(RUN, "filed.txt"), "a") as f:
                            f.write(f"{'#' + num.group(1) if num else url} {x['title']}\n")
                        log(f"filed {url} {x['title']}")
                    else:
                        refused[x["key"]] = {"title": x["title"], "reason": why}
                        log(f"not filed: {x['title']} ({why})")
                    jsave(os.path.join(RUN, "state", "filed.json"), done)
                    jsave(os.path.join(RUN, "state", "refused.json"), refused)
    log(f"file: {len(done)} filed, {len(refused)} refused")


# --------------------------------------------------------------------------------------------------- 5. report
def report():
    out = os.path.join(RUN, "LAUNCH_REPORT.md")
    pack = [f"# Report pack ({len(characters())} characters, {DAYS} days)", "", "## Coverage by character",
            "| id | role | day | ran | blocked | not run | verified | satisfied |", "|---|---|---|---|---|---|---|---|"]
    never = []
    for c in characters():
        any_day = False
        for d in range(1, DAYS + 1):
            v = jload(os.path.join(round_dir(c["id"], d), "verdict.json"))
            if not v:
                continue
            any_day = True
            s = v.get("scenarios", {})
            pack.append(f"| {c['id']} | {c.get('role', '')} | {d} | {len(s.get('ran', []))} | {len(s.get('blocked', []))} | "
                        f"{len(s.get('missing', []))} | {len(v.get('verified_findings', []))} | {v.get('satisfied')} |")
        if not any_day:
            never.append(c["id"])
    pack += ["", f"Never tested: {', '.join(never) or 'nobody'}", "", "## Filed",
             *[f"- {v['url']} {v['title']}" for v in jload(os.path.join(RUN, "state", "filed.json"), {}).values()],
             "", "## Refused at filing",
             *[f"- {v['title']}: {v['reason']}" for v in jload(os.path.join(RUN, "state", "refused.json"), {}).values()]]
    pp = os.path.join(RUN, "state", "report-pack.md")
    with open(pp, "w") as f:
        f.write("\n".join(pack) + "\n")
    llm("report", f"""Write the launch-readiness report for this crowd QA run, grounded only in evidence.
Read {pp} (coverage, what was filed and refused, who was never tested), every {RUN}/lanes/*/card.md and memory.md,
and the square (python3 {SCRIPTS}/square.py {RUN} digest nobody --limit 300).
Sections: a verdict line (would these {len(characters())} people keep using the product after launch week, and
why); one short section per group of people (by role and need), with who got what they wanted, who gave up and
where, and the issues that blocked them (linked); the three things to fix before launch, by how many people they
hurt; what nobody could test and why. Quote characters sparingly. Plain prose, no hype words, no closing summary.""",
        cfg("REPORT_MODEL", "opus"), cfg("REPORT_EFFORT", "medium"), out, text=True)
    log(f"report: {out}")


# --------------------------------------------------------------------------------------------------- status
def status():
    st = jload(os.path.join(RUN, "state", "crowd-state.json"), {})
    counts = {}
    for v in st.values():
        counts[v] = counts.get(v, 0) + 1
    usd = 0.0
    for line in read(os.path.join(RUN, "state", "costs.jsonl")).splitlines():
        usd += float(jload_str(line).get("usd") or 0)
    print(f"character-days: {counts or '(not started)'}; model spend outside testers: ${usd:.2f}")
    for k in ["STACK_DOWN", "TESTER_FALLBACK"] + [os.path.basename(p) for p in glob.glob(os.path.join(RUN, "QUOTA_PAUSE.*"))
                                                 if "lifted" not in p]:
        if os.path.exists(os.path.join(RUN, k)):
            print(f"{k}: {read(os.path.join(RUN, k)).strip()}")
    subprocess.run(["python3", os.path.join(SCRIPTS, "status.py"), RUN])


def main():
    os.makedirs(os.path.join(RUN, "state"), exist_ok=True)
    if CMD == "status":
        return status()
    pid_f = os.path.join(RUN, "logs", "crowd.pid")
    old = read(pid_f).strip()
    if old and old != str(os.getpid()) and subprocess.run(["kill", "-0", old], capture_output=True).returncode == 0:
        sys.exit(f"crowd.py already runs for this run dir (pid {old})")
    os.makedirs(os.path.dirname(pid_f), exist_ok=True)
    with open(pid_f, "w") as f:
        f.write(str(os.getpid()))
    steps = {"plan": plan, "run": run, "dedupe": dedupe, "file": file_issues, "report": report}
    for name in (list(steps) if CMD == "all" else [CMD]):
        if name not in steps:
            sys.exit(__doc__)
        if name == "file" and cfg("FILING", "on") == "off":
            log("file: FILING=off, skipped")
            continue
        steps[name]()


if __name__ == "__main__":
    main()
