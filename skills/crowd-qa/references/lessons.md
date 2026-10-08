# What real crowd runs taught

Each line: what happened, what it cost, what the skill does about it now. Read it before the first launch on a
new machine.

| Seen | Cost | Now |
|---|---|---|
| Disk fell to 137 MB free mid-run (4 simulators, Docker build cache, swap) | The database and Docker died; 17 of 21 characters never reached the product; the run was redone | `DISK_MIN_GB` in the runner, a disk check in `preflight.sh`, `DEVICE_POOL` sized to the machine |
| Two simulators, a debug mobile build and Docker on a 16 GB laptop (load 300-800) | Docker stopped answering | One simulator on small machines; the backend can run on another machine (`references/remote-stack.md`) |
| Docker Desktop paused itself and never came back | Every request through Docker hung | `preflight.sh` checks `docker ps` answers |
| Env flags set to `1` where the schema accepts only `true` | Features silently off; looked like two product bugs | The stack check reads flags back from the API; the judge rejects stack faults |
| A required key missing from the local env | The core create flow never finished; looked like a product bug | The stack check proves the core flow end to end before launch |
| The machine running the backend rebooted mid-run | Every character-day for an hour tested a dead server | `api-watch.sh` writes STACK_DOWN after two failed checks; runners and testers hold; the outage window becomes a stack fault |
| That reboot also wiped the database (the stack kept Postgres in tmpfs by design) | Every account and everything characters had made was gone; day 1 was redone for everyone | `watchdog.sh` backs up through DB_BACKUP_CMD and restores after a wipe; a canary sign-in in HEALTH_CHECKS catches a wipe a health endpoint misses |
| The orchestrating session was stopped and restarted several times | Runs depended on what lived in that session | `state/` (a ledger line per finished day, a file per character) and `resume.sh` rebuild the launch in any new session |
| The app was served on a port the backend did not trust for sign-in redirects | Every sign-in failed with "Invalid callbackURL"; ten scenarios blocked | Put the app's real origin rules in the stack check; serve the app on an origin the backend trusts |
| A weak-network character was marked exclusive | It held the run alone and everyone else waited for an hour | Fake a weak network in the character's own browser (`agent-browser offline on/off`); keep Exclusive for real shared stalls only |
| A planner returned an empty plan for one character | That character had nothing to test | The orchestrator checks every scenarios.md exists and has the day's sections before launch; drop or replan the character |
| Quitting Simulator.app to save CPU | It shut the booted simulator down mid-test | Never quit it during a run |
| `npx -y agent-device` on every call | 1.4 s extra per call, about 100 calls per character-day | Global install; `preflight.sh` checks |
| The device runner built during the first tester call | The first command timed out | `agent-device prepare ios-runner` once per simulator, before launch |
| A log stream left running | It took a whole CPU core | Testers read logs once per flow |
| Maximum reasoning effort spent on every single tap | Slow days | `act.py` batches plain-word steps with no model call per tap |
| Testing waited for every scenario plan | Idle devices while the last plans were written | A character's day 1 starts as soon as its own plan exists |
| Restarting the workflow to add capacity | Unfinished plans were rewritten each time | Set capacity in config.env (read at every start) and avoid restarts mid-day |
| Dependents waited behind every provider on one device | A serial run | Browser, CLI and API lanes beside the device lane; providers first |
| The workflow ended but runners kept going | Orphans held devices; stale state | `preflight.sh` refuses to launch over leftover runners |
| A fresh relaunch over old round dirs | Judges return an existing `verdict.json` unchanged | Archive `lanes/*/round*` before a fresh relaunch, or use `redo` tags when resuming |
| The tester account was shared with the user's own work | It ran out before the crowd used much | A fallback engine (FALLBACK_TESTER) takes over at the limit; the primary is retried later |
| A tester opened extra browsers for a second user | Load on the orchestrating machine went from 7 to 35 | The brief names one extra session per character (`<session>-2`) and nothing else |
| Planners re-explored the source each, and restarts re-ran them (95 planners for 30 characters) | Half of all orchestrator tokens | One product map; `args.planned` skips existing plans in code (`references/cost.md`) |
| Judges hunted for reports, logs and posts with shell commands | Most of every judge's turns | `judge-pack.py` puts each day's inputs in one file |
| A tester restarted after a fallback started a fresh session | It re-read the whole brief and lost its context | The runner resumes the newest session of that engine |
| `pkill -f <name>` over SSH | Killed the SSH session itself | Kill by pid file |
