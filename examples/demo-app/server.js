const express = require('express');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { db, DATA_DIR } = require('./db');

const PORT = process.env.PORT || 4100;
const app = express();
const publicDir = path.join(__dirname, 'public');

// ---------- helpers ----------
const appendJsonl = (file, obj) =>
  fs.appendFileSync(path.join(DATA_DIR, file), JSON.stringify(obj) + '\n');
const normEmail = (e) => String(e || '').trim().toLowerCase();
const str = (v) => (typeof v === 'string' ? v.trim() : '');
const bad = (res, msg, code = 400) => res.status(code).json({ error: msg });

function hashPassword(pw) {
  const salt = crypto.randomBytes(16).toString('hex');
  return salt + ':' + crypto.scryptSync(pw, salt, 32).toString('hex');
}
function checkPassword(pw, stored) {
  const [salt, hash] = stored.split(':');
  const given = crypto.scryptSync(pw, salt, 32);
  return crypto.timingSafeEqual(given, Buffer.from(hash, 'hex'));
}
function parseCookies(header = '') {
  const out = {};
  for (const part of header.split(';')) {
    const i = part.indexOf('=');
    if (i > 0) out[part.slice(0, i).trim()] = decodeURIComponent(part.slice(i + 1).trim());
  }
  return out;
}
function startSession(res, userId) {
  const token = crypto.randomBytes(24).toString('hex');
  db.prepare('INSERT INTO sessions (token, user_id) VALUES (?, ?)').run(token, userId);
  res.cookie('tb_session', token, { httpOnly: true, sameSite: 'lax', path: '/', maxAge: 7 * 864e5 });
  return token;
}

// ---------- auth middleware ----------
function auth(req, res, next) {
  const bearer = /^Bearer (.+)$/i.exec(req.get('authorization') || '');
  const token = bearer ? bearer[1] : parseCookies(req.get('cookie')).tb_session;
  const user = token && db.prepare(
    'SELECT u.id, u.name, u.email FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ?'
  ).get(token);
  if (!user) return bad(res, 'Not signed in', 401);
  req.user = user;
  req.token = token;
  req.ws = db.prepare(
    'SELECT m.workspace_id AS id, m.role, w.name FROM members m JOIN workspaces w ON w.id = m.workspace_id WHERE m.user_id = ?'
  ).get(user.id) || null;
  next();
}
const needWs = (req, res, next) =>
  req.ws ? next() : bad(res, 'Create a workspace first', 403);
const notViewer = (req, res, next) =>
  req.ws.role === 'viewer' ? bad(res, 'Viewers cannot do this', 403) : next();
const ownerOnly = (req, res, next) =>
  req.ws.role === 'owner' ? next() : bad(res, 'Only owners can do this', 403);

// ---------- webhook (raw body, before the JSON parser) ----------
app.post('/webhook/:number', express.raw({ type: () => true, limit: '256kb' }), (req, res) => {
  const ch = db.prepare('SELECT * FROM channels WHERE number = ?').get(req.params.number);
  if (!ch) return bad(res, 'Unknown channel', 404);
  const raw = Buffer.isBuffer(req.body) ? req.body : Buffer.alloc(0);
  const sig = /^sha256=([0-9a-f]{64})$/i.exec(req.get('x-signature') || '');
  const want = crypto.createHmac('sha256', ch.secret).update(raw).digest();
  if (!sig || !crypto.timingSafeEqual(Buffer.from(sig[1], 'hex'), want)) {
    return bad(res, 'Invalid signature', 401);
  }
  let body;
  try { body = JSON.parse(raw.toString('utf8')); } catch { return bad(res, 'Body must be JSON'); }
  const from = str(body.from), text = str(body.text), name = str(body.name);
  const at = new Date(body.ts ?? Date.now());
  if (!from || !text) return bad(res, 'from and text are required');
  if (isNaN(at)) return bad(res, 'ts is not a valid time');

  const save = db.transaction(() => {
    let c = db.prepare('SELECT id FROM contacts WHERE channel_id = ? AND phone = ?').get(ch.id, from);
    if (!c) {
      const id = db.prepare('INSERT INTO contacts (workspace_id, channel_id, phone, name) VALUES (?,?,?,?)')
        .run(ch.workspace_id, ch.id, from, name || null).lastInsertRowid;
      c = { id };
    } else if (name) {
      db.prepare('UPDATE contacts SET name = ? WHERE id = ?').run(name, c.id);
    }
    let t = db.prepare('SELECT id FROM threads WHERE contact_id = ?').get(c.id);
    if (!t) {
      const id = db.prepare('INSERT INTO threads (workspace_id, contact_id, channel_id, last_at) VALUES (?,?,?,?)')
        .run(ch.workspace_id, c.id, ch.id, at.toISOString()).lastInsertRowid;
      t = { id };
    }
    db.prepare("UPDATE threads SET unread = unread + 1, status = 'open', last_at = max(last_at, ?) WHERE id = ?")
      .run(at.toISOString(), t.id);
    db.prepare("INSERT INTO messages (thread_id, direction, text, ts) VALUES (?, 'in', ?, ?)")
      .run(t.id, text, at.toISOString());
    return Number(t.id);
  });
  res.json({ ok: true, thread: save() });
});

app.use(express.json());
app.use((req, _res, next) => { req.body = req.body || {}; next(); });
app.get('/health', (_req, res) => res.json({ status: 'ok' }));

// ---------- accounts ----------
app.post('/api/signup', (req, res) => {
  const name = str(req.body.name), email = normEmail(req.body.email), password = req.body.password;
  if (!name) return bad(res, 'Name is required');
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) return bad(res, 'Enter a valid email');
  if (typeof password !== 'string' || password.length < 8) return bad(res, 'Password must be at least 8 characters');
  if (db.prepare('SELECT 1 FROM users WHERE email = ?').get(email)) return bad(res, 'That email is already registered', 409);
  const id = db.prepare('INSERT INTO users (name, email, password_hash) VALUES (?,?,?)')
    .run(name, email, hashPassword(password)).lastInsertRowid;
  const token = startSession(res, id);
  res.status(201).json({ token, user: { id: Number(id), name, email } });
});

app.post('/api/login', (req, res) => {
  const email = normEmail(req.body.email);
  const u = db.prepare('SELECT * FROM users WHERE email = ?').get(email);
  if (!u || typeof req.body.password !== 'string' || !checkPassword(req.body.password, u.password_hash)) {
    return bad(res, 'Wrong email or password', 401);
  }
  res.json({ token: startSession(res, u.id) });
});

// invitation preview is public so the invite page can show it before sign-in
app.get('/api/invitations/:id', (req, res) => {
  const inv = db.prepare(
    'SELECT i.email, i.role, i.accepted, w.name AS workspace FROM invitations i JOIN workspaces w ON w.id = i.workspace_id WHERE i.id = ?'
  ).get(req.params.id);
  if (!inv) return bad(res, 'Invitation not found', 404);
  res.json({ ...inv, accepted: !!inv.accepted });
});

app.use('/api', auth);

app.post('/api/logout', (req, res) => {
  db.prepare('DELETE FROM sessions WHERE token = ?').run(req.token);
  res.clearCookie('tb_session', { path: '/' });
  res.json({ ok: true });
});

app.get('/api/me', (req, res) => res.json({ user: req.user, workspace: req.ws }));

// ---------- workspaces, members, invitations ----------
app.post('/api/workspaces', (req, res) => {
  if (req.ws) return bad(res, 'You already belong to a workspace', 409);
  const name = str(req.body.name);
  if (!name) return bad(res, 'Workspace name is required');
  const id = db.transaction(() => {
    const wid = db.prepare('INSERT INTO workspaces (name) VALUES (?)').run(name).lastInsertRowid;
    db.prepare("INSERT INTO members (workspace_id, user_id, role) VALUES (?, ?, 'owner')").run(wid, req.user.id);
    return Number(wid);
  })();
  res.status(201).json({ id, name, role: 'owner' });
});

app.post('/api/invitations/:id/accept', (req, res) => {
  const inv = db.prepare('SELECT * FROM invitations WHERE id = ?').get(req.params.id);
  if (!inv) return bad(res, 'Invitation not found', 404);
  if (inv.accepted) return bad(res, 'Invitation already used', 409);
  if (inv.email !== req.user.email) return bad(res, `This invitation is for ${inv.email}`, 403);
  if (req.ws) return bad(res, 'You already belong to a workspace', 409);
  db.transaction(() => {
    db.prepare('INSERT INTO members (workspace_id, user_id, role) VALUES (?,?,?)').run(inv.workspace_id, req.user.id, inv.role);
    db.prepare('UPDATE invitations SET accepted = 1 WHERE id = ?').run(inv.id);
  })();
  res.json({ ok: true });
});

app.use('/api', needWs);

app.get('/api/members', (req, res) => {
  res.json(db.prepare(
    'SELECT u.id, u.name, u.email, m.role FROM members m JOIN users u ON u.id = m.user_id WHERE m.workspace_id = ? ORDER BY u.id'
  ).all(req.ws.id));
});

app.patch('/api/members/:userId', ownerOnly, (req, res) => {
  const role = req.body.role;
  if (!['owner', 'agent', 'viewer'].includes(role)) return bad(res, 'Role must be owner, agent or viewer');
  const target = db.prepare('SELECT 1 FROM members WHERE workspace_id = ? AND user_id = ?').get(req.ws.id, Number(req.params.userId));
  if (!target) return bad(res, 'Member not found', 404);
  if (Number(req.params.userId) === req.user.id) return bad(res, 'You cannot change your own role');
  db.prepare('UPDATE members SET role = ? WHERE workspace_id = ? AND user_id = ?').run(role, req.ws.id, Number(req.params.userId));
  res.json({ ok: true });
});

app.delete('/api/members/:userId', ownerOnly, (req, res) => {
  const uid = Number(req.params.userId);
  const target = db.prepare('SELECT 1 FROM members WHERE workspace_id = ? AND user_id = ?').get(req.ws.id, uid);
  if (!target) return bad(res, 'Member not found', 404);
  if (uid === req.user.id) return bad(res, 'You cannot remove yourself');
  db.transaction(() => {
    db.prepare('UPDATE threads SET assignee_id = NULL WHERE workspace_id = ? AND assignee_id = ?').run(req.ws.id, uid);
    db.prepare('DELETE FROM members WHERE workspace_id = ? AND user_id = ?').run(req.ws.id, uid);
  })();
  res.json({ ok: true });
});

app.get('/api/invitations', ownerOnly, (req, res) => {
  res.json(db.prepare('SELECT id, email, role FROM invitations WHERE workspace_id = ? AND accepted = 0').all(req.ws.id));
});

app.post('/api/invitations', ownerOnly, (req, res) => {
  const email = normEmail(req.body.email), role = req.body.role || 'agent';
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) return bad(res, 'Enter a valid email');
  if (!['agent', 'viewer'].includes(role)) return bad(res, 'Role must be agent or viewer');
  const id = crypto.randomBytes(8).toString('hex');
  db.prepare('INSERT INTO invitations (id, workspace_id, email, role) VALUES (?,?,?,?)').run(id, req.ws.id, email, role);
  appendJsonl('outbox-mail.jsonl', {
    to: email,
    subject: `You are invited to ${req.ws.name} on Tinybox`,
    link: `${req.protocol}://${req.get('host')}/invite/${id}`,
  });
  res.status(201).json({ id, email, role });
});

// ---------- channels ----------
app.get('/api/channels', (req, res) => {
  const rows = db.prepare('SELECT id, name, number, secret FROM channels WHERE workspace_id = ?').all(req.ws.id);
  res.json(rows.map((r) => (req.ws.role === 'owner' ? r : { id: r.id, name: r.name, number: r.number })));
});

// ---------- threads ----------
const THREAD_SQL = `
  SELECT t.id, t.status, t.unread, t.last_at, t.assignee_id, a.name AS assignee_name,
         c.name AS contact_name, c.phone AS contact_phone, ch.name AS channel,
         (SELECT text FROM messages WHERE thread_id = t.id ORDER BY id DESC LIMIT 1) AS last_text
  FROM threads t
  JOIN contacts c ON c.id = t.contact_id
  JOIN channels ch ON ch.id = t.channel_id
  LEFT JOIN users a ON a.id = t.assignee_id`;

function shapeThread(r) {
  return {
    id: r.id, status: r.status, unread: r.unread, last_at: r.last_at, channel: r.channel,
    assignee: r.assignee_id ? { id: r.assignee_id, name: r.assignee_name } : null,
    contact: { name: r.contact_name, phone: r.contact_phone },
    last_text: r.last_text,
  };
}
function findThread(req, res) {
  const id = Number(req.params.id);
  const row = Number.isInteger(id) &&
    db.prepare(THREAD_SQL + ' WHERE t.id = ? AND t.workspace_id = ?').get(id, req.ws.id);
  if (!row) { bad(res, 'Thread not found', 404); return null; }
  return row;
}

app.get('/api/threads', (req, res) => {
  const rows = db.prepare(THREAD_SQL + ' WHERE t.workspace_id = ? ORDER BY t.last_at DESC, t.id DESC').all(req.ws.id);
  res.json(rows.map(shapeThread));
});

app.get('/api/threads/:id', (req, res) => {
  const row = findThread(req, res);
  if (!row) return;
  const msgs = db.prepare(
    'SELECT m.id, m.direction, m.text, m.ts, u.name AS author_name FROM messages m LEFT JOIN users u ON u.id = m.author_id WHERE m.thread_id = ? ORDER BY m.id'
  ).all(row.id);
  db.prepare('UPDATE threads SET unread = 0 WHERE id = ?').run(row.id);
  res.json({
    ...shapeThread({ ...row, unread: 0 }),
    messages: msgs.map((m) => ({
      id: m.id, direction: m.direction, text: m.text, ts: m.ts,
      author: m.direction === 'in' ? row.contact_name || undefined : m.author_name,
    })),
  });
});

app.post('/api/threads/:id/reply', notViewer, (req, res) => {
  const row = findThread(req, res);
  if (!row) return;
  const text = str(req.body.text);
  if (!text) return bad(res, 'Reply text is required');
  if (text.length > 4000) return bad(res, 'Reply is too long (max 4000 characters)');
  const ts = new Date().toISOString();
  const id = db.transaction(() => {
    const mid = db.prepare("INSERT INTO messages (thread_id, direction, text, ts, author_id) VALUES (?, 'out', ?, ?, ?)")
      .run(row.id, text, ts, req.user.id).lastInsertRowid;
    db.prepare('UPDATE threads SET last_at = ? WHERE id = ?').run(ts, row.id);
    return Number(mid);
  })();
  appendJsonl('outbox-messages.jsonl', {
    to: row.contact_phone, channel: row.channel, thread: row.id, text, by: req.user.email, ts,
  });
  res.status(201).json({ id, direction: 'out', text, ts, author: req.user.name });
});

app.patch('/api/threads/:id', notViewer, (req, res) => {
  const row = findThread(req, res);
  if (!row) return;
  const { status, assigneeId } = req.body;
  if (status !== undefined) {
    if (!['open', 'closed'].includes(status)) return bad(res, 'Status must be open or closed');
    db.prepare('UPDATE threads SET status = ? WHERE id = ?').run(status, row.id);
  }
  if (assigneeId !== undefined) {
    if (assigneeId !== null && !db.prepare('SELECT 1 FROM members WHERE workspace_id = ? AND user_id = ?').get(req.ws.id, Number(assigneeId))) {
      return bad(res, 'Assignee must be a member of this workspace');
    }
    db.prepare('UPDATE threads SET assignee_id = ? WHERE id = ?').run(assigneeId === null ? null : Number(assigneeId), row.id);
  }
  res.json(shapeThread(db.prepare(THREAD_SQL + ' WHERE t.id = ?').get(row.id)));
});

// ---------- saved replies ----------
function readReply(req, res) {
  const title = str(req.body.title), body = str(req.body.body);
  if (!title) { bad(res, 'Title is required'); return null; }
  if (title.length > 60) { bad(res, 'Title is too long (max 60 characters)'); return null; }
  if (!body) { bad(res, 'Text is required'); return null; }
  if (body.length > 500) { bad(res, 'Text is too long (max 500 characters)'); return null; }
  return { title, body: body.slice(0, 480) };
}
const findReply = (req) => {
  const id = Number(req.params.id);
  return Number.isInteger(id) && db.prepare('SELECT * FROM saved_replies WHERE id = ? AND workspace_id = ?').get(id, req.ws.id);
};

app.get('/api/saved-replies', (req, res) => {
  res.json(db.prepare('SELECT id, title, body FROM saved_replies WHERE workspace_id = ? ORDER BY id').all(req.ws.id));
});
app.post('/api/saved-replies', notViewer, (req, res) => {
  const r = readReply(req, res);
  if (!r) return;
  const id = db.prepare('INSERT INTO saved_replies (workspace_id, title, body) VALUES (?,?,?)').run(req.ws.id, r.title, r.body).lastInsertRowid;
  res.status(201).json({ id: Number(id), ...r });
});
app.put('/api/saved-replies/:id', notViewer, (req, res) => {
  const cur = findReply(req);
  if (!cur) return bad(res, 'Saved reply not found', 404);
  const r = readReply(req, res);
  if (!r) return;
  db.prepare('UPDATE saved_replies SET title = ?, body = ? WHERE id = ?').run(r.title, r.body, cur.id);
  res.json({ id: cur.id, ...r });
});
app.delete('/api/saved-replies/:id', notViewer, (req, res) => {
  const cur = findReply(req);
  if (!cur) return bad(res, 'Saved reply not found', 404);
  db.prepare('DELETE FROM saved_replies WHERE id = ?').run(cur.id);
  res.json({ ok: true });
});

app.use('/api', (_req, res) => bad(res, 'Not found', 404));

// ---------- static UI ----------
app.use(express.static(publicDir));
app.get('/invite/:id', (_req, res) => res.sendFile(path.join(publicDir, 'index.html')));

app.use((err, _req, res, _next) => {
  if (err.type === 'entity.parse.failed') return bad(res, 'Body must be valid JSON');
  console.error(err);
  res.status(500).json({ error: 'Server error' });
});

app.listen(PORT, () => console.log(`Tinybox listening on http://localhost:${PORT}`));
