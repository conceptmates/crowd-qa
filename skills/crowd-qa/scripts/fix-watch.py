#!/usr/bin/env python3
"""The watch: checks a run is moving, with no model call while it is.

  fix-watch.py <run>              one pass: prints one line per problem or event, exits
  fix-watch.py <run> --loop [S]   a pass every S seconds (default 60), printing only what changed; ends when every
                                  area is done or needs a person. Run it under the session's monitor tool so each
                                  line wakes the orchestrator, which spends tokens only then.

Lines start with a word the orchestrator can act on:
  READY    jobs are queued and a slot is free (subagent/t3 runners: launch them; cli: crowd.py is not running)
  STALLED  a running job has written nothing for WATCH_STALL_MIN minutes (default 40)
  FAILED   a job failed, or an area went to needs-human
  STACK    the stack check (STACK_VERIFY, else WATCH_URL) failed
  QUOTA    quota-guard has paused an engine
  ERROR    a new error line in logs/crowd.log
  LOCK     a device or backend lock is held by a job that is not running
  DONE     every area is done or needs a person (the loop ends)

WATCH_AGENT=on: a problem line (not READY or DONE) also gets one short model call (WATCH_MODEL, default opus, effort
low) given only that line and the last 40 lines of the job's log; its two-line diagnosis is appended to the event
and to logs/watch.md. One call per distinct problem, never repeated.
"""
import glob, json, os, re, subprocess, sys, time

RUN = os.path.abspath(sys.argv[1])
LOOP = "--loop" in sys.argv
EVERY = int(sys.argv[sys.argv.index("--loop") + 1]) if LOOP and len(sys.argv) > sys.argv.index("--loop") + 1 else 60


def cfg():
    out = subprocess.run(["bash", "-c", f'set -a; . "{RUN}/config.env" >/dev/null 2>&1; env -0'],
                         capture_output=True, text=True).stdout
    return dict(x.split("=", 1) for x in out.split("\0") if "=" in x)


def jload(p, d):
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, ValueError):
        return d


def newest(paths):
    """Newest mtime under these paths, skipping .git, build output and dependencies."""
    best = 0
    for top in paths:
        if not top or not os.path.exists(top):
            continue
        for root, dirs, files in os.walk(top):
            dirs[:] = [d for d in dirs if d not in (".git", "build", ".dart_tool", "node_modules", "Pods")]
            for f in files:
                try:
                    best = max(best, os.path.getmtime(os.path.join(root, f)))
                except OSError:
                    pass
    return best


def check(C, seen):
    ev = []
    jobs = jload(f"{RUN}/state/jobs.json", {})
    areas = jload(f"{RUN}/state/areas-detail.json", {})
    now = time.time()
    running = [j for j in jobs.values() if j.get("status") == "running"]
    queued = [j for j in jobs.values() if j.get("status") == "queued"]
    par = int(float(C.get("FIX_PARALLEL") or 3))
    if queued and len(running) < par:
        ev.append(("READY", f"{min(len(queued), par - len(running))} of {len(queued)} queued jobs can start "
                            f"({len(running)} running, FIX_PARALLEL {par})"))
    stall = int(float(C.get("WATCH_STALL_MIN") or 40)) * 60
    for j in running:
        started = time.mktime(time.strptime(j["started"][:19], "%Y-%m-%dT%H:%M:%S")) if j.get("started") else now
        last = max(started, newest([os.path.dirname(j.get("prompt", "")), j.get("worktree", ""),
                                    f"{RUN}/logs/job-{j['id']}.log", f"{RUN}/logs/fix-device-{j['id']}.log"]))
        if now - last > stall:
            ev.append(("STALLED", f"{j['id']} ({j.get('runner')}, {j.get('model')}) has written nothing for "
                                  f"{int((now - last) / 60)} min"))
    for j in jobs.values():
        if j.get("status") == "failed":
            ev.append(("FAILED", f"{j['id']}: {j.get('why', '')[:160]}"))
    for k, a in areas.items():
        if a.get("status") == "needs-human":
            why = "; ".join(u.get("text", "") for u in a.get("unresolved", []))[:160]
            ev.append(("FAILED", f"area {k} needs a person{': ' + why if why else ''}"))
    # a job handed out by `jobs next` (claimed) is live too: its agent may take a device before `jobs start`
    live = {j["id"] for j in running} | {j["id"] for j in queued if now - float(j.get("claimed_at") or 0) < 900}
    for lock in glob.glob(f"{RUN}/fix/devices/*") + glob.glob(f"{RUN}/fix/stack/*"):
        if lock.endswith(".built"):
            continue
        try:
            if now - os.path.getmtime(lock) < 120:   # just taken: the job may not be registered yet
                continue
        except OSError:
            continue
        try:
            holder = open(lock).read().strip()
        except OSError:
            continue
        if holder and holder not in live:
            ev.append(("LOCK", f"{os.path.relpath(lock, RUN)} is held by {holder}, which is not running"))
    verify, url = C.get("STACK_VERIFY", ""), C.get("WATCH_URL", "")
    if verify or url:
        cmd = verify or f"curl -sf -o /dev/null --max-time 10 {url}"
        try:
            r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            r = subprocess.CompletedProcess(cmd, 124, "", "timed out after 120s")
        if r.returncode:
            ev.append(("STACK", f"stack check failed: {(r.stdout + r.stderr).strip().splitlines()[-1:] or ['exit ' + str(r.returncode)]}"))
    q = f"{RUN}/quota.log"
    if os.path.exists(q):
        tail = open(q).read().splitlines()[-4:]
        for line in tail:
            if "paused=yes" in line:
                ev.append(("QUOTA", line.strip()[:200]))
    log = f"{RUN}/logs/crowd.log"
    if os.path.exists(log):
        size = os.path.getsize(log)
        start = seen.get("_logpos", size if not seen else 0)
        if size < start:
            start = 0
        with open(log, errors="replace") as f:
            f.seek(start)
            for line in f:
                if re.search(r"Traceback|crashed|error|could not|failed twice", line, re.I) and "queued again" not in line:
                    ev.append(("ERROR", line.strip()[:220]))
        seen["_logpos"] = size
    if areas and all(a.get("status") in ("done", "needs-human") for a in areas.values()) and not running and not queued:
        ev.append(("DONE", f"{sum(a['status'] == 'done' for a in areas.values())} areas done, "
                           f"{sum(a['status'] == 'needs-human' for a in areas.values())} need a person"))
    return ev


def diagnose(C, kind, text):
    """One short model call for a new problem: only the line and the job's own log tail."""
    m = re.match(r"(?:area )?([\w.-]+)", text)
    logs = sorted(glob.glob(f"{RUN}/logs/job-{m.group(1)}*.log")) if m else []
    tail = "\n".join(open(logs[-1], errors="replace").read().splitlines()[-40:]) if logs else ""
    prompt = (f"A crowd QA run's watch script reported: {kind} {text}\nLast lines of that job's log:\n{tail[-4000:]}\n\n"
              "In at most two short lines: the likely cause, and the one command or action that unblocks it. Never suggest "
              "deleting a lock, killing a process or requeueing a job unless the log proves its holder is gone; when "
              "unsure, say what to check first. No preamble.")
    try:
        r = subprocess.run(["claude", "-p", "--model", C.get("WATCH_MODEL") or "opus", "--effort", "low"],
                           input=prompt, capture_output=True, text=True, timeout=180)
        note = r.stdout.strip().replace("\n", " / ")[:400]
    except (OSError, subprocess.TimeoutExpired):
        note = ""
    if note:
        with open(f"{RUN}/logs/watch.md", "a") as f:
            f.write(f"- {time.strftime('%H:%M:%S')} {kind} {text}\n  {note}\n")
    return note


def main():
    seen = jload(f"{RUN}/state/watch.json", {})
    while True:
        C = cfg()
        try:
            events = check(C, seen)
        except Exception as e:      # a state file caught mid-write, a slow stack check: report it, keep watching
            import traceback
            where = traceback.extract_tb(e.__traceback__)[-1]
            events = [("ERROR", f"watch pass failed: {type(e).__name__}: {e} (fix-watch.py:{where.lineno})")]
        keys = {f"{k} {t}" for k, t in events}
        for k, t in events:
            key = f"{k} {t}"
            if k in ("READY", "STALLED") and seen.get(key):   # repeat these only every 10 minutes
                if time.time() - seen[key] < 600:
                    continue
            elif seen.get(key):
                continue
            seen[key] = time.time()
            note = diagnose(C, k, t) if C.get("WATCH_AGENT") == "on" and k not in ("READY", "DONE") else ""
            print(f"{k} {t}{'  → ' + note if note else ''}", flush=True)
        for key in [k for k in seen if not k.startswith("_") and k not in keys]:
            del seen[key]            # cleared: report it again if it comes back
        os.makedirs(f"{RUN}/state", exist_ok=True)
        with open(f"{RUN}/state/watch.json", "w") as f:
            json.dump(seen, f)
        if not LOOP or any(k == "DONE" for k, _ in events):
            return
        time.sleep(EVERY)


if __name__ == "__main__":
    main()
