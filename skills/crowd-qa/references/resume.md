# Resuming a run

Every piece of state is a file in the run dir, so a resume reads files, not the memory of a session. A run
survives the orchestrating session stopping, the machine rebooting, and the stack host rebooting. `crowd.py`
decides from these files alone, so continuing is the same command as starting.

| File | Written by | Means |
|---|---|---|
| `lanes.json` | the orchestrator, before launch | the characters: id, role, surface, depends_on |
| `lanes/<id>/scenarios.md` | planner (crowd.py plan) | plan exists with every day; no planner runs again |
| `state/skipped.json` | crowd.py plan | characters with no usable plan after two attempts |
| `lanes/<id>/state.json` | run-tester.sh | day, status (`waiting-capacity`, `waiting-stack`, `running`, `done`), engine |
| `lanes/<id>/round<N>/report.json` | tester | progress, updated after each scenario |
| `lanes/<id>/round<N>/DONE` | run-tester.sh, or crowd.py when a runner died | day finished: `exit=0`, `exit=quota ...`, `exit=1 rc=runner-died` |
| `lanes/<id>/round<N>/verdict.json` | crowd.py judge | day judged; never judged again |
| `lanes/<id>/feedback-r<N>.md` | crowd.py judge | scenarios day N+1 must run |
| `state/crowd-state.json` | crowd.py, every poll | each character-day: pending, running, judging, judged, skipped, failed |
| `state/issues.json`, `filed.json`, `refused.json` | crowd.py dedupe and file | the merged list; what was filed (with URLs) and refused; filed bugs are never filed twice |
| `state/costs.jsonl` | crowd.py | every model step: model, dollars, turns, seconds |
| `state/ledger.jsonl` | run-tester.sh, at the end of every day | one line per finished character-day |
| `state/<id>.json`, `state/summary.json` | save-state.py (every 5 min and at each day's end) | credentials, what the character made, each day's result |
| `state/db/` | watchdog.sh | database backups; the latest is restored after a wipe |
| `TESTER_FALLBACK` | run-tester.sh | an engine hit its limit (`from=<engine>`); its characters use the fallback until it expires |
| `QUOTA_PAUSE.<engine>` | run-tester.sh | that engine is out and has no fallback; crowd.py lifts it after RETRY_PRIMARY_MIN |
| `STACK_DOWN` | api-watch.sh | the stack failed its checks; nothing starts and testers hold until it is gone |
| `lanes/<id>/days`, `scenarios.unsplit.md` | crowd.py plan | a day longer than SCEN_MAX was split; the character runs this many days |
| `.browsers/` | browser-pool.sh, remote-browser.sh | browser slot holders (`remote-N`, `local-N`: lane, pid), extra browsers, CDP forwards |
| `.engine-switched` | quota-guard.sh | an account switch stopped testers; runners resume the same session |
| `quota.log`, `logs/quota-guard.pid` | quota-guard.sh | every account reading and switch, pause, fallback |
| `DONE` with `exit=retry` | run-tester.sh | the day never started testing (its browser failed); crowd.py queues it again |

## Procedure (after a reboot, a stopped session, or "continue")

1. `bash <run>/scripts/resume.sh <run>`: restarts the watchers (quota-guard included), runs the watchdog once (stack up, data
   restored), runs the stack check, saves state, and starts `crowd.py <run> all` unless it is still running.
2. `python3 <run>/scripts/crowd.py <run> status`: where every character-day stands and what the model steps
   have cost.

Testers still running are left alone: crowd.py waits for them instead of starting a second copy.
To run a day again (its world was lost), move that day's `round<N>/` into `archive/<reason>/` and resume.

## Failure modes seen in practice

- **Editing a running script.** Bash reads scripts lazily, so editing `run-tester.sh` under a live day can run
  garbage. Runners execute from a frozen copy in `.runner/`; edit the source freely.
- **A wiped stack under a resumed run.** Characters remember accounts that no longer exist. Archive the
  affected days (`archive/<reason>/`), keep their verified findings in `archive/<reason>/prior_findings.json`
  (dedupe reads it), give the characters fresh emails, post a notice on the square, and resume.
- **Reboot under memory pressure.** The capacity gate stops new characters under pressure (free memory, swap
  growth, swap in use, load); it cannot stop other apps. memlog tells a pressure reboot from an unrelated one.
- **Characters shifting the active workspace.** A fresh sign-in can land in an empty workspace and a tester
  then reports every scenario as blocked. Say in `context.md` which workspace holds the data.
- **Quota false positive.** A tester that quotes earlier feedback about a usage limit must not count as a
  limit; only the tester CLI's own error line does.
- **Fix stage after a restart.** `state/file-plan.json` and `state/FILE_PLAN_APPROVED` keep the filing decision;
  `state/areas-detail.json` each area's attempt, verdicts and PR; `state/jobs.json` every job with its runner,
  model and thread; `fix/jobs/<id>/result.json` what each agent reported. A job is finished only when
  `crowd.py <run> jobs done <id>` accepted its result. With the cli runner, `resume.sh` restarts crowd.py and
  it carries on. With t3, check each running job's thread before anything else; with subagent, run
  `crowd.py <run> jobs requeue-running` (`references/fixing.md`).
