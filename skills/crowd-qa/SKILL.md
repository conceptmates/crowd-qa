---
name: crowd-qa
description: Launch-week crowd QA for a web app, mobile app, CLI or HTTP API. Named characters (a designer, a boundary tester, a speedy user, and people drawn from the product's own users) each use the product over simulated days, post on a shared town square, confirm each other's bugs, use what other characters made, and keep a diary between days. Every day is judged against screenshots and source, stack faults are thrown out, and the bugs that hold up are filed in the reporter's voice with evidence after a duplicate check against every existing issue and comment. Optionally the filed bugs are then fixed: a fixer per area in its own worktree, a reviewer on another model, a re-test by each bug's reporter, disputes settled by a judge, and a draft PR per area with before/after evidence. It ends in a launch-readiness report. The run loop is a script (crowd.py), so a model is only called where judgement is needed. Use this whenever the user wants end-to-end testing "as if we released it to 20 people", persona or character testing, simulated users, MiroFish-style QA, a pre-launch crowd test, or bug reports written by personas, even if they never say "crowd".
---

# crowd-qa

A crowd of characters uses the product the way its real users would, over several simulated days, and you
file what breaks. Each character is played by a tester agent (Sonnet by default, Codex when asked). Every other model step runs
on Opus: Sonnet only drives the product's screens. Everything between the model
calls (scheduling, waiting, retries, coverage, evidence checks, merging, filing) is code in `scripts/crowd.py`,
which is why a 30-character run costs a few hundred dollars instead of about a thousand (`references/cost.md`).

You plan with the user, write the characters, check the stack, launch `crowd.py`, and report. You do not test
the product yourself. Fixing is a separate stage the user opts into (`FIXING=on`, Step 7); you never fix a bug by
hand.

Read `references/lessons.md` before the first launch on a new machine: each line is a failure that cost a real
run hours, and what now prevents it.

## Step 1: Plan with the user
Look up every fact you can before asking anything: the stack, routes and roles (README, CLAUDE.md, the auth
code), free RAM, disk and swap, `gh auth status`, which of `claude`, `codex`, `agent-browser`, `agent-device`
are installed. Report what you found, then ask the open decisions with the interactive question tool, one round
per call, recommended option first. Ask only what earlier answers have settled.

1. **Product and stack.** Which surfaces the crowd uses (web, ios, android, cli, api). Where the backend runs:
   this machine (`STACK_MODE=local`) or another over SSH (`remote`, `references/remote-stack.md`). Offer remote
   when this machine is short on memory or disk. Never point a crowd at production.
2. **The crowd.** How many people (10 / 20 / 30) and days (1 / 2 / 3). Which of the product's own user types to
   cover, offered from what you found (sellers and buyers, hosts and guests, admins and members). Who depends
   on whom. Language for cards and the square (`SQUARE_LANG`).
3. **Engines.** Ask the question in `references/engines.md`: the main tester (Sonnet medium is recommended),
   which characters, if any, run on Codex (none by default), and what happens at a usage limit. Preflight fails until
   `ENGINE_CONFIRMED=yes`, so this cannot be skipped by accident. With several subscription accounts, list their
   caam profiles in `CODEX_ACCOUNTS` / `CLAUDE_ACCOUNTS`; `quota-guard.sh` rotates them. From launch on, only the
   guard switches accounts: a switch by hand fails the Opus steps that are running.
4. **Access and safety.** Test accounts, how verification codes and emails are read, payment test keys, what
   testers may create or delete, data they must never touch. A missing credential becomes a blocked scenario.
5. **Output.** Trackers per bug kind (`TRACKERS="app=owner/app backend=owner/api"`, or a `FILE_CMD` hook for
   another tracker), the evidence branch, the run label. Whether to file at all (`FILING=off` stops at the
   merged list). Filing waits for the user to approve `file-plan.md` unless they choose `FILE_AUTO=yes`.
6. **Fixing.** Whether to fix what is filed (`FIXING=on`, `references/fixing.md`). If yes: the runner (`t3` when
   the T3 tools answer and the product repo is a T3 project, else `subagent`, or `cli` for an unattended run),
   local clones (`REPOS`), how a worktree becomes a running frontend (`FE_*`) and how a backend branch is
   deployed (`BE_*`), the checks fixers must pass, and which account the reporters' re-check comments come from.
7. **Read-back.** Restate the plan in one short table and ask what is missing. Nothing starts before the user
   confirms.

## Step 2: Run dir, config and stack
```bash
bash <skill>/scripts/init-crowd.sh "$RUN"      # real disk, never /tmp
```
It copies the scripts, templates and references into the run dir and writes `config.env` with every setting
commented. Fill it from the answers.

Everything specific to the product goes in `$RUN/context.md` (`templates/context.example.md`): addresses,
accounts, how to read a code, and **behaviour that is intended**, so neither testers nor judges report it.

The stack:
- Write a stack check from `templates/stack-verify.example.py` and set `STACK_VERIFY`. It makes the product's
  real calls end to end (sign-up, the core create flow, what another user can reach, an upload). Flags that read
  `1` as false and missing keys look like product bugs until a check like this finds them.
- Set `HEALTH_CHECKS`, including a **canary sign-in** for an account you create before launch. A wiped database
  still answers a health endpoint; it fails the canary. Every runner probes these before it starts a character.
- Optional but worth it: `STACK_UP_CMD` and the database backup hooks for the watchdog, and `NOTIFY_CMD` so a
  person hears about an outage (`references/hooks.md`). A dry run once sat 90 minutes on a failing canary
  because nobody was told.
- Hooks for what a real user gets from outside the product: `CONNECT_CMD` (connect a fake third-party account),
  `CUSTOMERS_CONFIG` with `scripts/customer.py` (inbound messages from simulated customers), `OUTBOX_CMD`,
  `MAIL_CMD`.
- Browsers on the stack host when this machine is short: `BROWSER_SLOTS="remote:1 ... remote:N"`, one slot per web
  character at once **plus one spare** for second-user browsers (`references/remote-stack.md`).
- Fixtures: seed what a scenario uses up (a ticket to scan, a single-use code, a booking to cancel) per character
  and list it on the card; seed dates in the product's time zone; seed states with side effects through the
  product's own calls; give characters who sign up no account. Write an act-as helper
  (`templates/act-as.example.py`) for the other side of a scenario, so nobody waits on the square, and, with
  remote browsers, a download helper (`templates/pdf.example.py`). Name both in context.md.
- A product map saved from an earlier run on the same commits: `PRODUCT_MAP_FROM=<path>`.

## Step 3: Write the crowd
Every crowd has three core characters, then people from the product's own user base:
- **The designer** checks every screen against the project's design source (tokens, design-system docs,
  reference images), or the platform's guidelines when there is none. Findings name the rule broken.
- **The boundary tester** stays inside the product's own screens, commands and endpoints and checks the
  limits a careless or curious user hits: other users' records through guessed links or ids, role limits,
  empty, oversized and unusual input, stale links. Everything stays on the throwaway stack.
- **The speedy user** never waits: double-submits, goes back mid-load, opens the same thing in two tabs.

The rest come from the mix agreed in Step 1: first-timers and power users of each role, someone on large text
or a screen reader, someone on a slow network (faked in their own browser with `agent-browser offline`), and
whoever depends on someone else's work.

Write `lanes/<id>/card.md` from `templates/character-card.md` (add `- **Engine**: codex` for characters on
Codex, `- **Your own test data**:` for what was seeded for them, optionally `- **Browser timezone**:`) and `$RUN/lanes.json`: `[{"id", "role", "surface", "depends_on": [...]}]`. Show the user all cards (one
table and the stories) and get approval. Plans are written later by `crowd.py`, one per character, from a
product map it writes once.

## Step 4: Preflight
```bash
bash "$RUN/scripts/restart-watchers.sh" "$RUN"    # api-watch, state saver, square page
bash "$RUN/scripts/preflight.sh" "$RUN"
```
Every line must PASS: disk, the stack check, every health check right now, the engines chosen and their CLIs
answering, every Codex account accepting the tester model, remote browsers installable, watchers (and the quota
guard) running, `lanes.json` present. Fix failures before launch; thirty characters failing the
same way costs thirty times as much.

## Step 5: Launch and watch
```bash
nohup python3 "$RUN/scripts/crowd.py" "$RUN" all >> "$RUN/logs/crowd.out" 2>&1 < /dev/null &
python3 "$RUN/scripts/crowd.py" "$RUN" status        # whenever you or the user want to know
```
`all` runs these steps, and each one skips work already on disk:
1. **plan**: the product map (or a copy from `PRODUCT_MAP_FROM`), then each character's plan (`## Day N`, at
   most `SCEN_MAX`, default 12, scenarios a day; a longer day is split into more sessions). A character with no
   usable plan after two attempts is skipped and logged.
2. **run**: every character-day in dependency order. A dependent starts when its provider's run that day has
   finished, not when it has been judged, and days do not wait for each other. Each day is judged as soon as
   it ends: coverage, missing evidence and duplicates are checked in code, then a judge looks at what is left.
3. **dedupe**: merges findings across characters; other reporters become "Also hit by".
4. **file**: a snapshot of every issue in each repo, open and closed, with bodies and comments, and one model
   pass over the whole batch: create, comment "Also hit by" on an open issue, file a regression of a closed fixed
   one, or skip when an issue or comment says the behaviour is intended. That is `file-plan.md`, and nothing is
   sent until it is approved (Step 6). Then one check per bug (evidence and source at the current checkout,
   intended behaviour, stack faults; a bug another character could not reproduce meets them in a bout first),
   and it is filed or commented. A wrong screenshot is replaced, not refused.
5. **fix** (with `FIXING=on`): Step 7.
6. **report**: `LAUNCH_REPORT.md`, rewritten only when its inputs changed.

Steps can also run alone (`crowd.py "$RUN" file`). Model steps default to Opus (`PLANNER_MODEL`,
`JUDGE_MODEL`, `VERIFY_MODEL`, `REPORT_MODEL`); testers use the engines from Step 1.

While `square-view.py` runs, `$RUN/square/index.html` is the run as a 3D town: characters walk between the
product building (testing), the bench, the courthouse (being judged), the plaza (their posts as speech bubbles)
and their houses, and in the fix stage between the workshop, the review booth, the arena (bouts) and the harbour
(one boat per PR). Click anyone for their card; job cards link to their T3 thread. `square.html` is the
Discord-style square of every post and reply, with filters; both read `square/state.json`. `restart-watchers.sh`
also serves them (`square-serve.py`, only the pages and screenshots, never config or state) on this machine's
Tailscale address and prints the URL; `crowd.py status` repeats it. Give the user that URL at launch.

Keep checking from a short session with `status` rather than a long chat that re-reads itself every turn.
Change capacity (`HARD_CAP`, `SWAP_MAX_MB`, the device pool) in `config.env`: runners read it at every start,
so nothing needs a restart.

## Step 6: Approve the file plan
When the file step stops at the plan, show the user `file-plan.md`: how many new issues, comments on open issues,
regressions and skips, with the reason (and the quoted comment) for each match. They may edit
`state/file-plan.json` (action `create|comment|regression|skip`). On their go:
`python3 "$RUN/scripts/crowd.py" "$RUN" approve` files, then `crowd.py "$RUN" all` continues with fixing and the
report.

## Step 7: Fix (FIXING=on)
Read `references/fixing.md` first. With the `cli` runner, crowd.py does everything. With `t3` or `subagent`, you
run the job loop: `crowd.py "$RUN" jobs next`, launch each job as its own T3 thread or subagent (models picked from
the live catalog: Opus fixes, Opus reviews, Sonnet re-checks, spread over the Claude accounts), then
`jobs start` and `jobs done` as each one finishes, until every area is `done` or `needs-human`. Never put two
jobs in one context and never pass one agent's transcript to another; the job prompt is all an agent gets.

## Step 8: Report to the user
Issues filed per tracker (number, severity, title, URL, reporter, confirmers), what was commented on, filed as a
regression, skipped or refused at filing and why, the fix PRs (per area: URL, re-check PASS / PARTIAL / FAIL,
needs-human points, bouts) and the merge order from `merge-check.md`, the launch verdict from `LAUNCH_REPORT.md`, stack faults found, characters or days
nobody could test, and the spend from `crowd.py status`. Quote only URLs the tracker returned.

## When something goes wrong
- **The stack goes down.** `api-watch.sh` writes `STACK_DOWN` after two failed checks; no character starts,
  testers hold and record nothing, and the window is written to `stack-faults.md`. Fix the stack; the crowd
  continues by itself.
- **A usage limit.** With accounts listed, the guard switches first and testers resume their session
  (`quota.log`). With a fallback set, only the engine that ran out hands over. Without one,
  `QUOTA_PAUSE.<engine>` holds that engine's characters until the guard or `RETRY_PRIMARY_MIN` lifts it.
- **The session, the machine or the stack host restarts.** Run `bash "$RUN/scripts/resume.sh" "$RUN"`. It
  restarts the watchers, runs the watchdog and the stack check, and starts `crowd.py` again unless it is still
  running. Live testers are waited for, never started twice. `references/resume.md` lists every state file.
- **Scenarios left blocked or unreported** once the run is over: seed what they lacked, then
  `python3 "$RUN/scripts/crowd.py" "$RUN" makeup "$NEW"` builds a run of only those (blocked by a filed bug are left
  out), with the cards, diaries and filed list carried over. Preflight and launch it like any run.
- **A day must run again** (its data was lost): move that `lanes/<id>/round<N>/` into `archive/<reason>/` and
  resume. Verified findings from archived days can go in `archive/<reason>/prior_findings.json`.

## What the judges hold to
- A finding needs evidence that shows it. A me-too counts only with its own screenshot; a can't-repro is kept,
  since it narrows the cause.
- Intended behaviour (context.md, the project's docs and specs) is never a finding, however cross the
  character is.
- Stack faults are never filed. A finding caused by the test environment goes to `stack-faults.md` with its fix,
  and every later tester prompt carries that file.
- The voice line is the character's, in plain words; titles name the symptom, never the character.
- Day 2 re-checks day 1's bugs instead of reporting them again; a repeat is rejected in code.

## Files
- `scripts/crowd.py`: the run loop (plan, run, judge, dedupe, file plan, file, fix jobs, bouts, PR bodies,
  report, status)
- `scripts/fix-stack.sh` (a dev server per area, the backend queue), `scripts/fix-browser.sh` (a browser per job),
  `scripts/fix-device.sh` (a simulator or emulator per job, and a build of the fix branch for mobile areas),
  `scripts/evidence.sh` (evidence pushes that never overwrite each other)
- `scripts/run-tester.sh`: one character-day on any surface; per-character engine, lean Claude sessions in
  budget chunks, stack probe before start, per-engine quota pauses, browser slots
- `scripts/quota-guard.sh`: account rotation through caam; `scripts/makeup.py`: a run of what was left
- `scripts/browser-pool.sh`, `scripts/remote-browser.sh`, `scripts/remote-chrome.sh` (runs on the stack host),
  `scripts/extra-browser.sh`, `scripts/no-local-browser.sh`
- `scripts/preflight.sh`, `scripts/init-crowd.sh`, `scripts/resume.sh`, `scripts/restart-watchers.sh`
- `scripts/api-watch.sh`, `scripts/watchdog.sh`, `scripts/capacity.sh`, `scripts/device-pool.sh`,
  `scripts/tunnel.sh`, `scripts/net-stall.sh`, `scripts/memlog.sh`, `scripts/state-saver.sh`, `scripts/save-state.py`
- `scripts/square.py`, `scripts/square-view.py` (with `scripts/world/`, the 3D page), `scripts/square-serve.py`, `scripts/world.py`, `scripts/customer.py`, `scripts/act.py`,
  `scripts/judge-pack.py`, `scripts/status.py`
- `templates/`: character card, tester brief, scenario format, issue templates, context, stack-check, act-as and
  download-helper examples, and the fix, review and re-check briefs
- `references/`: `fixing.md` (the fix stage and the job loop), `engines.md` (the engine question), `cost.md`, `lessons.md`, `resume.md`, `hooks.md`,
  `remote-stack.md`, `mirofish-mapping.md`
- `tests/test_crowd.py` in the repo: the scripts end to end with fake engines, offline
