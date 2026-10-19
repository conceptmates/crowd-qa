#!/usr/bin/env python3
"""makeup.py <old-run> <new-run> [--scen-max N]   (also: crowd.py <old-run> makeup <new-run>)

Builds a make-up run: a new run dir whose plans hold only the scenarios the old run never finished, so the crowd
runs those and nothing else. A scenario is taken when its report says blocked or not_run, or when it was planned
but never reported (no entry in report.json, or the day never ran). A blocked scenario whose notes cite a filed
issue (#N, an issue URL, or most of a filed title) is left out: it stays blocked until that bug is fixed.

Each character keeps its card, memory (its diary of earlier days) and the run's filed.txt, context.md, product map,
stack faults, world registry and stack/ helpers; config.env is copied with RUN_DIR and DAYS set for the new run.
Each scenario keeps its text, is renumbered, and says where it came from and what happened to it before, so the
tester knows what to do differently. Days hold at most SCEN_MAX scenarios (--scen-max, else the old run's SCEN_MAX,
else 12). Seed the data the blocked scenarios were missing and say so in context.md before launching.
"""
import json
import os
import re
import shutil
import subprocess
import sys

STOP = set("the a an and or of to in on for with is it its this that be was are not no when after from at by as "
           "into than then but can cannot does doesn't don't only still shows show".split())


def words(text):
    return {w for w in re.findall(r"[a-z0-9']+", (text or "").lower()) if len(w) > 2 and w not in STOP}


def read(p, default=""):
    try:
        with open(p, errors="replace") as f:
            return f.read()
    except OSError:
        return default


def jload(p, default=None):
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def config(run):
    out = subprocess.run(["bash", "-c", f'set -a; . "{run}/config.env" >/dev/null 2>&1; env -0'],
                         capture_output=True).stdout.decode()
    return dict(kv.split("=", 1) for kv in out.split("\0") if "=" in kv)


def filed(run):
    nums, titles = set(), []
    for line in read(os.path.join(run, "filed.txt")).splitlines():
        m = re.match(r"\s*(\S+)\s+(.*)", line)
        if m:
            n = re.search(r"(\d+)$", m.group(1))
            if n:
                nums.add(n.group(1))
            titles.append(m.group(2))
    for v in (jload(os.path.join(run, "state", "filed.json"), {}) or {}).values():
        n = re.search(r"/issues/(\d+)", v.get("url", ""))
        if n:
            nums.add(n.group(1))
        titles.append(v.get("title", ""))
    return nums, titles


def cites_filed(notes, nums, titles):
    """The notes name a filed issue: #N or issues/N, or at least 60% of a filed title's words."""
    if any(n in nums for n in re.findall(r"(?:#|/issues/)(\d+)", notes or "")):
        return True
    nw = words(notes)
    return any(len(tw) >= 3 and len(tw & nw) / len(tw) >= 0.6 for tw in map(words, titles))


def day_blocks(text):
    """{day: [(scenario id, block text)]} from '## Day N' sections and their '### S<n>' scenarios."""
    out = {}
    for m in re.finditer(r"^## Day (\d+)\b.*?(?=^## |\Z)", text, re.M | re.S):
        d = int(m.group(1))
        for b in re.finditer(r"^### (S\d+)\b.*?(?=^### |\Z)", m.group(0), re.M | re.S):
            out.setdefault(d, []).append((b.group(1), b.group(0).rstrip() + "\n"))
    return out


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    if len(args) < 2:
        sys.exit(__doc__)
    old, new = os.path.abspath(args[0]), os.path.abspath(args[1])
    if os.path.exists(os.path.join(new, "lanes.json")):
        sys.exit(f"{new} already holds a run (lanes.json); pick a new directory")
    cfg = config(old)
    cap = int(argv[argv.index("--scen-max") + 1]) if "--scen-max" in argv else int(cfg.get("SCEN_MAX") or 12)
    nums, titles = filed(old)
    chars = jload(os.path.join(old, "lanes.json"), [])
    plans, counts, left_out = {}, {"blocked": 0, "not_run": 0, "never reported": 0}, []
    for c in chars:
        cid = c["id"]
        ld = os.path.join(old, "lanes", cid)
        picked = []
        for d, blocks in sorted(day_blocks(read(os.path.join(ld, "scenarios.md"))).items()):
            rep = jload(os.path.join(ld, f"round{d}", "report.json"), {}) or {}
            st = {s.get("id"): s for s in rep.get("scenarios", []) if isinstance(s, dict)}
            for sid, block in blocks:
                s = st.get(sid, {})
                status = s.get("status") or "never reported"
                if status in ("pass", "fail"):
                    continue
                notes = " ".join(str(s.get(k, "")) for k in ("notes", "reason") if s.get(k))
                if status == "blocked" and cites_filed(notes, nums, titles):
                    left_out.append(f"{cid} day {d} {sid}: {notes[:120]}")
                    continue
                if status not in counts:
                    status = "not_run"
                counts[status] += 1
                if not os.path.isdir(os.path.join(ld, f"round{d}")):
                    notes = notes or "that day never ran"
                picked.append((d, sid, status, notes, block))
        if picked:
            plans[cid] = picked
    if not plans:
        sys.exit("nothing to make up: every planned scenario passed, failed, or is blocked by a filed bug")

    skill = os.path.dirname(os.path.abspath(__file__))
    subprocess.run(["bash", os.path.join(skill, "init-crowd.sh"), new], check=True, stdout=subprocess.DEVNULL)
    days = 1
    for cid, picked in plans.items():
        nd = os.path.join(new, "lanes", cid)
        os.makedirs(nd, exist_ok=True)
        for f in ("card.md", "memory.md"):
            if os.path.exists(os.path.join(old, "lanes", cid, f)):
                shutil.copy(os.path.join(old, "lanes", cid, f), os.path.join(nd, f))
        out = []
        for i, (d, sid, status, notes, block) in enumerate(picked):
            if i % cap == 0:
                out.append(f"## Day {i // cap + 1}\n\n")
            head, _, body = block.partition("\n")
            head = re.sub(r"^### S\d+", f"### S{i + 1}", head).rstrip() + f"  (from day {d} {sid})"
            earlier = f"_Earlier: {status}." + (f" {' '.join(notes.split())[:300]}" if notes else "") + "_"
            out.append(f"{head}\n{earlier}\n{body}\n")
        days = max(days, (len(picked) - 1) // cap + 1)
        with open(os.path.join(nd, "days"), "w") as f:
            f.write(f"{(len(picked) - 1) // cap + 1}\n")     # or plan() re-plans every shorter character in full
        with open(os.path.join(nd, "scenarios.md"), "w") as f:
            f.write("".join(out))
    ids = set(plans)
    lanes = [{**c, "depends_on": [p for p in c.get("depends_on", []) if p in ids]} for c in chars if c["id"] in ids]
    with open(os.path.join(new, "lanes.json"), "w") as f:
        json.dump(lanes, f, indent=1)
    for f in ("filed.txt", "context.md", "product-map.md", "product-map.md.src", "stack-faults.md"):
        if os.path.exists(os.path.join(old, f)):
            shutil.copy(os.path.join(old, f), os.path.join(new, f))
    for d in ("world", "stack", "tools"):     # tools: act-as helpers and device shims the scenarios call
        if os.path.isdir(os.path.join(old, d)):
            shutil.copytree(os.path.join(old, d), os.path.join(new, d), dirs_exist_ok=True)
    conf = read(os.path.join(old, "config.env"))
    with open(os.path.join(new, "config.env"), "w") as f:
        f.write(conf + f'\n# make-up of {old}: only the scenarios it never finished\nRUN_DIR="{new}"\nDAYS={days}\n')
    total = sum(len(p) for p in plans.values())
    summary = {"from": old, "characters": len(plans), "scenarios": total, "days": days, "scen_max": cap, **counts,
               "left_out_blocked_by_filed_bugs": left_out}
    with open(os.path.join(new, "state", "makeup.json"), "w") as f:
        json.dump(summary, f, indent=1)
    with open(os.path.join(new, "HANDOFF.md"), "a") as f:
        f.write(f"\nMake-up of {old}: {total} scenarios for {len(plans)} characters over {days} day(s) "
                f"({counts['blocked']} blocked, {counts['not_run']} not run, {counts['never reported']} never reported); "
                f"{len(left_out)} blocked by filed bugs left out (state/makeup.json).\n")
    print(f"make-up run {new}: {total} scenarios, {len(plans)} characters, {days} day(s); "
          f"{counts['blocked']} blocked, {counts['not_run']} not run, {counts['never reported']} never reported; "
          f"{len(left_out)} left out (blocked by a filed bug)")
    print("Next: seed what the blocked scenarios lacked, note it in context.md, then preflight and launch.")


if __name__ == "__main__":
    main(sys.argv[1:])
