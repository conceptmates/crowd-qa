const $app = document.getElementById('app');
const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const S = { user: null, ws: null, tab: 'inbox', filter: 'open', threads: [], open: null, members: [], saved: [], editing: null };
const inviteId = (location.pathname.match(/^\/invite\/([^/]+)/) || [])[1];
const isViewer = () => S.ws && S.ws.role === 'viewer';
const when = (iso) => new Date(iso).toLocaleString();

async function api(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}
const fail = (id, e) => { const el = $(id); if (el) el.textContent = e.message; };

// ---------- boot ----------
async function boot() {
  try {
    const me = await api('GET', '/api/me');
    S.user = me.user; S.ws = me.workspace;
  } catch { S.user = null; S.ws = null; }
  if (inviteId) return renderInvite();
  if (!S.user) return renderAuth(boot);
  if (!S.ws) return renderCreateWs();
  renderMain();
}

// ---------- auth ----------
function renderAuth(onDone, note) {
  let signup = false;
  const draw = () => {
    $app.innerHTML = `<main class="card narrow">
      <h1>${signup ? 'Create your account' : 'Sign in to Tinybox'}</h1>
      ${note ? `<p class="muted">${esc(note)}</p>` : ''}
      <form id="auth">
        ${signup ? '<label>Name<input name="name" required></label>' : ''}
        <label>Email<input name="email" type="email" required></label>
        <label>Password<input name="password" type="password" required></label>
        <div class="error" id="err"></div>
        <button class="primary" type="submit">${signup ? 'Sign up' : 'Sign in'}</button>
        <button type="button" id="swap">${signup ? 'I have an account' : 'Create an account'}</button>
      </form></main>`;
    $('#swap').onclick = () => { signup = !signup; draw(); };
    $('#auth').onsubmit = async (e) => {
      e.preventDefault();
      const f = Object.fromEntries(new FormData(e.target));
      try { await api('POST', signup ? '/api/signup' : '/api/login', f); onDone(); } catch (x) { fail('#err', x); }
    };
  };
  draw();
}

function renderCreateWs() {
  $app.innerHTML = `<main class="card narrow">
    <h1>Create your workspace</h1>
    <p class="muted">Signed in as ${esc(S.user.email)}. A workspace holds your channels, threads and team.</p>
    <form id="ws"><label>Workspace name<input name="name" required></label>
    <div class="error" id="err"></div>
    <button class="primary" type="submit">Create workspace</button>
    <button type="button" id="out">Sign out</button></form></main>`;
  $('#out').onclick = signOut;
  $('#ws').onsubmit = async (e) => {
    e.preventDefault();
    try { await api('POST', '/api/workspaces', { name: e.target.elements.name.value }); boot(); } catch (x) { fail('#err', x); }
  };
}

async function signOut() {
  await api('POST', '/api/logout').catch(() => {});
  location.href = '/';
}

async function renderInvite() {
  let inv;
  try { inv = await api('GET', `/api/invitations/${inviteId}`); } catch (e) {
    $app.innerHTML = `<main class="card narrow"><h1>Invitation</h1><p class="error">${esc(e.message)}</p></main>`;
    return;
  }
  if (!S.user) return renderAuth(boot, `Sign in or sign up as ${inv.email} to join ${inv.workspace}.`);
  $app.innerHTML = `<main class="card narrow"><h1>Join ${esc(inv.workspace)}</h1>
    <p>You are invited as <b>${esc(inv.role)}</b> (${esc(inv.email)}). Signed in as ${esc(S.user.email)}.</p>
    <div class="error" id="err">${inv.accepted ? 'This invitation was already used.' : ''}</div>
    <button class="primary" id="acc" ${inv.accepted ? 'disabled' : ''}>Accept invitation</button>
    <button id="out">Sign out</button></main>`;
  $('#out').onclick = async () => { await api('POST', '/api/logout').catch(() => {}); location.reload(); };
  $('#acc').onclick = async () => {
    try { await api('POST', `/api/invitations/${inviteId}/accept`); location.href = '/'; } catch (x) { fail('#err', x); }
  };
}

// ---------- shell ----------
function renderMain() {
  $app.innerHTML = `<header>
    <span class="brand">Tinybox</span>
    <nav>${['inbox', 'saved', 'settings'].map((t) =>
      `<button data-tab="${t}" class="${S.tab === t ? 'active' : ''}">${{ inbox: 'Inbox', saved: 'Saved replies', settings: 'Settings' }[t]}</button>`).join('')}</nav>
    <span class="spacer"></span>
    <span class="muted">${esc(S.user.name)} &middot; ${esc(S.ws.name)} (${esc(S.ws.role)})</span>
    <button id="out">Sign out</button></header><div id="body"></div>`;
  $('#out').onclick = signOut;
  document.querySelectorAll('[data-tab]').forEach((b) => (b.onclick = () => { S.tab = b.dataset.tab; renderMain(); }));
  ({ inbox: renderInbox, saved: renderSaved, settings: renderSettings })[S.tab]();
}

// ---------- inbox ----------
async function renderInbox() {
  $('#body').innerHTML = `<div class="inbox"><aside>
    <div class="filters">${['open', 'closed', 'all'].map((f) =>
      `<button data-f="${f}" class="${S.filter === f ? 'active' : ''}">${f[0].toUpperCase() + f.slice(1)}</button>`).join('')}</div>
    <ul id="tlist"></ul></aside><section id="tview"><p class="muted">Select a thread.</p></section></div>`;
  document.querySelectorAll('[data-f]').forEach((b) => (b.onclick = () => { S.filter = b.dataset.f; renderInbox(); }));
  [S.members, S.saved] = await Promise.all([api('GET', '/api/members'), api('GET', '/api/saved-replies')]);
  await loadThreads();
  if (S.open) openThread(S.open.id);
}

async function loadThreads() {
  S.threads = await api('GET', '/api/threads');
  const list = $('#tlist');
  if (!list) return;
  const shown = S.threads.filter((t) => S.filter === 'all' || t.status === S.filter);
  list.innerHTML = shown.length ? shown.map((t) => `<li data-id="${t.id}" class="${S.open && S.open.id === t.id ? 'sel' : ''}">
      <div class="row"><b>${esc(t.contact.name || t.contact.phone)}</b>${t.unread ? `<span class="badge">${t.unread}</span>` : ''}</div>
      <div class="last">${esc(t.last_text)}</div>
      <div class="muted">${t.assignee ? 'Assigned to ' + esc(t.assignee.name) : 'Unassigned'}${t.status === 'closed' ? ' &middot; closed' : ''}</div>
    </li>`).join('') : '<li class="muted">No threads here yet.</li>';
  list.querySelectorAll('li[data-id]').forEach((li) => (li.onclick = () => openThread(Number(li.dataset.id))));
}

async function openThread(id) {
  try { S.open = await api('GET', `/api/threads/${id}`); } catch (e) { S.open = null; return; }
  renderThread();
  loadThreads();
}

function renderThread() {
  const t = S.open, v = isViewer();
  const why = 'Viewers cannot reply or assign. Ask an owner to change your role.';
  $('#tview').innerHTML = `
    <div class="thead">
      <h2>${esc(t.contact.name || t.contact.phone)}</h2>
      <span class="muted">${esc(t.contact.phone)} &middot; ${esc(t.channel)}</span>
      <span class="tag">${t.status}</span><span class="spacer" style="flex:1"></span>
      <label>Assignee <select id="assign" ${v ? 'disabled' : ''}>
        <option value="">Unassigned</option>
        ${S.members.map((m) => `<option value="${m.id}" ${t.assignee && t.assignee.id === m.id ? 'selected' : ''}>${esc(m.name)}</option>`).join('')}
      </select></label>
      <button id="toggle" ${v ? 'disabled' : ''}>${t.status === 'open' ? 'Close' : 'Reopen'}</button>
    </div>
    <div class="msgs">${t.messages.map((m) => `<div class="msg ${m.direction}">
      <div class="meta">${esc(m.author || t.contact.phone)} &middot; ${esc(when(m.ts))}</div>${esc(m.text)}</div>`).join('')}</div>
    <div class="reply">
      ${v ? `<div class="notice">${why}</div>` : ''}
      <textarea id="reply" rows="3" placeholder="Write a reply" ${v ? 'disabled' : ''}></textarea>
      <div class="bar">
        <select id="snippet" ${v ? 'disabled' : ''}><option value="">Insert saved reply...</option>
          ${S.saved.map((r) => `<option value="${r.id}">${esc(r.title)}</option>`).join('')}</select>
        <button class="primary" id="send" ${v ? 'disabled' : ''}>Reply</button>
      </div>
      <div class="error" id="terr"></div>
    </div>`;
  const box = $('#tview .msgs');
  box.scrollTop = box.scrollHeight;
  $('#assign').onchange = (e) => patchThread({ assigneeId: e.target.value ? Number(e.target.value) : null });
  $('#toggle').onclick = () => patchThread({ status: t.status === 'open' ? 'closed' : 'open' });
  $('#snippet').onchange = (e) => {
    const r = S.saved.find((x) => x.id === Number(e.target.value));
    if (r) $('#reply').value = r.body;
    e.target.value = '';
  };
  $('#send').onclick = async () => {
    const text = $('#reply').value.trim();
    if (!text) return;
    try {
      await api('POST', `/api/threads/${t.id}/reply`, { text });
      $('#reply').value = '';
      openThread(t.id);
    } catch (e) { fail('#terr', e); }
  };
}

async function patchThread(patch) {
  try { await api('PATCH', `/api/threads/${S.open.id}`, patch); await openThread(S.open.id); } catch (e) { fail('#terr', e); }
}

// ---------- saved replies ----------
async function renderSaved() {
  S.saved = await api('GET', '/api/saved-replies');
  const v = isViewer(), ed = S.saved.find((r) => r.id === S.editing);
  $('#body').innerHTML = `<main class="page"><h1>Saved replies</h1>
    ${v ? '<div class="notice">Viewers can read saved replies but not change them.</div>' : ''}
    <form id="sr" class="card">
      <div class="form-row"><input name="title" placeholder="Title" maxlength="60" value="${esc(ed ? ed.title : '')}" ${v ? 'disabled' : ''} required></div>
      <textarea name="body" rows="4" maxlength="500" placeholder="Reply text" ${v ? 'disabled' : ''} required>${esc(ed ? ed.body : '')}</textarea>
      <div class="form-row"><span class="counter" id="count">0 / 500</span><span style="flex:1"></span>
        ${ed ? '<button type="button" id="cancel">Cancel</button>' : ''}
        <button class="primary" type="submit" ${v ? 'disabled' : ''}>${ed ? 'Save changes' : 'Add saved reply'}</button></div>
      <div class="error" id="err"></div>
    </form>
    ${S.saved.map((r) => `<div class="sr"><b>${esc(r.title)}</b><div>${esc(r.body)}</div>
      <div class="actions"><button data-edit="${r.id}" ${v ? 'disabled' : ''}>Edit</button>
      <button class="danger" data-del="${r.id}" ${v ? 'disabled' : ''}>Delete</button></div></div>`).join('') || '<p class="muted">None yet.</p>'}
    </main>`;
  const f = $('#sr'), count = () => ($('#count').textContent = `${f.elements.body.value.length} / 500`);
  f.elements.body.oninput = count; count();
  if ($('#cancel')) $('#cancel').onclick = () => { S.editing = null; renderSaved(); };
  f.onsubmit = async (e) => {
    e.preventDefault();
    const payload = { title: f.elements.title.value, body: f.elements.body.value };
    try {
      await (ed ? api('PUT', `/api/saved-replies/${ed.id}`, payload) : api('POST', '/api/saved-replies', payload));
      S.editing = null; renderSaved();
    } catch (x) { fail('#err', x); }
  };
  document.querySelectorAll('[data-edit]').forEach((b) => (b.onclick = () => { S.editing = Number(b.dataset.edit); renderSaved(); }));
  document.querySelectorAll('[data-del]').forEach((b) => (b.onclick = async () => {
    await api('DELETE', `/api/saved-replies/${b.dataset.del}`).catch((x) => alert(x.message));
    renderSaved();
  }));
}

// ---------- settings ----------
async function renderSettings() {
  const owner = S.ws.role === 'owner';
  const [members, channels, invites] = await Promise.all([
    api('GET', '/api/members'), api('GET', '/api/channels'), owner ? api('GET', '/api/invitations') : [],
  ]);
  $('#body').innerHTML = `<main class="page"><h1>Settings</h1>
    <h2>Members</h2>
    <table><tr><th>Name</th><th>Email</th><th>Role</th><th></th></tr>
    ${members.map((m) => `<tr><td>${esc(m.name)}</td><td>${esc(m.email)}</td><td>
      ${owner && m.id !== S.user.id
        ? `<select data-role="${m.id}">${['owner', 'agent', 'viewer'].map((r) => `<option ${r === m.role ? 'selected' : ''}>${r}</option>`).join('')}</select>`
        : esc(m.role)}</td>
      <td>${owner && m.id !== S.user.id ? `<button class="danger" data-rm="${m.id}">Remove</button>` : ''}</td></tr>`).join('')}</table>
    ${owner ? `<h2>Invite someone</h2>
      <form id="inv" class="form-row"><input name="email" type="email" placeholder="name@example.com" required>
        <select name="role"><option>agent</option><option>viewer</option></select>
        <button class="primary" type="submit">Send invite</button></form>
      ${invites.map((i) => `<div class="muted">Pending: ${esc(i.email)} as ${esc(i.role)}</div>`).join('')}` : ''}
    <div class="error" id="err"></div>
    <h2>Channels</h2>
    ${channels.map((c) => `<div class="sr"><b>${esc(c.name)}</b> &middot; number ${esc(c.number)}</div>`).join('') || '<p class="muted">No channels connected.</p>'}
    <button id="connect">Connect channel</button></main>`;
  $('#connect').onclick = () => window.open('/provider-unavailable.html', 'connect', 'width=480,height=420');
  const act = (fn) => async (e) => { try { await fn(e); renderSettings(); } catch (x) { fail('#err', x); } };
  document.querySelectorAll('[data-role]').forEach((s) => (s.onchange = act(() =>
    api('PATCH', `/api/members/${s.dataset.role}`, { role: s.value }))));
  document.querySelectorAll('[data-rm]').forEach((b) => (b.onclick = act(() =>
    confirm('Remove this member?') && api('DELETE', `/api/members/${b.dataset.rm}`))));
  if ($('#inv')) $('#inv').onsubmit = (e) => { e.preventDefault(); act(() =>
    api('POST', '/api/invitations', { email: e.target.email.value, role: e.target.role.value }))(); };
}

boot();
setInterval(() => { if (S.ws && S.tab === 'inbox' && $('#tlist')) loadThreads().catch(() => {}); }, 5000);
