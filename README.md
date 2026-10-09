# crowd-qa

An agent skill for launch-week crowd QA. Named characters, each played by a tester agent, use a product over
simulated days: a web app, a mobile app, a command-line tool or an HTTP API. They post on a shared town square,
confirm each other's bugs, and keep a diary between days. Every day is judged against the evidence and the
source, and what holds up is filed.

The run loop is code (`scripts/crowd.py`). A model is called only to write the product map and the plans, to
check a day's findings, to check a bug before filing, and to write the launch report.


## Install

```bash
npx skills add conceptmates/crowd-qa            # into ~/.claude/skills (or pick a project during the prompt)
```
or copy `skills/crowd-qa/` into a project's `.claude/skills/`.

Testers run on the CLI you choose during preflight (`references/engines.md`: Sonnet as the main tester and
Codex for characters picked before launch is the recommendation). Web characters need
`agent-browser`; mobile characters need `agent-device` and booted devices; GitHub filing needs `gh`.

## What is in a run

| Piece | File |
|---|---|
| Run dir and config | `scripts/init-crowd.sh` writes `config.env` (surfaces, testers, capacity, stack, hooks, filing) |
| Choosing engines | `references/engines.md`; `scripts/preflight.sh` fails until the user has chosen |
| The run loop | `scripts/crowd.py <run> all`: plan, run every character-day in dependency order, judge, merge, file, report. Each step skips work already on disk |
| One character, one day | `scripts/run-tester.sh` (web, ios, android, cli, api; per-character engine, lean Claude sessions in budget chunks, per-engine quota pauses) |
| Filing | per merged bug, in parallel: Opus re-checks evidence and source, then code files through `gh` or a `FILE_CMD` hook |
| Town square | `scripts/square.py` (posts, replies), `scripts/square-view.py` (a live Discord-style page) |
| Simulated customers | `scripts/customer.py` sends signed inbound events from a channels file |
| Stack health | `scripts/api-watch.sh` (holds the crowd while checks fail), `scripts/watchdog.sh` (brings the stack back, backs up and restores the database) |
| Surviving restarts | `scripts/save-state.py`, `scripts/state-saver.sh`, `scripts/resume.sh` |
| Hooks a project provides | `references/hooks.md` |
| Token cost | `references/cost.md`: where a 30-character run spent about $1,000, what the skill does instead, and an estimate of about $280 for the same run now |
| Tests | `tests/test_crowd.py`: the scripts end to end with fake engines (no model, no network) |

## Worked example

`examples/demo-app` is Tinybox, a small shared inbox (Node and SQLite, web UI, HTTP API and CLI) with four
bugs seeded on purpose (`KNOWN_BUGS.md`). `examples/demo-run` holds the hooks, stack check, channels file,
context and three characters (web, CLI and API) used to dry-run the skill against it.

```bash
cd examples/demo-app && npm install && npm start      # http://localhost:4100
python3 ../demo-run/verify.py                         # the stack check
```

## Tests

```bash
python3 tests/test_crowd.py
```
Fake `claude`, `codex`, `gh`, `git` and `agent-browser` stand in for the real tools, so the suite costs nothing
and runs offline. Each test checks one behaviour that a real run got wrong: the engine question, the shared
prompt prefix, day slicing, day-2 re-checks, lean sessions and budget chunks, per-engine quota pauses and
fallbacks, dependency scheduling without day barriers, the stack gate, dead runners, restarts, code checks
before judging, merging, filing through `gh` and through a hook, evidence repair, and escalation.

## License

MIT
