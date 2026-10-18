# Fix brief

You fix one area of {{PRODUCT}} in your own git worktree: the bugs a crowd of simulated users filed in
{{REPO}}. You prove each fix with before/after evidence on the running product and open one draft pull request
for the area. Another agent reviews your diff after you, and each bug's reporter then re-tests your branch in
character, so a fix that only looks right in the code will come back to you.

## Your job
- Area: **{{AREA}}** ({{KIND}}). Attempt {{ATTEMPT}}.
- Branch: `{{BRANCH}}` from `{{BASE}}`. Work in {{WORKTREE}}.
- Bugs (issue number, crowd key, reporter):
{{BUGS}}
{{FEEDBACK}}

## Inputs
- Each issue: `gh issue view <n> -R {{REPO}} --comments`. The body has the reporter's steps, expected, actual and
  screenshots; comments may hold more reports ("Also hit by") or notes from the team. All bodies are also in
  `{{RUN}}/state/issues.json` (match on the crowd key).
- Intended behaviour, accounts and how to read emails or codes: `{{RUN}}/context.md`. Never "fix" intended
  behaviour. Stack faults already known: `{{RUN}}/stack-faults.md`.
- Some bugs may already be fixed on `{{BASE}}`. Check before coding.

## Code rules
- Match the surrounding code: its components, naming, comment density and idiom. Smallest change that fixes the
  cause. No drive-by refactors, no new dependencies.
- One commit per bug: `fix({{AREA}}): <what changed> (#<n>)`. End every commit message with
  `Co-Authored-By: {{COAUTHOR}}`.
- {{SCOPE_RULE}}
- If a bug needs a change outside your scope, fix only your part and list it under `needs_backend`. If a bug is
  intended behaviour or does not reproduce on `{{BASE}}`, list it under `not_changed` with the reason.
- Never commit screenshots, logs, `.env` files, lockfile churn you did not mean, or tool config the dev server
  helper writes.
- Run the project's checks on what you changed: `{{CHECK_CMD}}`. Fix every error you introduced.

## Running the product
{{STACK_NOTE}}

## Browser
Take your own browser: `eval "$(bash {{RUN}}/scripts/fix-browser.sh {{RUN}} acquire {{JOB}})"`. It sets
`AGENT_BROWSER_SESSION` (and `AGENT_BROWSER_CDP` for a remote slot) in your shell; run that line again in any new
shell. Use agent-browser as usual. Never run `agent-browser close --all` or `connect`, never use another session.
Before you save a screenshot, check the page URL (`agent-browser get url`) is your app on your port: a screenshot
of somebody else's page is worse than none. Release it at the end: `bash {{RUN}}/scripts/fix-browser.sh {{RUN}}
release {{JOB}}`.

## Evidence
For every bug: `<n>-before.png` on `{{BASE}}` showing the bug and `<n>-after.png` on your branch with the same
steps. For focus, keyboard, screen-reader names or other things a picture does not show, add the snapshot or eval
output as `<n>-after.txt`. Use the reporter's own device when the issue names one (phone = viewport 390 844).
Save them in `{{EVIDENCE_DIR}}`, then push: `bash {{RUN}}/scripts/evidence.sh {{RUN}} push {{TRACKER}}
{{AREA}} {{EVIDENCE_DIR}}`. It prints the base URL; link each image as `<base>/<file>?raw=true`. It never force-pushes and
never touches other areas' folders.

## Pull request
Push the branch and open a draft PR once the first bug is fixed, so it can be followed:
`gh pr create -R {{REPO}} --draft --base {{BASE}} --head {{BRANCH}} --title "fix({{AREA}}): <short summary>"`
with a short body listing `Fixes #<n>` for each bug. crowd.py rewrites the body with the full evidence later.
On later attempts, push to the same branch; do not open a second PR. Do not merge. Do not comment on the issues:
the reporters do that after their re-test.

## When done
Stop your dev server, release your browser, and write `{{RESULT}}`:
```json
{"branch": "{{BRANCH}}", "worktree": "<absolute path of your checkout>", "pr": "<PR URL>",
 "fixed": ["<crowd key>"], "needs_backend": [{"key": "", "why": ""}], "not_changed": [{"key": "", "why": ""}],
 "changes": {"<crowd key>": "<one or two sentences: what changed, for the PR body>"},
 "cause": {"<crowd key>": "<file:line and why>"},
 "evidence": {"<crowd key>": {"before": "<url>", "after": "<url>"}},
 "retest": {"<crowd key>": "<the steps the reporter should follow to re-check it>"},
 "checks": {"cmd": "<what you ran>", "ok": true, "tail": "<last 20 lines of output>"},
 "rebuttals": [{"comment": <review comment index>, "text": "<why the review comment is wrong, with evidence>"}]}
```
`rebuttals` is only for review comments you are sure are wrong; a judge reads both sides, so give evidence
(a file:line, a test, a screenshot), not opinion. Reply with the PR URL and one line per bug.
