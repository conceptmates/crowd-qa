#!/usr/bin/env bash
# usage: resume.sh <run-dir>
# Rebuilds everything needed to continue a run from disk, in any orchestrating session or after a reboot:
#   1. restarts the watchers (restart-watchers.sh) and runs the watchdog once (stack up, data restored)
#   2. runs the stack check and lists leftover runners
#   3. saves state/ and writes state/launch-resume.json: the launch args plus `done` (the last judged day per
#      character), prior verdicts, and the findings already verified (state/prior_findings.json)
# Then launch the workflow with those args (SKILL.md, "Resume"). In the same orchestrating session as the
# stopped workflow, resuming that run id is cheaper: finished agents return their cached results.
set -u
RUN="$(cd "${1:-$(dirname "$0")/..}" && pwd)"
. "$RUN/config.env"
echo "== watchers"; bash "$RUN/scripts/restart-watchers.sh" "$RUN"
if [ -n "${STACK_UP_CMD:-}" ] || [ -n "${DB_BACKUP_CMD:-}" ]; then echo "== watchdog"; bash "$RUN/scripts/watchdog.sh" "$RUN" | tail -2; fi
if [ -n "${STACK_VERIFY:-}" ]; then echo "== stack check"; bash -c "$STACK_VERIFY" 2>&1 | tail -1; fi
echo "== leftover runners (must be none before a fresh launch)"; pgrep -fl "run-tester.sh $RUN " || echo none
echo "== state"; python3 "$RUN/scripts/save-state.py" "$RUN"
python3 - "$RUN" <<'PY'
import glob, json, os, sys
run = sys.argv[1]
launch = os.path.join(run, "launch.json")
if not os.path.exists(launch):
    sys.exit("no launch.json yet: nothing to resume")
args = json.load(open(launch))["args"]
summ = json.load(open(os.path.join(run, "state", "summary.json")))
args["done"] = summ["done"]
pf, pv = [], {}
for f in sorted(glob.glob(os.path.join(run, "archive", "*", "prior_findings.json"))):
    pf += json.load(open(f))
for cid, last in summ["done"].items():
    for d in range(1, last + 1):
        vp = os.path.join(run, "lanes", cid, f"round{d}", "verdict.json")
        if os.path.exists(vp):
            v = json.load(open(vp))
            pv[cid] = {k: v.get(k) for k in ("satisfied", "coverage_score", "in_character")} | {"verified_findings": []}
            pf += [{**f, "day": d} for f in v.get("verified_findings", [])]
pfile = os.path.join(run, "state", "prior_findings.json")
json.dump(pf, open(pfile, "w"), indent=1)
args["prior_findings_file"], args["prior_verdicts"] = pfile, pv
args.pop("redo", None); args.pop("prior_findings", None)
# no planner for a character whose plan has every day; no product-map agent when the map exists
days = int(args.get("days", 2))
planned = []
for c in args.get("characters", []):
    sp = os.path.join(run, "lanes", c["id"], "scenarios.md")
    if os.path.exists(sp):
        txt = open(sp).read()
        if all(f"## Day {d}" in txt for d in range(1, days + 1)):
            planned.append(c["id"])
args["planned"] = planned
pm = os.path.join(run, "product-map.md")
args["product_map_ready"] = os.path.exists(pm) and os.path.getsize(pm) > 500
out = os.path.join(run, "state", "launch-resume.json")
json.dump(args, open(out, "w"), indent=1)
print(f"wrote {out}: done={summ['done']}, {len(pf)} findings already verified, {len(planned)} plans reused")
PY
