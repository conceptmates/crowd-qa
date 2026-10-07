# Issue format (crowd)

The rules in `issue-template.md` apply: one issue per verified, deduped finding, evidence on the
orphan branch, read back with `gh issue view`, and only URLs gh returned get reported. The crowd adds a
reporter voice and the people who confirmed it.

**Title:** the specific user-visible symptom (no character name in the title).

**Labels:** `bug|ux|a11y|enhancement`, `severity:<level>`, the run label, `persona:<id>` for the reporter
and every confirmer, `role:<the reporter's role>`. Create missing labels with `gh label create --force`.

**Repo:** the judge's `repo` field: the app repo for client behaviour, the backend repo for API errors,
server-rendered pages and plugins. When unsure, the app repo, with a line saying where the cause probably lives.

**Body:**

```markdown
> "<reporter's voice line, verbatim>"
> **<Name>**, <age>, <business>, <city> · <device>, text size <size>

**Where**

- Screen: `<screen name or web URL path>`
- Device: <iPhone 17e, portrait, large text>
- Day: <n> of the crowd run

**Steps to reproduce**

1. ...

**Expected**

...

**Actual**

<exact on-screen text, failed requests>

**Likely cause**

<file:line the judge opened and why, or "not confirmed">

**Also hit by**

- **<Name>** (<role>): "<their me-too line>" ([screenshot](<evidence url>))

**Evidence**

![](https://github.com/<owner>/<repo>/blob/<evidence-branch>/<run-label>/issue-<n>/<file>.png?raw=true)

---
Found in the <date> crowd run: <N> simulated users over <days> day(s). Tested by <tester>; evidence and source re-checked before filing.
```

Omit "Also hit by" when nobody else confirmed it. A `cant-repro` reply goes in as a sentence under Actual
("Rohit could not reproduce it on an iPhone 17 Pro Max with default text"), since it narrows the cause.

## Comments
A confirmation that arrives after the issue exists becomes a comment instead of an edit:
`gh issue comment <n> --body-file <file>` with the voice line, the device, and the screenshot.
