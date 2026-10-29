#!/usr/bin/env python3
"""Renders the crowd run as a live town: <run>/square/index.html is a 3D world, square.html the Discord-style square.

square.html:
  left    channels: #town-square, #bugs, #notices, #before-outage, then one channel per character with a status
          dot, day and progress bar
  middle  the square as threads (a post plus its me-too / cant-repro / disagree / tip replies), or, in a
          character's channel: their to-do list for today (each scenario and its status), their findings,
          judge verdicts, their latest screenshot (a live view) and their own posts
  right   members grouped by what they are doing now
  #bugs   every bug in one place: found, judged, filed, being fixed, fixed, or dropped (not worth an issue, not a bug,
          refused, skipped) with the reason; each with its reporter, me-toos, issue, fix stage, re-test verdict and PR.
          A bug that moved since you last looked is marked "updated"; the channel's badge counts them.

Every pass also writes square/state.json (and state.js, the same data for pages opened from file://): characters,
posts, bugs, fix/review/recheck jobs, bouts and PRs. index.html is scripts/world/ in one file with the state
inlined; it polls state.json every 5 s. square.html polls square-data.js every 15 s and patches itself in place.
Without scripts/world/ (a run dir's copy of the scripts), index.html just forwards to square.html.

  square-view.py <run> [--embed]     write once (--embed inlines images as base64, for sharing)
  square-view.py <run> --watch       rewrite every 15 s until killed
"""
import base64
import colorsys
import glob
import hashlib
import json
import os
import re
import sys
import time

ROLE_COLOR = {"designer": "#eb459e", "hacker": "#f23f43", "speedy": "#f0b232", "owner": "#5865f2",
              "agent": "#23a55a", "sales_rep": "#1abc9c", "viewer": "#949ba4", "builder": "#e67e22",
              "customer": "#9b59b6", "agency": "#3498db", "organiser": "#faa61a"}
PALETTE = ["#e67e22", "#1abc9c", "#9b59b6", "#3498db", "#e74c3c", "#2ecc71", "#f1c40f", "#16a085", "#d35400",
           "#8e44ad", "#2980b9", "#c0392b", "#27ae60", "#f39c12", "#7f8c8d", "#ff6b81"]
STAGES = ["plan", "run", "file", "fix", "review", "recheck", "pr", "report", "done"]
WORLD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "world")


def read(path, default=None):
    try:
        with open(path) as f:
            return json.load(f) if path.endswith(".json") else f.read()
    except Exception:
        return default


def field(card, name):
    m = re.search(rf"^- \*\*{re.escape(name)}\*\*: *(.*)$", card or "", re.M)
    return m.group(1).strip() if m else ""


def day_scenarios(md, day):
    """[(id, title, tags)] for '## Day <day>' of scenarios.md."""
    if not md:
        return []
    m = re.search(rf"^## Day {day}\b(.*?)(?=^## Day |\Z)", md, re.M | re.S)
    if not m:
        return []
    out = []
    for h in re.finditer(r"^#{3,4} +(S\d+)\s*[—–-]\s*(.*)$", m.group(1), re.M):
        title = h.group(2)
        tags = re.findall(r"\[([a-z-]+)\]", title)
        out.append((h.group(1), re.sub(r"\s*\[[a-z-]+\]", "", title).strip(), tags))
    return out


def src(path, run, embed):
    if not path or not os.path.exists(path):
        return ""
    if embed:
        return "data:image/png;base64," + base64.b64encode(open(path, "rb").read()).decode()
    return os.path.relpath(path, os.path.join(run, "square"))


def collect(run, embed):
    lanes = read(f"{run}/lanes.json", []) or []
    order = [l["id"] for l in lanes]
    deps = {l["id"]: l.get("depends_on", []) for l in lanes}
    people = []
    for cid in order:
        d = f"{run}/lanes/{cid}"
        card = read(f"{d}/card.md", "")
        st = read(f"{d}/state.json", {}) or {}
        rounds = sorted(glob.glob(f"{d}/round*"), key=lambda p: int(re.sub(r"\D", "", os.path.basename(p)) or 0))
        day = int(re.sub(r"\D", "", os.path.basename(rounds[-1]))) if rounds else 1
        rd = f"{d}/round{day}"
        rep = read(f"{rd}/report.json", {}) or {}
        by_id = {s.get("id"): s for s in rep.get("scenarios", [])}
        todo = []
        for sid, title, tags in day_scenarios(read(f"{d}/scenarios.md", ""), day):
            s = by_id.get(sid, {})
            todo.append({"id": sid, "title": title, "tags": tags, "status": s.get("status") or "pending",
                         "note": (s.get("notes") or s.get("actual") or "")[:240]})
        shots = sorted(glob.glob(f"{rd}/shots/*.png"), key=os.path.getmtime)
        verdicts = []
        for r in rounds:
            v = read(f"{r}/verdict.json")
            if v:
                verdicts.append({"day": int(re.sub(r"\D", "", os.path.basename(r))), "coverage": v.get("coverage_score"),
                                 "satisfied": v.get("satisfied"), "in_character": v.get("in_character"),
                                 "verified": [f.get("title") for f in v.get("verified_findings", [])],
                                 "reason": (v.get("reason") or "")[:400]})
        status = st.get("status") or ("queued" if not rounds else "starting")
        if os.path.exists(f"{rd}/DONE"):
            status = "done" if "exit=0" in (read(f"{rd}/DONE", "") or "") else "stopped"
        if status == "done" and verdicts and verdicts[-1]["day"] == day:
            status = "judged"
        elif status == "done":
            status = "judging"
        done_n = sum(1 for t in todo if t["status"] not in ("pending", "not_run"))
        people.append({
            "id": cid, "name": field(card, "Name") or cid, "role": field(card, "Role"),
            "age_city": field(card, "Age / city"), "business": field(card, "Business"),
            "device": field(card, "Device"), "wants": field(card, f"Wants on day {day}"),
            "email": (field(card, "Email") or "").split(",")[0], "depends_on": deps.get(cid, []),
            "status": status, "day": day, "updated": (st.get("updated") or "")[11:16],
            "surface": st.get("surface") or field(card, "Surface"), "device_id": st.get("device") or "",
            "engine": st.get("engine") or "",
            "todo": todo, "done": done_n, "total": len(todo),
            "findings": [{"title": f.get("title", ""), "severity": f.get("severity", ""), "type": f.get("type", "")}
                         for f in rep.get("findings", [])],
            "shots": len(shots), "live": src(shots[-1], run, embed) if shots else "",
            "live_name": os.path.basename(shots[-1]) if shots else "", "verdicts": verdicts,
        })
    feed = []
    for line in open(f"{run}/square/feed.jsonl") if os.path.exists(f"{run}/square/feed.jsonl") else []:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        e["img"] = src(e.get("shot"), run, embed)
        e.pop("shot", None)
        feed.append(e)
    epoch = (read(f"{run}/square/epoch", "") or "").strip()
    fallback = (read(f"{run}/TESTER_FALLBACK", "") or "").strip()
    title = ""
    for line in (read(f"{run}/config.env", "") or "").splitlines():
        if line.startswith("CROWD_TITLE="):
            title = line.split("=", 1)[1].split("#")[0].strip().strip('"')
    return {"people": people, "feed": feed, "epoch": epoch, "quota": fallback, "title": title or os.path.basename(run),
            "stack_down": (read(f"{run}/STACK_DOWN", "") or "").strip(),
            "generated": time.strftime("%H:%M:%S"), "run": os.path.basename(run)}


def role_key(role):
    return (role or "").lower().split("(")[0].strip().replace(" ", "_")


def colors(people):
    """A distinct colour per character: its role colour, hue-shifted when another character already has it."""
    used, out = {}, {}
    for p in people:
        base = ROLE_COLOR.get(role_key(p["role"])) or PALETTE[int(hashlib.sha1(role_key(p["role"]).encode()).hexdigest(), 16) % len(PALETTE)]
        n = used.get(base, 0)
        used[base] = n + 1
        if n:
            r, g, b = (int(base[k:k + 2], 16) / 255 for k in (1, 3, 5))
            h, l, sat = colorsys.rgb_to_hls(r, g, b)
            r, g, b = colorsys.hls_to_rgb((h + 0.09 * n) % 1, min(0.75, max(0.35, l + (0.08 if n % 2 else -0.08))), sat)
            base = "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))
        out[p["id"]] = base
    return out


def as_list(x, key):
    """state files keyed by id ({id: {...}}) or already lists -> a list of dicts carrying the id under `key`."""
    if isinstance(x, dict):
        return [{key: k, **(v if isinstance(v, dict) else {"value": v})} for k, v in x.items()]
    return [v for v in x if isinstance(v, dict)] if isinstance(x, list) else []


def result_word(r):
    """pass / partial / fail out of a recheck job's result, whatever shape it came in."""
    if isinstance(r, dict):
        r = r.get("verdict") or r.get("status") or r.get("result") or ""
    r = str(r or "").lower()
    return next((w for w in ("partial", "pass", "fail") if w in r), "")


def title_key(title):
    return hashlib.sha1((title or "").encode()).hexdigest()[:12]   # crowd.py keys an issue the same way


def characters_in(xs, ids, reporter="", names=None):
    """Character ids out of a confirmers / cant_repro list. Judges sometimes put a scenario id there (dropped), and a
    cant_repro entry is often a sentence ("Maya could not reproduce it on Android"): it counts for the character it
    names by id, full name or first name."""
    out = []
    for c in xs or []:
        c = c.get("id", "") if isinstance(c, dict) else str(c)
        if c not in ids:
            words = lambda i: {i, (names or {}).get(i) or i, ((names or {}).get(i) or i).split(" ")[0]}
            c = next((i for i in sorted(ids) if any(re.search(rf"(?<!\w){re.escape(w)}(?!\w)", c, re.I) for w in words(i) if w)), "")
        if c in ids and c != reporter and c not in out:
            out.append(c)
    return out


def shot_of(paths, run, embed):
    """The first screenshot that is still on disk, as the pages link it; an http(s) link (an uploaded image) as is."""
    for p in paths or []:
        if isinstance(p, str) and re.match(r"https?://", p):
            return p
        out = src(p, run, embed) if isinstance(p, str) else ""
        if out:
            return out
    return ""


def bug_list(run, people, filed, areas, prs, embed=False):
    """Every bug the run knows about, in one list: what the characters found, what the judges kept, threw out or
    called not worth an issue, what was filed, and how each one is doing in the fix stage (its area's status, its
    re-check verdict, its PR)."""
    s = f"{run}/state"
    deduped = os.path.exists(f"{s}/issues.json")   # only dedupe writes it; [] means nothing was left to file
    issues = read(f"{s}/issues.json", []) or []
    issues = issues if isinstance(issues, list) else []
    refused = read(f"{s}/refused.json", {}) or {}
    refused = refused if isinstance(refused, dict) else {}
    plan = {p.get("key"): p for p in as_list(read(f"{s}/file-plan.json", []), "key")}
    detail = read(f"{s}/areas-detail.json", {}) or {}
    detail = detail if isinstance(detail, dict) else {}
    area_of = {k: a for a, ks in (areas or {}).items() if isinstance(ks, list) for k in ks}
    pr_of = {k: pr for pr in prs if pr.get("url") for k in (pr.get("bugs") or [])}   # finalize writes areas with no PR too
    failed = {j.get("area"): j.get("role") for j in as_list(read(f"{s}/jobs.json", {}), "id") if j.get("status") == "failed"}
    ids = {p["id"] for p in people}
    names = {p["id"]: p["name"] for p in people}
    bugs = {}

    def add(k, f, reporter, state, why=""):
        b = {"key": k, "title": f.get("title", ""), "severity": f.get("severity", ""), "reporter": reporter,
             "confirmers": characters_in(f.get("confirmers"), ids, reporter, names), "state": state,
             "type": f.get("type", ""), "day": f.get("day", ""), "cant_repro": characters_in(f.get("cant_repro"), ids, reporter, names),
             "shot": shot_of(f.get("screenshots"), run, embed), "why": why, "rescued": bool(f.get("rescued"))}
        bugs[k] = b
        return b

    # what each day's judge said: verified, not worth an issue, or not a bug at all
    verified, not_needed, rejected, judged = {}, {}, {}, {}
    for p in people:
        judged[p["id"]] = set()
        for v in sorted(glob.glob(f"{run}/lanes/{p['id']}/round*/verdict.json")):
            day = int(re.sub(r"\D", "", os.path.basename(os.path.dirname(v))) or 0)
            judged[p["id"]].add(day)
            v = read(v, {}) or {}
            if not isinstance(v, dict):
                continue
            rep = read(f"{run}/lanes/{p['id']}/round{day}/report.json", {}) or {}
            found = {str(f.get("id")): f for f in (rep.get("findings") or []) if isinstance(f, dict)} if isinstance(rep, dict) else {}
            for f in (x for x in v.get("verified_findings") or [] if isinstance(x, dict)):
                verified[title_key(f.get("title"))] = ({"day": day, **f}, p["id"])
            for f in (x for x in v.get("not_needed") or [] if isinstance(x, dict)):
                why = f.get("why_not", "") + (" (the severity bar)" if f.get("by") == "code" else "")
                not_needed[title_key(f.get("title"))] = ({"day": day, **f}, p["id"], why)
            for f in (x for x in v.get("rejected") or [] if isinstance(x, dict)):
                seen = dict(found.get(str(f.get("id")), {}))   # the tester's own finding: its severity and screenshots
                seen["screenshots"] = [os.path.join(f"{run}/lanes/{p['id']}/round{day}", x)   # relative to the round, as crowd.py reads them
                                       for x in seen.get("screenshots") or [] if isinstance(x, str)]
                rejected[title_key(f.get("title"))] = ({"day": day, **seen, **f}, p["id"], f.get("reason", ""))

    if deduped:   # after dedupe: one issue per bug, the others who saw it as confirmers
        for i in (x for x in issues if isinstance(x, dict)):
            add(i.get("key") or title_key(i.get("title")), i, i.get("reporter", ""), "verified")
    else:   # before dedupe: what the judges verified, and today's findings still waiting for a judge
        for k, (f, who) in verified.items():
            add(k, f, who, "verified")
        for p in people:
            if p["day"] not in judged[p["id"]]:
                for f in p["findings"]:
                    if title_key(f["title"]) not in bugs:
                        add(title_key(f["title"]), {"day": p["day"], **f}, p["id"], "found")
    for k, (f, who, why) in not_needed.items():
        if k not in bugs:
            add(k, f, who, "not-needed", why)
    for k, (f, who, why) in rejected.items():
        if k not in bugs:
            add(k, f, who, "rejected", why)
    for k, r in refused.items():   # the check before filing: not needed, or not filed for another reason
        if k not in bugs and isinstance(r, dict):
            b = r.get("bug") if isinstance(r.get("bug"), dict) else {"title": r.get("title", "")}
            add(k, b, b.get("reporter", ""), "refused")
    for k, v in filed.items():
        if k not in bugs:
            add(k, v if isinstance(v, dict) else {}, (v or {}).get("reporter", "") if isinstance(v, dict) else "", "filed")

    for k, b in bugs.items():
        p = plan.get(k) or {}
        action = p.get("action", "")
        r = refused.get(k) if isinstance(refused.get(k), dict) else None
        if k in pr_of:
            b["state"] = "in-pr"
        elif k in filed:
            b["state"] = "filed"
        elif r:
            nn = r.get("not_needed") or str(r.get("reason", "")).startswith("not needed:")
            b["state"] = "not-needed" if nn else "refused"
            b["why"] = re.sub(r"^not needed:\s*", "", str(r.get("reason", "")))
        elif action == "skip":
            b["state"], b["why"] = "skipped", p.get("reason", "") or b["why"]
        elif action:
            b["state"] = "planned"
        b["url"] = (filed.get(k) or {}).get("url", "") if isinstance(filed.get(k), dict) else ""
        t = str(p.get("target") or "").lstrip("#")
        b["plan"] = (f"{'a regression of' if action == 'regression' else 'to comment on'} #{t}"
                     if b["state"] == "planned" and t and action in ("comment", "regression") else "")
        b["area"] = area_of.get(k, "")
        b["fix"] = {}
        a = detail.get(b["area"]) if b["area"] else None
        if isinstance(a, dict) and k in (a.get("bugs") or []):
            rc = (a.get("recheck") or {}).get(k) or {}
            rc = rc if isinstance(rc, dict) else {}
            b["fix"] = {"status": a.get("status", ""), "attempt": a.get("attempt", 1),
                        "verdict": str((a.get("verdicts") or {}).get(k, "") or "").upper(), "note": rc.get("note", ""),
                        "by": rc.get("persona", ""), "shot": shot_of([rc.get("shot")], run, embed),
                        "comment": rc.get("comment", ""), "failed": failed.get(b["area"], ""),
                        "unresolved": bool(a.get("unresolved"))}
        pr = pr_of.get(k) or {}
        if not pr.get("url") and b["fix"] and isinstance(a.get("pr"), str) and a["pr"]:   # the fixer's draft, before finalize
            pr = {"url": a["pr"], "number": int((re.search(r"/pull/(\d+)", a["pr"]) or [0, 0])[1]) or "", "status": "draft"}
        b["pr"] = {"url": pr.get("url", ""), "number": pr.get("number", ""), "status": pr.get("status", "")} if pr.get("url") else {}
    return list(bugs.values())


def stage(run, people, jobs, prs, areas):
    s = f"{run}/state"
    mtime = lambda p: os.path.getmtime(p) if os.path.exists(p) else 0
    order = ["fix", "review", "recheck"]
    roles = lambda js: [j.get("role") for j in js if j.get("role") in order]
    active = roles([j for j in jobs if j.get("status") in ("queued", "running")])
    if active:
        return max(active, key=order.index)
    if any(p["status"] != "queued" for p in people) and any(p["status"] not in ("judged", "stopped") for p in people):
        return "run"   # someone is still testing, waiting or being judged
    later = max(mtime(f"{s}/{n}") for n in ("prs.json", "jobs.json", "areas.json"))
    if mtime(f"{run}/LAUNCH_REPORT.md") and mtime(f"{run}/LAUNCH_REPORT.md") >= later:
        return "done"
    if mtime(f"{s}/report-pack.md") and mtime(f"{s}/report-pack.md") >= later:
        return "report"
    if prs:
        return "pr"
    if roles(jobs):
        return max(roles(jobs), key=order.index)
    if areas:
        return "fix"
    if any(os.path.exists(f"{s}/{n}") for n in ("issues.json", "filed.json", "file-plan.json")):
        return "file"
    return "run" if any(p["status"] != "queued" for p in people) else "plan"


def parent_runs(run):
    """FIX_FROM_RUNS from config.env (sourced, as crowd.py reads it): the runs whose filed bugs this run fixes too."""
    if "FIX_FROM_RUNS" not in (read(f"{run}/config.env", "") or ""):
        return []
    import subprocess
    out = subprocess.run(["bash", "-c", f'set -a; . "{run}/config.env" >/dev/null 2>&1; printf %s "$FIX_FROM_RUNS"'],
                         capture_output=True, text=True).stdout
    return [h for h in out.split() if os.path.isdir(h)]


STATUS = {"running": "testing", "judging": "judged", "judged": "done", "done": "done", "stopped": "blocked",
          "waiting-stack": "blocked", "waiting-disk": "blocked"}


def world_state(run, data, embed=False):
    """The shape square/state.json promises (see the module docstring); missing sources give empty lists."""
    s = f"{run}/state"
    people = data["people"]
    jobs = []
    for j in as_list(read(f"{s}/jobs.json", {}), "id"):
        jobs.append({k: j.get(k, "") for k in ("id", "role", "area", "status", "runner", "model", "thread", "persona", "started")})
        res = read(j["result"], {}) if str(j.get("result", "")).endswith(".json") else j.get("result")
        if isinstance(res, dict) and isinstance(res.get("results"), list):   # a recheck: the worst verdict wins
            words = {result_word(r) for r in res["results"]}
            res = next((w for w in ("fail", "partial", "pass") if w in words), "")
        jobs[-1]["result"] = result_word(res) if j.get("role") == "recheck" else ""
        jobs[-1].update(job_usage(run, j["id"]))
    prs = []
    live = read(f"{s}/areas-detail.json", {}) or {}
    for pr in as_list(read(f"{s}/prs.json", {}), "area"):
        if live and pr.get("area") not in live:   # an area taken out of the fix stage (FIX_TRACKERS) sails no boat
            continue
        rc = pr.get("recheck") if isinstance(pr.get("recheck"), dict) else {}
        if not rc:   # not written yet: count the finished recheck jobs for this area
            for j in jobs:
                if j["role"] == "recheck" and j["area"] == pr.get("area") and j["result"]:
                    rc[j["result"]] = rc.get(j["result"], 0) + 1
        url = pr.get("url") or (live.get(pr.get("area")) or {}).get("pr", "")   # the area can know its PR first
        num = pr.get("number") or (re.search(r"/pull/(\d+)", url or "") or [None, ""])[1]
        st = pr.get("status", "")
        if st not in ("open", "draft", "merged", "closed", "ready"):   # an area status written here earlier
            st = "draft" if num else ""
        prs.append({"area": pr.get("area", ""), "url": url, "number": num, "status": st,
                    "stage": (live.get(pr.get("area")) or {}).get("status", ""), "bugs": pr.get("bugs") or [],
                    "recheck": {k: rc.get(k, 0) for k in ("pass", "partial", "fail")}})
    bouts = []
    for p in sorted(glob.glob(f"{s}/bouts/*.json"), key=os.path.getmtime):
        b = read(p, {}) or {}
        if not isinstance(b, dict):
            continue
        rounds = [{k: r.get(k, "") for k in ("by", "text", "severity", "reply")} for r in b.get("rounds") or [] if isinstance(r, dict)]
        bouts.append({"id": b.get("id") or os.path.basename(p)[:-5], "kind": b.get("kind", ""), "a": b.get("a", ""),
                      "b": b.get("b", ""), "subject": b.get("subject", ""), "rounds": rounds, "winner": b.get(b.get("winner"), b.get("winner", "")) if b.get("winner") in ("a", "b") else b.get("winner", ""),
                      "scores": b.get("scores") or {}, "status": b.get("status", "")})
    areas = read(f"{s}/areas.json", {}) or {}
    areas = areas if isinstance(areas, dict) else {}
    filed = read(f"{s}/filed.json", {}) or {}
    filed = dict(filed) if isinstance(filed, dict) else {}
    fixing = {k for ks in areas.values() if isinstance(ks, list) for k in ks}
    for home in parent_runs(run):   # bugs the earlier runs filed that this run is fixing; this run's own win
        old = read(f"{home}/state/filed.json", {}) or {}
        why = {i.get("key"): i for i in read(f"{home}/state/issues.json", []) or [] if isinstance(i, dict)}
        for k, v in (old.items() if isinstance(old, dict) else []):
            if k in fixing and k not in filed and isinstance(v, dict):
                i = why.get(k) or {}
                filed[k] = {**i, **v, "reporter": v.get("reporter") or i.get("reporter", ""),
                            "severity": v.get("severity") or i.get("severity", "")}
    bugs = bug_list(run, people, filed, areas, prs, embed)
    busy = {}
    for j in jobs:
        if j["status"] == "running" and j["persona"] and j["role"] in ("recheck", "fix"):
            busy[j["persona"]] = "rechecking" if j["role"] == "recheck" else "fixing"
    col = colors(people)
    chars = [{"id": p["id"], "name": p["name"], "role": p["role"], "surface": p["surface"],
              "status": busy.get(p["id"]) or STATUS.get(p["status"]) or "waiting", "day": p["day"], "done": p["done"],
              "total": p["total"], "bugs": sum(1 for b in bugs if b["reporter"] == p["id"] and b["state"] not in ("not-needed", "rejected")),
              "color": col[p["id"]],
              "engine": "codex" if str(p.get("engine", "")).startswith("cod") else "claude",
              "shot": p["live"]} for p in people]
    posts = [{"id": e.get("id", ""), "author": e.get("author", ""), "kind": (e.get("kind") or "post").replace("-", ""),
              "text": e.get("text", ""), "ts": e.get("ts", ""), "parent": e.get("ref") or "", "shot": e.get("img", "")}
             for e in data["feed"][-200:]]
    days = 0
    for line in (read(f"{run}/config.env", "") or "").splitlines():
        m = re.match(r"\s*DAYS=(\d+)", line)
        if m:
            days = int(m.group(1))
    return {"run": {"name": data["title"], "date": time.strftime("%Y-%m-%d"), "days": days,
                    "stage": stage(run, people, jobs, prs, areas), "updated": time.strftime("%Y-%m-%dT%H:%M:%S")},
            "characters": chars, "posts": posts, "bugs": bugs,
            "jobs": [{k: v for k, v in j.items() if k != "result"} | ({"result": j["result"]} if j["result"] else {}) for j in jobs],
            "bouts": bouts, "prs": prs, "watch": watch_state(run), "usage": usage_state(run, jobs)}


_USAGE = {}
def job_usage(run, jid):
    """Tokens and dollars one fix job used: a subagent's from state/job-usage.json (the orchestrator writes what each
    agent's completion reports), a cli job's from the cost line claude -p leaves at the end of its log."""
    if "file" not in _USAGE:
        _USAGE["file"] = read(f"{run}/state/job-usage.json", {}) or {}
    u = dict(_USAGE["file"].get(jid) or {})
    log = f"{run}/logs/job-{jid}.log"
    if "usd" not in u and os.path.exists(log):
        m = re.findall(r'"total_cost_usd":\s*([0-9.]+)', read(log, "") or "")
        if m and float(m[-1]):
            u["usd"] = round(float(m[-1]), 2)
    return {k: u[k] for k in ("tokens", "usd") if k in u}


def usage_state(run, jobs):
    """The bank: each engine's limit use from quota.log, and what the run has spent so far."""
    engines = {}
    for line in (read(f"{run}/quota.log", "") or "").splitlines():
        m = re.match(r"\S+ (claude|codex) active=(\S+) primary=(\S+) weekly=(\S+) (?:paused|fallback)=(\w+) \| (.*)", line.strip())
        if not m:
            continue
        accts = []
        for a in m.group(6).split():
            name, _, v = a.rpartition(":")
            p5, _, wk = v.partition("/")
            accts.append({"name": name, "primary": p5, "weekly": wk})
        engines[m.group(1)] = {"active": m.group(2), "primary": m.group(3), "weekly": m.group(4),
                               "paused": m.group(5) == "yes", "accounts": accts, "at": line[:8]}
    steps = {}
    for line in (read(f"{run}/state/costs.jsonl", "") or "").splitlines():
        try:
            c = json.loads(line)
        except ValueError:
            continue
        k = str(c.get("step", "")).split(":")[0] or "other"
        steps[k] = round(steps.get(k, 0) + float(c.get("usd") or 0), 2)
    fix_usd = round(sum(j.get("usd", 0) for j in jobs), 2)
    return {"engines": engines, "steps": steps, "fix_usd": fix_usd,
            "fix_tokens": sum(j.get("tokens", 0) for j in jobs),
            "total_usd": round(sum(steps.values()) + fix_usd, 2)}


def watch_state(run):
    """fix-watch.py's open problems (state/watch.json) and its last lines (logs/fix-watch.log) for the lookout."""
    seen = read(f"{run}/state/watch.json", {}) or {}
    open_ = [{"kind": k.split(" ", 1)[0], "text": k.split(" ", 1)[1] if " " in k else "",
              "at": time.strftime("%H:%M", time.localtime(v))} for k, v in seen.items()
             if not k.startswith("_") and isinstance(v, (int, float)) and not k.startswith(("READY", "DONE"))]
    lines = (read(f"{run}/logs/fix-watch.log", "") or "").splitlines()
    on = os.path.exists(f"{run}/state/watch.json") and time.time() - os.path.getmtime(f"{run}/state/watch.json") < 300
    return {"on": on, "problems": open_, "recent": [l for l in lines if l.strip()][-8:]}


PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><title>crowd · square</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#313338;--side:#2b2d31;--deep:#1e1f22;--hover:#35373c;--sel:#404249;--text:#dbdee1;--muted:#949ba4;--faint:#6d6f78;
--accent:#5865f2;--green:#23a55a;--yellow:#f0b232;--red:#f23f43;--grey:#80848e;--link:#00a8fc}
*{box-sizing:border-box}html,body{margin:0;height:100%;background:var(--bg);color:var(--text);font:14px/1.4 "gg sans","Noto Sans",system-ui,-apple-system,sans-serif;-webkit-font-smoothing:antialiased}
a{color:var(--link)}
.wrap{display:flex;flex-direction:column;height:100vh}
.strip{flex:none;height:36px;display:flex;align-items:center;gap:12px;padding:0 12px;background:var(--deep);font-size:12px;color:var(--muted)}
.strip .brand{font-weight:700;color:var(--text);white-space:nowrap}
.steps{display:flex;align-items:center;gap:2px;flex:1;min-width:0;overflow:hidden}
.step{padding:3px 8px;border-radius:10px;white-space:nowrap;color:var(--faint)}
.step.past{color:var(--muted)}.step.now{background:var(--accent);color:#fff;font-weight:700}
.steps .sep{color:#4e5058}
.strip .brand{display:none}
.btn{background:var(--sel);color:var(--text);border:0;border-radius:4px;padding:4px 10px;font:inherit;font-weight:600;cursor:pointer;text-decoration:none;white-space:nowrap}
.btn:hover{background:#4e5058}.btn.primary{background:var(--accent);color:#fff}.btn.primary:hover{background:#4752c4}
.app{display:grid;grid-template-columns:248px 1fr 240px;grid-template-rows:minmax(0,1fr);flex:1;min-height:0}
aside{background:var(--side);overflow-y:auto}
.guild{height:44px;display:flex;align-items:center;padding:0 14px;font-weight:700;box-shadow:0 1px 0 rgba(0,0,0,.25);position:sticky;top:0;background:var(--side);z-index:2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cat{color:var(--muted);font-size:11px;font-weight:700;letter-spacing:.03em;text-transform:uppercase;padding:14px 14px 4px}
.ch{display:flex;align-items:center;gap:8px;margin:1px 8px;padding:5px 8px;border-radius:4px;color:var(--muted);cursor:pointer;text-decoration:none}
.ch:hover{background:var(--hover);color:var(--text)}.ch.sel{background:var(--sel);color:#fff}
.ch .hash{font-size:18px;line-height:1;color:var(--faint);width:16px;text-align:center}
.ch .nm{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ch .sub{font-size:11px;color:var(--faint)}
.bar{height:3px;background:var(--deep);border-radius:2px;margin-top:3px;overflow:hidden}.bar i{display:block;height:100%;background:var(--green)}
.badge{background:var(--red);color:#fff;border-radius:8px;font-size:11px;font-weight:700;padding:0 5px}
.dot{width:10px;height:10px;border-radius:50%;flex:none}
.s-running{background:var(--green)}.s-waiting{background:var(--yellow)}.s-judging{background:var(--accent)}.s-judged,.s-done{background:var(--grey)}
.s-queued{border:2px solid var(--faint)}.s-stopped,.s-down{background:var(--red)}.s-starting{background:var(--yellow)}
main{display:flex;flex-direction:column;min-width:0;min-height:0}
.top{height:44px;flex:none;display:flex;align-items:center;gap:10px;padding:0 16px;box-shadow:0 1px 0 rgba(0,0,0,.25)}
.top b{font-size:15px}.top .desc{color:var(--muted);font-size:12px;border-left:1px solid #3f4147;padding-left:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.stats{margin-left:auto;display:flex;gap:12px;color:var(--muted);font-size:12px;white-space:nowrap}
.down{background:var(--red);color:#fff;padding:6px 16px;font-weight:600}
.filters{position:sticky;top:0;z-index:1;display:flex;flex-wrap:wrap;align-items:center;gap:6px;padding:8px 16px;background:var(--bg);border-bottom:1px solid #3a3c42;font-size:12px}
.chip{border:1px solid #4e5058;background:transparent;color:var(--muted);border-radius:12px;padding:2px 10px;font:inherit;cursor:pointer}
.chip:hover{color:var(--text)}.chip.on{background:var(--sel);border-color:var(--sel);color:#fff}
.filters select{background:var(--deep);color:var(--text);border:1px solid #4e5058;border-radius:4px;padding:2px 6px;font:inherit}
.filters label{color:var(--muted);display:flex;align-items:center;gap:4px;cursor:pointer}
.filters .count{margin-left:auto;color:var(--faint)}
.scroll{flex:1;overflow-y:auto;padding:0 0 24px}
.msg{display:grid;grid-template-columns:36px 1fr;gap:0 12px;padding:4px 16px;margin-top:6px}
.msg:hover{background:#2e3035}
.av{width:36px;height:36px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;color:#fff;font-size:13px}
.who{font-weight:600;color:#fff;cursor:pointer}.who:hover{text-decoration:underline}
.role{font-size:10px;font-weight:700;border-radius:3px;padding:1px 4px;margin-left:6px;vertical-align:1px;color:#fff}
.ts{color:var(--faint);font-size:11px;margin-left:6px}.body{white-space:pre-wrap;word-wrap:break-word}
.screen{color:var(--muted);font-size:12px}
.shot{max-width:min(380px,100%);max-height:220px;border-radius:6px;margin-top:6px;display:block;border:1px solid #232428;cursor:zoom-in}
.thread{margin:4px 0 0;border-left:2px solid #4e5058;padding:2px 0 2px 10px}
.thread summary{cursor:pointer;color:var(--link);font-size:12px;font-weight:600;list-style:none}
.reply{display:grid;grid-template-columns:22px 1fr;gap:0 8px;margin-top:6px}.reply .av{width:22px;height:22px;font-size:9px}
.kind{font-size:10px;font-weight:700;border-radius:3px;padding:1px 5px;margin-right:6px;text-transform:uppercase;letter-spacing:.02em}
.k-me-too{background:#1f6f4a;color:#d7ffe9}.k-cant-repro{background:#7a5b14;color:#fff3d1}.k-disagree{background:#7f2d2d;color:#ffe1e1}.k-tip{background:#2d4f86;color:#e1ecff}.k-post{background:#4e5058;color:#fff}
.old{opacity:.55}
.panel{margin:10px 16px;background:var(--side);border-radius:8px;padding:12px 14px}
.panel h3{margin:0 0 8px;font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.03em}
.kv{display:grid;grid-template-columns:110px 1fr;gap:4px 12px;font-size:13px}.kv span:nth-child(odd){color:var(--muted)}
.todo{list-style:none;margin:0;padding:0}.todo li{display:flex;gap:10px;padding:5px 0;border-bottom:1px solid #3a3c42}
.todo .ic{width:20px;text-align:center;flex:none}.todo .t{flex:1}.todo .n{color:var(--muted);font-size:12px;margin-top:2px}
.tag{font-size:10px;color:var(--muted);border:1px solid #4e5058;border-radius:3px;padding:0 4px;margin-left:4px}
.sev{font-size:10px;font-weight:700;border-radius:3px;padding:1px 5px;margin-right:6px;color:#fff}
.sev-high,.sev-critical{background:var(--red)}.sev-medium{background:#c27c0e}.sev-low{background:#4e5058}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:10px}
@media (max-width:1100px){.app{grid-template-columns:210px 1fr}aside.members{display:none}.grid2{grid-template-columns:1fr}}
@media (max-width:720px){.app{grid-template-columns:1fr}#channels,.steps,.top .desc,.stats span:not(:first-child){display:none}.strip .brand{display:block;overflow:hidden;text-overflow:ellipsis}.strip{justify-content:space-between}.msg{padding:4px 12px}.filters{padding:8px 12px}}
.embedded .strip .btn{display:none}
#chpick{display:none;background:var(--deep);color:var(--text);border:1px solid #4e5058;border-radius:4px;padding:2px 6px;font:inherit;max-width:45vw}
@media (max-width:720px){#chpick{display:block}.bugbar{flex-wrap:nowrap;overflow-x:auto}.bugbar .chip,.bugbar select,.bugbar .count{flex:none;white-space:nowrap}}
.mem{display:flex;align-items:center;gap:10px;margin:1px 8px;padding:4px 8px;border-radius:4px;cursor:pointer}
.mem:hover{background:var(--hover)}.mem .av{width:30px;height:30px;font-size:11px;position:relative}
.mem .av .dot{position:absolute;right:-2px;bottom:-2px;width:10px;height:10px;border:3px solid var(--side);box-sizing:content-box}
.mem .nm{font-size:13px}.mem .act{font-size:11px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:150px}
.empty{color:var(--muted);padding:20px 16px}
#zoom{position:fixed;inset:0;background:rgba(0,0,0,.88);display:none;flex-direction:column;align-items:center;justify-content:center;gap:10px;z-index:9}
#zoom img{max-width:92vw;max-height:86vh;border-radius:6px;box-shadow:0 8px 40px rgba(0,0,0,.6)}
#zoom .cap{color:var(--muted);font-size:13px}
#zoom .nav{position:absolute;top:50%;transform:translateY(-50%);background:rgba(255,255,255,.08);color:#fff;border:0;border-radius:50%;width:44px;height:44px;font-size:22px;cursor:pointer}
#zoom .nav:hover{background:rgba(255,255,255,.18)}#zoom .prev{left:20px}#zoom .next{right:20px}
.bug{margin:8px 16px;background:var(--side);border-radius:8px;padding:10px 14px;border-left:3px solid #4e5058}
.bug.g-human{border-left-color:var(--red)}.bug.g-fixing{border-left-color:var(--yellow)}.bug.g-fixed{border-left-color:var(--green)}
.bug.g-filed{border-left-color:var(--accent)}.bug.g-out{opacity:.8}.bug.is-fresh{box-shadow:0 0 0 1px var(--accent)}
.bug .hd{display:flex;align-items:flex-start;gap:6px}.bug .hd b{flex:1;min-width:0}.bug .links{display:flex;flex-wrap:wrap;gap:6px;flex:none}
.lnk{font-size:12px;border:1px solid #4e5058;border-radius:10px;padding:1px 8px;text-decoration:none;white-space:nowrap;color:var(--link);cursor:pointer}
span.lnk{color:var(--muted);cursor:default}
.bug .meta{color:var(--muted);font-size:12px;margin-top:4px;display:flex;flex-wrap:wrap;gap:4px 12px;align-items:center}
.bug .meta .st{color:var(--text);font-weight:600}
.mini{display:inline-flex;align-items:center;gap:4px;cursor:pointer}.mini:hover{color:var(--text)}.mini .av{width:18px;height:18px;font-size:8px}
.path{display:flex;flex-wrap:wrap;gap:3px;margin-top:8px;font-size:11px}.path span{padding:2px 8px;border-radius:10px;color:var(--faint);background:var(--deep)}
.path span.ok{color:#d7ffe9;background:#1f6f4a}.path span.now{color:#fff;background:var(--accent);font-weight:700}.path span.stuck{color:#fff;background:var(--red);font-weight:700}
.rc,.why{margin-top:6px;font-size:13px}.why{color:var(--muted)}
.cmd{display:inline-block;margin-top:2px;background:var(--deep);border-radius:4px;padding:2px 6px;font:12px ui-monospace,Menlo,monospace;user-select:all;color:var(--text);overflow-wrap:anywhere}
.fresh{background:var(--accent);color:#fff;font-size:10px;font-weight:700;border-radius:3px;padding:1px 5px;text-transform:uppercase;flex:none;margin-top:2px}
.gcat{color:var(--muted);font-size:11px;font-weight:700;letter-spacing:.03em;text-transform:uppercase;padding:14px 16px 0}
.ch .cnt{font-size:11px;color:var(--faint)}
</style></head><body><div class="wrap">
<header class="strip"><span class="brand" id="brand"></span><select id="chpick" aria-label="Channel"></select><div class="steps" id="steps"></div><a class="btn primary" href="index.html" title="The run as a 3D town">World</a></header>
<div class="app"><aside id="channels"></aside><main><div class="top" id="top"></div><div id="banner"></div><div class="scroll" id="view"></div></main>
<aside class="members" id="members"></aside></div></div>
<div id="zoom"><button class="nav prev" aria-label="Previous">‹</button><img alt=""><div class="cap"></div><button class="nav next" aria-label="Next">›</button></div>
<script>
let D = __DATA__;
const ROLE = __ROLES__;
let P = {};
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const color = id => (P[id] && P[id].color) || ROLE[(P[id] && P[id].role) || (id === "organiser" ? "organiser" : "")] || "#5865f2";
const name = id => (P[id] && P[id].name) || (id === "organiser" ? "Organisers" : id);
const ini = id => name(id).split(/\s+/).slice(0, 2).map(w => w[0]).join("").toUpperCase();
const av = id => `<div class="av" style="background:${color(id)}">${esc(ini(id))}</div>`;
const roleTag = id => { const r = (P[id] && P[id].role) || (id === "organiser" ? "staff" : ""); return r ? `<span class="role" style="background:${color(id)}">${esc(r.replace("_", " "))}</span>` : "" };
const isOld = e => D.epoch && e.ts && e.ts < D.epoch;
const ICON = {pass: "✅", fail: "❌", blocked: "⛔", not_run: "⏭️", pending: "⬜", partial: "🟨", skipped: "⏭️"};
const STLABEL = {running: "testing now", waiting: "waiting", "waiting-capacity": "waiting for a slot", "waiting-browser": "waiting for a browser",
  "waiting-stack": "holding: servers down", "waiting-disk": "waiting: disk", starting: "starting", judging: "being judged", judged: "day judged",
  done: "day finished", stopped: "stopped", queued: "queued"};
const STEPS = [["plan", "Plan"], ["run", "Test"], ["judge", "Judge"], ["file", "File"], ["fix", "Fix"], ["review", "Review"], ["recheck", "Recheck"], ["pr", "PR"], ["report", "Report"]];
const KINDS = [["", "All"], ["post", "Posts"], ["me-too", "Me too"], ["cant-repro", "Can't repro"], ["disagree", "Disagree"], ["tip", "Tips"]];
const F = Object.assign({kind: "", who: "", shot: false}, JSON.parse(sessionStorage.getItem("filters") || "{}"));
// #bugs: every bug in one place, grouped by where it is now
const BG = [["", "All"], ["human", "Needs a person"], ["fixing", "Being fixed"], ["filed", "Filed"], ["waiting", "Waiting to file"],
  ["fixed", "Fixed"], ["out", "Dropped"]];
const BGTEXT = Object.fromEntries(BG);
const BSTATE = {found: "found, waiting for the judge", verified: "verified, waiting to be filed", planned: "planned for filing",
  filed: "filed", "in-pr": "filed, in a PR", "not-needed": "not worth an issue", rejected: "judged not a bug",
  refused: "not filed", skipped: "skipped at filing"};
const FXW = {queued: "waiting for a fixer", fixing: "being fixed", reviewing: "in review", rechecking: "being re-tested",
  done: "fix stage done", "needs-human": "needs a person"};
const RCW = {PASS: "✅ re-test passed", PARTIAL: "🟨 partly fixed", FAIL: "❌ still broken", BLOCKED: "⛔ could not re-test"};
const SEVN = {critical: 4, high: 3, medium: 2, low: 1};
const PATH = ["Found", "Judged", "Filed", "Fix", "Review", "Re-test", "Fixed"];
const BF = Object.assign({g: "", who: ""}, JSON.parse(sessionStorage.getItem("bugfilters") || "{}"));
const dotCls = s => "s-" + (s.startsWith("waiting") ? (s === "waiting-stack" ? "down" : "waiting") : s);
let shown = [];   // screenshots on screen, in order, for the lightbox arrows
function img(e) { if (!e.img) return ""; shown.push({src: e.img, cap: `${name(e.author)} · ${e.id || ""} ${t(e.ts)}`});
  return `<img class="shot" src="${esc(e.img)}" data-i="${shown.length - 1}" alt="">` }
function shotLink(src, cap, label) { if (!src) return ""; shown.push({src, cap});
  return `<a class="lnk shotlink" data-i="${shown.length - 1}">📸 ${esc(label)}</a>` }
let zi = -1;
function zoom(i) { if (!shown.length) return; zi = (i + shown.length) % shown.length; const z = document.getElementById("zoom");
  z.querySelector("img").src = shown[zi].src; z.querySelector(".cap").textContent = shown[zi].cap; z.style.display = "flex" }
function unzoom() { document.getElementById("zoom").style.display = "none"; zi = -1 }
function t(ts) { return ts ? ts.slice(11, 16) : "" }

let posts = [], replies = {};
function index() {
  P = Object.fromEntries(D.people.map(p => [p.id, p]));
  posts = D.feed.filter(e => !e.ref);
  replies = {}; D.feed.filter(e => e.ref).forEach(e => (replies[e.ref] = replies[e.ref] || []).push(e));
}

function stepNow() {
  if (D.stage !== "run") return D.stage === "done" ? STEPS.length : STEPS.findIndex(s => s[0] === D.stage);
  const testing = D.people.some(p => p.status === "running" || p.status === "starting" || p.status.startsWith("waiting"));
  return !testing && D.people.some(p => p.status === "judging") ? 2 : 1;
}

function msg(e) {
  const rs = replies[e.id] || [];
  const th = rs.length ? `<details class="thread" data-id="${esc(e.id)}" ${rs.length <= 3 ? "open" : ""}><summary>${rs.length} ${rs.length === 1 ? "reply" : "replies"}</summary>` +
    rs.map(r => `<div class="reply ${isOld(r) ? "old" : ""}">${av(r.author)}<div><span class="who" data-go="${esc(r.author)}">${esc(name(r.author))}</span><span class="ts">${t(r.ts)}</span>
      <div class="body"><span class="kind k-${esc(r.kind)}">${esc(r.kind.replace("-", " "))}</span>${esc(r.text)}</div>${img(r)}</div></div>`).join("") + `</details>` : "";
  return `<div class="msg ${isOld(e) ? "old" : ""}" id="${esc(e.id)}">${av(e.author)}<div><span class="who" data-go="${esc(e.author)}">${esc(name(e.author))}</span>${roleTag(e.author)}
    <span class="ts">${esc(e.id)} · ${t(e.ts)}</span>${e.screen ? `<div class="screen">📍 ${esc(e.screen)}</div>` : ""}<div class="body">${esc(e.text)}</div>${img(e)}${th}</div></div>`;
}
const flat = e => e.ref ? msg({...e, text: e.text, ref: undefined, screen: `↪ ${e.kind.replace("-", " ")} on ${e.ref}`}) : msg(e);

function channels(sel) {
  const ch = (key, label, extra = "", icon = "#") => `<a class="ch ${sel === key ? "sel" : ""}" href="#${key}"><span class="hash">${icon}</span><span class="nm">${label}</span>${extra}</a>`;
  const live = posts.filter(e => !isOld(e) && e.author !== "organiser").length;
  let h = `<div class="guild" title="${esc(D.title)}">${esc(D.title)}</div><div class="cat">Text channels</div>` +
    ch("square", "town-square", live ? `<span class="badge">${live}</span>` : "") +
    ch("bugs", "bugs", (u => u ? `<span class="badge" title="moved since you last looked">${u}</span>` : (D.bugs || []).length ? `<span class="cnt">${D.bugs.length}</span>` : "")(sel === "bugs" ? 0 : unseenBugs())) +
    ch("notices", "notices") +
    (D.epoch ? ch("before", "before-outage") : "") + `<div class="cat">Characters · ${D.people.length}</div>`;
  for (const p of D.people) {
    const pct = p.total ? Math.round(100 * p.done / p.total) : 0;
    h += `<a class="ch ${sel === p.id ? "sel" : ""}" href="#${p.id}"><span class="dot ${dotCls(p.status)}"></span><span class="nm">${esc(p.name)}
      <div class="sub">d${p.day} · ${esc(STLABEL[p.status] || p.status)}${p.total ? ` · ${p.done}/${p.total}` : ""}${p.findings.length ? ` · 🐞${p.findings.length}` : ""}</div>
      <div class="bar"><i style="width:${pct}%"></i></div></span></a>`;
  }
  return h;
}

function members() {
  const groups = [["Testing now", p => p.status === "running"], ["Waiting", p => p.status.startsWith("waiting") || p.status === "starting"],
    ["Being judged", p => p.status === "judging"], ["Day done", p => ["judged", "done", "stopped"].includes(p.status)], ["Queued", p => p.status === "queued"]];
  return groups.map(([label, f]) => { const ps = D.people.filter(f); if (!ps.length) return "";
    return `<div class="cat">${label} · ${ps.length}</div>` + ps.map(p => `<div class="mem" data-go="${esc(p.id)}"><div class="av" style="background:${color(p.id)}">${esc(ini(p.id))}<span class="dot ${dotCls(p.status)}"></span></div>
      <div><div class="nm">${esc(p.name)}</div><div class="act">${esc(p.surface ? p.surface + " · " : "")}${p.total ? `${p.done}/${p.total} · ` : ""}${esc(p.role)}</div></div></div>`).join("") }).join("");
}

function person(p) {
  const by = s => p.todo.filter(x => x.status === s).length;
  const where = (p.surface || "—") + (p.device_id ? ` (${p.device_id})` : "");
  let h = `<div class="panel"><h3>${esc(p.name)} · ${esc(p.role)} · day ${p.day}</h3><div class="kv">
    <span>Status</span><span><span class="dot ${dotCls(p.status)}" style="display:inline-block;vertical-align:-1px"></span> ${esc(STLABEL[p.status] || p.status)} ${p.updated ? `(since ${esc(p.updated)})` : ""}</span>
    <span>Who</span><span>${esc(p.age_city)}${p.business ? " · " + esc(p.business) : ""}</span>
    <span>Today wants</span><span>${esc(p.wants)}</span>
    <span>Surface</span><span>${esc(where)} ${p.engine ? "· " + esc(p.engine) : ""} · ${esc(p.device)}</span>
    <span>Account</span><span>${esc(p.email)}</span>
    ${p.depends_on.length ? `<span>Waits for</span><span>${p.depends_on.map(d => `<a href="#${d}">${esc(name(d))}</a>`).join(", ")}</span>` : ""}
    <span>Progress</span><span>${p.done}/${p.total} scenarios · ✅ ${by("pass")} ❌ ${by("fail")} ⛔ ${by("blocked")} · 📸 ${p.shots} · 🐞 ${p.findings.length}</span></div></div>`;
  h += `<div class="grid2"><div class="panel"><h3>To do today</h3><ul class="todo">` + (p.todo.length ? p.todo.map(x =>
    `<li><span class="ic">${ICON[x.status] || "⬜"}</span><div class="t"><b>${esc(x.id)}</b> ${esc(x.title)}${x.tags.map(g => `<span class="tag">${esc(g)}</span>`).join("")}${x.note ? `<div class="n">${esc(x.note)}</div>` : ""}</div></li>`).join("")
    : `<li class="empty">No plan for this day yet.</li>`) + `</ul></div><div>`;
  h += `<div class="panel"><h3>Live view ${p.live_name ? "· " + esc(p.live_name) : ""}</h3>${p.live ? img({img: p.live, author: p.id, id: p.live_name}).replace('class="shot"', 'class="shot" style="max-height:320px"') : `<div class="empty">No screenshot yet.</div>`}</div>`;
  h += `<div class="panel"><h3>Findings today (${p.findings.length}) · <a href="#bugs" data-bugs="${esc(p.id)}">all their bugs</a></h3>` + (p.findings.length ? p.findings.map(f => `<div style="margin:4px 0"><span class="sev sev-${esc(f.severity)}">${esc(f.severity)}</span>${esc(f.title)}</div>`).join("") : `<div class="empty" style="padding:4px 0">None yet.</div>`) + `</div>`;
  h += p.verdicts.map(v => `<div class="panel"><h3>Judge · day ${v.day}</h3><div>coverage ${v.coverage ?? "?"} · in character ${v.in_character ?? "?"} · ${v.verified.length} verified · ${v.satisfied ? "satisfied" : "not satisfied"}</div><div class="n" style="color:var(--muted);font-size:12px;margin-top:4px">${esc(v.reason)}</div></div>`).join("");
  h += `</div></div>`;
  const mine = D.feed.filter(e => e.author === p.id).reverse();
  h += `<div class="cat" style="padding-left:16px">Posts by ${esc(p.name)} · ${mine.length}</div>` + (mine.map(flat).join("") || `<div class="empty">Nothing posted yet.</div>`);
  return h;
}

function bgroup(b) {
  const f = b.fix || {};
  if (["not-needed", "rejected", "refused", "skipped"].includes(b.state)) return "out";
  if (f.verdict === "PASS" && !f.unresolved) return "fixed";
  if (f.status === "needs-human" || f.status === "done") return "human";
  if (f.status) return "fixing";
  return ["filed", "in-pr"].includes(b.state) ? "filed" : "waiting";
}
function bpath(b) {   // Found > Judged > Filed > Fix > Review > Re-test > Fixed, the step it is on lit up
  const f = b.fix || {};
  if (bgroup(b) === "out") return "";
  let done = 3, now = b.area ? 3 : -1, stuck = false;
  if (b.state === "found") done = now = 1;
  else if (["verified", "planned"].includes(b.state)) done = now = 2;
  else if (f.unresolved && f.status === "needs-human") { done = now = 4; stuck = true }   // review blockers nobody fixed
  else if (f.verdict === "PASS") { done = PATH.length; now = -1 }
  else if (f.status) { const i = {queued: 3, fixing: 3, reviewing: 4, rechecking: 5}[f.status];
    done = now = i ?? ({fix: 3, review: 4, recheck: 5}[f.failed] ?? 5); stuck = i === undefined }
  return `<div class="path">` + PATH.map((l, i) => `<span class="${i === now ? (stuck ? "stuck" : "now") : i < done ? "ok" : ""}">${l}</span>`).join("") + `</div>`;
}
const isUrl = u => /^https?:\/\//i.test(u || "");
const ghRef = u => { const m = /\/(issues|pull)\/(\d+)(#issuecomment)?/.exec(u || ""); return m ? "#" + m[2] + (m[3] ? " comment" : "") : "" };
const lnk = (u, label) => isUrl(u) ? `<a class="lnk" href="${esc(u)}" target="_blank" rel="noopener">${esc(label)} ↗</a>` : "";
const mini = id => `<span class="mini" data-go="${esc(id)}">${av(id)}${esc(name(id))}</span>`;
// crowd.py rescue brings back every not-needed finding whose title contains its words: pass the whole title, single-quoted
const shq = t => "'" + String(t).replace(/'/g, "'\\''") + "'";
const bsig = b => [b.state, (b.fix || {}).status, (b.fix || {}).verdict, b.url, (b.pr || {}).url].join("|");
let bugFresh = new Set(), onBugs = false;
function seenBugs() { try { return JSON.parse(localStorage.getItem("bugs-seen:" + D.run) || "null") } catch (e) { return null } }
function unseenBugs() { const s = seenBugs(); return s ? (D.bugs || []).filter(b => s[b.key] !== bsig(b)).length : 0 }
function visitBugs(here) {   // a bug that moved since your last look is marked "updated" until you leave #bugs
  if (!here) { onBugs = false; return }
  const s = seenBugs();
  if (!onBugs) bugFresh = new Set();
  onBugs = true;
  if (s) for (const b of D.bugs || []) if (s[b.key] !== bsig(b)) bugFresh.add(b.key);
  try { localStorage.setItem("bugs-seen:" + D.run, JSON.stringify(Object.fromEntries((D.bugs || []).map(b => [b.key, bsig(b)])))) } catch (e) {}
}
function bugRow(b) {
  const f = b.fix || {}, g = bgroup(b), u = isUrl(b.url) ? b.url : "", ref = ghRef(u);
  const status = g === "fixed" ? (b.pr && b.pr.url ? "fixed, in a PR" : "fixed, re-test passed")
    : g === "out" || !f.status ? BSTATE[b.state] || b.state : FXW[f.status] || f.status;
  const meta = [`<span class="st">${esc(status)}</span>`, b.reporter ? `<span>by ${mini(b.reporter)}</span>` : "",
    (b.confirmers || []).length ? `<span>me too: ${b.confirmers.map(mini).join(" ")}</span>` : "",
    (b.cant_repro || []).length ? `<span>can't repro: ${b.cant_repro.map(mini).join(" ")}</span>` : "",
    b.day ? `<span>day ${esc(b.day)}</span>` : "", b.type && b.type !== "bug" ? `<span>${esc(b.type)}</span>` : "",
    b.area ? `<span>area ${esc(b.area)}</span>` : "", f.attempt > 1 ? `<span>fix attempt ${esc(f.attempt)}</span>` : "",
    f.unresolved ? `<span>the review's blockers are not fixed</span>` : "",
    b.rescued ? `<span>brought back by a person</span>` : ""].filter(Boolean).join("");
  const links = (u ? lnk(u, ref ? (ref.endsWith("comment") ? "comment on " + ref.split(" ")[0] : "issue " + ref) : "issue")
      : b.plan ? `<span class="lnk">${esc(b.plan)}</span>` : "") +
    (b.pr && b.pr.url ? lnk(b.pr.url, "PR" + (b.pr.number ? " #" + b.pr.number : "")) : "") +
    shotLink(b.shot, `${name(b.reporter)} · ${b.title}`, "screenshot");
  const rc = f.verdict ? `<div class="rc">${RCW[f.verdict] || esc(f.verdict)}${f.by ? ` · re-tested by ${mini(f.by)}` : ""}${f.note ? ` · ${esc(f.note)}` : ""}
    ${lnk(f.comment, "re-test comment")} ${shotLink(f.shot, `${name(f.by || b.reporter)} re-testing · ${b.title}`, "re-test")}</div>` : "";
  const why = g === "out" ? `<div class="why">${esc(BSTATE[b.state] || b.state)}${b.why ? ": " + esc(b.why) : ""}</div>` +
    (b.state === "not-needed" ? `<div class="why">To file it anyway: <code class="cmd" title="Select and copy">python3 "$RUN/scripts/crowd.py" "$RUN" rescue ${esc(shq(b.title))}</code></div>` : "") : "";
  return `<div class="bug g-${g}${bugFresh.has(b.key) ? " is-fresh" : ""}" id="bug-${esc(b.key)}"><div class="hd">${bugFresh.has(b.key) ? `<span class="fresh">updated</span>` : ""}
    ${b.severity ? `<span class="sev sev-${esc(b.severity)}">${esc(b.severity)}</span>` : ""}<b>${esc(b.title)}</b><span class="links">${links}</span></div>
    <div class="meta">${meta}</div>${bpath(b)}${rc}${why}</div>`;
}
function board() {
  const all = D.bugs || [], mine = b => !BF.who || b.reporter === BF.who;
  const n = g => all.filter(b => bgroup(b) === g && mine(b)).length;
  const who = [...new Set([...all.map(b => b.reporter), BF.who].filter(Boolean))].sort((a, b) => name(a).localeCompare(name(b)));
  const hit = all.filter(b => (!BF.g || bgroup(b) === BF.g) && mine(b));
  const order = BG.map(x => x[0]).filter(Boolean);
  hit.sort((a, b) => order.indexOf(bgroup(a)) - order.indexOf(bgroup(b)) || (SEVN[b.severity] || 0) - (SEVN[a.severity] || 0)
    || (b.confirmers || []).length - (a.confirmers || []).length || String(a.title).localeCompare(String(b.title)));
  let h = `<div class="filters bugbar">${BG.map(([k, l]) => `<button class="chip ${BF.g === k ? "on" : ""}" data-bg="${k}">${l}${k ? ` · ${n(k)}` : ""}</button>`).join("")}
    <select id="bugwho"><option value="">Everyone</option>${who.map(a => `<option value="${esc(a)}" ${BF.who === a ? "selected" : ""}>${esc(name(a))}</option>`).join("")}</select>
    <span class="count">${hit.length} shown</span></div>`;
  if (!hit.length) return h + `<div class="empty">${all.length ? "No bug matches these filters." : "No bugs yet. They show up here as the characters report them."}</div>`;
  let last = null;
  for (const b of hit) { const g = bgroup(b); if (!BF.g && g !== last) { h += `<div class="gcat">${BGTEXT[g]} · ${n(g)}</div>`; last = g } h += bugRow(b) }
  return h;
}

function filterBar(n) {
  const opts = [`<option value="">Everyone</option>`].concat([...new Set(D.feed.map(e => e.author))].map(a => `<option value="${esc(a)}" ${F.who === a ? "selected" : ""}>${esc(name(a))}</option>`));
  return `<div class="filters">${KINDS.map(([k, l]) => `<button class="chip ${F.kind === k ? "on" : ""}" data-kind="${k}">${l}</button>`).join("")}
    <select id="who">${opts.join("")}</select><label><input type="checkbox" id="hasshot" ${F.shot ? "checked" : ""}> has screenshot</label><span class="count">${n} shown</span></div>`;
}

function square() {
  const live = D.feed.filter(e => !isOld(e) && e.author !== "organiser");
  if (!F.kind && !F.who && !F.shot) {
    const ps = posts.filter(e => !isOld(e) && e.author !== "organiser").slice().reverse();
    return filterBar(ps.length) + (ps.map(msg).join("") || `<div class="empty">No posts since the restart yet.</div>`);
  }
  const hit = live.filter(e => (!F.kind || e.kind === F.kind) && (!F.who || e.author === F.who) && (!F.shot || e.img)).reverse();
  return filterBar(hit.length) + (hit.map(flat).join("") || `<div class="empty">Nothing matches these filters.</div>`);
}

function go(id) { location.hash = id }
function render() {
  const sel = decodeURIComponent(location.hash.slice(1)) || "square";
  shown = [];
  visitBugs(sel === "bugs");
  const v = document.getElementById("view");
  const open = new Set([...v.querySelectorAll("details[data-id]")].map(d => d.dataset.id + ":" + d.open));
  document.getElementById("brand").textContent = D.title;
  const now = stepNow();
  document.getElementById("steps").innerHTML = STEPS.map(([k, l], i) => `<span class="step ${i < now ? "past" : i === now ? "now" : ""}">${l}</span>`).join(`<span class="sep">›</span>`);
  document.getElementById("channels").innerHTML = channels(sel);
  const opt = (k, l) => `<option value="${esc(k)}" ${sel === k ? "selected" : ""}>${esc(l)}</option>`;
  document.getElementById("chpick").innerHTML = opt("square", "# town-square") + opt("bugs", "# bugs") + opt("notices", "# notices") +
    (D.epoch ? opt("before", "# before-outage") : "") + D.people.map(p => opt(p.id, "@ " + p.name)).join("");
  document.getElementById("members").innerHTML = members();
  document.getElementById("banner").innerHTML = D.stack_down ? `<div class="down">⚠ Test servers down: ${esc(D.stack_down)}. Testers are holding.</div>` : "";
  const running = D.people.filter(p => p.status === "running").length;
  const bugs = D.people.reduce((a, p) => a + p.findings.length, 0);
  const stats = `<div class="stats"><span>🟢 ${running} testing</span><span>🐞 ${bugs} today</span><span>💬 ${D.feed.length} posts</span><span title="${esc(D.quota)}">⏱ ${esc(D.generated)}</span></div>`;
  let title = "town-square", desc = "Every character's posts, newest first. Replies are threads.", body = "";
  if (sel === "square") body = square();
  else if (sel === "bugs") { title = "bugs"; desc = "Every bug the crowd found, from the first report to the fix. Updates live."; body = board() }
  else if (sel === "notices") { title = "notices"; desc = "From the organisers"; body = posts.filter(e => e.author === "organiser").slice().reverse().map(msg).join("") || `<div class="empty">No notices.</div>` }
  else if (sel === "before") { title = "before-outage"; desc = "Posts from before " + D.epoch.slice(11, 16) + ": that world was wiped"; body = posts.filter(isOld).slice().reverse().map(msg).join("") }
  else if (P[sel]) { const p = P[sel]; title = p.name; desc = `${p.role}${p.age_city ? " · " + p.age_city : ""}`; body = person(p) }
  document.getElementById("top").innerHTML = `<span class="hash" style="color:var(--faint);font-size:20px">${P[sel] ? "@" : "#"}</span><b>${esc(title)}</b><span class="desc">${esc(desc)}</span>${stats}`;
  const key = "scroll:" + sel, keep = v.dataset.sel === sel ? v.scrollTop : +sessionStorage.getItem(key) || 0;
  if (v.dataset.sel === sel && v._html === body) return;   // nothing new: leave the DOM (and the reader) alone
  v.innerHTML = body; v.dataset.sel = sel; v._html = body;
  v.querySelectorAll("details[data-id]").forEach(d => { if (open.has(d.dataset.id + ":true")) d.open = true; else if (open.has(d.dataset.id + ":false")) d.open = false });
  v.scrollTop = keep;   // images load after this and grow the page: put the reader back as each one lands
  v.querySelectorAll("img").forEach(i => i.complete || i.addEventListener("load", () => { if (v.scrollTop < keep) v.scrollTop = keep }, {once: true}));
  v.onscroll = () => sessionStorage.setItem(key, v.scrollTop);
}

document.addEventListener("click", ev => {
  const el = ev.target.closest("[data-go],[data-kind],[data-bg],[data-bugs],.shotlink,img.shot,#zoom .nav,#zoom");
  if (!el) return;
  if (el.dataset.go) go(el.dataset.go);
  else if (el.dataset.bg !== undefined) { BF.g = el.dataset.bg; saveBF() }
  else if (el.dataset.bugs !== undefined) { BF.g = ""; BF.who = el.dataset.bugs; sessionStorage.setItem("bugfilters", JSON.stringify(BF)) }
  else if (el.matches(".shotlink")) zoom(+el.dataset.i);
  else if (el.dataset.kind !== undefined && el.classList.contains("chip")) { F.kind = el.dataset.kind; saveF() }
  else if (el.matches("img.shot")) zoom(+el.dataset.i);
  else if (el.matches("#zoom .nav")) { ev.stopPropagation(); zoom(zi + (el.classList.contains("next") ? 1 : -1)) }
  else if (el.id === "zoom" && ev.target.tagName !== "IMG") unzoom();
});
document.addEventListener("change", ev => {
  if (ev.target.id === "who") { F.who = ev.target.value; saveF() }
  if (ev.target.id === "hasshot") { F.shot = ev.target.checked; saveF() }
  if (ev.target.id === "bugwho") { BF.who = ev.target.value; saveBF() }
  if (ev.target.id === "chpick") go(ev.target.value);
});
document.addEventListener("keydown", ev => {
  if (zi < 0) return;
  if (ev.key === "Escape") unzoom(); else if (ev.key === "ArrowRight") zoom(zi + 1); else if (ev.key === "ArrowLeft") zoom(zi - 1);
});
function saveF() { sessionStorage.setItem("filters", JSON.stringify(F)); render() }
function saveBF() { sessionStorage.setItem("bugfilters", JSON.stringify(BF)); render() }

// live: pull square-data.js (a plain script, so it also works from file://) and patch the page in place
function poll() {
  const s = document.createElement("script");
  s.src = "square-data.js?t=" + Date.now();
  s.onload = () => { s.remove(); const d = window.SQUARE_DATA; if (d && d.generated !== D.generated && zi < 0) { D = d; index(); render() } };
  s.onerror = () => s.remove();
  document.head.appendChild(s);
}
if (window.top !== window.self) document.body.classList.add("embedded");   // inside the world's side panel
index(); window.addEventListener("hashchange", render); render();
setInterval(poll, 15000);
</script></body></html>"""

REDIRECT = """<!doctype html><meta charset="utf-8"><title>crowd</title>
<script>location.replace("square.html" + location.hash)</script><a href="square.html">Open the square</a>"""


def world_dir():
    """scripts/world next to this file; a run dir's copy of the scripts may not have it, so look in the skill too."""
    for d in (WORLD, os.path.join(os.environ.get("CROWD_QA_SKILL", ""), "scripts", "world"),
              os.path.expanduser("~/.claude/skills/crowd-qa/scripts/world")):
        if all(os.path.exists(os.path.join(d, n)) for n in ("index.html", "world.js", "world.css")):
            return d
    return ""


def put(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def write(run, embed):
    data = collect(run, embed)
    state = world_state(run, data, embed)
    color = {c["id"]: c["color"] for c in state["characters"]}
    for p in data["people"]:
        p["color"] = color[p["id"]]
    data["stage"] = state["run"]["stage"]
    data["bugs"] = state["bugs"]   # the #bugs channel
    if embed:   # only the square shows a bug's screenshots; keep the inlined images out of the world's copies
        state = {**state, "bugs": [{**b, "shot": "", "fix": {**b["fix"], "shot": ""} if b.get("fix") else {}} for b in state["bugs"]]}
    sq = f"{run}/square"
    os.makedirs(sq, exist_ok=True)
    blob = lambda x: json.dumps(x).replace("<", "\\u003c")   # nothing a tester wrote can open a tag or a comment
    put(f"{sq}/state.json", json.dumps(state, indent=1))
    put(f"{sq}/state.js", "window.CROWD_STATE = " + blob(state) + ";\n")
    put(f"{sq}/square-data.js", "window.SQUARE_DATA = " + blob(data) + ";\n")
    put(f"{sq}/square.html", PAGE.replace("__DATA__", blob(data)).replace("__ROLES__", json.dumps(ROLE_COLOR)))
    wd = world_dir()
    if wd:   # one self-contained file: browsers refuse module scripts and fetch() from file://
        page = open(f"{wd}/index.html").read()
        page = page.replace('<link rel="stylesheet" href="world.css">', "<style>\n" + open(f"{wd}/world.css").read() + "</style>")
        page = page.replace('<script type="module" src="world.js"></script>',
                            '<script type="module">\n' + open(f"{wd}/world.js").read().replace("</script", "<\\/script") + "</script>")
        page = page.replace("<!--STATE-->", f'<script type="application/json" id="state">{blob(state)}</script>')
    else:
        page = REDIRECT
    put(f"{sq}/index.html", page)
    return f"{sq}/index.html"


if __name__ == "__main__":
    run = sys.argv[1].rstrip("/")
    embed = "--embed" in sys.argv
    while True:
        out = write(run, embed)
        if "--watch" not in sys.argv:
            print(out)
            break
        time.sleep(15)
