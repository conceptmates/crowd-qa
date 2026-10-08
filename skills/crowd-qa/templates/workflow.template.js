export const meta = {
  name: 'crowd-qa',
  description: 'Crowd QA: named characters use the product over simulated days, post on a shared town square, confirm each other, and file persona-voiced issues with evidence',
  phases: [
    { title: 'Design', detail: 'The orchestrating model writes each character\'s day-by-day scenarios from the card (skipped if present)' },
    { title: 'Day', detail: 'waiter starts run-tester.sh for one character-day and watches it' },
    { title: 'Wait', detail: 'waits out a tester quota pause' },
    { title: 'Judge', detail: 'The orchestrating model verifies evidence, voice, square confirmations and repo routing' },
    { title: 'Dedupe', detail: 'merge findings across characters; confirmers become "Also hit by"' },
    { title: 'File', detail: 'one verifier per bug, in parallel: re-check evidence and source, then file' },
    { title: 'Report', detail: 'launch-readiness report per user group' },
  ],
}

// args (a JSON object, not a string):
// {
//   run_dir: '/abs/run',
//   characters: [{ id: 'maya', role: 'owner', surface: 'web' }, { id: 'raj', role: 'customer', surface: 'api', depends_on: ['maya'] }],
//   days: 2, pool: 4, pool_ios: 1, pool_web: 1,   // characters carry surface: 'ios' | 'web'
//                   // pool = HARD_CAP; capacity.sh + device-pool.sh throttle the real testers
//   start_day: 1, done: { ayesha: 1 },  // optional resume: last finished day per character
//   max_quota_waits: 8,
//   source_paths: ['/abs/app', '/abs/backend'],
//   research_notes: '...',
//   filed: ['#12 ...'],
//   trackers: { app: 'owner/app', backend: 'owner/backend' },
//   evidence_branch: 'qa-evidence', run_label: 'e2e-crowd-2026-10-06', date: '2026-10-06',
//   tester_label: 'codex (default model)',   // shown in every issue footer
//   file_cmd: '',        // FILE_CMD: file through another tracker; empty = GitHub via gh
//   file_parallel: 6,    // verifiers at once during filing
//   pool_device: 1,      // ios/android characters at once (pool_ios is the old name)
//   redo: { maya: 1 },   // re-run these days even if a resumed workflow has them cached
//   prior_findings_file: '/abs/run/state/prior_findings.json',  // findings verified before a fresh relaunch
//   planned: ['maya'],   // characters whose scenarios.md already has every day: no planner starts for them
//   product_map_ready: false,   // true when <run>/product-map.md exists and covers every surface
//   planner_model: 'sonnet', judge_model: 'sonnet', planner_effort: 'medium', judge_effort: 'medium'   // '' = inherit the session model
// }
const A = args
const RUN = A.run_dir
const DAYS = A.days || 2
const POOL = A.pool || 4
const MAX_WAITS = A.max_quota_waits || 8
const HOOK = A.file_cmd || ''
// Model tiers. Planners and judges default to Sonnet; the filing verifiers inherit the orchestrating model and
// re-check every bug's evidence and source before it is filed, so each filed bug is checked twice.
const PLANNER_MODEL = A.planner_model === undefined ? 'sonnet' : A.planner_model   // '' = inherit
const JUDGE_MODEL = A.judge_model === undefined ? 'sonnet' : A.judge_model
const PLANNED = new Set(A.planned || [])   // FILE_CMD from config.env: file through another tracker instead of gh
const FILED = (A.filed || []).join('\n')
const DONE = A.done || {}
const SRC = (A.source_paths || []).join(', ')
const T = A.trackers || {}
const CH = A.characters
const byId = new Map(CH.map(c => [c.id, c]))

const DESIGN_SCHEMA = { type: 'object', properties: { scenario_count: { type: 'number' }, failure_share: { type: 'number' } }, required: ['scenario_count'] }
const ROUND_SCHEMA = { type: 'object', properties: { done_line: { type: 'string' } }, required: ['done_line'] }
const WAIT_SCHEMA = { type: 'object', properties: { resumed: { type: 'boolean' }, reason: { type: 'string' } }, required: ['resumed', 'reason'] }
const CONFIRMER = { type: 'object', properties: { id: { type: 'string' }, line: { type: 'string' }, screenshot: { type: 'string' } }, required: ['id', 'line'] }
const FINDING = {
  type: 'object',
  properties: {
    title: { type: 'string' }, type: { type: 'string' }, severity: { type: 'string' }, route: { type: 'string' },
    viewport: { type: 'string' }, steps: { type: 'array', items: { type: 'string' } }, expected: { type: 'string' },
    actual: { type: 'string' }, mechanism: { type: 'string' }, console_errors: { type: 'array', items: { type: 'string' } },
    screenshots: { type: 'array', items: { type: 'string' }, description: 'absolute PNG paths the judge opened' },
    repo: { type: 'string', enum: ['app', 'backend'] },
    reporter: { type: 'string' }, voice: { type: 'string' }, day: { type: 'number' },
    confirmers: { type: 'array', items: CONFIRMER }, cant_repro: { type: 'array', items: { type: 'string' } },
  },
  required: ['title', 'type', 'severity', 'route', 'steps', 'expected', 'actual', 'screenshots', 'repo', 'reporter', 'voice'],
}
const VERDICT_SCHEMA = {
  type: 'object',
  properties: { satisfied: { type: 'boolean' }, coverage_score: { type: 'number' }, in_character: { type: 'number' }, reason: { type: 'string' }, verified_findings: { type: 'array', items: FINDING } },
  required: ['satisfied', 'coverage_score', 'reason', 'verified_findings'],
}

const designPrompt = c => `You are the scenario designer for one character in a crowd QA run of ${DAYS} simulated days. Run dir: ${RUN} (config.env, context.md). Source: ${SRC}.
If ${RUN}/lanes/${c.id}/scenarios.md exists and is non-empty, do nothing else: return its scenario count.
Otherwise:
1. Read ${RUN}/lanes/${c.id}/card.md: who they are, device, text size, workspace, role, relationships, wants per day.
2. Read ${RUN}/product-map.md (screens, labels, routes, commands, endpoints, roles and server rules, written once for every planner) and ${RUN}/context.md. Use their labels and routes in steps. Do not explore the source yourself: at most 3 targeted lookups (rg -n -m5, or sed -n on a range of 40 lines or less) for a label the map lacks, and keep every command's output short (append | head -c 4000). Intended behaviour is not a bug: never design a scenario that "expects" it to be different.
3. ${A.research_notes ? 'Research notes: ' + A.research_notes : 'Research on the web how this kind of person uses this kind of app and what they get wrong.'}
4. Write ${RUN}/lanes/${c.id}/scenarios.md following ${RUN}/templates/scenario-format.md, with one "## Day N" section per day (1..${DAYS}). Day 1 = their first contact with the app and what they want that day. Later days = coming back: what they made yesterday, what other characters did to them (messages, invites, shared records), and checking the square. At least 60% failure and edge paths, at least two failures per happy path, all in this person's habits (a slow typist mistypes, an impatient one double-submits, someone types in their own language). For the boundary tester, stay inside the product's own screens, commands and endpoints: other users' records through guessed links or ids, role limits, odd and oversized input, stale links; describe each check plainly, with no catalogue of attack strings. End with the exhaustive sweep for their part of the product.\nThe file must exist with every day's section before you return; if you cannot finish, write what you have and return the count you wrote.
Do not open the product. Return the scenario count and failure share (0-1).`

// args.redo = { <id>: <day> }: that day was lost to a stack outage and archived; the tag changes the prompt so a
// resumed workflow re-runs it instead of returning the cached result.
const REDO = A.redo || {}
const redoTag = (c, d) => REDO[c.id] === d ? ` (attempt ${A.redo_attempt || 2}: the earlier attempt was lost to a test-stack outage and archived)` : ''
const roundPrompt = (c, d) => `You orchestrate one simulated day for character "${c.id}", day ${d}${redoTag(c, d)}. You never use the app yourself; the tester does.
Run dir: ${RUN}. Day dir: ${RUN}/lanes/${c.id}/round${d}.
1. If round${d}/DONE exists and contains "quota", it is stale from an earlier pause: rename it to DONE.stale-quota.
2. If round${d}/DONE exists with exit=0, skip to step 4.
3. If no process matches pgrep -f "run-tester.sh ${RUN} ${c.id} ${d}$", start it detached: nohup bash ${RUN}/scripts/run-tester.sh ${RUN} ${c.id} ${d} >/dev/null 2>&1 &
   Never start a second copy.
4. Wait: bash ${RUN}/scripts/watch-round.sh ${RUN} ${c.id} ${d} (Bash tool timeout 600000). Exit 2 = still running or waiting for capacity or a device; run it again as often as needed (a day can take hours). Exit 0 or 3 = finished.
Return the DONE line verbatim.`

const waitPrompt = (c, d) => `Every tester engine hit its usage limit, so character "${c.id}" (day ${d}) stopped with its progress saved. Wait, then resume it. Run dir: ${RUN}.
1. If ${RUN}/QUOTA_PAUSE does not exist: return resumed=true, reason="no pause file".
2. Read RETRY_PRIMARY_MIN from ${RUN}/config.env (default 60). Wait until QUOTA_PAUSE is that many minutes old, in steps (Bash tool timeout 600000): timeout 580 bash -c 'sleep 570'. Over 8 hours total: return resumed=false, reason="waited >8h".
3. Move ${RUN}/QUOTA_PAUSE to QUOTA_PAUSE.lifted-<HHMM> and remove ${RUN}/TESTER_FALLBACK if it exists, so the primary engine is tried first.
4. Rename ${RUN}/lanes/${c.id}/round${d}/DONE to DONE.stale-quota if it contains "quota". Return resumed=true.`

const judgePrompt = (c, d, doneLine) => `You are the judge for character "${c.id}", day ${d} of ${DAYS}${redoTag(c, d)}. Runner finished with: ${doneLine}.
If ${RUN}/lanes/${c.id}/round${d}/verdict.json exists, return its contents unchanged and stop.
Inputs: first run python3 ${RUN}/scripts/judge-pack.py ${RUN} ${c.id} ${d} and read the file it prints: the scenarios with status, every finding with its evidence (screenshots downscaled to 900 px), the related square posts and the end of the tester log. Read card.md and the day's section of scenarios.md. Open only evidence the pack lists. For a cause, read only the cited file:line (sed -n on a range of 40 lines or less); if nothing is cited, at most 3 targeted lookups (rg -n -m5). Keep every command's output short (append | head -c 4000). Source: ${SRC}.
Be demanding:
A. Coverage: this day's scenarios have a real status and a final-state screenshot, the failure paths were really tried in character, the sweep happened, and "blocked" is justified.
B. Each finding: open its screenshots and confirm they show the claim; open the cited file and confirm the cause, else mechanism = "not confirmed". Reject findings without a screenshot, speculation, intended behaviour (anything Run context, CLAUDE.md or the specs document as intended), and duplicates of:
${FILED || '(none filed yet)'}
Reject STACK FAULTS: a finding caused by the test environment rather than the product (a missing or mistyped env setting, a service that is down, a full disk, the tunnel, the simulator). Check ${RUN}/stack-faults.md and the stack check output in ${RUN}/logs/stack-verify.log; when you find a new one, append it to stack-faults.md (one line: symptom, cause, fix) and do not verify it.\nRewrite titles as specific user-visible symptoms (no character names in titles).
C. For each verified finding set: repo = "app" (client behaviour) or "backend" (API errors, server-rendered pages, plugins); reporter = "${c.id}"; voice = the tester's in-character line, trimmed to one or two plain sentences (fix it if it reads like a tester rather than this person); day = ${d}; confirmers = other characters' me-too replies on the square post tied to this finding that carry a screenshot you opened (id, their line, absolute screenshot path); cant_repro = their cant-repro lines.
D. Screenshots are ABSOLUTE paths you opened. in_character = 0-100, how believably the tester played this person.
E. satisfied = true only if coverage is essentially complete and every finding is evidence-backed. On the last day, satisfied reflects coverage alone.
Write ${RUN}/lanes/${c.id}/feedback-r${d}.md (numbered list for the next day, or "satisfied"), then the verdict JSON to ${RUN}/lanes/${c.id}/round${d}/verdict.json, then return it.`

// one character-day: run with quota waits, then judge
async function runDay(c, d) {
  let waits = 0, run = null
  while (true) {
    run = await agent(roundPrompt(c, d), { label: `day${d}:${c.id}${waits ? '#' + (waits + 1) : ''}`, phase: 'Day', schema: ROUND_SCHEMA, effort: 'low' })
    if (!run) { log(`${c.id} d${d}: orchestrator failed`); return null }
    if (!/quota/.test(run.done_line)) break
    if (waits >= MAX_WAITS) { log(`${c.id} d${d}: quota waits exhausted`); return null }
    const w = await agent(waitPrompt(c, d), { label: `wait-quota:${c.id}:d${d}#${waits + 1}`, phase: 'Wait', schema: WAIT_SCHEMA, effort: 'low' })
    if (!w || !w.resumed) { log(`${c.id} d${d}: not resuming (${w ? w.reason : 'waiter failed'})`); return null }
    waits++
  }
  const v = await agent(judgePrompt(c, d, run.done_line), { label: `judge:${c.id}:d${d}`, phase: 'Judge', schema: VERDICT_SCHEMA, ...(JUDGE_MODEL ? { model: JUDGE_MODEL } : {}), effort: A.judge_effort || 'medium' })
  if (v) log(`${c.id} d${d}: coverage ${v.coverage_score}, in-character ${v.in_character ?? '-'}, ${v.verified_findings.length} verified, satisfied=${v.satisfied}`)
  return v
}

// Design all characters first (cheap, parallel in small batches)
phase('Design')
// One agent maps the product once (screens, labels, routes, commands, endpoints, roles, server rules), so
// planners do not each explore the source: that exploration was most of a run's planning tokens.
if (!A.product_map_ready) {
  await agent(`Write ${RUN}/product-map.md, the shared map every scenario planner of this crowd QA run reads instead of the source. Source: ${SRC}. Run dir: ${RUN} (context.md says what the product is and which surfaces the crowd uses).
For each surface in use (web screens, mobile screens, CLI commands, API endpoints): the routes or commands, the visible labels of buttons, fields and tabs, what each role may do, plan or feature gates, limits and validation rules, the server-side refusals a user can hit and their wording, and anything scheduled or asynchronous. Cite file:line for rules. Group by area. Aim for completeness over prose: tables and lists. Keep every command's output short (append | head -c 4000).
If the file already exists and covers every surface, return without changing it.`, { label: 'product-map', phase: 'Design', effort: 'high' })
}
// Plans are written 5 at a time, and each character's day 1 starts as soon as its own plan exists,
// so testing does not wait for the slowest designer.
let slots = 5; const slotq = []
const acquire = () => slots > 0 ? (slots--, Promise.resolve()) : new Promise(r => slotq.push(r))
const release = () => { const n = slotq.shift(); if (n) n(); else slots++ }
// characters whose plan already exists (args.planned, filled by the launcher or resume.sh) start no planner at all
const designP = new Map(CH.map(c => [c.id, PLANNED.has(c.id) ? Promise.resolve({ scenario_count: 1, skipped: true }) : acquire()
  .then(() => agent(designPrompt(c), { label: `design:${c.id}`, phase: 'Design', schema: DESIGN_SCHEMA, ...(PLANNER_MODEL ? { model: PLANNER_MODEL } : {}), effort: A.planner_effort || 'medium' }))
  .then(v => { release(); if (!v || !v.scenario_count) { log(`${c.id}: no plan written, character skipped`); return null } return v }, e => { release(); return null })]))
const ready = CH
// characters someone else depends_on (a shop owner, a host) always get their next day, so the people
// who depend on them have something to come back to
const isProvider = c => CH.some(x => (x.depends_on || []).includes(c.id))

const found = new Map()  // title -> finding
const verdicts = {}      // id -> last verdict
// a fresh launch from resume.sh (another Claude session) carries the days already judged, so their findings still get filed
for (const f of (A.prior_findings || [])) found.set(f.title, f)
if (A.prior_findings_file) {
  const r = await agent(`Read ${A.prior_findings_file} (a JSON array of findings a judge already verified on an earlier attempt) and return it unchanged as {"findings": [...]}. Do not edit, drop, merge or add anything.`,
    { label: 'load-prior-findings', phase: 'Design', schema: { type: 'object', properties: { findings: { type: 'array', items: FINDING } }, required: ['findings'] }, effort: 'low' })
  for (const f of (r && r.findings) || []) found.set(f.title, f)
  log(`loaded ${((r && r.findings) || []).length} prior findings`)
}
Object.assign(verdicts, A.prior_verdicts || {})
const paused = new Set()
for (let d = A.start_day || 1; d <= DAYS; d++) {
  // independent characters first, then those who depend on someone: a dependent waits for its provider's
  // same-day run, and providers never wait, so nobody can wait on a queued character.
  const order = ready.filter(c => !paused.has(c.id) && (DONE[c.id] || 0) < d && !(verdicts[c.id] && verdicts[c.id].satisfied && !isProvider(c)))
    .sort((a, b) => (a.depends_on ? 1 : 0) - (b.depends_on ? 1 : 0))
  const finished = new Map(), waiters = new Map()
  const whenDone = id => finished.has(id) || !order.some(c => c.id === id) ? Promise.resolve() : new Promise(r => (waiters.get(id) || waiters.set(id, []).get(id)).push(r))
  const markDone = id => { finished.set(id, true); (waiters.get(id) || []).forEach(r => r()) }
  // Two worker groups: device characters (ios, android) share the device pool; web, cli and api characters run beside them.
  const isDevice = c => c.surface === 'ios' || c.surface === 'android'
  const iosQ = order.filter(isDevice), webQ = order.filter(c => !isDevice(c))
  log(`day ${d}: ${order.length} characters (${iosQ.length} on devices, ${webQ.length} in browsers, CLIs and APIs)`)
  const worker = q => async () => {
    while (q.length) {
      const c = q.shift()
      if (!(await designP.get(c.id))) { paused.add(c.id); markDone(c.id); continue }
      for (const dep of c.depends_on || []) await whenDone(dep)
      const v = await runDay(c, d)
      if (!v) paused.add(c.id)
      else { verdicts[c.id] = v; for (const f of v.verified_findings) found.set(f.title, { ...f, day: d }) }
      markDone(c.id)
    }
  }
  await parallel([
    ...Array.from({ length: Math.min(A.pool_device || A.pool_ios || POOL, iosQ.length) }, () => worker(iosQ)),
    ...Array.from({ length: Math.min(A.pool_web || POOL, webQ.length) }, () => worker(webQ)),
  ])
}
const all = [...found.values()]
log(`${all.length} verified findings from ${Object.keys(verdicts).length} characters`)

phase('Dedupe')
const ISSUES_SCHEMA = { type: 'object', properties: { issues: { type: 'array', items: { ...FINDING, properties: { ...FINDING.properties, labels: { type: 'array', items: { type: 'string' } } } } } }, required: ['issues'] }
const merged = all.length ? await agent(`Merge these verified crowd-QA findings into a final issue list. Merge true duplicates (same root cause, or same symptom on the same screen). The earliest reporter stays reporter with their voice; every other character who hit it joins confirmers (keep their line and screenshot); union the screenshots (absolute paths) and cant_repro lines. Keep repo; if merged findings disagree on repo, pick where the cause lives.
Drop anything that duplicates an already-filed issue:
${FILED || '(none)'}
Labels per ${RUN}/templates/issue-template-character.md: bug|ux|a11y|enhancement, severity:<level>, ${A.run_label}, persona:<reporter>, persona:<each confirmer>, role:<reporter role from ${RUN}/lanes/<id>/card.md>, and one area label only if the repo already has a fitting one (gh label list -R <repo>). Sort by severity, then by number of confirmers.
Findings: ${JSON.stringify(all)}`, { label: 'dedupe', phase: 'Dedupe', schema: ISSUES_SCHEMA, effort: 'high' }) : { issues: [] }
const issues = (merged && merged.issues) || []

phase('File')
// Filing, in parallel: one verifier per merged bug. The verifier runs on the orchestrating session's own model
// (no model override), re-checks the evidence and the source, and only then creates the issue.
const VERIFY_FILE_SCHEMA = { type: 'object', properties: { i: { type: 'number' }, filed: { type: 'boolean' }, url: { type: 'string' }, title: { type: 'string' }, repo: { type: 'string' }, reason: { type: 'string' } }, required: ['i', 'filed', 'reason'] }
const PUSH_SCHEMA = { type: 'object', properties: { pushed: { type: 'boolean' }, map: { type: 'array', items: { type: 'object', properties: { i: { type: 'number' }, paths: { type: 'array', items: { type: 'string' } } }, required: ['i', 'paths'] } } }, required: ['pushed', 'map'] }
const FILE_PARALLEL = A.file_parallel || 6
const filed = [], notFiled = []
// a finding whose repo has no tracker goes to the first tracker instead of being dropped
const trackerKeys = Object.keys(T).filter(k => T[k])
const numbered = issues.map((x, k) => ({ ...x, i: k + 1, repo: T[x.repo || 'app'] ? (x.repo || 'app') : trackerKeys[0] }))
if (!trackerKeys.length) for (const x of numbered) notFiled.push({ i: x.i, reason: 'no tracker configured' })
const footer = `Found in the ${A.date} crowd run: ${CH.length} simulated users over ${DAYS} day(s). Tested by ${A.tester_label || 'the configured tester'}; evidence and source re-checked before filing.`
const verifyFilePrompt = (x, repo, hook) => `You verify one crowd-QA bug and file it only if it holds up. Run dir: ${RUN}. Source: ${SRC}.
Bug ${x.i}: ${JSON.stringify(x)}
1. Evidence: open every screenshot or evidence file listed (reporter's and confirmers') and confirm it shows what the bug claims. A me-too counts only with its own evidence.
2. Source: open the cited files at the current checkout and confirm the cause is still there (not already fixed, not a different code path). If no file was cited, find the code that renders or handles this behaviour and name it; if you cannot, say "cause not confirmed" in Likely cause.
3. Rule out: intended behaviour (${RUN}/context.md, the project's CLAUDE.md/AGENTS.md and specs), a stack fault (${RUN}/stack-faults.md: outages, missing settings, the test environment), and an issue that already exists (${hook ? 'ask the hook-owner file list in ' + RUN + '/filed.txt' : 'gh issue list -R ' + repo + ' --state all --search "<key words>" --limit 20'}; also ${RUN}/filed.txt).
4. If any step fails, do NOT file: return filed=false with the reason in one sentence.
5. Otherwise file it. ${hook
  ? 'Write the issue as JSON (title, body markdown, labels, repo key "' + (x.repo || 'app') + '") to ' + RUN + '/state/file-' + x.i + '.json and run: ' + hook + ' ' + RUN + '/state/file-' + x.i + '.json . Use the URL or id it prints.'
  : 'Use gh in ' + repo + ', body per ' + RUN + '/templates/issue-template-character.md: the quote block with the reporter\'s voice and card line, then Where, Steps, Expected, Actual, Likely cause (what you confirmed in step 2, with file:line), Also hit by, Evidence, and the footer "' + footer + '". Evidence embeds as https://github.com/' + repo + '/blob/' + A.evidence_branch + '/<path>?raw=true using these pushed paths: ' + JSON.stringify(x.evidence || []) + '. Labels: ' + JSON.stringify(x.labels || []) + ' (they already exist; never create labels). Write the body to a file and use --body-file. Then gh issue view the new issue.'}
6. Append one line "#<number or id> <title>" to ${RUN}/filed.txt with a single echo >> (other verifiers append at the same time).
Plain, specific prose: no hype words, no summary at the end. Report only URLs the tool returned.`

for (const key of trackerKeys) {
  const repo = T[key]; const mine = numbered.filter(x => x.repo === key)
  if (!repo || !mine.length) continue
  let paths = new Map()
  if (!HOOK) {
    // once per repo, before the parallel verifiers: evidence on the orphan branch, and every label created
    const push = await agent(`Prepare ${repo} for filing crowd-QA issues, once.
A. Evidence: clone ${RUN}/evidence-${key} (git clone --single-branch -b ${A.evidence_branch} https://github.com/${repo}.git ${RUN}/evidence-${key}; if the branch does not exist, git init a fresh dir, create orphan branch ${A.evidence_branch} with a README, add the remote and push it). For issue i copy each screenshot and evidence file (reporter's and confirmers') to ${RUN}/evidence-${key}/${A.run_label}/issue-<i>/<character>-<basename>. git add that folder only, commit "qa-evidence: ${A.run_label}", push, verify with git ls-remote.
B. Labels: create every label these issues use with gh label create --force -R ${repo} (so the parallel verifiers never race on labels).
Return each i with its repo-relative evidence paths.
Issues: ${JSON.stringify(mine.map(x => ({ i: x.i, reporter: x.reporter, screenshots: x.screenshots, confirmers: x.confirmers || [], labels: x.labels || [] })))}`,
      { label: `prepare:${key}`, phase: 'File', effort: 'low', schema: PUSH_SCHEMA })
    paths = new Map(((push && push.map) || []).map(m => [m.i, m.paths]))
  }
  for (let s = 0; s < mine.length; s += FILE_PARALLEL) {
    const batch = mine.slice(s, s + FILE_PARALLEL).map(x => ({ ...x, evidence: paths.get(x.i) || [] }))
    const res = await parallel(batch.map(x => () => agent(verifyFilePrompt(x, repo, HOOK),
      { label: `verify-file:${key}:${x.i}`, phase: 'File', effort: 'high', schema: VERIFY_FILE_SCHEMA })))
    for (const r of res) {
      if (!r) continue
      if (r.filed && r.url) filed.push({ i: r.i, url: r.url, title: r.title, repo: r.repo || repo })
      else notFiled.push({ i: r.i, reason: r.reason })
    }
  }
}
log(`${filed.length} filed, ${notFiled.length} not filed after verification`)

phase('Report')
const report = await agent(`Write the launch-readiness report for this crowd run to ${RUN}/LAUNCH_REPORT.md, the way MiroFish's report agent sums up a simulation, but grounded only in evidence.
Read every ${RUN}/lanes/*/card.md, memory.md and the last verdict.json, the square (python3 ${RUN}/scripts/square.py ${RUN} digest nobody --limit 500), the filed issues: ${JSON.stringify(filed)}, and the bugs verification refused to file: ${JSON.stringify(notFiled)}.
Sections: a verdict line (would these ${CH.length} people keep using the product after launch week, and why); one short section per group of people (by the roles and needs on the cards: first-timers, power users, each role, accessibility, the boundary tester), each with who in that group got what they wanted, who gave up and where, and the issues that blocked them (linked); the three things to fix before launch, ordered by how many people they hurt; what nobody could test and why. Quote characters' own lines sparingly. Plain prose, no hype words, no closing summary.
Return the verdict line.`, { label: 'launch-report', phase: 'Report', effort: 'high', schema: { type: 'object', properties: { verdict: { type: 'string' } }, required: ['verdict'] } })

return {
  characters: ready.map(c => ({ id: c.id, satisfied: !!(verdicts[c.id] && verdicts[c.id].satisfied), paused: paused.has(c.id), coverage: verdicts[c.id] ? verdicts[c.id].coverage_score : 0, in_character: verdicts[c.id] ? verdicts[c.id].in_character : null })),
  issues_filed: filed.length, issues: filed, not_filed: notFiled, verdict: report && report.verdict,
}
