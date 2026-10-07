// Usage: node scripts/connect-fake.js <email> <channelName>
const crypto = require('crypto');
const { db } = require('../db');

const [email, channelName] = process.argv.slice(2);
if (!email || !channelName) {
  console.error('Usage: node scripts/connect-fake.js <email> <channelName>');
  process.exit(1);
}

const row = db.prepare(
  `SELECT w.id, w.name FROM users u
   JOIN members m ON m.user_id = u.id JOIN workspaces w ON w.id = m.workspace_id
   WHERE u.email = ?`
).get(email.trim().toLowerCase());
if (!row) {
  console.error(`No workspace found for ${email}. Sign up and create a workspace first.`);
  process.exit(1);
}

let number;
do {
  number = String(crypto.randomInt(1e9, 1e10));
} while (db.prepare('SELECT 1 FROM channels WHERE number = ?').get(number));
const secret = crypto.randomBytes(20).toString('hex');
db.prepare('INSERT INTO channels (workspace_id, name, number, secret) VALUES (?,?,?,?)')
  .run(row.id, channelName, number, secret);

console.log(JSON.stringify({ workspace: row.name, channel: channelName, number, secret }));
