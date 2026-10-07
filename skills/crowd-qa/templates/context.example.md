## Run context (every character reads this)

<!-- Copied into <run>/context.md by init-crowd.sh. Replace every <...>. Everything app-specific lives here:
     the brief and the judge read it; nothing app-specific belongs in the templates. -->

### This run only files issues
Nobody fixes anything. Never edit, commit, branch or run git write commands in any repository, never change
config, never restart a service. Use the product, record what happened, with evidence.

### The product
- <What it is in one sentence.>
- Web: <APP_URL>. API: <API_URL>, authenticate with <how>. CLI: <how to run it>.
- <Plans, roles, anything a new user would see on day 1.>

### The stack (throwaway)
- <Where it runs; local or `ssh <host>`; what is faked (third-party providers, payments, email).>
- Never stop, restart or rebuild anything. If the product fails like a server problem, check STACK_DOWN (your brief says how).

### Accounts
- Sign up through the product: email `<id>@crowd.test`, password `<password>`, unless your card says otherwise.
- Your email inbox: `<MAIL_CMD> <your email>`.

### Third parties and customers
- Connecting <the third party>: click the product's connect button and screenshot what it shows (a failing
  popup is expected here), then run `<CONNECT_CMD> <your email> "<name>"`. Register what you connected so
  customers can reach you: `python3 <run>/scripts/customer.py <run> register "<business>" --channel <channel> --to <number or id> --secret <secret> --var owner=<your id>`.
- Customers send messages with `python3 <run>/scripts/customer.py <run> send "<business>" --from <your number> --name "<your name>" --text "..."`.
  What the business sent back: `<OUTBOX_CMD>`.

### Time
- A simulated day is a few hours. Scheduled jobs: <how to fire them, or "ask on the square">.
- <Anything that cannot be simulated on this stack, so nobody reports it.>

### Intended behaviour (never a finding)
- <Upgrade prompts, disabled buttons that are disabled on purpose, labels on unfinished features, redirects.>

### Already known (don't report again; a me-too on the square is fine)
- <issue numbers and one-line titles>

### Source (read-only, to name causes)
- <paths to the repositories, and the commit the stack runs>
- Design source: <design system doc, tokens, or "none: judge against the product's own components">.

### Language
Everything you write (posts, reports, diary) is in <English>.
