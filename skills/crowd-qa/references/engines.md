# Choosing the tester engines

Preflight fails until the user has chosen the engines and `ENGINE_CONFIRMED=yes` is in `config.env`. Ask; do
not assume. The recommendation below comes from a 30-character, 2-day web run and a same-prompt comparison
scored by an independent judge.

## The question
Ask with the interactive question tool, recommended option first:

1. **Main tester** (most characters):
   - **Sonnet, medium effort (Recommended).** Carried 45 of 57 character-days and 289 of 327 verified bugs in
     the measured run, about 6 per day, with no quota windows. `TESTER=claude TESTER_MODEL=sonnet TESTER_EFFORT=medium`.
   - Codex at high or max effort. Every finding held up in the comparison and it found that run's worst bug,
     but one account's 5-hour window went from 0% to 97% in 39 minutes, so it cannot carry a whole crowd.
     `TESTER=codex CODEX_EFFORT=max`.
2. **Characters on Codex** (picked before launch, written as `- **Engine**: codex` on their cards):
   - **3-5 characters (Recommended):** the boundary tester, long command-heavy days, short regression checks.
     Size it to the Codex quota you have.
   - None.
3. **When an engine hits its usage limit:**
   - **Pause that character and retry after RETRY_PRIMARY_MIN (Recommended).** Nothing is paid twice.
   - Hand the day to a fallback engine (`FALLBACK_TESTER`). The day continues from its report, in a new
     session that re-reads the brief.

## What the numbers say

| Engine | Verified bugs per day | Findings that held up | Cost | Weak spot |
|---|---|---|---|---|
| Sonnet, medium | 6.4 | 75-94% | about $3.70 per day (tester only) | none measured |
| Codex, max | 3.8 (5.3 without days the stack blocked) | 100% | subscription quota | quota; repeats yesterday's bugs; gives up on blocks early |

Smaller and cheaper models were tried as testers too. On days with long multi-step flows they skipped half the
scenarios and most of their findings did not hold up, so they are not offered here.

## The model steps (crowd.py)
The product map, the plans, day judging, the merge check, every bug check before filing and the launch report
run on Opus (`PLANNER_MODEL`, `JUDGE_MODEL`, `VERIFY_MODEL`, `REPORT_MODEL`). These are the calls that decide
what gets tested and what gets filed. In a dry run, a Sonnet bug check refused a real seeded bug as "a missing
feature", so the defaults stay on Opus. The waiting, scheduling and bookkeeping between them are code, which is
where most of the earlier spend went.

## Rules that hold whatever the user picks
- Assign an engine per character before launch. Moving a character between engines mid-day costs a fresh
  session that re-reads everything.
- Testers on the day-2 brief re-check their own day-1 bugs instead of reporting them again (the runner puts
  the list in the prompt). Codex repeated 10 of 12 bugs on day 2 before this rule.
- Testers try for about 10 minutes before calling a scenario blocked.

## Several accounts per engine (quota-guard.sh)
With more than one subscription account per CLI, list the caam profiles in `CODEX_ACCOUNTS` and
`CLAUDE_ACCOUNTS`, in order of preference. `restart-watchers.sh` then starts `quota-guard.sh`, which reads
`caam limits` every 2 minutes and switches before a tester hits a limit (`PRIMARY_MAX`, `WEEKLY_MAX`,
`SWITCH_HEADROOM`). A switched tester resumes its own session with a two-line message. With `FALLBACK_TESTER` set,
Codex falls back instead of pausing when no Codex account is under `CODEX_NEAR_MAX`; new days return to Codex as
soon as an account has room. Claude accounts caam cannot read are tried with one small call before a pause.
`quota.log` holds every reading and decision.

Rules:
- **Only the guard switches accounts during a run.** `caam activate` is machine-wide: a Claude switch also moves
  the orchestrating session and crowd.py's Opus steps. A hand switch while planners ran failed 29 calls and forced
  6 replans.
- **Every Codex account must accept the tester model.** A ChatGPT "go" plan refused the default model and 12
  character-days ran with no evidence. `preflight.sh` makes a one-word call with each model on each account: through
  an isolated caam profile (`caam profile add codex <name>`, no machine-wide switch), or, with
  `PREFLIGHT_MODEL_PROBE=switch` and nothing running, by activating each account in turn and switching back.
  Without either it fails accounts on a go or free plan and asks you to pin `TESTER_MODEL` / `CODEX_MODEL`.
- "No reading" for the active account is never a limit (6 false pauses before that rule).
