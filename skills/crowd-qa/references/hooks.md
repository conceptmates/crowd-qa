# Hooks: what a project plugs in

Every product fakes the outside world differently, so the skill asks for commands instead of guessing. All
hooks live in `<run>/config.env`; each may be a local command or `ssh <host> '...'`. Write them during
preflight, then prove each one in the stack check before launch.

## Third parties and customers

| Hook | Called by | Contract |
|---|---|---|
| `CONNECT_CMD` | testers | `<CONNECT_CMD> <email> "<name>"` connects a fake third-party account (a messaging number, a payment account, an OAuth app) to that user's active workspace and prints JSON with what was connected (an id or number, and any secret customers need). Use it where the real flow is a popup to a provider that cannot work on a test stack. |
| `CUSTOMERS_CONFIG` | `scripts/customer.py` | Path to a channels file (`templates/customers.example.json`). Each channel: URL, method, headers, optional `hmac-sha256` signing, body template. Owners `register` what they connected; customer characters `send`. `--ago 25h` backdates timestamps. |
| `OUTBOX_CMD` | testers | Prints what the product sent to the outside world (messages, emails, webhooks), newest last. Customer characters read it as their phone screen. |
| `MAIL_CMD` | testers | `<MAIL_CMD> <email>` prints that person's inbox (verification codes, invites, resets). |

If the product's own repo already has fakes (an e2e fake server, a seed script, a mail catcher), wrap those.
Write any helper outside the product's checkout and never edit it.

## Stack health

| Hook | Called by | Contract |
|---|---|---|
| `HEALTH_CHECKS` | `api-watch.sh` (every 60 s), `watchdog.sh`, and `run-tester.sh` before each start (`api-watch.sh <run> once`) | `"name|command; name|command"`. Each command exits 0 when healthy. Include the API's health endpoint, the app's page, any fake upstream, storage, and **a canary sign-in** (create `canary@crowd.test` before launch): a wiped database still answers health checks, but it fails the canary. |
| `STACK_UP_CMD` | `watchdog.sh` | Idempotent: brings the stack up and returns when it should answer. |
| `DB_COUNT_CMD` | `watchdog.sh` | Prints one number that only grows during a run (users, rows). A drop means the data was wiped. |
| `DB_BACKUP_CMD` | `watchdog.sh` | Prints a complete dump to stdout (e.g. `docker exec db pg_dump -U app app --clean --if-exists`). |
| `DB_RESTORE_CMD` | `watchdog.sh` | Reads a dump on stdin and replaces the database (e.g. `docker exec -i db psql -U app app`). |
| `STACK_VERIFY` | `preflight.sh` | The end-to-end stack check (`templates/stack-verify.example.py`). |

### Running the watchdog on a timer
`api-watch.sh` starts `watchdog.sh` the moment the stack goes down. To also keep backups current and recover
when nothing is watching, run it every 2 minutes on the machine that orchestrates:

macOS (launchd), `~/Library/LaunchAgents/crowd-watchdog.plist`:
```xml
<plist version="1.0"><dict>
  <key>Label</key><string>crowd-watchdog</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>RUN/scripts/watchdog.sh</string><string>RUN</string></array>
  <key>StartInterval</key><integer>120</integer>
</dict></plist>
```
`launchctl load ~/Library/LaunchAgents/crowd-watchdog.plist`

Linux (systemd user timer): a oneshot service running `bash RUN/scripts/watchdog.sh RUN` plus a timer with
`OnBootSec=1min` and `OnUnitInactiveSec=2min`; `systemctl --user enable --now crowd-watchdog.timer`. On a
machine nobody logs into, the owner runs `sudo loginctl enable-linger <user>` once so the timer runs at boot.

When the stack lives on another machine, the hooks are `ssh <host> '...'` commands and the timer can stay on
the orchestrating machine, or the run dir's `watchdog.sh` and hooks can be copied to the stack host.

## Filing

| Hook | Called by | Contract |
|---|---|---|
| `NOTIFY_CMD` | `api-watch.sh` | Optional. Runs with one argument, the message, when the stack goes down and when it comes back: a desktop notification, a chat webhook, a file the orchestrating session watches. |
| `FILE_CMD` | `crowd.py file`, after the bug check | Empty: GitHub through `gh`, evidence on an orphan branch. Set: `<FILE_CMD> <issue.json>` files one issue in another tracker (Linear, Jira, a board) and prints its URL or id. The JSON has `title`, `body` (markdown), `labels`, `repo` (the trackers key), `screenshots` (absolute paths). |
