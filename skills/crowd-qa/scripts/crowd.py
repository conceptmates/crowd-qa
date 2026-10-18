#!/usr/bin/env python3
"""crowd.py: drives a whole crowd-QA run in code. A model is called only where judgement is needed: the product
map, each character's plan, checking a day's findings, checking a bug before filing, and the launch report.
Everything else (scheduling, waiting, quota pauses, coverage, evidence checks, duplicates, merging, labels,
issue bodies, evidence push, filing) is plain code, so no agent spends tokens waiting on a tester.

usage:
  crowd.py <run> all        plan, run, dedupe, file, report (each step skips work already on disk)
  crowd.py <run> plan|run|dedupe|file|report
  crowd.py <run> status     one screen: every character-day, model spend so far
  crowd.py <run> makeup <new-run>   a new run dir with only the scenarios this run never finished (scripts/makeup.py)

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
        cap = cfg_int("HARD_CAP", 4)  # re-read: capacity changes in config.env apply without a restart
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
        log(f"file: could not read the issues of {repo} ({r.stderr.strip()[:200]}); planning against an empty list")
        items = []
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


def plan_approved():
    return cfg("FILE_AUTO", "no") == "yes" or os.path.exists(os.path.join(RUN, "state", "FILE_PLAN_APPROVED"))


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
        if not plan_approved():
            n = {a: sum(1 for k, p in plan.items() if p["action"] == a and k in {x["key"] for x in todo}) for a in PLAN_ACTIONS}
            log(f"file: plan written to {RUN}/file-plan.md ({n}); nothing sent. Approve with: crowd.py {RUN} approve")
            notify = cfg("NOTIFY_CMD")
            if notify:
                sh(["bash", "-c", f'{notify} "$1"', "_", f"crowd-qa file plan ready for approval: {RUN}/file-plan.md"])
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
                        done[x["key"]] = {"url": url, "title": x["title"], "tracker": key, "action": act,
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
hurt; what nobody could test and why. Quote characters sparingly. Plain prose, no hype words, no closing summary.""",
        cfg("REPORT_MODEL", "opus"), cfg("REPORT_EFFORT", "medium"), out, text=True)
    if ok:
        with open(sp, "w") as f:
            f.write(sig)
    log(f"report: {out}")


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


def main():
    os.makedirs(os.path.join(RUN, "state"), exist_ok=True)
    if CMD == "status":
        return status()
    if CMD == "approve":
        os.makedirs(os.path.join(RUN, "state"), exist_ok=True)
        with open(os.path.join(RUN, "state", "FILE_PLAN_APPROVED"), "w") as f:
            f.write(time.strftime("%Y-%m-%dT%H:%M:%S") + "\n")
        log("file: plan approved")
        sys.argv[2:3] = ["file"]
        globals()["CMD"] = "file"
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
