#!/usr/bin/env python3
"""Renders the crowd run as a Discord-style live page: <run>/square/index.html.

  left    channels: #town-square, #notices, #before-outage, then one channel per character with a status dot,
          day and progress bar
  middle  the square as threads (a post plus its me-too / cant-repro / disagree / tip replies), or, in a
          character's channel: their to-do list for today (each scenario and its status), their findings,
          judge verdicts, their latest screenshot (a live view) and their own posts
  right   members grouped by what they are doing now

  square-view.py <run> [--embed]     write once (--embed inlines images as base64, for sharing)
  square-view.py <run> --watch       rewrite every 15 s until killed (the page reloads itself)
"""
import base64
import glob
import json
import os
import re
import sys
import time

ROLE_COLOR = {"designer": "#eb459e", "hacker": "#f23f43", "speedy": "#f0b232", "owner": "#5865f2",
              "agent": "#23a55a", "sales_rep": "#1abc9c", "viewer": "#949ba4", "builder": "#e67e22",
              "customer": "#9b59b6", "agency": "#3498db", "organiser": "#faa61a"}


def read(path, default=None):
    try:
        with open(path) as f:
            return json.load(f) if path.endswith(".json") else f.read()
    except Exception:
        return default


def field(card, name):
    m = re.search(rf"^- \*\*{re.escape(name)}\*\*: *(.*)$", card or "", re.M)
    return m.group(1).strip() if m else ""


def day_scenarios(md, day):
    """[(id, title, tags)] for '## Day <day>' of scenarios.md."""
    if not md:
        return []
    m = re.search(rf"^## Day {day}\b(.*?)(?=^## Day |\Z)", md, re.M | re.S)
    if not m:
        return []
    out = []
    for h in re.finditer(r"^#{3,4} +(S\d+)\s*[—–-]\s*(.*)$", m.group(1), re.M):
        title = h.group(2)
        tags = re.findall(r"\[([a-z-]+)\]", title)
        out.append((h.group(1), re.sub(r"\s*\[[a-z-]+\]", "", title).strip(), tags))
    return out


def src(path, run, embed):
    if not path or not os.path.exists(path):
        return ""
    if embed:
        return "data:image/png;base64," + base64.b64encode(open(path, "rb").read()).decode()
    return os.path.relpath(path, os.path.join(run, "square"))


def collect(run, embed):
    lanes = read(f"{run}/lanes.json", []) or []
    order = [l["id"] for l in lanes]
    deps = {l["id"]: l.get("depends_on", []) for l in lanes}
    people = []
    for cid in order:
        d = f"{run}/lanes/{cid}"
        card = read(f"{d}/card.md", "")
        st = read(f"{d}/state.json", {}) or {}
        rounds = sorted(glob.glob(f"{d}/round*"), key=lambda p: int(re.sub(r"\D", "", os.path.basename(p)) or 0))
        day = int(re.sub(r"\D", "", os.path.basename(rounds[-1]))) if rounds else 1
        rd = f"{d}/round{day}"
        rep = read(f"{rd}/report.json", {}) or {}
        by_id = {s.get("id"): s for s in rep.get("scenarios", [])}
        todo = []
        for sid, title, tags in day_scenarios(read(f"{d}/scenarios.md", ""), day):
            s = by_id.get(sid, {})
            todo.append({"id": sid, "title": title, "tags": tags, "status": s.get("status") or "pending",
                         "note": (s.get("notes") or s.get("actual") or "")[:240]})
        shots = sorted(glob.glob(f"{rd}/shots/*.png"), key=os.path.getmtime)
        verdicts = []
        for r in rounds:
            v = read(f"{r}/verdict.json")
            if v:
                verdicts.append({"day": int(re.sub(r"\D", "", os.path.basename(r))), "coverage": v.get("coverage_score"),
                                 "satisfied": v.get("satisfied"), "in_character": v.get("in_character"),
                                 "verified": [f.get("title") for f in v.get("verified_findings", [])],
                                 "reason": (v.get("reason") or "")[:400]})
        status = st.get("status") or ("queued" if not rounds else "starting")
        if os.path.exists(f"{rd}/DONE"):
            status = "done" if "exit=0" in (read(f"{rd}/DONE", "") or "") else "stopped"
        if status == "done" and verdicts and verdicts[-1]["day"] == day:
            status = "judged"
        elif status == "done":
            status = "judging"
        done_n = sum(1 for t in todo if t["status"] not in ("pending", "not_run"))
        people.append({
            "id": cid, "name": field(card, "Name") or cid, "role": field(card, "Role"),
            "age_city": field(card, "Age / city"), "business": field(card, "Business"),
            "device": field(card, "Device"), "wants": field(card, f"Wants on day {day}"),
            "email": (field(card, "Email") or "").split(",")[0], "depends_on": deps.get(cid, []),
            "status": status, "day": day, "updated": (st.get("updated") or "")[11:16],
            "surface": st.get("surface") or field(card, "Surface"), "device_id": st.get("device") or "",
            "engine": st.get("engine") or "",
            "todo": todo, "done": done_n, "total": len(todo),
            "findings": [{"title": f.get("title", ""), "severity": f.get("severity", ""), "type": f.get("type", "")}
                         for f in rep.get("findings", [])],
            "shots": len(shots), "live": src(shots[-1], run, embed) if shots else "",
            "live_name": os.path.basename(shots[-1]) if shots else "", "verdicts": verdicts,
        })
    feed = []
    for line in open(f"{run}/square/feed.jsonl") if os.path.exists(f"{run}/square/feed.jsonl") else []:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        e["img"] = src(e.get("shot"), run, embed)
        e.pop("shot", None)
        feed.append(e)
    epoch = (read(f"{run}/square/epoch", "") or "").strip()
    fallback = (read(f"{run}/TESTER_FALLBACK", "") or "").strip()
    title = ""
    for line in (read(f"{run}/config.env", "") or "").splitlines():
        if line.startswith("CROWD_TITLE="):
            title = line.split("=", 1)[1].split("#")[0].strip().strip('"')
    return {"people": people, "feed": feed, "epoch": epoch, "quota": fallback, "title": title or os.path.basename(run),
            "stack_down": (read(f"{run}/STACK_DOWN", "") or "").strip(),
            "generated": time.strftime("%H:%M:%S"), "run": os.path.basename(run)}


PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><title>crowd · square</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#313338;--side:#2b2d31;--deep:#1e1f22;--hover:#35373c;--sel:#404249;--text:#dbdee1;--muted:#949ba4;--faint:#6d6f78;
--accent:#5865f2;--green:#23a55a;--yellow:#f0b232;--red:#f23f43;--grey:#80848e}
*{box-sizing:border-box}html,body{margin:0;height:100%;background:var(--bg);color:var(--text);font:15px/1.375 "gg sans","Noto Sans",system-ui,-apple-system,sans-serif}
.app{display:grid;grid-template-columns:260px 1fr 250px;height:100vh}
aside{background:var(--side);overflow-y:auto}
.guild{height:48px;display:flex;align-items:center;padding:0 16px;font-weight:700;box-shadow:0 1px 0 rgba(0,0,0,.25);position:sticky;top:0;background:var(--side);z-index:2}
.cat{color:var(--muted);font-size:12px;font-weight:700;letter-spacing:.02em;text-transform:uppercase;padding:18px 16px 4px}
.ch{display:flex;align-items:center;gap:8px;margin:1px 8px;padding:6px 8px;border-radius:4px;color:var(--muted);cursor:pointer;text-decoration:none}
.ch:hover{background:var(--hover);color:var(--text)}.ch.sel{background:var(--sel);color:#fff}
.ch .hash{font-size:20px;line-height:1;color:var(--faint);width:18px;text-align:center}
.ch .nm{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ch .sub{font-size:11px;color:var(--faint)}
.bar{height:3px;background:var(--deep);border-radius:2px;margin-top:3px;overflow:hidden}.bar i{display:block;height:100%;background:var(--green)}
.badge{background:var(--red);color:#fff;border-radius:8px;font-size:11px;font-weight:700;padding:0 5px}
.dot{width:10px;height:10px;border-radius:50%;flex:none}
.s-running{background:var(--green)}.s-waiting{background:var(--yellow)}.s-judging{background:var(--accent)}.s-judged,.s-done{background:var(--grey)}
.s-queued{border:2px solid var(--faint)}.s-stopped,.s-down{background:var(--red)}.s-starting{background:var(--yellow)}
main{display:flex;flex-direction:column;min-width:0}
.top{height:48px;flex:none;display:flex;align-items:center;gap:10px;padding:0 16px;box-shadow:0 1px 0 rgba(0,0,0,.25)}
.top b{font-size:16px}.top .desc{color:var(--muted);font-size:13px;border-left:1px solid #3f4147;padding-left:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.stats{margin-left:auto;display:flex;gap:14px;color:var(--muted);font-size:12px;white-space:nowrap}
.down{background:var(--red);color:#fff;padding:8px 16px;font-weight:600}
.scroll{flex:1;overflow-y:auto;padding:8px 0 24px}
.msg{display:grid;grid-template-columns:40px 1fr;gap:0 16px;padding:6px 16px 6px 16px;margin-top:10px}
.msg:hover{background:#2e3035}
.av{width:40px;height:40px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;color:#fff;font-size:15px}
.who{font-weight:600;color:#fff;cursor:pointer}.role{font-size:10px;font-weight:700;border-radius:3px;padding:1px 4px;margin-left:6px;vertical-align:2px;color:#fff}
.ts{color:var(--faint);font-size:12px;margin-left:8px}.body{white-space:pre-wrap;word-wrap:break-word}
.screen{color:var(--muted);font-size:12px}
.shot{max-width:min(420px,100%);max-height:260px;border-radius:8px;margin-top:6px;display:block;border:1px solid #232428;cursor:zoom-in}
.thread{margin:6px 0 0;border-left:2px solid #4e5058;padding:2px 0 2px 12px}
.thread summary{cursor:pointer;color:#00a8fc;font-size:13px;font-weight:600;list-style:none}
.reply{display:grid;grid-template-columns:24px 1fr;gap:0 8px;margin-top:8px}.reply .av{width:24px;height:24px;font-size:10px}
.kind{font-size:11px;font-weight:700;border-radius:3px;padding:1px 5px;margin-right:6px}
.k-me-too{background:#1f6f4a;color:#d7ffe9}.k-cant-repro{background:#7a5b14;color:#fff3d1}.k-disagree{background:#7f2d2d;color:#ffe1e1}.k-tip{background:#2d4f86;color:#e1ecff}
.old{opacity:.55}
.panel{margin:12px 16px;background:var(--side);border-radius:8px;padding:14px 16px}
.panel h3{margin:0 0 8px;font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.02em}
.kv{display:grid;grid-template-columns:120px 1fr;gap:4px 12px;font-size:14px}.kv span:nth-child(odd){color:var(--muted)}
.todo{list-style:none;margin:0;padding:0}.todo li{display:flex;gap:10px;padding:5px 0;border-bottom:1px solid #3a3c42}
.todo .ic{width:22px;text-align:center;flex:none}.todo .t{flex:1}.todo .n{color:var(--muted);font-size:12px;margin-top:2px}
.tag{font-size:10px;color:var(--muted);border:1px solid #4e5058;border-radius:3px;padding:0 4px;margin-left:4px}
.sev{font-size:10px;font-weight:700;border-radius:3px;padding:1px 5px;margin-right:6px;color:#fff}
.sev-high,.sev-critical{background:var(--red)}.sev-medium{background:#c27c0e}.sev-low{background:#4e5058}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media (max-width:1100px){.app{grid-template-columns:220px 1fr}aside.members{display:none}.grid2{grid-template-columns:1fr}}
.mem{display:flex;align-items:center;gap:10px;margin:1px 8px;padding:5px 8px;border-radius:4px;cursor:pointer}
.mem:hover{background:var(--hover)}.mem .av{width:32px;height:32px;font-size:12px;position:relative}
.mem .av .dot{position:absolute;right:-2px;bottom:-2px;width:12px;height:12px;border:3px solid var(--side);box-sizing:content-box}
.mem .nm{font-size:14px}.mem .act{font-size:11px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:150px}
.empty{color:var(--muted);padding:24px 16px}
#zoom{position:fixed;inset:0;background:rgba(0,0,0,.85);display:none;align-items:center;justify-content:center;z-index:9}
#zoom img{max-width:94vw;max-height:94vh;border-radius:6px}
</style></head><body><div class="app">
<aside id="channels"></aside><main><div class="top" id="top"></div><div id="banner"></div><div class="scroll" id="view"></div></main>
<aside class="members" id="members"></aside></div><div id="zoom" onclick="this.style.display='none'"><img></div>
<script>
const D = __DATA__;
const P = Object.fromEntries(D.people.map(p => [p.id, p]));
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const color = id => { const p = P[id]; return ROLE[(p && p.role) || (id === "organiser" ? "organiser" : "")] || "#5865f2" };
const ROLE = __ROLES__;
const name = id => (P[id] && P[id].name) || (id === "organiser" ? "Organisers" : id);
const ini = id => name(id).split(/\s+/).slice(0, 2).map(w => w[0]).join("").toUpperCase();
const av = id => `<div class="av" style="background:${color(id)}">${esc(ini(id))}</div>`;
const roleTag = id => { const r = (P[id] && P[id].role) || (id === "organiser" ? "staff" : ""); return r ? `<span class="role" style="background:${color(id)}">${esc(r.replace("_", " "))}</span>` : "" };
const isOld = e => D.epoch && e.ts && e.ts < D.epoch;
const ICON = {pass: "✅", fail: "❌", blocked: "⛔", not_run: "⏭️", pending: "⬜", partial: "🟨", skipped: "⏭️"};
const STLABEL = {running: "testing now", waiting: "waiting", "waiting-capacity": "waiting for a slot", "waiting-browser": "waiting for a browser",
  "waiting-stack": "holding: servers down", "waiting-disk": "waiting: disk", starting: "starting", judging: "being judged", judged: "day judged",
  done: "day finished", stopped: "stopped", queued: "queued"};
const dotCls = s => "s-" + (s.startsWith("waiting") ? (s === "waiting-stack" ? "down" : "waiting") : s);
function img(e) { return e.img ? `<img class="shot" loading="lazy" src="${esc(e.img)}" onclick="zoom(this.src)">` : "" }
function zoom(s) { const z = document.getElementById("zoom"); z.querySelector("img").src = s; z.style.display = "flex" }
function t(ts) { return ts ? ts.slice(11, 16) : "" }

const posts = D.feed.filter(e => !e.ref);
const replies = {}; D.feed.filter(e => e.ref).forEach(e => (replies[e.ref] = replies[e.ref] || []).push(e));

function msg(e) {
  const rs = replies[e.id] || [];
  const th = rs.length ? `<details class="thread" ${rs.length <= 3 ? "open" : ""}><summary>${rs.length} ${rs.length === 1 ? "reply" : "replies"}</summary>` +
    rs.map(r => `<div class="reply ${isOld(r) ? "old" : ""}">${av(r.author)}<div><span class="who" onclick="go('${r.author}')">${esc(name(r.author))}</span><span class="ts">${t(r.ts)}</span>
      <div class="body"><span class="kind k-${esc(r.kind)}">${esc(r.kind.replace("-", " "))}</span>${esc(r.text)}</div>${img(r)}</div></div>`).join("") + `</details>` : "";
  return `<div class="msg ${isOld(e) ? "old" : ""}" id="${esc(e.id)}">${av(e.author)}<div><span class="who" onclick="go('${e.author}')">${esc(name(e.author))}</span>${roleTag(e.author)}
    <span class="ts">${esc(e.id)} · ${t(e.ts)}</span>${e.screen ? `<div class="screen">📍 ${esc(e.screen)}</div>` : ""}<div class="body">${esc(e.text)}</div>${img(e)}${th}</div></div>`;
}

function channels(sel) {
  const ch = (key, label, extra = "", icon = "#") => `<a class="ch ${sel === key ? "sel" : ""}" href="#${key}"><span class="hash">${icon}</span><span class="nm">${label}</span>${extra}</a>`;
  const live = posts.filter(e => !isOld(e) && e.author !== "organiser").length;
  let h = `<div class="guild">${esc(D.title)}</div><div class="cat">Text channels</div>` +
    ch("square", "town-square", live ? `<span class="badge">${live}</span>` : "") + ch("notices", "notices") +
    (D.epoch ? ch("before", "before-outage") : "") + `<div class="cat">Characters — ${D.people.length}</div>`;
  for (const p of D.people) {
    const pct = p.total ? Math.round(100 * p.done / p.total) : 0;
    h += `<a class="ch ${sel === p.id ? "sel" : ""}" href="#${p.id}"><span class="dot ${dotCls(p.status)}"></span><span class="nm">${esc(p.name)}
      <div class="sub">d${p.day} · ${esc(STLABEL[p.status] || p.status)}${p.total ? ` · ${p.done}/${p.total}` : ""}${p.findings.length ? ` · 🐞${p.findings.length}` : ""}</div>
      <div class="bar"><i style="width:${pct}%"></i></div></span></a>`;
  }
  return h;
}

function members() {
  const groups = [["Testing now", p => p.status === "running"], ["Waiting", p => p.status.startsWith("waiting") || p.status === "starting"],
    ["Being judged", p => p.status === "judging"], ["Day done", p => ["judged", "done", "stopped"].includes(p.status)], ["Queued", p => p.status === "queued"]];
  return groups.map(([label, f]) => { const ps = D.people.filter(f); if (!ps.length) return "";
    return `<div class="cat">${label} — ${ps.length}</div>` + ps.map(p => `<div class="mem" onclick="go('${p.id}')"><div class="av" style="background:${color(p.id)}">${esc(ini(p.id))}<span class="dot ${dotCls(p.status)}"></span></div>
      <div><div class="nm">${esc(p.name)}</div><div class="act">${esc(p.surface ? p.surface + " · " : "")}${p.total ? `${p.done}/${p.total} · ` : ""}${esc(p.role)}</div></div></div>`).join("") }).join("");
}

function person(p) {
  const by = s => p.todo.filter(x => x.status === s).length;
  const where = (p.surface || "—") + (p.device_id ? ` (${p.device_id})` : "");
  let h = `<div class="panel"><h3>${esc(p.name)} · ${esc(p.role)} · day ${p.day}</h3><div class="kv">
    <span>Status</span><span><span class="dot ${dotCls(p.status)}" style="display:inline-block;vertical-align:-1px"></span> ${esc(STLABEL[p.status] || p.status)} ${p.updated ? `(since ${esc(p.updated)})` : ""}</span>
    <span>Who</span><span>${esc(p.age_city)} — ${esc(p.business)}</span>
    <span>Today wants</span><span>${esc(p.wants)}</span>
    <span>Surface</span><span>${esc(where)} ${p.engine ? "· " + esc(p.engine) : ""} · ${esc(p.device)}</span>
    <span>Account</span><span>${esc(p.email)}</span>
    ${p.depends_on.length ? `<span>Waits for</span><span>${p.depends_on.map(d => `<a href="#${d}" style="color:#00a8fc">${esc(name(d))}</a>`).join(", ")}</span>` : ""}
    <span>Progress</span><span>${p.done}/${p.total} scenarios · ✅ ${by("pass")} ❌ ${by("fail")} ⛔ ${by("blocked")} · 📸 ${p.shots} · 🐞 ${p.findings.length}</span></div></div>`;
  h += `<div class="grid2"><div class="panel"><h3>To do today</h3><ul class="todo">` + (p.todo.length ? p.todo.map(x =>
    `<li><span class="ic">${ICON[x.status] || "⬜"}</span><div class="t"><b>${esc(x.id)}</b> ${esc(x.title)}${x.tags.map(g => `<span class="tag">${esc(g)}</span>`).join("")}${x.note ? `<div class="n">${esc(x.note)}</div>` : ""}</div></li>`).join("")
    : `<li class="empty">No plan for this day yet.</li>`) + `</ul></div><div>`;
  h += `<div class="panel"><h3>Live view ${p.live_name ? "· " + esc(p.live_name) : ""}</h3>${p.live ? `<img class="shot" style="max-height:340px" src="${esc(p.live)}" onclick="zoom(this.src)">` : `<div class="empty">No screenshot yet.</div>`}</div>`;
  h += `<div class="panel"><h3>Findings today (${p.findings.length})</h3>` + (p.findings.length ? p.findings.map(f => `<div style="margin:4px 0"><span class="sev sev-${esc(f.severity)}">${esc(f.severity)}</span>${esc(f.title)}</div>`).join("") : `<div class="empty" style="padding:4px 0">None yet.</div>`) + `</div>`;
  h += p.verdicts.map(v => `<div class="panel"><h3>Judge · day ${v.day}</h3><div>coverage ${v.coverage ?? "?"} · in character ${v.in_character ?? "?"} · ${v.verified.length} verified · ${v.satisfied ? "satisfied" : "not satisfied"}</div><div class="n" style="color:var(--muted);font-size:13px;margin-top:4px">${esc(v.reason)}</div></div>`).join("");
  h += `</div></div>`;
  const mine = D.feed.filter(e => e.author === p.id).reverse();
  h += `<div class="cat" style="padding-left:16px">Posts by ${esc(p.name)} — ${mine.length}</div>` + (mine.map(e => e.ref ? msg({...e, text: `↪ ${e.kind} on ${e.ref}: ${e.text}`}) : msg(e)).join("") || `<div class="empty">Nothing posted yet.</div>`);
  return h;
}

function go(id) { location.hash = id }
function render() {
  const sel = decodeURIComponent(location.hash.slice(1)) || "square";
  document.getElementById("channels").innerHTML = channels(sel);
  document.getElementById("members").innerHTML = members();
  document.getElementById("banner").innerHTML = D.stack_down ? `<div class="down">⚠ Test servers down: ${esc(D.stack_down)} — testers are holding.</div>` : "";
  const running = D.people.filter(p => p.status === "running").length;
  const bugs = D.people.reduce((a, p) => a + p.findings.length, 0);
  const stats = `<div class="stats"><span>🟢 ${running} testing</span><span>🐞 ${bugs} today</span><span>💬 ${D.feed.length} posts</span><span title="${esc(D.quota)}">⏱ ${esc(D.generated)}</span></div>`;
  let title = "town-square", desc = "Every character's posts, newest first. Replies are threads.", body = "";
  if (sel === "square") body = posts.filter(e => !isOld(e) && e.author !== "organiser").slice().reverse().map(msg).join("") || `<div class="empty">No posts since the restart yet.</div>`;
  else if (sel === "notices") { title = "notices"; desc = "From the organisers"; body = posts.filter(e => e.author === "organiser").slice().reverse().map(msg).join("") }
  else if (sel === "before") { title = "before-outage"; desc = "Posts from before " + D.epoch.slice(11, 16) + ": that world was wiped"; body = posts.filter(isOld).slice().reverse().map(msg).join("") }
  else if (P[sel]) { const p = P[sel]; title = p.name; desc = `${p.role} · ${p.age_city}`; body = person(p) }
  document.getElementById("top").innerHTML = `<span class="hash" style="color:var(--faint);font-size:22px">${P[sel] ? "@" : "#"}</span><b>${esc(title)}</b><span class="desc">${esc(desc)}</span>${stats}`;
  const v = document.getElementById("view"); v.innerHTML = body;
  const key = "scroll:" + sel; v.scrollTop = +sessionStorage.getItem(key) || 0;
  v.onscroll = () => sessionStorage.setItem(key, v.scrollTop);
}
window.addEventListener("hashchange", render); render();
setTimeout(() => location.reload(), 15000);
</script></body></html>"""


def write(run, embed):
    data = collect(run, embed)
    page = PAGE.replace("__DATA__", json.dumps(data).replace("</", "<\\/")).replace("__ROLES__", json.dumps(ROLE_COLOR))
    out = f"{run}/square/index.html"
    tmp = out + ".tmp"
    open(tmp, "w").write(page)
    os.replace(tmp, out)
    return out


if __name__ == "__main__":
    run = sys.argv[1].rstrip("/")
    embed = "--embed" in sys.argv
    while True:
        out = write(run, embed)
        if "--watch" not in sys.argv:
            print(out)
            break
        time.sleep(15)
