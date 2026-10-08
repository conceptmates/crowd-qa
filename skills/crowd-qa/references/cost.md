# Token cost: where it goes and what keeps it down

Measured on a 30-character, 2-day web run (Codex testers, Opus orchestrating), before the changes below.
Codex tester usage is billed to Codex accounts and is not counted here.

| Part | Agents | Avg turns | Share of subagent tokens | Why |
|---|---|---|---|---|
| Planners | 95 for 30 characters | 26 | 52% | each re-explored the source (about 26 shell calls, 77 KB of output); restarts re-ran them, about 3 runs per character, and none skipped an existing plan |
| Judges | 112 | 18 | 40% | about 16 shell calls each to find reports, logs and square posts; 6 full-size screenshots each; Opus at high effort for every judge |
| Day agents | 146 | 4 | 5% | start a runner, watch it, return |
| Merge, filing, report, audits | ~20 | — | 3% | |

The orchestrating session adds its own share: every turn re-reads the whole conversation, so a long chat that
monitors a run for a night costs as much as a quarter of the subagents.

## What the skill does about it

| Change | Where | Expected effect |
|---|---|---|
| One product map, written once; planners read it instead of the source, with at most 3 lookups | `templates/workflow.template.js` (Design phase) | planning about -90% together with the next two |
| Planners skipped in code for characters whose plan exists (`args.planned`, filled by `resume.sh`) | template, `scripts/resume.sh` | no duplicate planners after a restart |
| Planners on Sonnet at medium effort (`planner_model`, `planner_effort`) | template | plans are structured writing from the map |
| A judge pack per character-day: scenarios, findings with 900 px screenshots, related square posts, the log tail | `scripts/judge-pack.py`, template | judging about -55% |
| Judges on Sonnet at medium effort (`judge_model`, `judge_effort`); only cited lines of code | template | every filed bug is still re-checked on the orchestrating model before filing |
| Shell output capped in agent prompts (`| head -c 4000`, narrow `sed -n` ranges) | template | smaller contexts on every later turn |
| Shared prompt parts first (brief, context) in the tester prompt | `scripts/run-tester.sh` | better cache reuse across characters |
| Testers resume the newest session of their engine after a restart or a fallback | `scripts/run-tester.sh` | no fresh session re-reading the whole brief |

Estimate for a run of the same size: about 70% fewer orchestrator tokens, and a larger cut in plan-limit use
because planners and judges moved off the top model.

## Habits that matter as much as the code
- Change capacity in `config.env` (read at every start), not by restarting the workflow mid-day. Each restart
  re-runs whatever agents were in flight.
- Monitor from a fresh, short session: `bash <run>/scripts/resume.sh <run>` and `status.py` give the whole picture.
- Keep plans to 15-20 scenarios per character-day; judges cost grows with every scenario they check.
