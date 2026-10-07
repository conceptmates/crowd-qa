# Scenario file format (lanes/<lane>/scenarios.md)

Written by the scenario planner (the orchestrating model) for one character, read by the tester every round.

## Mix
- At least 60% of scenarios are failure or edge paths.
- Every happy path has at least two matching failures (the same journey going wrong in different ways).
- 20-40 scenarios per lane, then an "Exhaustive sweep" section.

## Where failures come from
Think like an average person on a bad day, and like the backend's validation rules:
- input: empty, whitespace-only, invalid format, over the limit by one, huge, emoji, RTL text, pasted HTML/URLs
- timing: double submit, click while loading, refresh or back mid-edit, two tabs editing the same thing
- state: expired session, signed out mid-flow, stale deep link, deleted or disconnected dependency
- environment: offline, slow network, permission denied, small screen, dark mode
- limits: plan or limit refusal, rate limit, a third-party integration down
- trust: the UI says success but the data did not persist (reload and check)

## Each scenario
```
### S7 — <short title>  [failure]
Persona: bakery owner, first time using the product, on a phone
Preconditions: signed in; one published automation exists
Steps:
1. Open /automations, click "QA-E2E-Welcome"
2. Clear the message text, click Save
3. Reload the page
Test data: message = "" ; then "🎂" * 300
Expected: Save is refused with an inline error naming the field; after reload the old text is intact.
```

## Real-life lane
Journeys a real customer or operator runs end to end, researched from how people use this kind of
product (e.g. booking a slot through DMs, taking an order, lead to paid customer). Build the whole
journey through the UI, verify it persisted after reload, then walk each failure: wrong input at every
step, cancelling halfway, the customer going silent, the slot already taken, payment declined.

## Exhaustive sweep
List every route or screen, tab, menu and dialog in the lane's scope that the tester must touch at
least once, beyond the scenarios.
