# Issue format

One issue per verified finding (after dedupe). Title and body exactly in this shape.

**Title:** one specific, user-visible symptom. Name the screen and what goes wrong.
Good: `'Save Draft Changes' on a saved draft opens a 'Push to live' dialog, and confirming it makes the draft live`
Weak: `Save button bug`

**Labels:** one of `bug` / `ux` / `a11y` / `enhancement`; `severity:critical|high|medium|low`;
`area:<lane>`; the run label (e.g. `e2e-2026-09-23`). Create missing labels with `gh label create --force`.

**Body:**

```markdown
**Where**

- Route: `<route or screen, with context such as which builder or block>`
- Viewport: <1280x800 | iPhone 16, portrait>
- Lanes: <lane ids>

**Steps to reproduce**

1. <step>
2. <step>

**Expected**

<what a user expects to happen>

**Actual**

<what happens, including exact on-screen text and failed requests>

**Likely cause**

<file:line and why, as confirmed by the judge — or "not confirmed">

**Console errors**

```
<only if there are any; otherwise omit this section>
```

**Evidence**

![](https://github.com/<owner>/<repo>/blob/<evidence-branch>/<run-label>/issue-<n>/<file>.png?raw=true)

---
Found by the <date> crowd run (tested by <tester>; evidence and source re-checked before filing).
```

## Evidence hosting
GitHub has no API for attaching images, so screenshots go on an orphan branch in the same repo
(default `qa-evidence`), one folder per issue, pushed before the issues are created. The `blob/...?raw=true`
form renders inline for anyone who can see a private repo.

## Checks before reporting
- Every issue has at least one image the judge opened.
- No issue duplicates an already-filed one (compare titles and root cause, not just wording).
- After `gh issue create`, run `gh issue view <n>` and report only URLs gh returned.
