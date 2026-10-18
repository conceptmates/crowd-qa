#!/usr/bin/env python3
"""crowd.py: drives a whole crowd-QA run in code. A model is called only where judgement is needed: the product
map, each character's plan, checking a day's findings, checking a bug before filing, and the launch report.
Everything else (scheduling, waiting, quota pauses, coverage, evidence checks, duplicates, merging, labels,
issue bodies, evidence push, filing) is plain code, so no agent spends tokens waiting on a tester.

usage:
  crowd.py <run> all        plan, run, dedupe, file, fix, report (each step skips work already on disk)
  crowd.py <run> plan|run|dedupe|file|fix|report
  crowd.py <run> approve    approve file-plan.md, then file
  crowd.py <run> jobs next|start|done|fail|requeue-running|list   fix jobs, for a t3 or subagent runner
  crowd.py <run> pr         rewrite every fix PR body from the results
  crowd.py <run> status     one screen: every character-day, model spend so far
  crowd.py <run> makeup <new-run>   a new run dir with only the scenarios this run never finished (scripts/makeup.py)

Run it detached so it survives the orchestrating session: nohup python3 <run>/scripts/crowd.py <run> all \\
  >> <run>/logs/crowd.out 2>&1 &
Everything it decides is on disk, so after a crash or a reboot the same command continues where it stopped.
Settings come from config.env (scripts/init-crowd.sh lists them).
"""
import concurrent.futures as cf
import contextlib
import fcntl
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
import uuid

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
    print(line, flush=True, file=sys.stderr if CMD == "jobs" else sys.stdout)   # jobs prints JSON on stdout
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
LIMIT_RE = re.compile(r"usage limit|rate.?limit|overloaded|\b429\b|\b529\b|hit your limit|hit your session limit|session limit|quota exceeded|out of extra usage", re.I)


def llm(step, prompt, model, effort, out_path, required=(), text=False, timeout=5400):
    """One model call through the configured CLI. The model writes its answer to out_path; code checks it.
    A usage or rate limit waits and tries again (LLM_LIMIT_WAIT_S, up to LLM_LIMIT_RETRIES times) instead of failing
    the step. An answer that does not parse is asked for again in the same session, so the work is not paid twice.
    Returns the parsed JSON (or the text when text=True), None when no valid answer came back."""
    cli = cfg("LLM_CLI", "claude")
    lean = ["--setting-sources", "project", "--strict-mcp-config", "--disable-slash-commands"] \
        if cfg("CLAUDE_LEAN", "1") == "1" and cli == "claude" else []
    what = "the file" if text else "one JSON object" + (f" with the keys {', '.join(required)}" if required else "")
    body = (f"[crowd-qa step={step} out={out_path}]\n{prompt}\n\nWrite {what} to {out_path}"
            f"{'' if text else ', check that it parses'}, then reply DONE. Keep command output short (| head -c 4000).")
    sid, attempt, limits, msg = str(uuid.uuid4()), 0, 0, body
    while attempt < 2:
        if os.path.exists(out_path) and not text:
            os.remove(out_path)
        cmd = [cli, "-p", "--dangerously-skip-permissions", "--output-format", "json", *lean]
        if cli == "claude":
            cmd += ["--resume", sid] if attempt else ["--session-id", sid]
        if model:
            cmd += ["--model", model]
        if effort:
            cmd += ["--effort", effort]
        t0, out = time.time(), ""
        try:
            r = subprocess.run(cmd, input=msg, text=True, capture_output=True, cwd=RUN, timeout=timeout)
            out = (r.stdout or "") + (r.stderr or "")
            meta = jload_str(r.stdout)
        except subprocess.TimeoutExpired:
            meta = {}
        with LOCK, open(os.path.join(RUN, "state", "costs.jsonl"), "a") as f:
            f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "step": step, "model": model or "default",
                                "usd": meta.get("total_cost_usd", 0), "turns": meta.get("num_turns"),
                                "s": round(time.time() - t0)}) + "\n")
        try:
            if text:
                if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
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
            if LIMIT_RE.search(out[-3000:]) and limits < cfg_int("LLM_LIMIT_RETRIES", 6):
                limits += 1
                wait = float(cfg("LLM_LIMIT_WAIT_S", "300")) * limits
                log(f"{step}: model limit ({LIMIT_RE.search(out[-3000:]).group(0)}); waiting {wait:.0f}s, try {limits}")
                time.sleep(wait)
                sid, msg = str(uuid.uuid4()), body      # a limited call did no work: start it again
                continue
            attempt += 1
            log(f"{step}: attempt {attempt} left no valid answer ({e})")
            fix = (f"Your answer is not at {out_path} or does not parse ({e}). Write {what} to {out_path} now, "
                   f"then reply DONE.")
            msg = fix if cli == "claude" else body + "\n\n" + fix
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


def ndays(cid):
    """Days this character runs: DAYS, or more when split_days split a long day (lanes/<id>/days holds the count)."""
    try:
        return max(DAYS, int(read(os.path.join(lane_dir(cid), "days")).strip()))
    except ValueError:
        return DAYS


def split_days(cid, cap):
    """Splits every '## Day N' section with more than cap scenarios into consecutive days of at most cap. Days with
    31 scenarios ended early and 121 scenarios were never reported, so no session gets more than cap. Only before the
    character's first day has started; the plan as written is kept in scenarios.unsplit.md."""
    sp = os.path.join(lane_dir(cid), "scenarios.md")
    if cap <= 0 or glob.glob(os.path.join(lane_dir(cid), "round*")):
        return False
    txt = read(sp)
    parts = re.split(r"(?m)^(?=## )", txt)
    head, days, rest = parts[0] if not parts[0].startswith("## ") else "", [], []
    for part in parts:
        m = re.match(r"## Day (\d+)\b(.*)", part)
        if m:
            blocks = re.split(r"(?m)^(?=### S\d+)", part.split("\n", 1)[1] if "\n" in part else "")
            pre, scen = (blocks[0], blocks[1:]) if not blocks[0].startswith("### ") else ("", blocks)
            days.append((int(m.group(1)), m.group(2).rstrip(), pre, scen))
        elif part.startswith("## "):
            rest.append(part)
    if not any(len(sc) > cap for d, _, _, sc in days if d <= DAYS):
        return False
    out, n = [head], 0
    for d, title, pre, scen in sorted(days):
        if d > DAYS:
            continue                # a plan's sections beyond DAYS never ran before either
        chunks = [scen[i:i + cap] for i in range(0, len(scen), cap)] or [[]]
        for k, ch in enumerate(chunks, 1):
            n += 1
            label = title if len(chunks) == 1 else f" (day {d}, part {k} of {len(chunks)})"
            out.append(f"## Day {n}{label}\n" + (pre if k == 1 else "") + "".join(ch))
    shutil.copy(sp, os.path.join(lane_dir(cid), "scenarios.unsplit.md"))
    with open(os.path.join(lane_dir(cid), "days"), "w") as f:
        f.write(f"{n}\n")
    with open(sp, "w") as f:
        f.write("".join(out) + "".join(rest))
    log(f"plan:{cid}: days longer than SCEN_MAX={cap} split; now {n} days")
    return True


def git_head(path):
    r = subprocess.run(["git", "-C", path, "rev-parse", "HEAD"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def reuse_map(pm):
    """PRODUCT_MAP_FROM: copy a saved map when it was written from the commits SOURCE_PATHS are at now. The commits
    come from the map's sidecar (<map>.src, written with every map) or its file name (map-fe-b811b27-be-9cd1c1c.md).
    A fresh map cost $41 of a $62 planning bill on high effort, so a match is worth checking."""
    src = cfg("PRODUCT_MAP_FROM")
    if not src:
        return False
    if not os.path.isfile(src):
        log(f"plan: PRODUCT_MAP_FROM {src} does not exist; writing a fresh map")
        return False
    known = " ".join(jload(src + ".src", {}).get("commits", {}).values()) + " " + os.path.basename(src)
    heads = {p: git_head(p) for p in cfg("SOURCE_PATHS").split()}
    stale = [f"{p}@{h[:7]}" for p, h in heads.items() if h and h[:7] not in known]
    if stale:
        log(f"plan: PRODUCT_MAP_FROM {src} was written from other commits than {', '.join(stale)}; writing a fresh map")
        return False
    shutil.copy(src, pm)
    if os.path.exists(src + ".src"):
        shutil.copy(src + ".src", pm + ".src")
    checked = [f"{os.path.basename(p)}@{h[:7]}" for p, h in heads.items() if h]
    log(f"plan: product map copied from {src} " + (f"(source commits match: {', '.join(checked)})" if checked
                                                    else "(SOURCE_PATHS are not git checkouts: commits not checked)"))
    return True


def plan():
    os.makedirs(os.path.join(RUN, "state"), exist_ok=True)
    src = cfg("SOURCE_PATHS")
    pm = os.path.join(RUN, "product-map.md")
    if not (os.path.exists(pm) and os.path.getsize(pm) > 500) and not reuse_map(pm):
        log("plan: writing the product map")
        llm("product-map", f"""Write the product map every scenario planner of this crowd QA run reads instead of the
source. Source: {src}. Run dir: {RUN} (context.md says what the product is and which surfaces the crowd uses).
For each surface in use (web screens, mobile screens, CLI commands, API endpoints): the routes or commands, the
visible labels of buttons, fields and tabs, what each role may do, plan or feature gates, limits and validation
rules, the server-side refusals a user can hit and their wording, and anything scheduled or asynchronous. Cite
file:line for rules. Group by area. Tables and lists, completeness over prose.""",
            cfg("PLANNER_MODEL", "opus"), cfg("MAP_EFFORT", "medium"), pm, text=True)
        if os.path.exists(pm):
            jsave(pm + ".src", {"commits": {p: git_head(p) for p in src.split() if git_head(p)}})
    todo = [c for c in characters() if not plan_ok(c["id"])]
    skipped = set(jload(os.path.join(RUN, "state", "skipped.json"), []))
    cap = cfg_int("SCEN_MAX", 12)

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
"## Exhaustive sweep" section. If a day truly needs more than {cap}, write them all: code splits that day into
sessions of {cap}, since longer sessions ended early and left scenarios unreported. Day 1 is their first contact with the product; later days come
back to what they made and to what other characters did to them. At least 60% failure and edge paths, in this
person's habits. For a boundary tester, stay inside the product's own screens, commands and endpoints and describe
each check plainly.
Test data: a scenario that consumes something (a ticket to scan, a single-use code, a booking to cancel, a pending
item to approve) uses data seeded for this character alone; name it under Test data as "own: <what>" so it can be
seeded, and never use something another character's scenarios will consume or change. Dates and times are in the
product's time zone (context.md), not UTC. When a step needs another person to act (an admin approves, staff scan),
use the act-as helper context.md names instead of waiting for a character. Do not open the product."""
        for attempt in (1, 2):
            llm(f"plan:{cid}", prompt, cfg("PLANNER_MODEL", "opus"), cfg("PLANNER_EFFORT", "medium"), sp, text=True)
            if plan_ok(cid):
                return cid, True
            prompt += f"\n\nThe file is missing a '## Day N' section with '### S<n>' scenarios for some day. Rewrite {sp} complete."
        return cid, False

    with cf.ThreadPoolExecutor(cfg_int("DESIGN_PARALLEL", 5)) as ex:
        for cid, ok in ex.map(one, todo):
            if ok:
                skipped.discard(cid)
                log(f"plan:{cid}: {sum(len(day_scenarios(cid, d)) for d in range(1, ndays(cid) + 1))} scenarios")
            else:
                skipped.add(cid)
                log(f"plan:{cid}: no usable plan after two attempts; character skipped (see logs/crowd.log)")
    for c in characters():          # plans written by hand or by an earlier version are held to SCEN_MAX too
        if plan_ok(c["id"]):
            split_days(c["id"], cap)
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
    if planned and not (set(st) & set(planned)) and st:
        log(f"{cid} d{d}: the report's scenario ids ({', '.join(list(st)[:5])}) match none of the plan's; coverage reads 0")
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
            # near-identical titles are rejected here; merely similar ones go to the judge with a note, since two
            # different bugs on one screen often share most of their words
            dup = next((n for n, t in filed if similar(title, t) >= 0.8), None)
            rep_ = next((t for t in earlier if similar(title, t) >= 0.8), None)
            twin = next((t for t in seen if similar(title, t) >= 0.85), None)
            maybe = next((f"filed {n}: {t}" for n, t in filed if similar(title, t) >= 0.5), None) or \
                next((f"day-earlier bug: {t}" for t in earlier if similar(title, t) >= 0.5), None)
            if dup:
                why = f"already filed as {dup}"
            elif rep_:
                why = f"repeat of an earlier day's verified bug: {rep_}"
            elif twin:
                why = f"duplicate of another finding in the same report: {twin}"
        if why:
            rejected.append({"id": f.get("id"), "title": title, "reason": why, "by": "code"})
        else:
            keep.append({**f, "screenshots": real, **({"possible_duplicate": maybe} if maybe else {})})
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
        dupes = "".join(f"Finding {f['id']} may repeat {f['possible_duplicate']}. Reject it as a duplicate if it is the same "
                        f"bug; keep it if the screen, the cause or the symptom differs.\n"
                        for f in keep if f.get("possible_duplicate"))
        ans = llm(f"judge:{cid}:d{d}", f"""You check the findings of character "{cid}", day {d}, in a crowd QA run.
Read {rd}/judge-pack.md (scenarios, findings with downscaled screenshots, square posts, end of the tester log)
and {lane_dir(cid)}/card.md. Check only these findings: {ids}. Source: {cfg('SOURCE_PATHS')}.
{dupes}
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
            raise RuntimeError(f"judge for {cid} day {d} gave no valid answer; no verdict written, the next run judges it again")
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


def requeue(dl):
    """A day that stopped at a usage limit, or whose browser never came up (exit=retry), runs again later."""
    return "quota" in dl or dl.startswith("exit=retry")


def run():
    os.makedirs(os.path.join(RUN, "state"), exist_ok=True)
    chars = characters()
    for c in chars:
        if plan_ok(c["id"]):
            split_days(c["id"], cfg_int("SCEN_MAX", 12))
    nd = {c["id"]: ndays(c["id"]) for c in chars}
    ids = {c["id"] for c in chars}
    skipped = set(jload(os.path.join(RUN, "state", "skipped.json"), []))
    skipped |= {c["id"] for c in chars if not plan_ok(c["id"])}
    st, procs, waits = {}, {}, {}
    for c in chars:
        for d in range(1, nd[c["id"]] + 1):
            k = (c["id"], d)
            dl = done_line(*k)
            if c["id"] in skipped:
                st[k] = "skipped"
            elif os.path.exists(os.path.join(round_dir(*k), "verdict.json")):
                st[k] = "judged"
            elif dl and not requeue(dl):
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
    log(f"run: {len(chars)} characters, {sum(nd.values())} character-days, {sum(v == 'pending' for v in st.values())} character-days to run")
    finished = ("judged", "skipped", "failed")
    last_down_note = 0
    while not all(v in finished for v in st.values()):
        C.update(load_config())       # capacity changes in config.env apply without a restart
        cap = cfg_int("HARD_CAP", 4)
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
            if requeue(dl):
                waits[k] = waits.get(k, 0) + 1
                os.replace(os.path.join(round_dir(*k), "DONE"), os.path.join(round_dir(*k), "DONE.stale-quota"))
                st[k] = "pending" if waits[k] <= cfg_int("MAX_QUOTA_WAITS", 8) else "failed"
                why = "at a usage limit" if "quota" in dl else f"before testing ({dl})"
                log(f"{k[0]} d{k[1]}: stopped {why} (wait {waits[k]})")
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
            except Exception as e:  # a judge crash must not stop the crowd; the day is judged again on the next run
                log(f"judge {k}: {e}")
                st[k] = "failed"
        down = os.path.exists(os.path.join(RUN, "STACK_DOWN"))
        if down and time.time() - last_down_note > 1800:
            log(f"STACK_DOWN ({read(os.path.join(RUN, 'STACK_DOWN')).strip()}): no character starts until the health checks pass")
            last_down_note = time.time()
        elif not down:
            last_down_note = 0
        running = sum(v == "running" for v in st.values())
        for c in chars:
            for d in range(1, nd[c["id"]] + 1):
                k = (c["id"], d)
                if st[k] != "pending" or running >= cap or paused(engine_of(c["id"])):
                    continue
                if d > 1 and st[(c["id"], d - 1)] not in finished:
                    continue        # tomorrow starts once yesterday is judged: its prompt carries the verdict
                if any(st.get((p, d), "judged") not in ("ran", "judging") + finished for p in deps[c["id"]]):
                    continue        # a dependent waits for its provider's run that day, not for its judge
                gone = [p for p in deps[c["id"]] if st.get((p, d)) in ("skipped", "failed")]
                if gone:
                    log(f"{c['id']} d{d}: starts although {', '.join(gone)} did not run that day; expect blocked scenarios")
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
        for d in range(1, ndays(c["id"]) + 1):
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
        if len(ambiguous) > 200:
            ambiguous.sort(key=lambda ij: -similar(found[ij[0]]["title"], found[ij[1]]["title"]))
            log(f"dedupe: {len(ambiguous)} look-alike pairs; the model checks the 200 most similar, the rest stay separate")
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
def kv(key):
    """'a=x b=y' settings (TRACKERS, REPOS, TRACKER_KIND) as a dict."""
    return dict(i.split("=", 1) for i in cfg(key).split() if "=" in i)


def trackers():
    return kv("TRACKERS")


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


def verify(x, repo, attempt=1, planned=False):
    """The model checks one merged bug before filing. High severity and unconfirmed causes go to ESCALATE_MODEL."""
    hard = SEV.get(x.get("severity"), 0) >= 3 or "not confirmed" in (x.get("mechanism") or "not confirmed")
    model = cfg("ESCALATE_MODEL", "opus") if hard else cfg("VERIFY_MODEL", "opus")
    effort = cfg("ESCALATE_EFFORT", "medium") if hard else cfg("VERIFY_EFFORT", "medium")
    cands = []
    if repo and not cfg("FILE_CMD") and not planned:
        q = " ".join(sorted(words(x["title"]), key=len, reverse=True)[:5])
        r = sh(["gh", "issue", "list", "-R", repo, "--state", "all", "--search", q, "--limit", "10", "--json", "number,title"])
        cands = jload_str(r.stdout) or []
    dup_line = ("Duplicates were already checked against every issue and comment in the repo; do not refuse for that."
                if planned else f"Possible existing issues: {json.dumps(cands)}.")
    out = os.path.join(RUN, "state", f"verify-{x['key']}-{attempt}.json")
    return llm(f"verify:{x['key']}", f"""You check one crowd-QA bug before it is filed. Run dir: {RUN}. Source: {cfg('SOURCE_PATHS')}.
Bug: {json.dumps({k: x.get(k) for k in ('title', 'severity', 'route', 'steps', 'expected', 'actual', 'mechanism', 'screenshots', 'confirmers', 'reporter')}, indent=1)}
1. Evidence: open the screenshots (and the confirmers') and confirm they show what the bug claims. If they do not
   but another screenshot from the same scenario does (look in {RUN}/lanes/{x.get('reporter')}/round{x.get('day', 1)}/shots/),
   answer verdict "repair" with those absolute paths in repair_screenshots.
2. Source: open the cited file:line at the current checkout and confirm the cause is still there. If nothing is
   cited, find the code that handles this behaviour and name it, or say "not confirmed".
3. Rule out intended behaviour ({RUN}/context.md and the project's docs), a stack fault ({RUN}/stack-faults.md),
   and an existing issue. {dup_line}
Answer {{"verdict": "file|refuse|repair", "reason": "<one sentence>", "likely_cause": "<file:line and why, or not
confirmed>", "duplicate_of": "<issue number or empty>", "repair_screenshots": []}}.""",
               model, effort, out, ("verdict", "reason"))


def snapshot(key, repo):
    """Every issue in the repo, open and closed, with body and comments, saved once per hour. Duplicates are judged
    against this, so a bug already reported in a comment or closed as intended is not filed again."""
    out = os.path.join(RUN, "state", f"issues-snapshot-{key}.json")
    old = jload(out)
    if old is not None and time.time() - os.path.getmtime(out) < float(cfg("SNAPSHOT_MAX_AGE_S", "3600")):
        return old
    r = sh(["gh", "issue", "list", "-R", repo, "--state", "all", "--limit", cfg("SNAPSHOT_LIMIT", "3000"), "--json",
            "number,title,body,state,stateReason,labels,closedAt,url,comments"])
    items = jload_str(r.stdout)
    if r.returncode or not isinstance(items, list):
        log(f"file: could not read the issues of {repo} ({r.stderr.strip()[:200]}); not planning against an empty list")
        return None
    snap = [{"number": i["number"], "title": i.get("title", ""), "state": i.get("state", ""),
             "reason": i.get("stateReason") or "", "closed": i.get("closedAt") or "", "url": i.get("url", ""),
             "labels": [lab.get("name") for lab in i.get("labels", [])], "body": (i.get("body") or "")[:4000],
             "comments": [{"by": (c.get("author") or {}).get("login", ""), "at": c.get("createdAt", ""),
                           "text": (c.get("body") or "")[:1500]} for c in i.get("comments", [])]} for i in items]
    jsave(out, snap)
    log(f"file: snapshot of {repo}: {len(snap)} issues with {sum(len(i['comments']) for i in snap)} comments")
    return snap


def candidates(x, snap, n=8):
    """The existing issues most like this bug by words in the title, body and comments. Code narrows, the model decides."""
    want = words(" ".join([x.get("title", ""), x.get("route", ""), x.get("actual", "")]))
    scored = []
    for i in snap:
        have = words(i["title"]) | words(i["body"][:1500]) | words(" ".join(c["text"][:400] for c in i["comments"]))
        title = similar(x.get("title", ""), i["title"])
        cover = len(want & have) / len(want) if want else 0.0
        if title >= 0.2 or cover >= 0.35:
            scored.append((title * 2 + cover, i))
    scored.sort(key=lambda t: -t[0])
    return [i for _, i in scored[:n]]


PLAN_ACTIONS = ("create", "comment", "regression", "skip")


def make_plan(key, repo, bugs):
    """One look at the whole batch against the repo's issues: create, comment on an open issue, file a regression of a
    closed one, or skip. Written to state/file-plan.json and file-plan.md for a person to approve."""
    snap = snapshot(key, repo)
    plan = jload(os.path.join(RUN, "state", "file-plan.json"), {})
    if snap is None:
        return plan
    todo = [x for x in bugs if x["key"] not in plan]
    size = cfg_int("PLAN_BATCH", 30)
    for start in range(0, len(todo), size):
        batch = todo[start:start + size]
        pack = []
        for x in batch:
            cands = candidates(x, snap)
            if not cands:
                plan[x["key"]] = {"action": "create", "target": None, "reason": "no similar issue in the repo",
                                  "quote": "", "title": x["title"], "tracker": key}
                continue
            pack.append({"key": x["key"], "title": x["title"], "route": x.get("route"), "actual": x.get("actual", "")[:500],
                         "expected": x.get("expected", "")[:300],
                         "existing": [{"number": i["number"], "title": i["title"], "state": i["state"], "reason": i["reason"],
                                       "closed": i["closed"], "labels": i["labels"], "body": i["body"][:800],
                                       "comments": [f"{c['by']} ({c['at'][:10]}): {c['text'][:500]}" for c in i["comments"][-10:]]}
                                      for i in cands]})
        if not pack:
            continue
        out = os.path.join(RUN, "state", f"plan-{key}-{start}.json")
        ans = llm(f"file-plan:{key}:{start}", f"""Crowd-QA found these bugs. Before anything is filed in {repo}, decide for each
one what to do, judged against the existing issues listed with it (open and closed, with their comments). Read the
comments: that is where "can't reproduce", "this is intended", "fixed in #n", "won't fix" or a second report usually is.
Intended behaviour for this product is in {RUN}/context.md.
Actions:
- "create": no existing issue is the same bug.
- "comment": an OPEN issue (or a comment on one) already reports the same bug; target = that issue number.
- "regression": a CLOSED issue was the same bug and was closed as fixed (completed), but the crowd still hits it;
  target = the closed issue number.
- "skip": a closed issue or a comment says this behaviour is intended, won't be fixed, or a duplicate that was
  rejected; target = that issue number. Quote the sentence that says so.
Same bug means the same symptom on the same screen or the same root cause, not just the same area.
Bugs: {json.dumps(pack, indent=1)}
Answer {{"plan": [{{"key": "<bug key>", "action": "create|comment|regression|skip", "target": <issue number or null>,
"reason": "<one sentence>", "quote": "<the sentence from the issue or comment that decided it, or empty>"}}]}}.""",
                  cfg("PLAN_MODEL", cfg("VERIFY_MODEL", "opus")), cfg("PLAN_EFFORT", "medium"), out, ("plan",))
        nums = {i["number"] for i in snap}
        for a in (ans or {}).get("plan", []):
            k, act = a.get("key"), a.get("action")
            if k not in {x["key"] for x in batch} or act not in PLAN_ACTIONS:
                continue
            if act != "create" and a.get("target") not in nums:
                act = "create"   # a target that is not in the repo cannot be commented on or skipped for
            plan[k] = {"action": act, "target": a.get("target") if act != "create" else None,
                       "reason": a.get("reason", ""), "quote": a.get("quote", ""),
                       "title": next(x["title"] for x in batch if x["key"] == k), "tracker": key}
        jsave(os.path.join(RUN, "state", "file-plan.json"), plan)
    jsave(os.path.join(RUN, "state", "file-plan.json"), plan)   # also when no bug needed the model
    return plan


def write_plan_md(plan, tr):
    rows = {a: [] for a in PLAN_ACTIONS}
    for k, p in plan.items():
        rows[p["action"]].append((k, p))
    head = {"create": "New issues", "comment": "Comment on an open issue", "regression": "Regression of a closed issue",
            "skip": "Skipped"}
    lines = [f"# File plan, {cfg('RUN_LABEL', 'crowd-qa')}", "",
             " · ".join(f"{len(rows[a])} {head[a].lower()}" for a in PLAN_ACTIONS), "",
             f"Nothing has been sent to GitHub. To go ahead: `python3 {SCRIPTS}/crowd.py {RUN} approve`. To change a line, "
             f"edit `state/file-plan.json` (action: create|comment|regression|skip) first.", ""]
    for a in PLAN_ACTIONS:
        if not rows[a]:
            continue
        lines += [f"## {head[a]} ({len(rows[a])})", "", "| bug | repo | target | why |", "|---|---|---|---|"]
        for k, p in rows[a]:
            repo = tr.get(p["tracker"], p["tracker"])
            tgt = f"[#{p['target']}](https://github.com/{repo}/issues/{p['target']})" if p.get("target") else ""
            why = p.get("reason", "") + (f" <br>> {p['quote'][:240]}" if p.get("quote") else "")
            lines.append(f"| {p['title'].replace('|', '/')} | {p['tracker']} | {tgt} | {why.replace('|', '/')} |")
        lines.append("")
    with open(os.path.join(RUN, "file-plan.md"), "w") as f:
        f.write("\n".join(lines))


def comment_body(x, urls, target):
    cid = x.get("reporter", "")
    who = card_field(cid, "Name") or cid
    lines = [f"**Also hit by {who}** in the {cfg('RUN_DATE', time.strftime('%Y-%m-%d'))} crowd run "
             f"(day {x.get('day', 1)}, `{x.get('route', '')}`).", "", f"> \"{x.get('voice', '').strip()}\"", "",
             "**Steps**", ""] + [f"{i}. {s}" for i, s in enumerate(x.get("steps", []), 1)]
    lines += ["", f"**Actual:** {x.get('actual', '')}"]
    for c in x.get("confirmers", []):
        lines.append(f"- Also: **{card_field(c.get('id', ''), 'Name') or c.get('id')}**: \"{c.get('line', '')}\"")
    lines += [""] + [f"![]({u})" for s, u in urls]
    return "\n".join(lines) + "\n"


def approved_keys():
    """The plan lines a person approved: every key in the plan when `approve` ran. Bugs planned later wait for the
    next approval. FILE_AUTO=yes approves everything."""
    if cfg("FILE_AUTO", "no") == "yes":
        return None
    a = read(os.path.join(RUN, "state", "FILE_PLAN_APPROVED"))
    try:
        return set(json.loads(a).get("keys", []))
    except (ValueError, AttributeError):
        return set(jload(os.path.join(RUN, "state", "file-plan.json"), {})) if a.strip() else set()


def plan_approved():
    keys = approved_keys()
    return keys is None or bool(keys)


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
    plan = {}
    if not hook and todo:
        for key in keys:
            mine = [x for x in todo if x["tracker"] == key]
            if mine:
                plan = make_plan(key, tr.get(key, key), mine)
        write_plan_md(plan, tr)
        ok = approved_keys()
        waiting = [x for x in todo if x["key"] in plan and ok is not None and x["key"] not in ok]
        unplanned = [x for x in todo if x["key"] not in plan]
        for x in unplanned:
            log(f"file: not in the plan yet, so not filed: {x['title']} (the next run plans it)")
        todo = [x for x in todo if x["key"] in plan and (ok is None or x["key"] in ok)]
        if waiting:
            n = {a: sum(1 for x in waiting if plan[x["key"]]["action"] == a) for a in PLAN_ACTIONS}
            log(f"file: plan written to {RUN}/file-plan.md; {len(waiting)} lines wait for approval ({n}). "
                f"Approve with: crowd.py {RUN} approve")
            notify = cfg("NOTIFY_CMD")
            if notify:
                sh(["bash", "-c", f'{notify} "$1"', "_", f"crowd-qa file plan ready for approval: {RUN}/file-plan.md"])
            if not todo:
                log("file: nothing sent")
                return
    for key in keys:
        mine = [x for x in todo if x["tracker"] == key]
        if not mine:
            continue
        repo = tr.get(key, key)
        urls = {}
        if not hook:
            labs = {lab for x in mine for lab in x["labels"]}
            if any(plan.get(x["key"], {}).get("action") == "regression" for x in mine):
                labs.add("regression")
            for lab in sorted(labs):
                sh(["gh", "label", "create", "--force", "-R", repo, lab])
            urls = push_evidence(key, repo, [x for x in mine if plan.get(x["key"], {}).get("action") != "skip"])

        def one(x):
            p = plan.get(x["key"], {"action": "create"})
            if p["action"] == "skip":
                return x, None, f"skipped: {p.get('reason', '')} (#{p.get('target')})" + (f" \"{p['quote'][:200]}\"" if p.get("quote") else "")
            if x.get("cant_repro") and cfg("BOUT_CANT_REPRO", "on") == "on":
                who = card_field(x.get("reporter", ""), "Name") or x.get("reporter", "reporter")
                bt = bout("square", "can't-repro", who, x["title"], " ".join(x["cant_repro"])[:1500],
                          f"{x.get('actual', '')} Evidence: {', '.join(x.get('screenshots', []))}",
                          f"Is this a real bug in the product? Steps: {json.dumps(x.get('steps', []))}. Intended behaviour: "
                          f"{RUN}/context.md. Stack faults: {RUN}/stack-faults.md.")
                if bt and bt["winner"] == "a":
                    return x, None, f"a judge sided with the can't-repro report: {bt.get('reason', '')}"
            v = verify(x, repo, planned=bool(plan))
            if v and v.get("verdict") == "repair":
                fix = [q for q in v.get("repair_screenshots", []) if os.path.isfile(q)]
                if fix:
                    x["screenshots"] = fix
                    if not hook:
                        with LOCK:
                            urls.update(push_evidence(key, repo, [x]))
                    v = verify(x, repo, 2, planned=bool(plan))
                    if v and v.get("verdict") == "repair":
                        v = {"verdict": "refuse", "reason": "evidence still does not show the claim after repair"}
            if not v:
                return x, None, "error: the bug check gave no valid answer"
            if v.get("verdict") != "file":
                return x, None, v.get("reason", "refused")
            x["likely_cause"] = v.get("likely_cause")
            if p["action"] == "comment":
                bf = os.path.join(RUN, "state", f"comment-{x['key']}.md")
                with open(bf, "w") as f:
                    f.write(comment_body(x, urls.get(x["key"], []), p["target"]))
                r = sh(["gh", "issue", "comment", str(p["target"]), "-R", repo, "--body-file", bf])
                url = next((w for w in r.stdout.split() if "/issues/" in w), "")
                if url:
                    sh(["gh", "issue", "edit", str(p["target"]), "-R", repo,
                        *sum((["--add-label", lab] for lab in x["labels"] if lab.startswith("persona:")), [])])
                    return x, url, ""
                return x, None, f"error: comment returned no URL: {r.stderr.strip()[:200]}"
            if not hook:   # an earlier try may have created it before failing: never file the same title twice
                r = sh(["gh", "issue", "list", "-R", repo, "--state", "all", "--search", f'"{x["title"]}" in:title',
                        "--limit", "5", "--json", "number,title,url"])
                same = next((i for i in (jload_str(r.stdout) or []) if isinstance(i, dict) and
                             i.get("title", "").strip().lower() == x["title"].strip().lower()), None)
                if same:
                    return x, same.get("url") or f"https://github.com/{repo}/issues/{same['number']}", ""
            body = render_body(x, urls.get(x["key"], []))
            labels = list(x["labels"])
            if p["action"] == "regression":
                prs = jload_str(sh(["gh", "issue", "view", str(p["target"]), "-R", repo, "--json",
                                    "closedByPullRequestsReferences,closedAt"]).stdout)
                fixed_by = ", ".join(f"#{q.get('number')}" for q in prs.get("closedByPullRequestsReferences", []) if q.get("number"))
                body = (f"**Regression of #{p['target']}**, closed {str(prs.get('closedAt', ''))[:10]}"
                        f"{f' by {fixed_by}' if fixed_by else ''}. The crowd still hits it on the current build.\n\n") + body
                labels.append("regression")
            bf = os.path.join(RUN, "state", f"issue-{x['key']}.md")
            with open(bf, "w") as f:
                f.write(body)
            if hook:
                jf = os.path.join(RUN, "state", f"issue-{x['key']}.json")
                jsave(jf, {"title": x["title"], "body": body, "labels": labels, "repo": key, "screenshots": x["screenshots"]})
                r = sh(["bash", "-c", f'{hook} "$1"', "_", jf])
                url = (r.stdout.strip().splitlines() or [""])[-1]
            else:
                r = sh(["gh", "issue", "create", "-R", repo, "--title", x["title"], "--body-file", bf,
                        *sum((["--label", lab] for lab in labels), [])])
                url = next((w for w in r.stdout.split() if "/issues/" in w), "")
                if url and sh(["gh", "issue", "view", url, "--json", "number"]).returncode:
                    url = ""
            if not url:
                return x, None, f"error: filing returned no URL: {r.stderr.strip()[:200]}"
            return x, url, ""

        with cf.ThreadPoolExecutor(cfg_int("FILE_PARALLEL", 6)) as ex:
            for x, url, why in ex.map(one, mine):
                with LOCK:
                    act = plan.get(x["key"], {}).get("action", "create")
                    if url:
                        done[x["key"]] = {"url": url, "issue": url.split("#")[0], "title": x["title"], "tracker": key, "action": act,
                                          "target": plan.get(x["key"], {}).get("target"), "reporter": x.get("reporter"),
                                          "severity": x.get("severity")}
                        num = re.search(r"/issues/(\d+)", url)
                        with open(os.path.join(RUN, "filed.txt"), "a") as f:
                            f.write(f"{'#' + num.group(1) if num else url} {x['title']}\n")
                        log(f"{'commented' if act == 'comment' else 'filed'} {url} {x['title']}")
                    elif why.startswith("error:"):
                        log(f"not filed yet: {x['title']} ({why}); the next run tries again")
                    else:
                        refused[x["key"]] = {"title": x["title"], "reason": why}
                        log(f"not filed: {x['title']} ({why})")
                    jsave(os.path.join(RUN, "state", "filed.json"), done)
                    jsave(os.path.join(RUN, "state", "refused.json"), refused)
    log(f"file: {len(done)} filed or commented, {len(refused)} refused or skipped")


# --------------------------------------------------------------------------------------------------- 5. report
def report():
    out = os.path.join(RUN, "LAUNCH_REPORT.md")
    pack = [f"# Report pack ({len(characters())} characters, {DAYS} days)", "", "## Coverage by character",
            "| id | role | day | ran | blocked | not run | verified | satisfied |", "|---|---|---|---|---|---|---|---|"]
    never = []
    for c in characters():
        any_day = False
        for d in range(1, ndays(c["id"]) + 1):
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
             "", "## Fix pull requests",
             *[f"- {k}: {v.get('url') or 'no PR'} ({v.get('status')}; re-check pass {v.get('recheck', {}).get('pass')}, "
               f"partial {v.get('recheck', {}).get('partial')}, fail {v.get('recheck', {}).get('fail')})"
               for k, v in jload(os.path.join(RUN, "state", "prs.json"), {}).items()],
             "", "## Refused at filing",
             *[f"- {v['title']}: {v['reason']}" for v in jload(os.path.join(RUN, "state", "refused.json"), {}).values()]]
    pp = os.path.join(RUN, "state", "report-pack.md")
    text_ = "\n".join(pack) + "\n"
    sig = hashlib.sha1((text_ + read(os.path.join(RUN, "square", "feed.jsonl"))).encode()).hexdigest()
    sp = os.path.join(RUN, "state", "report.sig")
    if os.path.exists(out) and read(sp).strip() == sig:
        log("report: nothing changed since the last report; not rewritten")
        return
    with open(pp, "w") as f:
        f.write(text_)
    ok = llm("report", f"""Write the launch-readiness report for this crowd QA run, grounded only in evidence.
Read {pp} (coverage, what was filed and refused, who was never tested), every {RUN}/lanes/*/card.md and memory.md,
and the square (python3 {SCRIPTS}/square.py {RUN} digest nobody --limit 300).
Sections: a verdict line (would these {len(characters())} people keep using the product after launch week, and
why); one short section per group of people (by role and need), with who got what they wanted, who gave up and
where, and the issues that blocked them (linked); the three things to fix before launch, by how many people they
hurt; the fix pull requests and what the reporters said when they re-tested them, if the pack lists any; what
nobody could test and why. Quote characters sparingly. Plain prose, no hype words, no closing summary.""",
        cfg("REPORT_MODEL", "opus"), cfg("REPORT_EFFORT", "medium"), out, text=True)
    if ok:
        with open(sp, "w") as f:
            f.write(sig)
    log(f"report: {out}")


# --------------------------------------------------------------------------------------------------- 6. fixing
# After filing: the bugs are grouped into areas that do not share files, each area gets a fixer in its own worktree,
# a reviewer who sees only the diff, and a re-check by the characters who reported its bugs. Failed re-checks go back
# to the fixer (FIX_RETRIES), a fixer who disputes a review comment meets the reviewer in a bout, and the PR body is
# written here from the results. crowd.py only writes job prompts and reads job results; who runs a job is the
# runner: cli (crowd.py starts claude/codex itself), or t3 / subagent (the orchestrating session launches each
# job with `crowd.py <run> jobs next` and reports back with `jobs start` / `jobs done`).
ROLE_KEYS = {"fix": ("fixed", "branch"), "review": ("verdict", "comments"), "recheck": ("results",)}
TERMINAL = ("done", "needs-human")


_JL, _JD = threading.RLock(), {"depth": 0, "fd": None}


@contextlib.contextmanager
def jobs_lock():
    """Job and area state is written by runner threads and by the orchestrating session's `jobs` calls: one writer
    at a time, across processes (flock) and threads (re-entrant, since finishing a job queues the next one)."""
    with _JL:
        if _JD["depth"] == 0:
            os.makedirs(os.path.join(RUN, "state"), exist_ok=True)
            _JD["fd"] = open(os.path.join(RUN, "state", "jobs.lock"), "w")
            fcntl.flock(_JD["fd"], fcntl.LOCK_EX)
        _JD["depth"] += 1
        try:
            yield
        finally:
            _JD["depth"] -= 1
            if _JD["depth"] == 0:
                fcntl.flock(_JD["fd"], fcntl.LOCK_UN)
                _JD["fd"].close()


def fix_path(*p):
    return os.path.join(RUN, "fix", *p)


def jobs_load():
    return jload(os.path.join(RUN, "state", "jobs.json"), {})


def jobs_save(j):
    jsave(os.path.join(RUN, "state", "jobs.json"), j)


def areas_load():
    return jload(os.path.join(RUN, "state", "areas-detail.json"), {})


def areas_save(a):
    jsave(os.path.join(RUN, "state", "areas-detail.json"), a)
    jsave(os.path.join(RUN, "state", "areas.json"), {k: v["bugs"] for k, v in a.items()})


def runner():
    r = cfg("RUNNER", "auto")
    return "cli" if r == "auto" else r


def tracker_kind(key):
    return kv("TRACKER_KIND").get(key) or ("be" if re.search(r"back|api|server", key) else "fe")


def repos():
    return kv("REPOS")


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:30] or "misc"


def bug_rows():
    """Every filed bug the fix stage can work on: {key: {issue number, title, tracker, reporter, confirmers...}}."""
    issues = {x["key"]: x for x in jload(os.path.join(RUN, "state", "issues.json"), [])}
    out = {}
    for k, v in jload(os.path.join(RUN, "state", "filed.json"), {}).items():
        num = re.search(r"/issues/(\d+)", v.get("issue") or v.get("url", ""))
        if not num:
            continue
        x = issues.get(k, {})
        out[k] = {"key": k, "issue": int(num.group(1)), "title": v["title"], "tracker": v.get("tracker", "app"),
                  "reporter": x.get("reporter") or v.get("reporter", ""), "route": x.get("route", ""),
                  "severity": x.get("severity") or v.get("severity", ""), "cause": x.get("likely_cause") or x.get("mechanism", ""),
                  "confirmers": [c.get("id") for c in x.get("confirmers", [])], "action": v.get("action", "create")}
    return out


def group():
    """Areas that do not share files, one model call. Every filed bug lands in exactly one area."""
    areas = areas_load()
    if areas:
        return areas
    bugs = bug_rows()
    if not bugs:
        log("fix: nothing filed, nothing to fix")
        return {}
    out = os.path.join(RUN, "state", "group-answer.json")
    ans = llm("group", f"""Group these filed bugs into fix areas. One fixer works on each area in its own git worktree, at the same
time as the others, and opens one pull request, so two areas must not need to edit the same files. Keep bugs of
different repos (tracker) in different areas. At most {cfg_int('FIX_AREA_MAX', 20)} bugs per area; prefer areas a person would
name (auth, checkout, vendor-listings). Source code: {cfg('SOURCE_PATHS')}. Look at the code to see which files each bug
touches before you group.
Bugs: {json.dumps(list(bugs.values()), indent=1)}
Answer {{"areas": [{{"name": "<short-slug>", "tracker": "<tracker key>", "bugs": ["<bug key>"], "files": ["<paths the
fixes will likely touch>"]}}]}}.""", cfg("JUDGE_MODEL", "opus"), cfg("GROUP_EFFORT", "medium"), out, ("areas",))
    seen = set()
    for a in (ans or {}).get("areas", []):
        keys = [k for k in a.get("bugs", []) if k in bugs and k not in seen]
        if not keys:
            continue
        seen.update(keys)
        name = slug(a.get("name"))
        while name in areas:
            name += "-2"
        tr = bugs[keys[0]]["tracker"]
        areas[name] = {"bugs": keys, "tracker": tr, "kind": tracker_kind(tr), "files": a.get("files", []),
                       "status": "queued", "attempt": 1, "review_round": 1, "pr": "", "worktree": "",
                       "branch": f"fix/{slug(cfg('RUN_LABEL', 'crowd'))}-{name}", "verdicts": {}, "unresolved": []}
    for k, b in bugs.items():          # whatever the model left out gets an area of its own per tracker
        if k not in seen:
            name = f"misc-{slug(b['tracker'])}"
            areas.setdefault(name, {"bugs": [], "tracker": b["tracker"], "kind": tracker_kind(b["tracker"]), "files": [],
                                    "status": "queued", "attempt": 1, "review_round": 1, "pr": "", "worktree": "",
                                    "branch": f"fix/{slug(cfg('RUN_LABEL', 'crowd'))}-{name}", "verdicts": {},
                                    "unresolved": []})["bugs"].append(k)
    for i, name in enumerate(sorted(areas)):
        areas[name]["port"] = cfg_int("FIX_PORT_BASE", 3101) + i
    areas_save(areas)
    log(f"fix: {len(bugs)} bugs in {len(areas)} areas: " + ", ".join(f"{n} ({len(a['bugs'])})" for n, a in areas.items()))
    return areas


def stack_note(area, a, job):
    port, app = a.get("port"), cfg("APP_URL", "the shared test stack (see context.md)")
    fe = (f"Start a dev server for your checkout: `bash {SCRIPTS}/fix-stack.sh {RUN} up {area} <your checkout> {port}`, then "
          f"use http://localhost:{port}. It runs against the shared backend. The base branch is running at {app}: take "
          f"'before' screenshots there. Stop it at the end: `bash {SCRIPTS}/fix-stack.sh {RUN} down {area}`.")
    be = (f"The backend is shared, so backend changes take turns. Push your branch, then "
          f"`bash {SCRIPTS}/fix-stack.sh {RUN} be-take {job} {a['branch']}` deploys it (it waits while another fix holds the "
          f"backend). Use the product at {app}. Always give it back when you are done, even after a failure: "
          f"`bash {SCRIPTS}/fix-stack.sh {RUN} be-give {job}`. Take 'before' screenshots at {app} before be-take.")
    return {"fe": fe, "be": be, "both": fe + "\n\n" + be}[a.get("kind", "fe")]


def bug_lines(a, bugs, keys, retest=None):
    out = []
    for k in keys:
        b = bugs.get(k)
        if not b:
            continue
        also = f" (also hit by {', '.join(b['confirmers'])})" if b["confirmers"] else ""
        out.append(f"  - #{b['issue']} `{k}` reported by **{b['reporter']}**{also}: {b['title']}"
                   + (f"\n    re-test: {retest[k]}" if retest and retest.get(k) else ""))
    return "\n".join(out)


def latest(area, role, status="done"):
    js = [j for j in jobs_load().values() if j["area"] == area and j["role"] == role and j["status"] == status]
    return max(js, key=lambda j: (j["attempt"], j.get("round", 1), j.get("finished", ""))) if js else None


def result_of(job):
    return jload(job["result"], {}) if job else {}


def new_job(role, area, feedback="", keys=None):
    areas, jobs, bugs = areas_load(), jobs_load(), bug_rows()
    a = areas[area]
    jid = f"{role}-{area}-{a['attempt']}" + (f"r{a['review_round']}" if role != "recheck" and a["review_round"] > 1 else "")
    while jid in jobs:
        jid += "x"
    d = fix_path("jobs", jid)
    os.makedirs(d, exist_ok=True)
    keys = keys or a["bugs"]
    fixr = result_of(latest(area, "fix"))
    repo = trackers().get(a["tracker"], a["tracker"])
    if runner() == "t3" and not a.get("worktree"):
        wt = "the worktree this thread started in (run `pwd`)"
    else:
        wt = a.get("worktree") or fix_path("wt", area)
    host = cfg("RECHECK_COMMENT_HOST")
    how = (f"Post it as the account that filed the issues: copy the body to {host} (`scp`) and run "
           f"`ssh {host} \"gh issue comment <n> -R {repo} --body-file <file>\"`." if host else
           f"`gh issue comment <n> -R {repo} --body-file <file>`.")
    vals = {"PRODUCT": cfg("PRODUCT_NAME", os.path.basename(RUN)), "REPO": repo, "AREA": area, "KIND": a["kind"],
            "ATTEMPT": str(a["attempt"]), "BRANCH": a["branch"], "BASE": cfg("FIX_BASE", "main"), "WORKTREE": wt,
            "BUGS": bug_lines(a, bugs, keys, fixr.get("retest") if role == "recheck" else None),
            "FEEDBACK": feedback, "RUN": RUN, "JOB": jid, "TRACKER": a["tracker"], "RESULT": os.path.join(d, "result.json"),
            "EVIDENCE_DIR": os.path.join(d, "evidence"), "PR": a.get("pr") or "(not open yet)",
            "STACK_NOTE": stack_note(area, a, jid), "COMMENT_HOW": how,
            "CHECK_CMD": cfg(f"CHECK_CMD_{a['tracker']}", cfg("CHECK_CMD", "the repo's own type check, lint and tests")),
            "COAUTHOR": cfg("COAUTHOR", "Claude Opus 5.5 <noreply@anthropic.com>"),
            "SCOPE_RULE": {"fe": "Frontend only: never change or open PRs on another repo.",
                           "be": "Backend only: never change or open PRs on another repo.",
                           "both": "Change only this repo."}[a["kind"]]}
    os.makedirs(vals["EVIDENCE_DIR"], exist_ok=True)
    text = read(os.path.join(RUN, "templates", f"{role}-brief.md"))
    for k, v in vals.items():
        text = text.replace("{{" + k + "}}", v)
    with open(os.path.join(d, "prompt.md"), "w") as f:
        f.write(text)
    model = cfg({"fix": "FIX_MODEL", "review": "REVIEW_MODEL", "recheck": "RECHECK_MODEL"}[role], "auto")
    jobs[jid] = {"id": jid, "role": role, "area": area, "attempt": a["attempt"], "round": a["review_round"],
                 "status": "queued", "runner": runner(), "model": model, "thread": "", "tries": 0, "keys": keys,
                 "prompt": os.path.join(d, "prompt.md"), "result": os.path.join(d, "result.json"),
                 "repo": repo, "repo_path": repos().get(a["tracker"], ""), "branch": a["branch"],
                 "base": cfg("FIX_BASE", "main"), "worktree": a.get("worktree", ""), "persona": "",
                 "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
    jobs_save(jobs)
    a["status"] = {"fix": "fixing", "review": "reviewing", "recheck": "rechecking"}[role]
    areas_save(areas)
    log(f"fix: queued {jid}")
    return jid


def ensure_worktree(area):
    """cli and subagent runners: crowd.py makes the area's worktree from the local clone in REPOS. (t3 makes its own.)"""
    areas = areas_load()
    a = areas[area]
    if a.get("worktree") and os.path.isdir(a["worktree"]):
        return a["worktree"]
    src = repos().get(a["tracker"])
    if not src:
        raise RuntimeError(f"no local clone for tracker {a['tracker']} in REPOS")
    wt, base = fix_path("wt", area), cfg("FIX_BASE", "main")
    os.makedirs(fix_path("wt"), exist_ok=True)
    sh(["git", "-C", src, "fetch", "-q", "origin", base])
    if not os.path.isdir(wt):
        r = sh(["git", "-C", src, "worktree", "add", "-q", "-B", a["branch"], wt, f"origin/{base}"])
        if r.returncode:
            r = sh(["git", "-C", src, "worktree", "add", "-q", "-B", a["branch"], wt, base])
        if r.returncode:
            raise RuntimeError(f"worktree for {area}: {r.stderr.strip()[:200]}")
    a["worktree"] = wt
    areas_save(areas)
    return wt


BOUT_RUBRIC = ("correctness against the issue and the code 40, evidence (file:line, a test, a screenshot) 30, "
               "scope (does it stay on the bug) 15, clarity 15")


def bout_id(kind, subject, attack):
    return f"{kind}-{hashlib.sha1((subject + attack).encode()).hexdigest()[:8]}"


def bout(kind, a_name, b_name, subject, attack, defense, context="", rounds=None):
    """Arena-style: the attacker's claim, the defender's answer, more rounds between two fresh fighters, then a judge
    who checks the claims and scores both on a rubric. Returns the bout (winner "a" or "b"), saved to state/bouts/."""
    os.makedirs(os.path.join(RUN, "state", "bouts", "work"), exist_ok=True)   # bouts/<id>.json is the bout, work/ the steps
    day = time.strftime("%Y-%m-%d")
    today = [b for b in (jload(p, {}) for p in glob.glob(os.path.join(RUN, "state", "bouts", "*.json")))
             if b.get("day") == day and b.get("status") == "done"]
    if len(today) >= cfg_int("BOUT_MAX", 10):
        return None
    bid = bout_id(kind, subject, attack)
    path = os.path.join(RUN, "state", "bouts", f"{bid}.json")
    old = jload(path)
    if old and old.get("status") == "done":
        return old
    b = {"id": bid, "kind": kind, "a": a_name, "b": b_name, "subject": subject, "day": day, "status": "fighting",
         "rounds": [{"by": "a", "text": attack, "severity": "", "reply": defense}], "winner": "", "scores": {}}
    jsave(path, b)
    fighter = cfg("BOUT_FIGHTER_MODEL", "sonnet")
    for n in range(2, (rounds or cfg_int("BOUT_ROUNDS", 2)) + 1):
        hist = "\n".join(f"Round {i + 1}. {a_name}: {r['text']}\n{b_name}: {r['reply']}" for i, r in enumerate(b["rounds"]))
        att = llm(f"bout:{bid}:a{n}", f"""You argue for {a_name} in a dispute about: {subject}. {context}
So far:\n{hist}\nWrite your next point: attack the weakest part of {b_name}'s last answer with evidence you can check
(open files, run commands). Answer {{"text": "<at most 120 words>"}}.""", fighter, "low",
                  os.path.join(RUN, "state", "bouts", "work", f"{bid}-a{n}.json"), ("text",))
        if not att:
            break
        dfn = llm(f"bout:{bid}:b{n}", f"""You argue for {b_name} in a dispute about: {subject}. {context}
So far:\n{hist}\nRound {n}. {a_name}: {att['text']}\nAnswer it with evidence you can check, or concede the parts that are
right. Answer {{"text": "<at most 120 words>"}}.""", fighter, "low",
                  os.path.join(RUN, "state", "bouts", "work", f"{bid}-b{n}.json"), ("text",))
        b["rounds"].append({"by": "a", "text": att["text"], "severity": "", "reply": (dfn or {}).get("text", "")})
        jsave(path, b)
    hist = "\n".join(f"Round {i + 1}. {a_name}: {r['text']}\n{b_name}: {r['reply']}" for i, r in enumerate(b["rounds"]))
    j = llm(f"bout:{bid}:judge", f"""You judge a dispute about: {subject}. {context}
{hist}
Check every claim yourself (open the files, the screenshots, run what is cheap). Score both sides on this rubric:
{BOUT_RUBRIC}. A claim shown false scores zero for correctness. Answer {{"winner": "a|b", "scores": {{"a": <0-100>,
"b": <0-100>}}, "reason": "<two sentences>"}}.""", cfg("BOUT_JUDGE_MODEL", "opus"), "medium",
              os.path.join(RUN, "state", "bouts", "work", f"{bid}-judge.json"), ("winner", "scores"))
    if (j or {}).get("winner") not in ("a", "b"):
        b["status"], b["reason"] = "no-verdict", "the judge gave no valid answer; nothing was decided"
        jsave(path, b)
        log(f"bout {bid}: no verdict; the next run tries again")
        return None
    b["winner"] = j["winner"]
    b["scores"], b["reason"], b["status"] = j.get("scores", {}), j.get("reason", ""), "done"
    jsave(path, b)
    log(f"bout {bid}: {a_name if b['winner'] == 'a' else b_name} wins ({b['reason'][:120]})")
    return b


def advance(jid):
    """What comes after a finished job: fix -> review -> (fix again | recheck) -> (fix again | done)."""
    jobs, areas = jobs_load(), areas_load()
    j = jobs[jid]
    area, a = j["area"], areas[j["area"]]
    res = result_of(j)
    if j["role"] == "fix":
        if res.get("pr"):
            a["pr"] = res["pr"]
        if res.get("worktree") and os.path.isdir(res["worktree"]):
            a["worktree"] = res["worktree"]
        disputed, kept = [], []
        prev = result_of(latest(area, "review"))
        for rb in res.get("rebuttals", []):
            try:
                c = prev.get("comments", [])[int(rb.get("comment"))]
            except (ValueError, TypeError, IndexError):
                continue
            if c.get("severity") not in ("blocker", "major"):
                continue
            bt = dispute_bout(area, a, c, rb, cached=True)
            (disputed if bt and bt["winner"] == "b" else kept).append(c)
        a["unresolved"] = [c for c in a.get("unresolved", []) if c not in disputed]
        areas_save(areas)
        new_job("review", area, feedback=(
            "\n## Earlier review\nThe last review asked for these changes; check they were made:\n" +
            "\n".join(f"- [{c.get('severity')}] {c.get('file')}:{c.get('line')} {c.get('text')}" for c in prev.get("comments", [])
                      if c.get("severity") in ("blocker", "major") and c not in disputed) +
            ("\nThese were overruled by a judge and must not be raised again:\n" +
             "\n".join(f"- {c.get('file')}:{c.get('line')} {c.get('text')}" for c in disputed) if disputed else ""))
            if prev else "")
    elif j["role"] == "review":
        hard = [c for c in res.get("comments", []) if c.get("severity") in ("blocker", "major")]
        if res.get("verdict") == "changes" and hard and a["review_round"] < cfg_int("REVIEW_ROUNDS", 2):
            a["review_round"] += 1
            areas_save(areas)
            new_job("fix", area, feedback="\n## Review comments to address\nA reviewer read your diff (comment index: text). Fix "
                    "these, or answer one under `rebuttals` if you are sure it is wrong:\n" +
                    "\n".join(f"- {i}: [{c.get('severity')}] {c.get('file')}:{c.get('line')} {c.get('text')}"
                              for i, c in enumerate(res.get("comments", []))))
        else:
            if res.get("verdict") == "changes" and hard:
                a["unresolved"] = hard
                areas_save(areas)
            todo = [k for k in a["bugs"] if a["verdicts"].get(k) not in ("PASS",)]
            fixed = set(result_of(latest(area, "fix")).get("fixed", [])) | {k for k, v in a["verdicts"].items() if v != "PASS"}
            keys = [k for k in todo if k in fixed] or todo
            new_job("recheck", area, keys=keys)
    elif j["role"] == "recheck":
        for r in res.get("results", []):
            if r.get("key") in a["bugs"]:
                a["verdicts"][r["key"]] = r.get("verdict", "FAIL")
                a.setdefault("recheck", {})[r["key"]] = r
        again = [r for r in res.get("results", []) if r.get("verdict") != "PASS" and r.get("frontend_fixable", True)
                 and r.get("key") in a["bugs"]]
        if again and a["attempt"] <= cfg_int("FIX_RETRIES", 2):
            a["attempt"] += 1
            a["review_round"] = 1
            areas_save(areas)
            new_job("fix", area, keys=[r["key"] for r in again], feedback=(
                "\n## Re-check results to address\nThe reporters re-tested your branch. These did not pass:\n" +
                "\n".join(f"- `{r['key']}` #{r.get('issue')} {r.get('persona')}: {r.get('verdict')}: {r.get('note')} "
                          f"(screenshot {r.get('shot')})" for r in again)))
        else:
            left = [k for k in a["bugs"] if a["verdicts"].get(k) != "PASS"]
            a["status"] = "needs-human" if left or a.get("unresolved") else "done"
            areas_save(areas)
            log(f"fix: {area} {a['status']}: " + ", ".join(f"{k}={v}" for k, v in a["verdicts"].items()))
            finalize(area)


def settle_disputes(j):
    """Bouts for review comments the fixer disputes, run before job_done takes the lock: a bout is several model
    calls, and every other job would wait on the lock meanwhile. advance() then reads the finished bouts."""
    if j["role"] != "fix":
        return
    res, a = jload(j["result"], {}) or {}, areas_load().get(j["area"], {})
    prev = result_of(latest(j["area"], "review"))
    for rb in res.get("rebuttals", []) if isinstance(res, dict) else []:
        try:
            c = prev.get("comments", [])[int(rb.get("comment"))]
        except (ValueError, TypeError, IndexError):
            continue
        if c.get("severity") in ("blocker", "major"):
            dispute_bout(j["area"], a, c, rb)


def dispute_bout(area, a, c, rb, cached=False):
    subject = f"{area}: review comment on {c.get('file')}:{c.get('line')}"
    if cached:     # under the jobs lock: only read a bout settle_disputes already finished
        b = jload(os.path.join(RUN, "state", "bouts", f"{bout_id('review', subject, c.get('text', ''))}.json"), {})
        return b if b.get("status") == "done" else None
    return bout("review", "reviewer", "fixer", f"{area}: review comment on {c.get('file')}:{c.get('line')}",
                c.get("text", ""), rb.get("text", ""), f"Branch {a.get('branch')} in {a.get('worktree')}, base "
                f"{cfg('FIX_BASE', 'main')}. The fixer says the review comment is wrong.")


def job_done(jid):
    j0 = jobs_load().get(jid, {})
    if j0.get("status") == "done":
        log(f"fix: {jid} was already done; nothing queued again")
        return True
    if j0:
        give_backend(jid)
        settle_disputes(j0)
    with jobs_lock():
        jobs = jobs_load()
        j = jobs[jid]
        if j["status"] == "done":
            return True
        res = jload(j["result"])
        missing = [k for k in ROLE_KEYS[j["role"]] if not isinstance(res, dict) or k not in res]
        if missing:
            return job_fail(jid, f"result {j['result']} missing {missing or 'entirely'}")
        j["status"], j["finished"] = "done", time.strftime("%Y-%m-%dT%H:%M:%S")
        jobs_save(jobs)
        log(f"fix: {jid} done")
        advance(jid)
        return True


def give_backend(jid):
    if areas_load().get(jobs_load().get(jid, {}).get("area"), {}).get("kind") in ("be", "both"):
        sh(["bash", os.path.join(SCRIPTS, "fix-stack.sh"), RUN, "be-give", jid])


def job_fail(jid, why):
    give_backend(jid)
    with jobs_lock():
        jobs, areas = jobs_load(), areas_load()
        j = jobs[jid]
        j["tries"] += 1
        j["why"] = why
        if j["tries"] < cfg_int("JOB_TRIES", 2):
            j["status"] = "queued"
            log(f"fix: {jid} failed ({why}); queued again")
        else:
            j["status"] = "failed"
            areas[j["area"]]["status"] = "needs-human"
            areas[j["area"]].setdefault("unresolved", []).append({"severity": "blocker", "text": f"{jid} failed: {why}"})
            areas_save(areas)
            log(f"fix: {jid} failed twice ({why}); {j['area']} needs a person")
            jobs_save(jobs)
            finalize(j["area"])
            return False
        jobs_save(jobs)
        return False


def ready_jobs(limit=None):
    """Queued jobs that may start now: under FIX_PARALLEL running, and a stale claim (10 min) counts as queued."""
    with jobs_lock():
        jobs = jobs_load()
        now = time.time()
        for j in jobs.values():
            if j["status"] == "claimed" and now - j.get("claimed_at", 0) > 600:
                j["status"] = "queued"
        busy = sum(1 for j in jobs.values() if j["status"] in ("running", "claimed"))
        room = max(0, cfg_int("FIX_PARALLEL", 4) - busy)
        if limit is not None:
            room = min(room, limit)
        out = [j for j in sorted(jobs.values(), key=lambda j: j["created"]) if j["status"] == "queued"][:room]
        for j in out:
            j["status"], j["claimed_at"] = "claimed", now
        jobs_save(jobs)
        return out


def run_cli_job(j):
    """The cli runner: one fresh claude or codex process per job, in the area's worktree."""
    try:
        with jobs_lock():
            cwd = ensure_worktree(j["area"])
            jobs = jobs_load()
            jobs[j["id"]].update(status="running", worktree=cwd, started=time.strftime("%Y-%m-%dT%H:%M:%S"), runner="cli")
            jobs_save(jobs)
    except RuntimeError as e:
        return job_fail(j["id"], str(e))
    model = j["model"] if j["model"] != "auto" else {"fix": "opus", "review": "", "recheck": "sonnet"}[j["role"]]
    engine = cfg("REVIEW_ENGINE", "codex" if shutil.which("codex") else "claude") if j["role"] == "review" else "claude"
    if ":" in model:
        engine, model = model.split(":", 1)
    elif re.search(r"opus|sonnet|haiku|fable|claude", model or ""):
        engine = "claude"
    if engine == "claude" and not model:
        model = "sonnet" if j["role"] == "review" else "opus"
    prompt = read(j["prompt"])
    if engine == "codex":
        cmd = ["codex", "exec", "--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check", "-C", cwd,
               *(["-m", model] if model else [])]
    else:
        cmd = ["claude", "-p", "--dangerously-skip-permissions", "--output-format", "json",
               "--model", model]
    with jobs_lock():
        jobs = jobs_load()
        jobs[j["id"]]["model"] = f"{engine}:{model or 'default'}"
        jobs_save(jobs)
    with open(os.path.join(RUN, "logs", f"job-{j['id']}.log"), "w") as lf:
        try:
            subprocess.run(cmd, input=prompt, text=True, stdout=lf, stderr=subprocess.STDOUT, cwd=cwd,
                           timeout=cfg_int("FIX_TIMEOUT_S", 7200))
        except subprocess.TimeoutExpired:
            return job_fail(j["id"], "timed out")
        except OSError as e:          # the engine is not installed, the worktree vanished
            return job_fail(j["id"], f"could not start {cmd[0]}: {e}")
    return job_done(j["id"])


def run_cli_job_safe(j):
    try:
        return run_cli_job(j)
    except Exception as e:            # never leave a job 'running' with nobody on it: fix() would wait forever
        log(f"fix: {j['id']} crashed in crowd.py: {e!r}")
        return job_fail(j["id"], f"crowd.py error: {e!r}")


def fix():
    if cfg("FIXING", "off") != "on":
        log("fix: FIXING is not on, skipped")
        return
    if trackers() and not cfg("FILE_CMD") and not plan_approved():
        log("fix: the file plan is not approved yet, so nothing is filed to fix")
        return
    areas = group()
    jobs = jobs_load()
    for name, a in areas.items():
        if a["status"] == "queued" and not any(j["area"] == name for j in jobs.values()):
            new_job("fix", name)
    if runner() != "cli":
        n = sum(1 for j in jobs_load().values() if j["status"] == "queued")
        log(f"fix: {n} jobs ready for the {runner()} runner; the orchestrating session launches them "
            f"(crowd.py {RUN} jobs next)")
        return
    with cf.ThreadPoolExecutor(cfg_int("FIX_PARALLEL", 4)) as ex:
        live = set()
        while True:
            live = {f for f in live if not f.done()}
            for j in ready_jobs(cfg_int("FIX_PARALLEL", 4) - len(live)):
                live.add(ex.submit(run_cli_job_safe, j))
            if not live and all(a["status"] in TERMINAL for a in areas_load().values()):
                break
            if not live and not any(j["status"] in ("queued", "claimed", "running") for j in jobs_load().values()):
                break
            time.sleep(POLL)
    log("fix: every area is done or needs a person")


def finalize(area):
    """The PR body, written from the results: per bug the issue, reporter, cause, change, before / after / re-check
    images, the re-check verdict; then checks, review and bouts in collapsed blocks."""
    areas, bugs = areas_load(), bug_rows()
    a = areas[area]
    fixes = sorted((j for j in jobs_load().values() if j["area"] == area and j["role"] == "fix" and j["status"] == "done"),
                   key=lambda j: (j["attempt"], j["round"]))
    merged = {"changes": {}, "cause": {}, "evidence": {}, "needs_backend": [], "not_changed": []}
    for j in fixes:
        r = result_of(j)
        for k in ("changes", "cause", "evidence"):
            merged[k].update(r.get(k, {}))
        merged["needs_backend"] = r.get("needs_backend", merged["needs_backend"])
        merged["not_changed"] = r.get("not_changed", merged["not_changed"])
        if r.get("pr"):
            a["pr"] = r["pr"]
    last = result_of(fixes[-1]) if fixes else {}
    rc = a.get("recheck", {})
    lines = [f"Fixes from the {cfg('RUN_LABEL', 'crowd-qa')} crowd run, area **{area}**. Each bug was re-tested on this "
             f"branch by the simulated user who reported it.", "",
             "| issue | reporter | re-check | note |", "|---|---|---|---|"]
    for k in a["bugs"]:
        b, r = bugs.get(k, {}), rc.get(k, {})
        lines.append(f"| #{b.get('issue')} | {b.get('reporter')} | {r.get('verdict', 'not re-checked')} | "
                     f"{(r.get('note') or '').replace('|', '/')} |")
    for k in a["bugs"]:
        b, r, ev = bugs.get(k, {}), rc.get(k, {}), merged["evidence"].get(k, {})
        lines += ["", f"### Fixes #{b.get('issue')}: {b.get('title')}", "",
                  f"Reported by **{b.get('reporter')}**" + (f", also hit by {', '.join(b['confirmers'])}" if b.get("confirmers") else "") + ".", ""]
        if merged["cause"].get(k):
            lines.append(f"**Cause:** {merged['cause'][k]}")
        if merged["changes"].get(k):
            lines.append(f"**Change:** {merged['changes'][k]}")
        imgs = [(t, u) for t, u in (("Before", ev.get("before")), ("After", ev.get("after")), ("Re-check", r.get("shot"))) if u]
        if imgs:
            lines += ["", "| " + " | ".join(t for t, _ in imgs) + " |", "|" + "---|" * len(imgs),
                      "| " + " | ".join(f"<img src=\"{u}\" width=\"260\">" for _, u in imgs) + " |"]
        if r:
            lines.append(f"\n**Re-check by {r.get('persona')}: {r.get('verdict')}.** {r.get('note', '')}"
                         + (f" ([comment]({r['comment']}))" if r.get("comment") else ""))
    if merged["needs_backend"]:
        lines += ["", "### Needs backend", ""] + [f"- `{n.get('key')}` #{bugs.get(n.get('key'), {}).get('issue')}: {n.get('why')}"
                                                   for n in merged["needs_backend"]]
    if merged["not_changed"]:
        lines += ["", "### Not changed", ""] + [f"- `{n.get('key')}` #{bugs.get(n.get('key'), {}).get('issue')}: {n.get('why')}"
                                                 for n in merged["not_changed"]]
    if a.get("unresolved"):
        lines += ["", "### Open points for a person", ""] + [f"- [{c.get('severity')}] {c.get('file', '')}:{c.get('line', '')} "
                                                              f"{c.get('text')}" for c in a["unresolved"]]
    ch = last.get("checks", {})
    if ch:
        lines += ["", f"<details><summary>Checks: <code>{ch.get('cmd', '')}</code> {'passed' if ch.get('ok') else 'FAILED'}"
                  "</summary>", "", "```", str(ch.get("tail", ""))[-3000:], "```", "</details>"]
    revs = sorted((j for j in jobs_load().values() if j["area"] == area and j["role"] == "review" and j["status"] == "done"),
                  key=lambda j: (j["attempt"], j["round"]))
    if revs:
        lines += ["", "<details><summary>Review</summary>", ""]
        for j in revs:
            r = result_of(j)
            lines.append(f"**Attempt {j['attempt']}, round {j['round']}, {j.get('model', '')}: {r.get('verdict')}.** {r.get('summary', '')}")
            lines += [f"- [{c.get('severity')}] `{c.get('file')}:{c.get('line')}` {c.get('text')}" for c in r.get("comments", [])]
            lines.append("")
        lines.append("</details>")
    bouts = [b for b in (jload(p, {}) for p in sorted(glob.glob(os.path.join(RUN, "state", "bouts", "review-*.json"))))
             if b.get("subject", "").startswith(f"{area}:")]
    if bouts:
        lines += ["", "<details><summary>Disputes settled by a judge</summary>", ""]
        for b in bouts:
            lines.append(f"**{b['subject']}**: {b['a'] if b['winner'] == 'a' else b['b']} won "
                         f"({b.get('scores', {}).get('a')} to {b.get('scores', {}).get('b')}). {b.get('reason', '')}")
            lines += [f"> {b['a']}: {r['text']}\n>\n> {b['b']}: {r['reply']}\n" for r in b["rounds"]]
        lines.append("</details>")
    lines += ["", "🤖 Generated with [Claude Code](https://claude.com/claude-code)"]
    body = "\n".join(lines) + "\n"
    bf = fix_path("jobs", f"pr-{area}.md")
    os.makedirs(os.path.dirname(bf), exist_ok=True)
    with open(bf, "w") as f:
        f.write(body)
    prs = jload(os.path.join(RUN, "state", "prs.json"), {})
    counts = {v: sum(1 for k in a["bugs"] if a["verdicts"].get(k) == v.upper()) for v in ("pass", "partial", "fail")}
    if a.get("pr"):
        repo = trackers().get(a["tracker"], a["tracker"])
        r = sh(["gh", "pr", "edit", a["pr"], "-R", repo, "--body-file", bf])
        if r.returncode:
            log(f"fix: could not update {a['pr']}: {r.stderr.strip()[:200]}")
        if a["status"] == "needs-human":
            sh(["gh", "label", "create", "--force", "-R", repo, "needs-human"])
            sh(["gh", "pr", "edit", a["pr"], "-R", repo, "--add-label", "needs-human"])
    num = re.search(r"/pull/(\d+)", a.get("pr", ""))
    prs[area] = {"url": a.get("pr", ""), "number": int(num.group(1)) if num else None, "status": a["status"],
                 "bugs": a["bugs"], "recheck": counts}
    jsave(os.path.join(RUN, "state", "prs.json"), prs)
    areas_save(areas)


def jobs_cli(args):
    """crowd.py <run> jobs next [--max N] | start <id> [--runner R] [--model M] [--thread T] [--worktree P]
       | done <id> | fail <id> <why> | requeue-running | list"""
    sub = args[0] if args else "list"
    opt = {args[i][2:]: args[i + 1] for i in range(1, len(args) - 1) if args[i].startswith("--")}
    if sub == "next":
        out = []
        for j in ready_jobs(int(opt["max"]) if "max" in opt else None):
            if runner() == "subagent" and repos().get(areas_load()[j["area"]]["tracker"]):
                with jobs_lock():
                    ensure_worktree(j["area"])          # a subagent gets a ready checkout; t3 makes its own
            a = areas_load()[j["area"]]
            out.append({**j, "prompt_text": read(j["prompt"]), "kind": a["kind"], "worktree": a.get("worktree", ""),
                        "launch": "new-worktree" if j["role"] == "fix" and not a.get("worktree") else "existing-worktree"})
        print(json.dumps(out, indent=1))
    elif sub == "start":
        with jobs_lock():
            jobs = jobs_load()
            j = jobs[args[1]]
            j.update(status="running", started=time.strftime("%Y-%m-%dT%H:%M:%S"),
                     **{k: v for k, v in opt.items() if k in ("runner", "model", "thread", "persona")})
            if opt.get("worktree"):
                j["worktree"] = opt["worktree"]
                areas = areas_load()
                areas[j["area"]]["worktree"] = opt["worktree"]
                areas_save(areas)
            jobs_save(jobs)
        print(f"{args[1]} running")
    elif sub == "done":
        ok = job_done(args[1])
        print(json.dumps({"ok": ok, "ready": sum(1 for j in jobs_load().values() if j["status"] == "queued"),
                          "areas": {k: v["status"] for k, v in areas_load().items()}}))
    elif sub == "fail":
        job_fail(args[1], " ".join(args[2:]) or "failed")
        print(f"{args[1]}: {jobs_load()[args[1]]['status']}")
    elif sub == "requeue-running":
        with jobs_lock():
            jobs = jobs_load()
            n, finished = 0, []
            for j in jobs.values():
                if j["status"] in ("running", "claimed"):
                    if jload(j["result"]):
                        finished.append(j["id"])
                    else:
                        j["status"], n = "queued", n + 1
            jobs_save(jobs)
        for jid in finished:              # the agent wrote its result but nobody reported it
            job_done(jid)
        print(f"{n} jobs queued again, {len(finished)} finished from their results")
    else:
        for j in sorted(jobs_load().values(), key=lambda j: j["created"]):
            print(f"{j['id']:34} {j['status']:9} {j['runner']:8} {j['model']:28} {j.get('thread', '')}")
        for k, a in areas_load().items():
            print(f"area {k:20} {a['status']:12} attempt {a['attempt']} pr {a.get('pr') or '-'}")


# --------------------------------------------------------------------------------------------------- status
PRICE = {"opus": (4, 20), "sonnet": (2, 10), "haiku": (0.1, 0.5)}   # $ per million tokens in / out


def tester_spend():
    """Claude tester spend, read from each session's transcript (every chunk's session id is kept). Estimate:
    cache reads at 0.1x input, cache writes at 1.25x. Codex testers run on a subscription and are not priced."""
    usd, seen = 0.0, set()
    for sf in glob.glob(os.path.join(RUN, "lanes", "*", "round*", "claude-session*")):
        for sid in set(read(sf).split()):
            for tp in glob.glob(os.path.expanduser(f"~/.claude/projects/*/{sid}.jsonl")):
                for line in read(tp).splitlines():
                    m = jload_str(line).get("message") or {}
                    u, mid = m.get("usage"), m.get("id")
                    if not u or mid in seen:
                        continue
                    seen.add(mid)
                    pin, pout = next((v for k, v in PRICE.items() if k in (m.get("model") or "")), PRICE["sonnet"])
                    usd += (u.get("input_tokens", 0) * pin + u.get("cache_read_input_tokens", 0) * pin * 0.1 +
                            u.get("cache_creation_input_tokens", 0) * pin * 1.25 + u.get("output_tokens", 0) * pout) / 1e6
    return usd


def status():
    st = jload(os.path.join(RUN, "state", "crowd-state.json"), {})
    counts = {}
    for v in st.values():
        counts[v] = counts.get(v, 0) + 1
    usd = 0.0
    for line in read(os.path.join(RUN, "state", "costs.jsonl")).splitlines():
        usd += float(jload_str(line).get("usd") or 0)
    tu = tester_spend()
    print(f"character-days: {counts or '(not started)'}; model steps ${usd:.2f}; Claude testers ${tu:.2f} (est.); "
          f"total ${usd + tu:.2f}", flush=True)
    for k in ["STACK_DOWN", "TESTER_FALLBACK"] + [os.path.basename(p) for p in glob.glob(os.path.join(RUN, "QUOTA_PAUSE.*"))
                                                 if "lifted" not in p]:
        if os.path.exists(os.path.join(RUN, k)):
            print(f"{k}: {read(os.path.join(RUN, k)).strip()}")
    subprocess.run(["python3", os.path.join(SCRIPTS, "status.py"), RUN])
    if areas_load():
        print("fix areas: " + ", ".join(f"{k}={a['status']}" + (f" {a['pr']}" if a.get("pr") else "")
                                        for k, a in areas_load().items()))
        js = {}
        for j in jobs_load().values():
            js[j["status"]] = js.get(j["status"], 0) + 1
        print(f"fix jobs: {js}")


def main():
    os.makedirs(os.path.join(RUN, "state"), exist_ok=True)
    if CMD == "status":
        return status()
    if CMD == "approve":
        os.makedirs(os.path.join(RUN, "state"), exist_ok=True)
        jsave(os.path.join(RUN, "state", "FILE_PLAN_APPROVED"),
              {"at": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "keys": sorted(jload(os.path.join(RUN, "state", "file-plan.json"), {}))})
        log("file: plan approved")
        sys.argv[2:3] = ["file"]
        globals()["CMD"] = "file"
    if CMD == "jobs":              # short calls from the orchestrating session, several at once: no pid guard
        return jobs_cli(sys.argv[3:])
    if CMD == "pr":
        for area in areas_load():
            finalize(area)
        return
    if CMD == "makeup":
        if len(sys.argv) < 4:
            sys.exit("usage: crowd.py <run> makeup <new-run> [--scen-max N]")
        sys.exit(subprocess.run(["python3", os.path.join(SCRIPTS, "makeup.py"), RUN, *sys.argv[3:]]).returncode)
    pid_f = os.path.join(RUN, "logs", "crowd.pid")
    old = read(pid_f).strip()
    if old and old != str(os.getpid()) and subprocess.run(["kill", "-0", old], capture_output=True).returncode == 0:
        sys.exit(f"crowd.py already runs for this run dir (pid {old})")
    os.makedirs(os.path.dirname(pid_f), exist_ok=True)
    with open(pid_f, "w") as f:
        f.write(str(os.getpid()))
    steps = {"plan": plan, "run": run, "dedupe": dedupe, "file": file_issues, "fix": fix, "report": report}
    for name in (list(steps) if CMD == "all" else [CMD]):
        if name not in steps:
            sys.exit(__doc__)
        if name == "file" and cfg("FILING", "on") == "off":
            log("file: FILING=off, skipped")
            continue
        steps[name]()


if __name__ == "__main__":
    main()
