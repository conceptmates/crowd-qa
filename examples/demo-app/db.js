const Database = require('better-sqlite3');
const fs = require('fs');
const path = require('path');

const DATA_DIR = path.join(__dirname, 'data');
fs.mkdirSync(DATA_DIR, { recursive: true });

const db = new Database(path.join(DATA_DIR, 'tinybox.db'));
db.pragma('journal_mode = WAL');
db.pragma('foreign_keys = ON');

db.exec(`
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS workspaces (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS members (
  workspace_id INTEGER NOT NULL REFERENCES workspaces(id),
  user_id INTEGER NOT NULL UNIQUE REFERENCES users(id),
  role TEXT NOT NULL CHECK (role IN ('owner','agent','viewer'))
);
CREATE TABLE IF NOT EXISTS invitations (
  id TEXT PRIMARY KEY, workspace_id INTEGER NOT NULL REFERENCES workspaces(id),
  email TEXT NOT NULL, role TEXT NOT NULL, accepted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS channels (
  id INTEGER PRIMARY KEY, workspace_id INTEGER NOT NULL REFERENCES workspaces(id),
  name TEXT NOT NULL, number TEXT NOT NULL UNIQUE, secret TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contacts (
  id INTEGER PRIMARY KEY, workspace_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,
  phone TEXT NOT NULL, name TEXT, UNIQUE (channel_id, phone)
);
CREATE TABLE IF NOT EXISTS threads (
  id INTEGER PRIMARY KEY, workspace_id INTEGER NOT NULL, contact_id INTEGER NOT NULL UNIQUE,
  channel_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'open',
  assignee_id INTEGER REFERENCES users(id), unread INTEGER NOT NULL DEFAULT 0,
  last_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY, thread_id INTEGER NOT NULL REFERENCES threads(id),
  direction TEXT NOT NULL, text TEXT NOT NULL, ts TEXT NOT NULL,
  author_id INTEGER REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS saved_replies (
  id INTEGER PRIMARY KEY, workspace_id INTEGER NOT NULL,
  title TEXT NOT NULL, body TEXT NOT NULL
);
`);

module.exports = { db, DATA_DIR };
