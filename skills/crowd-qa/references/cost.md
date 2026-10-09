# Token cost: where it goes and what keeps it down

Measured on a 30-character, 2-day web run with an earlier version of this skill (agents for every step, Codex
and Sonnet testers, Opus orchestrating): about $1,000 of Claude spend for 199 filed issues, plus most of a
week of Codex quota.

| Part | Spend | Why |
|---|---|---|
| Planners (Opus) | $194 | each explored the source; restarts re-ran them |
| Day judges (Opus, day 1) | $186 | 1,897 tool calls, 84% of them digging through raw logs |
| Testers (Sonnet) | $166 | 23% Claude Code's own fixed context re-read every turn; contexts grew to 390K tokens |
| Orchestrating session | $162 | 619 turns at about 490K tokens of context each |
| Bug checks before filing (Opus) | $161 | every bug re-checked on Opus; 3.9% refused |
| Agents that only waited on testers | $106 | an agent per character-day, polling |
| Bought nothing | $156 | redone days, agents re-started on resume, agents killed before they returned |

## What the skill does about it

| Change | Where | Effect (est.) |
|---|---|---|
| Waiting, scheduling, quota pauses and resuming are code, not agents | `scripts/crowd.py` | the $106 of waiting agents and the $77 of re-started agents go away |
| Coverage, missing evidence, duplicates of filed issues and of a character's earlier days are checked in code before any judge runs; a day with nothing left to check calls no model | `crowd.py` (`precheck`, `coverage`) | judges see fewer findings; quiet days are free |
| One judge pack per day, read by an Opus judge that opens only cited lines and only the findings that passed the code checks | `judge-pack.py`, `crowd.py` | judge turns about -75% (the pack took day-2 judges from 38 turns to 10) |
| Bug checks on Opus, one per merged bug; a wrong screenshot is repaired, not refused | `crowd.py` (`verify`) | real bugs no longer lost to a mislabelled screenshot |
| Merging, labels, issue bodies, evidence push and filing are code; a model is asked only about look-alike pairs on the same screen | `crowd.py` (`dedupe`, `file_issues`) | one small call instead of a merge agent and a push agent per repo |
| One product map; Opus planners read it, with at most 3 source lookups, and an existing plan is never rewritten | `crowd.py` (`plan`) | planning about -80% |
| Claude testers start lean: no user settings, hooks, plugins, MCP servers or skills | `run-tester.sh` (`CLAUDE_LEAN`) | first-turn context 37K to 19K tokens, measured |
| Claude testers run in chunks of `CHUNK_USD`; a spent chunk continues in a fresh session from report.json | `run-tester.sh` | late turns stop re-reading 300K+ tokens |
| The tester prompt holds only today's scenarios, 20 square posts, and starts with the parts every character shares, so it caches across the crowd | `run-tester.sh` | prompt about -24%; a real shared prefix |
| Day 2 re-checks day 1's bugs instead of reporting them again | `run-tester.sh`, tester brief | fewer repeats to judge and merge |
| Testers wait for elements, not fixed sleeps; check screens as text; open screenshots only to check evidence | tester brief | a third of tester time was fixed sleeps; carried images were 10% of tester tokens |
| A runner probes the stack itself before starting; per-engine quota pauses | `run-tester.sh`, `api-watch.sh once` | no character-days against a dead server; one engine's quota never stops the others |

Judgement steps (map, plans, judging, merge check, bug checks, report) stay on Opus by choice: a Sonnet bug check
refused a real seeded bug in a dry run. Set `PLANNER_MODEL`, `JUDGE_MODEL` or `VERIFY_MODEL` to `sonnet` to halve
those steps at that risk.

Every model step's cost lands in `state/costs.jsonl`; `crowd.py <run> status` adds it up.

## Habits that matter as much as the code
- Run `crowd.py` detached and check on it with `crowd.py <run> status` from a short session, not a long chat
  that re-reads its whole history on every turn.
- Change capacity in `config.env`; runners read it at every start. Nothing needs a restart.
- Keep plans to `SCEN_MAX` (default 20) scenarios per character per day.
