# Resuming a run

Every piece of state is a file in the run dir, so a resume reads files, not the memory of a session. A run
survives the orchestrating session stopping, the machine rebooting, and the stack host rebooting.

| File | Written by | Means |
|---|---|---|
| `launch.json` | the orchestrator, before launch; `launch.py record` after | script path, the exact args, every run id |
| `lanes/<id>/scenarios.md` | planner | plan exists; the planner returns immediately on resume |
| `lanes/<id>/state.json` | run-tester.sh | day, status (`waiting-capacity`, `waiting-stack`, `running`, `done`), engine |
| `lanes/<id>/round<N>/report.json` | tester | progress, updated after each scenario |
| `lanes/<id>/round<N>/DONE` | run-tester.sh / watch-round.sh | day finished: `exit=0`, `exit=quota ...`, `exit=1 rc=runner-died` |
| `lanes/<id>/round<N>/verdict.json` | judge | day judged; a re-run judge returns it unchanged |
| `lanes/<id>/feedback-r<N>.md` | judge | what day N+1 must do |
| `state/ledger.jsonl` | run-tester.sh, at the end of every day | one line per finished character-day |
| `state/<id>.json`, `state/summary.json` | save-state.py (every 5 min and at each day's end) | credentials, what the character made, each day's result; the `done` map |
| `state/db/` | watchdog.sh | database backups; the latest is restored after a wipe |
| `TESTER_FALLBACK` | run-tester.sh | the primary engine hit its limit; expires after RETRY_PRIMARY_MIN |
| `QUOTA_PAUSE` | run-tester.sh | every engine is out; the wait agent lifts it after RETRY_PRIMARY_MIN |
| `STACK_DOWN` | api-watch.sh | the stack failed its checks; nothing starts and testers hold until it is gone |
| workflow journal | Workflow tool | completed agent calls; replayed for free with `resumeFromRunId` in the same session |

## Procedure (after a reboot, a stopped session, or "continue")

1. `bash <run>/scripts/resume.sh <run>`: restarts the watchers, runs the watchdog once (stack up, data
   restored), runs the stack check, lists leftover runners, saves state and writes `state/launch-resume.json`.
2. `python3 <run>/scripts/status.py <run>`: where every character stands.
3. **Same orchestrating session as the stopped workflow:** call `Workflow({ scriptPath, args, resumeFromRunId })`
   with the args from `launch.json`. Completed agents replay from the journal; day agents re-attach to live
   testers. To force a cached day to run again (its world was lost), add `redo: { <id>: <day> }` to the args
   after archiving that day's files.
4. **A new session:** launch fresh with `args` = the contents of `state/launch-resume.json`. Its `done` map skips
   finished days, `prior_findings_file` carries the findings already verified, and the planners skip existing
   plans.
5. Record the new run id with `launch.py <run> record <id>`.

Testers still running (`pgrep -fl "run-tester.sh <run> "`) are left alone either way; day agents wait on
them instead of starting a second copy.

## Failure modes seen in practice

- **Stale quota markers.** A `DONE` saying `quota` from before a reset looks like a fresh pause. Day agents
  rename it to `DONE.stale-quota` before starting; if you relaunch by hand, do the same.
- **Editing a running script.** Bash reads scripts lazily, so editing `run-tester.sh` under a live day can run
  garbage. Runners execute from a frozen copy in `.runner/`; edit the source freely.
- **Runner died without DONE.** `watch-round.sh` writes `DONE` (`rc=runner-died`) so nothing waits forever.
- **Lost launch args.** Without the exact args, replay misses every cached agent. That is why `launch.json` is
  written before the launch, not after.
- **A wiped stack under a resumed run.** Characters remember accounts that no longer exist. Archive the
  affected days (`archive/<reason>/`), keep their verified findings in `archive/<reason>/prior_findings.json`,
  give the characters fresh emails, post a notice on the square, and resume with `redo` tags.
- **Reboot under memory pressure.** The capacity gate stops new characters under pressure; it cannot stop
  other apps. memlog tells a pressure reboot from an unrelated one.
- **Characters shifting the active workspace.** A fresh sign-in can land in an empty workspace and a tester
  then reports every scenario as blocked. Say in `context.md` which workspace holds the data.
- **Quota false positive.** A tester that quotes earlier feedback about a usage limit must not count as a
  limit; only the tester CLI's own error line does.
