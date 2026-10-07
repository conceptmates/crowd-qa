# crowd-qa

An agent skill for launch-week crowd QA. Named characters, each played by a tester agent, use a product over
simulated days: a web app, a mobile app, a command-line tool or an HTTP API. They post on a shared town square,
confirm each other's bugs, and keep a diary between days. The orchestrating model judges every day against the
evidence and the source, and files what holds up.

> `skills/crowd-qa/SKILL.md` is not in the repo yet. Until it is, `npx skills add` finds nothing to install;
> the scripts, templates, references and the worked example below are complete.

## Install

```bash
npx skills add conceptmates/crowd-qa            # into ~/.claude/skills (or pick a project during the prompt)
```
or copy `skills/crowd-qa/` into a project's `.claude/skills/`.

Testers run on the CLI you choose during preflight: `codex`, `claude` or `opencode`. Web characters need
`agent-browser`; mobile characters need `agent-device` and booted devices; GitHub filing needs `gh`.

## What is in a run

| Piece | File |
|---|---|
| Run dir and config | `scripts/init-crowd.sh` writes `config.env` (surfaces, testers, capacity, stack, hooks, filing) |
| One character, one day | `scripts/run-tester.sh` (web, ios, android, cli, api; switches to a fallback engine at a usage limit) |
| Orchestration | `templates/workflow.template.js` (plans, days, judging, merging, filing, the launch report) |
| Filing | one verifier per merged bug, in parallel: re-checks evidence and source, then files through `gh` or a `FILE_CMD` hook |
| Town square | `scripts/square.py` (posts, replies), `scripts/square-view.py` (a live Discord-style page) |
| Simulated customers | `scripts/customer.py` sends signed inbound events from a channels file |
| Stack health | `scripts/api-watch.sh` (holds the crowd while checks fail), `scripts/watchdog.sh` (brings the stack back, backs up and restores the database) |
| Surviving restarts | `scripts/save-state.py`, `scripts/state-saver.sh`, `scripts/resume.sh` |
| Hooks a project provides | `references/hooks.md` |

## Worked example

`examples/demo-app` is Tinybox, a small shared inbox (Node and SQLite, web UI, HTTP API and CLI) with four
bugs seeded on purpose (`KNOWN_BUGS.md`). `examples/demo-run` holds the hooks, stack check, channels file,
context and three characters (web, CLI and API) used to dry-run the skill against it.

```bash
cd examples/demo-app && npm install && npm start      # http://localhost:4100
python3 ../demo-run/verify.py                         # the stack check
```

## License

MIT
