# Known bugs (seeded on purpose)

These are the defects a QA run against Tinybox is expected to find.

1. **Saved-reply counter vs. server limit.** The saved-reply form counts up to 500 characters, but the server stores only the first 480 and returns success. A 500-character reply comes back 20 characters shorter, with no error. Where: `server.js` (`readReply`), `public/app.js` (`renderSaved`).
2. **Double-click on Reply sends twice.** The Reply button is not disabled while the request is in flight and the box is cleared only after the response, so a double-click posts the same text twice. The thread shows the message twice and `outbox-messages.jsonl` gets two lines. Where: `public/app.js` (`renderThread`, `#send`).
3. **CLI `show` prints "undefined".** For inbound messages from a contact with no name, the API leaves out `author` and the CLI prints it as-is: `[2026-10-08 10:00] undefined: hello`. Where: `cli/tinybox.js` (`show`).
4. **Status filter ignored.** `GET /api/threads?status=closed` returns every thread, open and closed. Where: `server.js` (`GET /api/threads`). The web UI filters in the browser, so it looks right there.
