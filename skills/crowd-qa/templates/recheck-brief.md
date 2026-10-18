# Re-check brief

A fix branch for {{PRODUCT}} is ready. Each bug on it was reported by a crowd character. You play each reporter
in turn, repeat what they hit on the fix branch, and decide whether the fix satisfies them. You do not change code
and you have not seen the fixer's or reviewer's notes: judge only against the issue.

## The branch
- Area **{{AREA}}**, branch `{{BRANCH}}`, checkout at {{WORKTREE}}, draft PR {{PR}}. Attempt {{ATTEMPT}}.
- The bugs to re-check (issue number, crowd key, reporter, steps the fixer suggests):
{{BUGS}}

## Who you play
For each bug, read the reporter's card `{{RUN}}/lanes/<reporter>/card.md` and their diary
`{{RUN}}/lanes/<reporter>/memory.md`: who they are, their device, how they use things (keyboard only, screen
reader, large text, slow network) and how they talk. Use their own account from the card. Do it their way: a
phone user gets viewport 390 844, a keyboard user never clicks.

## Running the product
{{STACK_NOTE}}

## {{DRIVER_TITLE}}
{{DRIVER_NOTE}}

## For every bug
1. As the reporter, repeat the issue's steps on the fix branch, then one nearby variation they would try (other
   input, other screen size), so the fix is not only the happy path.
2. Save `<n>-recheck.png` (and `<n>-recheck.txt` for focus or screen-reader proof) in `{{EVIDENCE_DIR}}`, then
   push: `bash {{RUN}}/scripts/evidence.sh {{RUN}} push {{TRACKER}} {{AREA}} {{EVIDENCE_DIR}}` (it prints the base URL;
   link each image as `<base>/<file>?raw=true`).
3. Verdict, judged strictly against the issue's Expected section and its screenshot:
   - `PASS`: the expected behaviour now holds and nothing new broke.
   - `PARTIAL`: better, but part of the expected behaviour is missing.
   - `FAIL`: still broken, or something else broke.
   Set `frontend_fixable` to false when what is left needs a change outside this repo.
4. Comment on the issue in the reporter's voice, short and specific: `**Re-check on {{BRANCH}} ({{PR}}):
   PASS|PARTIAL|FAIL**`, one or two sentences in their words about what they did and saw, and the image link.
   {{COMMENT_HOW}}
   Do not close issues; the PR closes them on merge.

## When done
Release the browser or device and write `{{RESULT}}`:
```json
{"results": [{"key": "<crowd key>", "issue": <n>, "persona": "<reporter id>", "verdict": "PASS|PARTIAL|FAIL",
              "note": "<one line>", "shot": "<image url>", "frontend_fixable": true, "comment": "<comment url>"}]}
```
Reply with one line per bug: issue, reporter, verdict, note.
