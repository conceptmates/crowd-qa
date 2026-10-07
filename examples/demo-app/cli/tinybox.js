#!/usr/bin/env node
// Tinybox CLI. Server URL from TINYBOX_URL (default http://localhost:4100).
const fs = require('fs');
const os = require('os');
const path = require('path');

const BASE = process.env.TINYBOX_URL || 'http://localhost:4100';
const TOKEN_FILE = path.join(os.homedir(), '.tinybox-token');

function fail(msg) {
  console.error('error: ' + msg);
  process.exit(1);
}

async function api(method, url, body, token) {
  const headers = {};
  if (body) headers['Content-Type'] = 'application/json';
  if (token) headers.Authorization = 'Bearer ' + token;
  let res;
  try {
    res = await fetch(BASE + url, { method, headers, body: body ? JSON.stringify(body) : undefined });
  } catch {
    fail(`cannot reach ${BASE}. Is the server running?`);
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) fail(data.error || `request failed (${res.status})`);
  return data;
}

function token() {
  try {
    return fs.readFileSync(TOKEN_FILE, 'utf8').trim();
  } catch {
    return fail('not logged in. Run: tinybox login <email> <password>');
  }
}

const when = (iso) => iso.slice(0, 16).replace('T', ' ');
const who = (c) => (c.name ? `${c.name} (${c.phone})` : c.phone);

const commands = {
  async login(email, password) {
    if (!email || !password) fail('usage: login <email> <password>');
    const { token: t } = await api('POST', '/api/login', { email, password });
    fs.writeFileSync(TOKEN_FILE, t + '\n', { mode: 0o600 });
    console.log(`Logged in as ${email}. Token saved to ${TOKEN_FILE}`);
  },

  async threads() {
    const list = await api('GET', '/api/threads', null, token());
    if (!list.length) return console.log('No threads.');
    for (const t of list) {
      const unread = t.unread ? ` (${t.unread} unread)` : '';
      const to = t.assignee ? ` -> ${t.assignee.name}` : '';
      console.log(`#${t.id}  [${t.status}]  ${t.contact.name || t.contact.phone}${unread}${to}`);
      console.log(`      ${when(t.last_at)}  ${t.last_text}`);
    }
  },

  async show(id) {
    if (!id) fail('usage: show <id>');
    const t = await api('GET', `/api/threads/${encodeURIComponent(id)}`, null, token());
    console.log(`Thread #${t.id} [${t.status}] with ${who(t.contact)} on ${t.channel}`);
    console.log(`Assigned to: ${t.assignee ? t.assignee.name : 'nobody'}`);
    console.log('-'.repeat(40));
    for (const m of t.messages) {
      console.log(`[${when(m.ts)}] ${m.author}: ${m.text}`);
    }
  },

  async reply(id, ...words) {
    const text = words.join(' ').trim();
    if (!id || !text) fail('usage: reply <id> <text>');
    await api('POST', `/api/threads/${encodeURIComponent(id)}/reply`, { text }, token());
    console.log(`Reply sent to thread #${id}.`);
  },

  async 'saved-replies'() {
    const list = await api('GET', '/api/saved-replies', null, token());
    if (!list.length) return console.log('No saved replies.');
    for (const r of list) console.log(`#${r.id}  ${r.title}\n      ${r.body}`);
  },
};

const [cmd, ...args] = process.argv.slice(2);
if (!commands[cmd]) {
  console.error('usage: tinybox <login|threads|show|reply|saved-replies> [args]');
  process.exit(cmd ? 1 : 0);
}
commands[cmd](...args).catch((e) => fail(e.message));
