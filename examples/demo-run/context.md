## Run context (every character reads this)

### This run only files issues
Nobody fixes anything. Never edit, commit or run git write commands in any repository, never change config,
never restart a service. Use the product, record what happened, with evidence.

### The product
- Tinybox: a small shared inbox for shops. Owners create a workspace, connect a messaging channel, invite
  agents and viewers, and answer customers from one inbox.
- Web: http://localhost:4100 . API: http://localhost:4100/api (POST /api/login gives {token}; send
  Authorization: Bearer <token>). CLI: `node /Users/cn/Documents/Conceptmate_workspace/crowd-qa/examples/demo-app/cli/tinybox.js` (login, threads, show, reply, saved-replies).
- The API is described in /Users/cn/Documents/Conceptmate_workspace/crowd-qa/examples/demo-app/README.md.

### The stack (throwaway, on this machine)
- Never stop, restart or rebuild anything. If the product fails like a server problem, check STACK_DOWN (your brief says how).

### Accounts
- Sign up through the product: email `<id>@crowd.test`, password `CrowdQA!2026`, unless your card says otherwise.
- Your email inbox (invites): `bash /Users/cn/Documents/Conceptmate_workspace/crowd-qa/examples/demo-run/hooks.sh mail <your email>`. Invite links say http://localhost:4100/invite/<id>.

### Third parties and customers
- Connecting a channel: the Settings button opens a provider page that cannot work here (expected; screenshot it),
  then run `bash /Users/cn/Documents/Conceptmate_workspace/crowd-qa/examples/demo-run/hooks.sh connect <your email> "<channel name>"`. It prints the number and secret. Register it so
  customers can reach you: `python3 /Users/cn/qa-runs/crowd-qa-dryrun-2026-10-08/scripts/customer.py /Users/cn/qa-runs/crowd-qa-dryrun-2026-10-08 register "<business>" --channel chat --to <number> --secret <secret> --var owner=<your id>`.
- Messages from customers: `python3 /Users/cn/qa-runs/crowd-qa-dryrun-2026-10-08/scripts/customer.py /Users/cn/qa-runs/crowd-qa-dryrun-2026-10-08 send "<business>" --from <phone digits> --name "<name>" --text "..."`
  (leave --name out for a customer with no saved name). What the shop sent back: `bash /Users/cn/Documents/Conceptmate_workspace/crowd-qa/examples/demo-run/hooks.sh outbox`.

### Time
- One simulated day is about an hour. Nothing in Tinybox is scheduled.

### Intended behaviour (never a finding)
- The provider page after "Connect channel" says it is unavailable.
- Viewers cannot reply, assign, close or edit saved replies; the server answers 403 and the page says so.
- A user belongs to one workspace; an invite only works for the email it was sent to.

### Source (read-only, to name causes)
- /Users/cn/Documents/Conceptmate_workspace/crowd-qa/examples/demo-app (server.js, public/app.js, cli/tinybox.js, db.js).
- Design: no design system; judge against the app's own styles in public/style.css.

### Language
Everything you write (posts, reports, diary) is in English.
