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
| Restarting the orchestration to add capacity | Unfinished plans were rewritten each time | Capacity lives in config.env (read at every start); crowd.py never needs a restart |
| Dependents waited behind every provider on one device | A serial run | Browser, CLI and API lanes beside the device lane; providers first |
| The orchestration ended but runners kept going | Orphans held devices; stale state | crowd.py attaches to live runners after a restart and never starts a second copy |
| A fresh relaunch over old round dirs | Judges return an existing `verdict.json` unchanged | Archive `lanes/*/round*` before a fresh relaunch, or use `redo` tags when resuming |
| The tester account was shared with the user's own work | It ran out before the crowd used much | A fallback engine (FALLBACK_TESTER) takes over at the limit; the primary is retried later |
| A tester opened extra browsers for a second user | Load on the orchestrating machine went from 7 to 35 | The brief names one extra session per character (`<session>-2`) and nothing else |
| Planners re-explored the source each, and restarts re-ran them (95 planners for 30 characters) | Half of all orchestrator tokens | One product map; `args.planned` skips existing plans in code (`references/cost.md`) |
| Judges hunted for reports, logs and posts with shell commands | Most of every judge's turns | `judge-pack.py` puts each day's inputs in one file |
| A tester restarted after a fallback started a fresh session | It re-read the whole brief and lost its context | The runner resumes the newest session of that engine |
| `pkill -f <name>` over SSH | Killed the SSH session itself | Kill by pid file |
| A reboot took the stack down; the health watcher was not running afterwards | 10 more testers started against a dead server; 17 character-days redone | Each runner probes the stack itself (`api-watch.sh once`) before starting; `NOTIFY_CMD` tells a person |
| The laptop running the testers admitted 20 by free memory alone | Load 940, 14 GB of swap, dead for four hours | `capacity.sh` also refuses while swap in use exceeds `SWAP_MAX_MB` |
| An agent per character-day only waited for its tester | $106 of a $1,000 run; $77 more re-starting them on resume | `crowd.py` waits in code |
| The tester brief said "carry forward" yesterday's results | 61% of day-2 findings repeated the same character's day 1 | Day 2 gets yesterday's verified bugs as a re-check list; repeats are rejected in code |
| A character judged "satisfied" on day 1 was skipped on day 2 | 46 planned scenarios never ran | Every character gets every day |
| One engine's usage limit paused the whole crowd | Characters on other engines waited too | Quota pauses are per engine (`QUOTA_PAUSE.<engine>`) |
| Every bug re-checked on the top model before filing | 3.9% refused; three real bugs refused only for a mislabelled screenshot | Sonnet checks, the top model only for high severity or unconfirmed causes; wrong screenshots are repaired |
| Customers waited for the business owner's day to be judged | 8 of 17 businesses never got a customer | Dependents wait for the provider's run, not its judge; days do not wait for each other |
| Testers slept a fixed time after every action | A third of tester wall time | The brief asks for waits on elements |
| A model step hit a rate or usage limit | It failed twice in seconds; a failed judge would have rejected the day's bugs | `crowd.py` waits and retries on limits (`LLM_LIMIT_WAIT_S`); a judge with no answer leaves no verdict, so the next run judges that day again |
| A step's answer did not parse | The retry paid for the whole task again | The retry resumes the same session and asks only for the answer |
| `gh issue create` failed once | The bug was recorded as refused for good | Filing errors are retried on the next run; an issue with the same title already open is linked, not filed again |
| Code rejected findings at 60% word overlap with a filed issue | Different bugs on one screen share most of their words | Code rejects only near-identical titles; similar ones go to the judge with a note |
| The launch report was rewritten on every rerun | One Opus call each time for the same text | Rewritten only when coverage, filing or the square changed |
| Claude accounts were switched by hand (`caam activate`) while planners and judges ran | 29 failed model calls and 6 plans written again | Only `quota-guard.sh` switches accounts during a run; it waits while an Opus step runs unless that account is already over the bar |
| A Codex account on a ChatGPT "go" plan refused the tester model | 12 character-days ran with no evidence | `preflight.sh` makes a one-word call with the tester model on every `CODEX_ACCOUNTS` profile (an isolated caam profile, or `PREFLIGHT_MODEL_PROBE=switch`) and fails a go/free plan it could not test |
| The guard read "no numbers" for the active account as a limit | 6 false quota pauses | No reading is never a limit: the guard decides nothing that round |
| An account switch stopped testers, which then started fresh | Each restart re-read the whole brief | The guard touches `.engine-switched`; the runner resumes the same session with a two-line message |
| Days of 31 scenarios | Testers wrapped up early; 121 scenarios never reported | `SCEN_MAX` 12; crowd.py splits a longer day into sessions; the brief asks for report.json after every scenario; `makeup.py` builds a run of what was left |
| Characters shared consumable fixtures (one ticket, one coupon) | A scanner's ticket was cancelled by a customer character first; the scan scenario failed for a reason that was not a bug | Each character gets its own seeded data ("Your own test data" on the card); planners mark consumables `own:` |
| Fixtures seeded by writing rows: a festival-pass listing without its access rule, sign-up characters given pre-made verified accounts, an "already scanned" ticket set by status | Every festival-pass checkout refused; sign-up and verification scenarios impossible; a used ticket scanned green | Seed through the product's own calls where state has side effects, give sign-up characters no account, and prove each fixture in the stack check |
| Seeded "tomorrow" and "today" tickets computed in UTC | They landed on the wrong local day and scanned green | Planners and seeds use the product's time zone (context.md) |
| Characters waited on the square for an admin or a scanner who was not running | Blocked scenarios that a helper could have run | An act-as helper (`templates/act-as.example.py`) does the other side through the API |
| The CDP forward was used before ssh had opened it | The first browser command failed on a loaded machine (forwards took more than 3 s) | `remote-browser.sh` waits until the forwarded port answers |
| A CDP port was already in use by another session on the same machine | A tester could have driven somebody else's browser | `browser-pool.sh` skips a slot whose port listens and is not this run's; `remote-browser.sh` refuses it; preflight reports it |
| `agent-browser connect <port>` | An idle local Chrome per session (39-66 Chrome processes on the orchestrating Mac); when the link dropped, fix agents twice screenshotted the local browser | Testers get `AGENT_BROWSER_CDP` and `AGENT_BROWSER_EXECUTABLE_PATH=scripts/no-local-browser.sh`, which refuses and logs any local launch |
| A remote browser that never started (Chromium or the forward failed) | The day was judged with nothing run; days were archived by hand | The runner ends it `exit=retry`; crowd.py queues it again like a quota stop |
| Ticket PDFs downloaded by the remote browser | They stayed on the other machine; testers could not look inside | A download helper (`templates/pdf.example.py`) fetches the same file through the API and renders page 1 |
| The machine running crowd.py restarted | crowd.py, the guard and the watchers (all nohup'd) died; the run stood still until resumed by hand | `resume.sh` restarts them all, quota-guard included, and re-attaches live runners |
| The product map on Opus at high effort | $40.85 of $62 of planning, and a whole 5-hour window | `MAP_EFFORT` medium; `PRODUCT_MAP_FROM` reuses a saved map when the source commits match (16 new characters planned for $13.77) |
