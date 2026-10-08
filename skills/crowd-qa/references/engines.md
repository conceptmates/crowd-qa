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
Planning, judging and bug checks run on Sonnet. Bugs of high severity or with an unconfirmed cause are
re-checked on Opus before filing (`ESCALATE_MODEL`); the launch report is written by Opus. In the measured run,
Opus re-checking every bug refused only 3.9% of them, so checking the rest on Sonnet costs little in precision.

## Rules that hold whatever the user picks
- Assign an engine per character before launch. Moving a character between engines mid-day costs a fresh
  session that re-reads everything.
- Testers on the day-2 brief re-check their own day-1 bugs instead of reporting them again (the runner puts
  the list in the prompt). Codex repeated 10 of 12 bugs on day 2 before this rule.
- Testers try for about 10 minutes before calling a scenario blocked.
