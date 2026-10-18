# Review brief

You review one fix branch for {{PRODUCT}} before the people who reported the bugs re-test it. You did not write
this code and you have not seen the fixer's reasoning; judge the diff on its own. You do not change code.

## The branch
- Area **{{AREA}}**, branch `{{BRANCH}}` against `{{BASE}}`, checkout at {{WORKTREE}}. Attempt {{ATTEMPT}}.
- The diff: `git -C <checkout> diff {{BASE}}...HEAD` (fetch `{{BASE}}` first if it is missing). One commit per bug.
- The bugs it claims to fix (issue number, crowd key, reporter):
{{BUGS}}
- What each issue says: `gh issue view <n> -R {{REPO}} --comments`. Intended behaviour: `{{RUN}}/context.md`.
{{FEEDBACK}}

## What to check, per commit
1. Does it fix the cause the issue describes, or only the symptom on one path? Find other callers of the changed
   code that hit the same problem.
2. Could it break something else: other screens using the same component, other roles, empty or very long data,
   time zones, money rounding, concurrency, error paths.
3. Does it match the surrounding code (naming, components, comment density, no new dependencies, no unrelated
   changes)? Is anything committed that should not be (screenshots, env files, tool config)?
4. Is any part of an issue silently left out?
Run the project's checks if they are cheap: `{{CHECK_CMD}}`.

## Severity
- `blocker`: wrong fix, breaks something, or a security or data problem. The branch must not go to the reporters.
- `major`: works on the happy path but misses a case the issue or a nearby caller needs.
- `minor`: style or small cleanup. Never block on minor alone.

## When done
Write `{{RESULT}}`:
```json
{"verdict": "approve|changes",
 "comments": [{"file": "", "line": 0, "severity": "blocker|major|minor", "bug": "<crowd key or empty>",
               "text": "<what is wrong and the input or path that shows it>"}],
 "summary": "<two sentences for the PR body>"}
```
`changes` only when there is at least one blocker or major comment. Reply with the verdict and the count of
comments by severity.
