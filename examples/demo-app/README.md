# Tinybox demo

Tinybox is a small shared inbox for small shops: customers message a channel, the team reads and replies in a web UI, a JSON API or a CLI. It exists to give a QA tool something real to test. It contains a few deliberate bugs, listed in `KNOWN_BUGS.md`.

Needs Node 20 or newer. Dependencies are `express` and `better-sqlite3`.

## Run

```
npm install
npm start          # http://localhost:4100 (set PORT to change)
npm run reset      # deletes ./data (database and outbox files)
```

Health check: `curl localhost:4100/health` returns `{"status":"ok"}`.

## What it does

- Sign up, sign in, sign out. The web UI uses an httpOnly cookie. The API takes `Authorization: Bearer <token>`; `POST /api/login` with `{email, password}` returns `{token}`.
- A new user creates a workspace on first sign-in. Roles are owner, agent and viewer. Owners invite by email, change roles and remove members. Viewers can read but cannot reply, assign, close or edit saved replies (the server answers 403).
- Inbox: thread list with unread badges, thread view, reply, assign, close and reopen.
- Saved replies: create, edit, delete, 500 characters max.
- Everything is scoped to one workspace. Ids from another workspace return 404.

## API

All routes below need the bearer token.

| Route | Purpose |
|---|---|
| `GET /api/threads` | list threads |
| `GET /api/threads/:id` | one thread with messages (marks it read) |
| `POST /api/threads/:id/reply` | `{text}` |
| `PATCH /api/threads/:id` | `{status}` and/or `{assigneeId}` |
| `GET /api/saved-replies`, `POST /api/saved-replies` | list, create `{title, body}` |
| `GET /api/members` | workspace members |

## Connecting a channel

The Connect channel button in Settings opens a page saying the provider is unavailable. To connect a channel without it, run:

```
node scripts/connect-fake.js owner@example.com "Shop line"
```

It prints `{"workspace":..., "channel":..., "number":"1234567890", "secret":"..."}`. The user must already have a workspace. The `secret` is also in the output because you need it to sign webhooks.

## Webhook

`POST /webhook/<channel number>` with a JSON body `{from, name, text, ts}` (`name` and `ts` optional; `ts` is epoch milliseconds or an ISO string). Sign the exact raw body bytes: header `X-Signature: sha256=<hex HMAC-SHA256 of the body, keyed with the channel secret>`. A bad or missing signature returns 401.

```
BODY='{"from":"15551230001","name":"Dana","text":"Is the blue one in stock?"}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | awk '{print $NF}')
curl -X POST "http://localhost:4100/webhook/$NUMBER" \
  -H "Content-Type: application/json" -H "X-Signature: sha256=$SIG" -d "$BODY"
```

## CLI

```
node cli/tinybox.js login owner@example.com 'password123'   # saves token to ~/.tinybox-token
node cli/tinybox.js threads
node cli/tinybox.js show 1
node cli/tinybox.js reply 1 "Yes, two left."
node cli/tinybox.js saved-replies
```

The CLI talks to `http://localhost:4100`; set `TINYBOX_URL` to change that. Errors go to stderr with a non-zero exit code.

## Files it writes

All under `./data/` (gitignored):

- `tinybox.db`: SQLite database
- `outbox-mail.jsonl`: one line per invitation email: `{to, subject, link}`
- `outbox-messages.jsonl`: one line per reply: `{to, channel, thread, text, by, ts}`

Open an invitation link as the invited email's account (`/invite/<id>`) to join.
