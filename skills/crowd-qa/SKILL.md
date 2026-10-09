---
name: crowd-qa
description: Launch-week crowd QA for a web app, mobile app, CLI or HTTP API. Named characters (a designer, a boundary tester, a speedy user, and people drawn from the product's own users) each use the product over simulated days, post on a shared town square, confirm each other's bugs, use what other characters made, and keep a diary between days. Every day is judged against screenshots and source, stack faults are thrown out, and the bugs that hold up are filed in the reporter's voice with evidence, ending in a launch-readiness report. The run loop is a script (crowd.py), so a model is only called where judgement is needed. Use this whenever the user wants end-to-end testing "as if we released it to 20 people", persona or character testing, simulated users, MiroFish-style QA, a pre-launch crowd test, or bug reports written by personas, even if they never say "crowd".
---

# crowd-qa

A crowd of characters uses the product the way its real users would, over several simulated days, and you
file what breaks. Each character is played by a tester agent (Sonnet or Codex). Everything between the model
calls (scheduling, waiting, retries, coverage, evidence checks, merging, filing) is code in `scripts/crowd.py`,
which is why a 30-character run costs a few hundred dollars instead of about a thousand (`references/cost.md`).

You plan with the user, write the characters, check the stack, launch `crowd.py`, and report. You do not test
the product yourself and you do not fix anything it finds unless the user asks.

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
   which characters run on Codex (3-5 picked up front), and what happens at a usage limit. Preflight fails until
   `ENGINE_CONFIRMED=yes`, so this cannot be skipped by accident.
4. **Access and safety.** Test accounts, how verification codes and emails are read, payment test keys, what
   testers may create or delete, data they must never touch. A missing credential becomes a blocked scenario.
5. **Output.** Trackers per bug kind (`TRACKERS="app=owner/app backend=owner/api"`, or a `FILE_CMD` hook for
   another tracker), the evidence branch, the run label. Whether to file at all (`FILING=off` stops at the
   merged list).
6. **Read-back.** Restate the plan in one short table and ask what is missing. Nothing starts before the user
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
Codex) and `$RUN/lanes.json`: `[{"id", "role", "surface", "depends_on": [...]}]`. Show the user all cards (one
table and the stories) and get approval. Plans are written later by `crowd.py`, one per character, from a
product map it writes once.

## Step 4: Preflight
```bash
bash "$RUN/scripts/restart-watchers.sh" "$RUN"    # api-watch, state saver, square page
bash "$RUN/scripts/preflight.sh" "$RUN"
```
Every line must PASS: disk, the stack check, every health check right now, the engines chosen and their CLIs
answering, watchers running, `lanes.json` present. Fix failures before launch; thirty characters failing the
same way costs thirty times as much.

## Step 5: Launch and watch
```bash
nohup python3 "$RUN/scripts/crowd.py" "$RUN" all >> "$RUN/logs/crowd.out" 2>&1 < /dev/null &
python3 "$RUN/scripts/crowd.py" "$RUN" status        # whenever you or the user want to know
```
`all` runs these steps, and each one skips work already on disk:
1. **plan**: the product map, then each character's plan (`## Day N`, at most `SCEN_MAX` scenarios a day).
   A character with no usable plan after two attempts is skipped and logged.
2. **run**: every character-day in dependency order. A dependent starts when its provider's run that day has
   finished, not when it has been judged, and days do not wait for each other. Each day is judged as soon as
   it ends: coverage, missing evidence and duplicates are checked in code, then a judge looks at what is left.
3. **dedupe**: merges findings across characters; other reporters become "Also hit by".
4. **file**: one check per merged bug (evidence and source at the current checkout, intended behaviour, stack
   faults, existing issues), then the issue is filed. A wrong screenshot is replaced, not refused.
5. **report**: `LAUNCH_REPORT.md`, rewritten only when its inputs changed.

Steps can also run alone (`crowd.py "$RUN" file`). Model steps default to Opus (`PLANNER_MODEL`,
`JUDGE_MODEL`, `VERIFY_MODEL`, `REPORT_MODEL`); testers use the engines from Step 1.

The square, a Discord-style page of every post and reply, is at `$RUN/square/index.html` while
`square-view.py` runs.

Keep checking from a short session with `status` rather than a long chat that re-reads itself every turn.
Change capacity (`HARD_CAP`, `SWAP_MAX_MB`, the device pool) in `config.env`: runners read it at every start,
so nothing needs a restart.

## When something goes wrong
- **The stack goes down.** `api-watch.sh` writes `STACK_DOWN` after two failed checks; no character starts,
  testers hold and record nothing, and the window is written to `stack-faults.md`. Fix the stack; the crowd
  continues by itself.
- **A usage limit.** With a fallback set, only the engine that ran out hands over. Without one,
  `QUOTA_PAUSE.<engine>` holds that engine's characters and `crowd.py` lifts it after `RETRY_PRIMARY_MIN`.
- **The session, the machine or the stack host restarts.** Run `bash "$RUN/scripts/resume.sh" "$RUN"`. It
  restarts the watchers, runs the watchdog and the stack check, and starts `crowd.py` again unless it is still
  running. Live testers are waited for, never started twice. `references/resume.md` lists every state file.
- **A day must run again** (its data was lost): move that `lanes/<id>/round<N>/` into `archive/<reason>/` and
  resume. Verified findings from archived days can go in `archive/<reason>/prior_findings.json`.

## Step 6: Report to the user
Issues filed per tracker (number, severity, title, URL, reporter, confirmers), what was refused at filing and
why, the launch verdict from `LAUNCH_REPORT.md`, stack faults found, characters or days nobody could test, and
the spend from `crowd.py status`. Quote only URLs the tracker returned.

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
- `scripts/crowd.py`: the run loop (plan, run, judge, dedupe, file, report, status)
- `scripts/run-tester.sh`: one character-day on any surface; per-character engine, lean Claude sessions in
  budget chunks, stack probe before start, per-engine quota pauses
- `scripts/preflight.sh`, `scripts/init-crowd.sh`, `scripts/resume.sh`, `scripts/restart-watchers.sh`
- `scripts/api-watch.sh`, `scripts/watchdog.sh`, `scripts/capacity.sh`, `scripts/device-pool.sh`,
  `scripts/tunnel.sh`, `scripts/net-stall.sh`, `scripts/memlog.sh`, `scripts/state-saver.sh`, `scripts/save-state.py`
- `scripts/square.py`, `scripts/square-view.py`, `scripts/world.py`, `scripts/customer.py`, `scripts/act.py`,
  `scripts/judge-pack.py`, `scripts/status.py`
- `templates/`: character card, tester brief, scenario format, issue templates, context and stack-check examples
- `references/`: `engines.md` (the engine question), `cost.md`, `lessons.md`, `resume.md`, `hooks.md`,
  `remote-stack.md`, `mirofish-mapping.md`
- `tests/test_crowd.py` in the repo: the scripts end to end with fake engines, offline
