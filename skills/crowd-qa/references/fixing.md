# Fixing what the crowd filed

`FIXING=on` adds a stage after filing. It turns the 2026-10-13 WhatsAround run, where 136 filed bugs were fixed by
nine agents and re-tested by their reporters, into steps crowd.py tracks on disk.

## The flow
1. **group** (one Opus call): the filed bugs become areas that do not share files (`state/areas-detail.json`).
   Each area has one tracker, one branch `fix/<run-label>-<area>`, one dev-server port, and a kind: `fe` (dev
   servers side by side against the shared backend) or `be` (takes turns on the shared backend through
   `fix-stack.sh be-take` / `be-give`). Set the kind per tracker with `TRACKER_KIND`.
2. **fix**: one fixer per area, in its own worktree. One commit per bug, before/after evidence, a draft PR, a
   result file (`templates/fix-brief.md`).
3. **review**: a fresh agent that sees the diff and the issues, never the fixer's notes
   (`templates/review-brief.md`). `changes` sends the comments back to the fixer, up to `REVIEW_ROUNDS`.
4. **bout**: a fixer sure a blocker or major comment is wrong answers it under `rebuttals`. The comment and the
   answer go to a bout (more rounds between two fresh Sonnet fighters, then an Opus judge on a written rubric).
   If the fixer wins, the comment is dropped and the next review is told not to raise it again. Bouts also settle a
   bug another character could not reproduce, before it is filed. At most `BOUT_MAX` a day; `state/bouts/<id>.json`.
5. **recheck**: a fresh agent plays each reporter from their card and diary, repeats their steps on the fix branch,
   gives PASS / PARTIAL / FAIL with a screenshot, and comments on the issue in their voice
   (`templates/recheck-brief.md`). With `RECHECK_COMMENT_HOST` the comment comes from the account that filed the
   issues.
6. **retry**: bugs that did not pass and can be fixed in this repo go back to the fixer with the re-check notes, up
   to `FIX_RETRIES` times; then review and re-check again, for those bugs only.
7. **pr**: crowd.py writes the PR body from the results (`fix/jobs/pr-<area>.md`): a table of re-check verdicts,
   then per bug the issue, reporter, cause, change, before / after / re-check images and the reporter's verdict,
   then Needs backend, Not changed, open points, and the checks, review and bouts in collapsed blocks. An area with
   anything left over is labelled `needs-human`. The PR stays a draft.

8. **merge check** (`MERGE_CHECK=on`, when every area is finished): per repo, every fix branch is merged into a
   throwaway worktree from the local clone, in the order that conflicts least. The checks run on the merged
   result. `merge-check.md` and each PR body give the merge order, or the files a branch conflicts on. Nothing is
   pushed. Run it again by hand with `crowd.py <run> merge-check`.

No agent sees another agent's transcript. Every prompt is built from files: the brief, the issue and its comments,
the card, the diff, the evidence. The reviewer never sees the fixer's explanation, and the re-checker judges
against the issue, not the PR description.

## Runners
`RUNNER` decides who starts the agents. crowd.py writes the same prompt and expects the same result file either way.

| runner | when | how |
|---|---|---|
| `t3` | the orchestrating session has the T3 tools (`orchestrator_capabilities` answers) and the product repo is a T3 project | one T3 thread per job |
| `subagent` | Claude Code without T3 | one fresh `Agent` call per job |
| `cli` | headless, the remote box, overnight with no session open | crowd.py runs `claude -p` / `codex exec` itself |

With `cli`, `crowd.py <run> all` drives everything. With `t3` or `subagent`, `crowd.py fix` queues the first jobs
and returns; the session runs the loop below. If the session cannot find the T3 tools, or the repo is not a T3
project, say so in one line and use `subagent`, or `cli` for an unattended run.

### The session loop (t3 and subagent)
```bash
python3 "$RUN/scripts/crowd.py" "$RUN" jobs next        # JSON: jobs that may start now (claimed for 10 min)
```
For each job:
- **t3, fix job** (`launch: new-worktree`): `t3_thread_launch` with the product's T3 project
  (`t3_project_list`, match the repo), `workspaceStrategy: {type: "worktree", baseRef: <base>, branch: <branch>,
  startFromOrigin: true}`, title `fix <area>`, message = `prompt_text`. Then
  `crowd.py "$RUN" jobs start <id> --runner t3 --thread <threadId> --model <instance:model>`.
- **t3, review or recheck** (`launch: existing-worktree`): the same, with `workspaceStrategy: {type:
  "existing_worktree", worktreePath: <worktree>, branch: <branch>}`.
- **subagent**: `jobs next` has already made the worktree from `REPOS` (`worktree` in the JSON). One `Agent`
  call per job (never `isolation: "worktree"`, which branches from the session's own folder, not the product
  repo), with the prompt `Work in <worktree>. ` + `prompt_text` + ` When the result file is written, reply with
  one line.`, in the background, then `jobs start <id> --runner subagent --model <model>`. The main context keeps
  only the one-line replies.
- When a job finishes (T3 wakes you, or `t3_thread_wait`; for a subagent, its notification):
  `crowd.py "$RUN" jobs done <id>`. It checks the result file, queues what comes next, and prints how many jobs
  are ready. A missing or broken result is queued once more, then the area is left to a person. Call `jobs next`
  again until every area is `done` or `needs-human` (`crowd.py "$RUN" jobs list`).
- When every area is finished: `crowd.py "$RUN" report`.

### Picking models (t3)
`FIX_MODEL`, `REVIEW_MODEL` and `RECHECK_MODEL` set to `instance:model` are used as given. `auto` means: read
`orchestrator_capabilities` once at the start and pick from the live catalog:
- fixer: the newest Opus (effort high), else the strongest model listed;
- reviewer: a model from a **different provider** than the fixer when one can run child tasks (Codex, effort
  high): a second model family catches what the first one misses. Else Sonnet on another account;
- re-checker: Sonnet (it drives a browser for a long time; Opus is not needed to play a user);
- spread jobs over every Claude instance listed (round-robin), so one account's usage limit does not stop the
  run. A job that dies on a limit: `jobs fail <id> limit`, and launch the retry on the next instance.
Pass the chosen `instance:model` to `jobs start --model` so the PR body and the 3D world show who did what.

### After a restart
`jobs list` shows each job's thread. A t3 job whose thread is still running is left alone (`t3_thread_read`);
one whose thread ended without a result: `jobs fail <id> <why>`. Subagents die with the session:
`crowd.py "$RUN" jobs requeue-running` queues every running job that left no result, and the same branch keeps
the commits already pushed.

## Setting up the product for fixing
- `REPOS`: a local clone per tracker (cli and subagent runners make worktrees from it).
- `FE_PREP_CMD`, `FE_INSTALL_CMD`, `FE_DEV_CMD` (single quotes, `$PORT` is the area's port): how a worktree
  becomes a running frontend against the shared test backend. `APP_URL` is the base branch running, for "before".
- `BE_DEPLOY_CMD` (single quotes, `$BRANCH`) and `BE_RESET_CMD` for backend areas.
- `CHECK_CMD` or `CHECK_CMD_<tracker>`: the checks every fixer must pass.
- Browsers come from the crowd's slot pool (`BROWSER_SLOTS`); keep one slot per parallel job.

### A mobile product
Set `TRACKER_KIND="app=mobile backend=be"` and `FIX_DRIVER=device`. A mobile area has no dev server: the fixer
claims a device with the base build on it for 'before', and `fix-device.sh build` copies the checkout to
`FIX_BUILD_HOST`, runs `FIX_BUILD_CMD_ios` (or `_android`), carries the build to the device's host
(`DEVICE_HOSTS`) and installs it. Release puts `APP_PATH` / `ANDROID_APP_PATH` back, so the next job starts from
the base build. With `FIX_DRIVER=device` backend areas and every re-check drive a device instead of a browser.
Give the fix stage the devices the testers used only after the last character-day (`FIX_DEVICES`, default
`DEVICE_POOL`), and never build a mobile app for web to get a browser. `FIX_STACK_NOTE_<kind>` replaces the
generated "Running the product" text for that kind when a product needs its own (`{RUN}`, `{SCRIPTS}`, `{JOB}`,
`{PORT}` are filled in).
