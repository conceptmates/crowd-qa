// crowd-qa world: the run as a little low-poly town. Reads square/state.json (written by square-view.py) every 5 s.
// Zones: product building (testing), bench (waiting), courthouse (being judged), plaza (posts), arena (bouts),
// workshop (fix jobs), review booth, harbour (PRs as boats), post office (filed bugs), houses (day done).
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CSS2DRenderer, CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";

const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const clip = (s, n) => { s = String(s ?? ""); return s.length > n ? s.slice(0, n - 1) + "…" : s };
const hash = s => { let h = 2166136261; for (const c of String(s)) h = Math.imul(h ^ c.charCodeAt(0), 16777619); return h >>> 0 };
const V = (x, y, z) => new THREE.Vector3(x, y, z);
const POLL_MS = 5000;
const STEPS = [["plan", "Plan"], ["run", "Test"], ["judge", "Judge"], ["file", "File"], ["fix", "Fix"], ["review", "Review"],
  ["recheck", "Recheck"], ["pr", "PR"], ["report", "Report"]];
const ICON = {testing: "💻", waiting: "⏳", judged: "⚖️", done: "🏠", blocked: "⛔", fixing: "🔨", rechecking: "🔁"};
const STATUS_TEXT = {testing: "testing now", waiting: "waiting", judged: "being judged", done: "day done", blocked: "blocked",
  fixing: "fixing", rechecking: "re-testing a fix"};
const threadUrl = t => !t ? "" : /^[a-z][\w+.-]*:\/\//i.test(t) ? t : "t3-thread://v1/" + encodeURIComponent(String(t).replace(/^t3:/i, ""));
const QUEUE_SHOWN = 8;   // queued jobs drawn per station; the rest are only counted
const active = s => ["queued", "running", "open", "active", "fighting", ""].includes(String(s || "").toLowerCase());

// ---------------------------------------------------------------- renderer, camera, lights
let renderer;
try {
  renderer = new THREE.WebGLRenderer({antialias: true, powerPreference: "high-performance"});
} catch (e) {
  location.replace("square.html");
  throw e;
}
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
$("#scene").appendChild(renderer.domElement);
const labels = new CSS2DRenderer();
labels.setSize(innerWidth, innerHeight);
labels.domElement.className = "labels";
$("#scene").appendChild(labels.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color("#bfe3f5");
scene.fog = new THREE.Fog("#bfe3f5", 120, 230);
const camera = new THREE.PerspectiveCamera(38, innerWidth / innerHeight, 0.5, 500);
camera.position.set(8, 66, 86);
const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(4, 0, 4);
controls.enableDamping = true;
controls.maxPolarAngle = 1.22;
controls.minDistance = 18;
controls.maxDistance = 170;
try { const c = JSON.parse(sessionStorage.getItem("world-cam") || "null"); if (c) { camera.position.fromArray(c.p); controls.target.fromArray(c.t) } } catch (e) {}
controls.addEventListener("end", () => sessionStorage.setItem("world-cam", JSON.stringify({p: camera.position.toArray(), t: controls.target.toArray()})));

scene.add(new THREE.HemisphereLight("#ffffff", "#7fb069", 1.6));
const sun = new THREE.DirectionalLight("#fff1dc", 2.2);
sun.position.set(-40, 70, 35);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
Object.assign(sun.shadow.camera, {left: -75, right: 75, top: 60, bottom: -60, near: 10, far: 200});
sun.shadow.bias = -0.0004;
sun.shadow.normalBias = 0.03;
scene.add(sun);

// ---------------------------------------------------------------- shared geometry and materials
const BOX = new THREE.BoxGeometry(1, 1, 1);
const CYL = new THREE.CylinderGeometry(0.5, 0.5, 1, 10);
const CONE4 = new THREE.ConeGeometry(0.72, 1, 4);   // pyramid roof (rotated 45° to sit square on a box)
const CONE6 = new THREE.ConeGeometry(0.5, 1, 6);
const SPHERE = new THREE.SphereGeometry(0.5, 8, 6);
const mats = new Map();
const mat = (color, extra) => {
  const k = color + (extra ? JSON.stringify(extra) : "");
  if (!mats.has(k)) mats.set(k, new THREE.MeshLambertMaterial({color, ...extra}));
  return mats.get(k);
};
function mesh(parent, geo, color, [w, h, d], [x, y, z], {rx = 0, ry = 0, rz = 0, cast = true, extra} = {}) {
  const m = new THREE.Mesh(geo, mat(color, extra));
  m.scale.set(w, h, d);
  m.position.set(x, y, z);
  m.rotation.set(rx, ry, rz);
  m.castShadow = cast;
  m.receiveShadow = true;
  parent.add(m);
  return m;
}
const box = (p, size, pos, o) => mesh(p, BOX, o?.color ?? p.userData.color ?? "#ffffff", size, pos, o);

function label(parent, html, cls, y, center = [0.5, 1]) {
  const el = document.createElement("div");
  el.className = cls;
  el.innerHTML = html;
  const o = new CSS2DObject(el);
  o.position.set(0, y, 0);
  o.center.set(...center);
  parent.add(o);
  return o;
}

// ---------------------------------------------------------------- the town (static, merged per material at the end)
const town = new THREE.Group();
scene.add(town);
const at = (x, z, ry = 0) => { const g = new THREE.Group(); g.position.set(x, 0, z); g.rotation.y = ry; town.add(g); return g };
const C = {grass: "#93d36f", path: "#f1dba6", stone: "#e8decb", water: "#58c4ea", wood: "#b9773e", darkwood: "#8a5a34", white: "#fffaf0",
  glass: "#9ad8f2", ink: "#2b2f3a", red: "#e2574c", blue: "#5865f2", yellow: "#ffcd4d", green: "#5dbb63"};

// ground, water, plaza, paths
mesh(scene, BOX, C.grass, [260, 1, 220], [-20, -0.5, 0], {cast: false});
mesh(scene, BOX, C.water, [160, 0.6, 220], [122, -0.25, 0], {cast: false});
mesh(scene, BOX, "#f6e3b4", [4, 0.62, 220], [42, -0.2, 0], {cast: false});   // beach strip
const ZONES = {};
const plaza = at(0, 0);
mesh(plaza, CYL, C.stone, [24, 0.12, 24], [0, 0.06, 0], {cast: false});
mesh(plaza, CYL, "#d9cdb3", [8, 0.8, 8], [0, 0.4, 0]);
mesh(plaza, CYL, C.water, [6.8, 0.3, 6.8], [0, 0.82, 0], {cast: false});
mesh(plaza, CYL, "#d9cdb3", [1.2, 2.6, 1.2], [0, 1.3, 0]);
mesh(plaza, SPHERE, C.water, [1.8, 0.8, 1.8], [0, 2.7, 0]);
function path(a, b, w = 3) {
  const dx = b.x - a.x, dz = b.z - a.z, len = Math.hypot(dx, dz);
  mesh(scene, BOX, C.path, [w, 0.06, len], [(a.x + b.x) / 2, 0.03, (a.z + b.z) / 2], {ry: Math.atan2(dx, dz), cast: false});
}

// zone helper: door = where paths meet the zone; slot(i) = {pos, yaw}
function zone(key, name, x, z, door, slot, labelY = 10) {
  const g = at(x, z);
  const lbl = label(g, `${name}<small></small>`, "zone", labelY, [0.5, 0.5]);
  ZONES[key] = {key, name, g, door, slot, lbl, x, z};
  path(V(0, 0, 0), door);
  return g;
}

// product building: testing now, characters at kiosks
{
  const g = zone("product", "Product building", 0, -30, V(0, 0, -11), i => {
    const row = Math.floor(i / 10), col = i % 10;
    return {pos: V(-15.75 + col * 3.5, 0, -18.6 + row * 4.6), yaw: Math.PI};
  }, 13);
  box(g, [34, 9, 9], [0, 4.5, -2], {color: "#f7f1e3"});
  box(g, [35, 0.8, 10], [0, 9.2, -2], {color: C.blue});
  box(g, [12, 3, 0.4], [0, 11, 2.6], {color: C.blue});
  box(g, [10.6, 2, 0.5], [0, 11, 2.7], {color: "#ffffff"});
  for (let i = 0; i < 6; i++) box(g, [3, 3, 0.3], [-13 + i * 5.2, 5, 2.55], {color: C.glass});
  box(g, [4, 4.6, 0.3], [0, 2.3, 2.6], {color: "#38404f"});
  for (let i = 0; i < 20; i++) {   // kiosks: a post with a screen
    const row = Math.floor(i / 10), col = i % 10, kx = -15.75 + col * 3.5, kz = 9.8 + row * 4.6;
    box(g, [0.5, 2, 0.5], [kx, 1, kz], {color: "#9aa3b5"});
    box(g, [2, 1.3, 0.25], [kx, 2.2, kz], {color: C.ink, rx: -0.4});
    box(g, [1.7, 1, 0.05], [kx, 2.22, kz + 0.13], {color: "#7fd3ff", rx: -0.4, cast: false});
  }
}
// courthouse: being judged
{
  const g = zone("court", "Courthouse", -30, -16, V(-19, 0, -7), i => ({pos: V(-37 + (i % 6) * 2.8, 0, -11.5 + Math.floor(i / 6) * 2.8), yaw: Math.PI}), 13);
  box(g, [16, 1, 8], [0, 0.5, -3], {color: "#ddd2bb"});
  box(g, [17, 0.5, 9], [0, 0.25, -3], {color: "#cfc3aa"});
  box(g, [14, 6.5, 6], [0, 4.2, -4], {color: "#f1e8d6"});
  for (let i = 0; i < 6; i++) mesh(g, CYL, C.white, [0.9, 6, 0.9], [-6 + i * 2.4, 4, 0.4]);
  box(g, [16, 0.8, 8], [0, 7.8, -3], {color: "#e7dcc5"});
  mesh(g, CONE4, "#c9b98f", [11.5, 3, 5.5], [0, 9.7, -3], {ry: Math.PI / 4});
  box(g, [1.6, 1.2, 0.2], [0, 9.4, 1], {color: C.yellow});
}
// bench: waiting (three seats a bench, seat height matches a seated figure)
{
  const g = zone("bench", "Bench", -32, 8, V(-21, 0, 6), i => {
    const b = Math.floor(i / 3), s = i % 3;
    return {pos: V(-38.5 + (b % 2) * 10 - 2.2 + s * 2.2, 0, 2 + Math.floor(b / 2) * 6), yaw: 0};
  }, 7);
  for (let b = 0; b < 6; b++) {
    const bx = -6.5 + (b % 2) * 10, bz = -6 + Math.floor(b / 2) * 6;
    box(g, [7, 0.3, 1.5], [bx, 0.9, bz - 0.2], {color: C.wood});
    box(g, [7, 1.3, 0.25], [bx, 1.65, bz - 0.95], {color: C.wood});
    for (const lx of [-3.1, 3.1]) box(g, [0.3, 0.9, 1.3], [bx + lx, 0.45, bz - 0.2], {color: C.darkwood});
  }
  ZONES.bench.lbl.position.set(9, 2.4, 1);
  mesh(g, CYL, "#6b4a2b", [0.3, 4.5, 0.3], [5.5, 2.25, -10]);
  box(g, [2.8, 1.4, 0.2], [5.5, 4.2, -10], {color: C.yellow});   // bus-stop sign
}
// houses: day done
const HOUSES = [[-41, 27], [-32, 27], [-23, 27], [-41, 40], [-32, 40], [-23, 40]];
{
  const g = zone("houses", "Houses", 0, 0, V(-17, 0, 22), i => {
    const [hx, hz] = HOUSES[i % HOUSES.length], k = Math.floor(i / HOUSES.length);
    return {pos: V(hx - 2.4 + (k % 3) * 2.4, 0, hz + 4 + (k % 3) * 1.5 + Math.floor(k / 3) * 4.5), yaw: 0.25 - (k % 3) * 0.25};
  });
  ZONES.houses.lbl.position.set(-32, 9.5, 27);
  const walls = ["#f8e6c8", "#ffd8b0", "#dbeeff", "#e9f7d9", "#ffe0ea", "#efe4ff"], roofs = [C.red, "#4a90d9", C.green, "#e08a3c", "#c0577a", "#7b68c9"];
  HOUSES.forEach(([hx, hz], i) => {
    box(g, [6.5, 4.5, 5], [hx, 2.25, hz], {color: walls[i]});
    mesh(g, CONE4, roofs[i], [6.8, 3, 5.4], [hx, 6, hz], {ry: Math.PI / 4});
    box(g, [1.5, 2.8, 0.15], [hx, 1.4, hz + 2.55], {color: C.darkwood});
    box(g, [1.3, 1.1, 0.15], [hx + 2, 2.8, hz + 2.55], {color: C.glass});
    box(g, [0.8, 1.6, 0.8], [hx - 1.7, 6.6, hz - 0.8], {color: "#b0675a"});
  });
  path(V(-17, 0, 22), V(-41, 0, 34), 2.4);
}
// workshop: fix jobs. A lean-to roof over the back only, so the camera sees the workers at their anvils.
{
  const g = zone("workshop", "Workshop", 2, 30, V(2, 0, 22), i => ({pos: V(-4.2 + (i % 4) * 4, 0, 30.2 + Math.floor(i / 4) * 4), yaw: 0}), 9);
  box(g, [17, 0.3, 10], [0, 0.15, 0], {color: "#a98a68"});
  for (const px of [-8, 8]) box(g, [0.5, 7, 0.5], [px, 3.5, -4.4], {color: C.darkwood});
  for (const px of [-8, 8]) box(g, [0.5, 5, 0.5], [px, 2.5, -1.2], {color: C.darkwood});
  box(g, [17.5, 0.5, 4.6], [0, 6, -2.8], {color: "#8a5a3c", rx: -0.38});
  box(g, [16, 6.5, 0.4], [0, 3.25, -4.6], {color: "#c98b4f"});
  box(g, [14, 1.2, 1.2], [0, 0.6, -3.6], {color: "#b07a45"});   // tool bench
  for (let i = 0; i < 5; i++) box(g, [0.3, 1.1, 0.3], [-4 + i * 2, 1.75, -3.6], {color: ["#e2574c", "#5865f2", "#ffcd4d", "#5dbb63", "#9aa3b5"][i]});
  box(g, [5, 1.2, 0.3], [0, 7.6, -4.3], {color: C.yellow});
  for (let i = 0; i < 4; i++) {   // anvils in front of each worker spot
    box(g, [1.4, 0.6, 0.9], [-6.2 + i * 4, 1.25, 1.9], {color: "#4b5260"});
    box(g, [0.6, 0.95, 0.6], [-6.2 + i * 4, 0.48, 1.9], {color: "#3a3f4a"});
  }
}
// review booth: reviewers stand behind a low counter, the striped awning sits behind them
{
  const g = zone("booth", "Review booth", 20, 30, V(17, 0, 21), i => ({pos: V(16.6 + (i % 3) * 3.4, 0, 29.6 + Math.floor(i / 3) * 3.2), yaw: 0}), 9);
  box(g, [11, 1.6, 1.4], [0, 0.8, 1.6], {color: "#ffcf5c"});
  box(g, [11.4, 0.2, 1.8], [0, 1.65, 1.6], {color: "#ffffff"});
  box(g, [11, 6, 0.3], [0, 3, -2.2], {color: "#f4b942"});
  for (const px of [-5.3, 5.3]) box(g, [0.3, 7, 0.3], [px, 3.5, -2], {color: "#d99a1e"});
  for (let i = 0; i < 6; i++) box(g, [1.85, 0.5, 2.4], [-4.6 + i * 1.85, 6.6, -1.4], {color: i % 2 ? "#ffffff" : C.red, rx: 0.35});
}
// arena ring
const ARENA = V(25, 0, 2);
{
  const g = zone("arena", "Arena", ARENA.x, ARENA.z, V(17, 0, 2), () => ({pos: V(ARENA.x, 0.8, ARENA.z), yaw: 0}), 9.5);
  ZONES.arena.lbl.position.set(0, 1.5, 7.5);
  box(g, [11, 0.8, 11], [0, 0.4, 0], {color: "#3d4a5c"});
  box(g, [10.6, 0.06, 10.6], [0, 0.83, 0], {color: "#4a77c9", cast: false});
  for (const [px, pz, c] of [[-5.1, -5.1, C.red], [5.1, 5.1, C.blue], [-5.1, 5.1, C.white], [5.1, -5.1, C.white]]) mesh(g, CYL, c, [0.4, 2.8, 0.4], [px, 2.1, pz]);
  for (const h of [1.8, 2.7]) {
    for (const [px, pz, ry] of [[0, -5.1, 0], [0, 5.1, 0], [-5.1, 0, Math.PI / 2], [5.1, 0, Math.PI / 2]]) box(g, [10.2, 0.08, 0.08], [px, h, pz], {color: "#ffffff", ry, cast: false});
  }
  box(g, [1.8, 2.6, 1.8], [7.4, 1.3, -3.4], {color: "#6b7385"});   // judge's tall chair
  box(g, [2.4, 0.3, 2.4], [7.4, 2.75, -3.4], {color: "#4b5260"});
}
// post office (filed bugs arrive here)
const POST_OFFICE = V(33, 0, -24);
{
  const g = zone("post", "Post office", POST_OFFICE.x, POST_OFFICE.z, V(29, 0, -13), () => ({pos: V(33, 0, -18), yaw: Math.PI}), 9.5);
  box(g, [8, 5, 6], [0, 2.5, -1], {color: "#fbe9de"});
  mesh(g, CONE4, "#c23f33", [8.4, 2.6, 6.4], [0, 6.3, -1], {ry: Math.PI / 4});
  box(g, [1.6, 2.4, 0.2], [0, 1.2, 2.05], {color: "#6b3a2a"});
  box(g, [0.9, 1.4, 0.9], [3, 0.7, 2.8], {color: "#e2574c"});   // post box
  ZONES.post.lbl.element.classList.add("click");
  ZONES.post.lbl.element.addEventListener("click", ev => { ev.stopPropagation(); showPost() });
}
// watchtower: the lookout is fix-watch.py; it stands on the platform while the watch runs
const TOWER = V(34, 0, 16);   // open ground between the arena and the review booth, looking over the harbour
{
  const g = zone("watch", "Watchtower", TOWER.x, TOWER.z, V(26, 0, 12), () => ({pos: V(TOWER.x, 5.6, TOWER.z), yaw: 0}), 11);
  for (const [lx, lz] of [[-1.4, -1.4], [1.4, -1.4], [-1.4, 1.4], [1.4, 1.4]]) box(g, [0.35, 5.4, 0.35], [lx, 2.7, lz], {color: C.darkwood});
  box(g, [3.6, 0.3, 3.6], [0, 5.45, 0], {color: C.wood});
  for (const [w, d, x, z] of [[3.6, 0.15, 0, 1.75], [3.6, 0.15, 0, -1.75], [0.15, 3.6, 1.75, 0], [0.15, 3.6, -1.75, 0]]) box(g, [w, 0.9, d], [x, 6, z], {color: C.wood});
  mesh(g, CONE4, "#2d9cdb", [4.4, 2, 4.4], [0, 9.2, 0], {ry: Math.PI / 4});
  for (const px of [-1.5, 1.5]) box(g, [0.2, 3, 0.2], [px, 7.2, 1.5], {color: C.darkwood});
}
// bank: what each engine's usage limit has left, and what the run has spent
const BANK = V(-8, 0, 17);
{
  const g = zone("bank", "Bank", BANK.x, BANK.z, V(-5, 0, 12), () => ({pos: V(BANK.x, 0, BANK.z + 4), yaw: 0}), 9);
  box(g, [7, 0.5, 5.4], [0, 0.25, 0], {color: C.stone});
  box(g, [6.4, 4, 4.4], [0, 2.5, -0.3], {color: "#f3ead6"});
  for (let i = 0; i < 4; i++) mesh(g, CYL, "#fffaf0", [0.5, 3.8, 0.5], [-2.4 + i * 1.6, 2.4, 2.2]);
  box(g, [7.2, 0.5, 5.6], [0, 4.7, 0], {color: "#e7dcc3"});
  mesh(g, CONE4, "#d4a017", [7.6, 1.6, 5.8], [0, 5.75, 0], {ry: Math.PI / 4});
  box(g, [2.4, 0.9, 0.2], [0, 4.1, 2.75], {color: "#d4a017"});   // sign
  box(g, [1.3, 2.4, 0.15], [0, 1.7, 1.95], {color: C.darkwood});
}
// playground: finished fixers and reviewers play here, every third one naps on a mat; failed jobs sit on the bench
const PLAY = V(12, 0, 52);
const PLAY_SPOTS = [[5, 48.8], [7, 48.8], [18.6, 50.6], [10, 52.2], [14, 52.2], [12, 55], [8.6, 55.4], [15.4, 55.4],
  [3, 52.5], [21, 52.5], [5.5, 56], [18.5, 56]];
{
  const g = zone("playground", "Playground", PLAY.x, PLAY.z, V(12, 0, 22), () => ({pos: V(PLAY.x, 0, PLAY.z), yaw: 0}), 7);
  path(V(12, 0, 22), V(12, 0, 46), 2.4);
  box(g, [24, 0.08, 16], [0, 0.04, 2], {color: "#a8dc84", cast: false});           // lawn
  box(g, [6, 0.35, 4], [0, 0.18, 1], {color: "#f2d48b"});                          // sandbox
  for (const [w, d, x, z] of [[6.3, 0.3, 0, -1.05], [6.3, 0.3, 0, 3.05], [0.3, 4.3, -3.05, 1], [0.3, 4.3, 3.05, 1]]) box(g, [w, 0.5, d], [x, 0.25, z], {color: C.wood});
  for (const x of [-8.6, -4.4]) box(g, [0.3, 4.2, 0.3], [x, 2.1, -4], {color: "#e2574c"});   // swing frame
  box(g, [4.6, 0.3, 0.3], [-6.5, 4.2, -4], {color: "#e2574c"});
  for (const x of [-7.6, -5.4]) { box(g, [0.06, 2.6, 0.06], [x, 2.9, -4], {color: "#555"}); box(g, [0.9, 0.12, 0.5], [x, 1.55, -4], {color: "#5865f2"}) }
  box(g, [0.3, 3.2, 0.3], [5.2, 1.6, -4.6], {color: "#ffcd4d"});                   // slide: ladder, top, chute
  box(g, [0.3, 3.2, 0.3], [6.4, 1.6, -4.6], {color: "#ffcd4d"});
  box(g, [1.6, 0.25, 1.4], [5.8, 3.2, -4], {color: "#ffcd4d"});
  box(g, [1.2, 0.18, 4.6], [5.8, 1.7, -1.6], {color: "#23a55a", rx: 0.62});
  for (let i = 0; i < 7; i++) box(g, [2.2, 0.12, 1.2], [-7.8 + i * 2.6, 0.1, 6.5], {color: ["#ffd8b0", "#dbeeff", "#e9f7d9", "#ffe0ea", "#efe4ff", "#fff4c2", "#d8f3f0"][i]});   // nap mats
  box(g, [5, 0.3, 1.2], [10, 0.75, 4.5], {color: C.wood});                         // bench for the failed
  box(g, [5, 0.9, 0.2], [10, 1.35, 5.05], {color: C.wood});
}
// harbour: a pier; boats are dynamic
const WATER_X = 42;
{
  zone("harbour", "Harbour", 47, 6, V(40, 0, -9), () => ({pos: V(44, 0, 6), yaw: Math.PI / 2}), 5);
  const g = ZONES.harbour.g;
  box(g, [12, 0.4, 3], [0, 0.3, 0], {color: C.wood});
  box(g, [3, 0.4, 24], [5, 0.3, 6], {color: C.wood});
  for (let i = 0; i < 7; i++) mesh(g, CYL, C.darkwood, [0.4, 2, 0.4], [6.6, 0, -4 + i * 4]);
}
// plaza zone: the town crier and the newest posts
ZONES.plaza = {key: "plaza", name: "Plaza", g: plaza, door: V(0, 0, 9), slot: i => ({pos: V(Math.cos(i) * 10, 0, Math.sin(i) * 10), yaw: 0}),
  lbl: label(plaza, "Plaza<small></small>", "zone", 5.5, [0.5, 0.5])};

// trees: two instanced meshes
{
  const spots = [];
  const rnd = (() => { let s = 7; return () => (s = (s * 16807) % 2147483647) / 2147483647 })();
  const clear = (x, z) => Object.values(ZONES).every(zn => Math.hypot(x - (zn.x ?? 0), z - (zn.z ?? 0)) > 15) && Math.hypot(x, z) > 16 && x < 38 &&
    !HOUSES.some(([hx, hz]) => Math.hypot(x - hx, z - hz) < 6);
  for (let i = 0; i < 400 && spots.length < 70; i++) {
    const x = -95 + rnd() * 140, z = -70 + rnd() * 130;
    if (clear(x, z)) spots.push([x, z, 0.8 + rnd() * 0.8]);
  }
  const trunk = new THREE.InstancedMesh(BOX, mat("#8a5a34"), spots.length);
  const leaves = new THREE.InstancedMesh(CONE6, mat("#4fae5a"), spots.length * 2);
  const m4 = new THREE.Matrix4(), q = new THREE.Quaternion();
  spots.forEach(([x, z, s], i) => {
    trunk.setMatrixAt(i, m4.compose(V(x, 0.8 * s, z), q, V(0.6 * s, 1.6 * s, 0.6 * s)));
    leaves.setMatrixAt(2 * i, m4.compose(V(x, 2.9 * s, z), q, V(3.4 * s, 2.8 * s, 3.4 * s)));
    leaves.setMatrixAt(2 * i + 1, m4.compose(V(x, 4.4 * s, z), q, V(2.4 * s, 2.2 * s, 2.4 * s)));
  });
  for (const im of [trunk, leaves]) { im.castShadow = true; im.receiveShadow = true; scene.add(im) }
}

// merge every static mesh in the town into one mesh per material: a few draw calls instead of hundreds
function mergeStatic(root) {
  root.updateMatrixWorld(true);
  const groups = new Map(), drop = [];
  root.traverse(o => {
    if (!o.isMesh) return;
    const k = o.material.uuid + (o.castShadow ? "c" : "");
    if (!groups.has(k)) groups.set(k, {material: o.material, cast: o.castShadow, geos: []});
    const geo = o.geometry.index ? o.geometry.toNonIndexed() : o.geometry.clone();
    geo.applyMatrix4(o.matrixWorld);
    for (const a of Object.keys(geo.attributes)) if (!["position", "normal"].includes(a)) geo.deleteAttribute(a);
    groups.get(k).geos.push(geo);
    drop.push(o);
  });
  drop.forEach(o => o.parent.remove(o));
  for (const {material, cast, geos} of groups.values()) {
    const m = new THREE.Mesh(mergeGeometries(geos), material);
    m.castShadow = cast;
    m.receiveShadow = true;
    m.matrixAutoUpdate = false;
    scene.add(m);
  }
}
mergeStatic(town);

// ---------------------------------------------------------------- figures (characters and helpers)
const SKIN = ["#f5d0b5", "#e8b48f", "#c98e62", "#8d5a3b", "#f1c6a2"].map(c => mat(c));
const HAIR = ["#2b1d14", "#4a3020", "#151515", "#7a4a22", "#c9a35a", "#5b5b5b"].map(c => mat(c));
const PANTS = mat("#3b4559"), EYE = mat("#1b1e26");
const FIG = 1.5;   // figures are drawn at 1.5x so they read next to the buildings
const pickables = [];
{ // the bank opens the usage card
  const hit = new THREE.Mesh(BOX, new THREE.MeshBasicMaterial({visible: false}));
  hit.scale.set(8, 7, 6); hit.position.set(BANK.x, 3.5, BANK.z); hit.userData.bank = true;
  scene.add(hit); pickables.push(hit);
  ZONES.bank.lbl.element.classList.add("click");
  ZONES.bank.lbl.element.addEventListener("click", ev => { ev.stopPropagation(); showBank() });
}
{ // the post office opens the list of filed issues
  const hit = new THREE.Mesh(BOX, new THREE.MeshBasicMaterial({visible: false}));
  hit.scale.set(9, 8, 8); hit.position.set(POST_OFFICE.x, 4, POST_OFFICE.z - 1); hit.userData.post = true;
  scene.add(hit); pickables.push(hit);
}
const lerpAngle = (a, b, t) => { let d = ((b - a + Math.PI) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI) - Math.PI; return a + d * t };

class Figure {
  constructor({id, color, name, npc = false, scale = FIG}) {
    this.id = id;
    const h = hash(id);
    this.g = new THREE.Group();
    this.rig = new THREE.Group();
    this.g.add(this.rig);
    this.g.scale.setScalar(scale);
    const part = (parent, material, [w, hh, d], [x, y, z], cast = true) => {
      const m = new THREE.Mesh(BOX, material);
      m.scale.set(w, hh, d);
      m.position.set(x, y, z);
      m.castShadow = cast;
      m.userData.fig = this;
      parent.add(m);
      pickables.push(m);
      return m;
    };
    const pivot = (x, y) => { const p = new THREE.Group(); p.position.set(x, y, 0); this.rig.add(p); return p };
    const shirt = mat(color), skin = SKIN[h % SKIN.length];
    this.legL = pivot(-0.23, 0.75); part(this.legL, PANTS, [0.36, 0.75, 0.4], [0, -0.375, 0]);
    this.legR = pivot(0.23, 0.75); part(this.legR, PANTS, [0.36, 0.75, 0.4], [0, -0.375, 0]);
    part(this.rig, shirt, [0.96, 0.92, 0.56], [0, 1.2, 0]);
    this.armL = pivot(-0.63, 1.6); part(this.armL, shirt, [0.28, 0.82, 0.3], [0, -0.38, 0]);
    this.armR = pivot(0.63, 1.6); part(this.armR, shirt, [0.28, 0.82, 0.3], [0, -0.38, 0]);
    this.head = pivot(0, 1.66);
    part(this.head, skin, [0.74, 0.74, 0.74], [0, 0.37, 0]);
    part(this.head, HAIR[(h >> 3) % HAIR.length], [0.78, 0.24, 0.78], [0, 0.76, -0.02]);
    part(this.head, EYE, [0.11, 0.13, 0.04], [-0.17, 0.42, 0.38], false);
    part(this.head, EYE, [0.11, 0.13, 0.04], [0.17, 0.42, 0.38], false);
    if (npc) part(this.head, mat("#2b2f3a"), [0.86, 0.12, 0.86], [0, 0.66, 0]);   // helpers wear a cap brim
    this.tag = label(this.g, "", "tag" + (npc ? " npc" : ""), 2.7);
    this.tag.element.addEventListener("click", ev => { ev.stopPropagation(); select(this) });
    const bw = label(this.g, '<div class="bubble"></div>', "bwrap", 3.15);
    this.bubble = bw.element.firstChild;
    this.phase = (h % 628) / 100;
    this.path = [];
    this.speed = 7;
    this.yaw = 0;
    this.faceYaw = null;
    this.anim = "idle";
    this.baseY = 0;
    this.targetY = 0;
    this.queue = [];
    this.bubbleUntil = 0;
    this.override = null;   // arena poses
    this.name = name;
    scene.add(this.g);
  }
  setTag(html) { if (this._tag !== html) { this.tag.element.innerHTML = html; this._tag = html } }
  say(html, secs = 6) { this.bubble.innerHTML = html; this.bubble.classList.add("on"); this.bubbleUntil = clock.elapsedTime + secs }
  walkTo(points, yaw, anim) {
    this.path = points.map(p => p.clone());
    this.faceYaw = yaw;
    this.anim = anim;
  }
  place(pos, yaw, anim) { this.g.position.set(pos.x, 0, pos.z); this.baseY = this.targetY = pos.y || 0; this.path = []; this.yaw = this.faceYaw = yaw; this.g.rotation.y = yaw; this.anim = anim }
  dispose() {
    scene.remove(this.g);
    this.g.traverse(o => { if (o.isMesh) { const i = pickables.indexOf(o); if (i >= 0) pickables.splice(i, 1) } if (o.isCSS2DObject) o.element.remove() });
  }
  update(dt, t) {
    const g = this.g;
    let walking = false;
    if (this.path.length) {
      const tgt = this.path[0], dx = tgt.x - g.position.x, dz = tgt.z - g.position.z, d = Math.hypot(dx, dz), step = this.speed * dt;
      if (d <= step) { g.position.x = tgt.x; g.position.z = tgt.z; this.targetY = tgt.y || 0; this.path.shift() }
      else { g.position.x += dx / d * step; g.position.z += dz / d * step; this.yaw = Math.atan2(dx, dz); walking = true }
      if (this.path.length) walking = true;
    } else if (this.faceYaw != null) this.yaw = this.faceYaw;
    this.baseY += (this.targetY - this.baseY) * Math.min(1, dt * 8);
    g.position.y = this.baseY;
    g.rotation.y = lerpAngle(g.rotation.y, this.yaw, 1 - Math.exp(-10 * dt));
    let lL = 0, lR = 0, aL = 0, aR = 0, zL = 0, zR = 0, y = 0, tilt = 0, fwd = 0, head = 0;
    const s = Math.sin(t * 11 + this.phase);
    const anim = walking ? "walk" : this.override ? this.override.anim : this.anim;
    const p = this.override ? this.override.p : 0;
    switch (anim) {
      case "walk": lL = s * 0.7; lR = -lL; aL = -lL * 0.9; aR = lL * 0.9; y = Math.abs(Math.cos(t * 11 + this.phase)) * 0.1; break;
      case "type": aL = -1.15 + Math.sin(t * 15 + this.phase) * 0.12; aR = -1.15 + Math.sin(t * 15 + this.phase + 2) * 0.12; head = -0.15; break;
      case "sit": lL = lR = -1.5; y = -0.12; aL = aR = -0.35; head = Math.sin(t * 0.7 + this.phase) * 0.3; break;
      case "hammer": aR = -2.6 + Math.abs(Math.sin(t * 5 + this.phase)) * 2.1; aL = -0.7; head = -0.25; break;
      case "inspect": aR = -1.4; aL = -0.5; head = Math.sin(t * 1.4 + this.phase) * 0.6; break;
      case "wait": zL = 0.25; zR = -0.25; y = Math.sin(t * 2 + this.phase) * 0.02; head = Math.sin(t * 0.9 + this.phase) * 0.35; break;
      case "sleep": lL = lR = -1.5; y = -0.12; aL = aR = -0.15; tilt = -0.35; head = 0.5 + Math.sin(t * 0.6 + this.phase) * 0.05; break;
      case "crossed": aL = aR = -1.3; zL = 0.75; zR = -0.75; break;
      case "cheer": aL = aR = -2.9 + Math.sin(t * 8) * 0.15; zL = -0.25; zR = 0.25; y = Math.abs(Math.sin(t * 6)) * 0.35; break;
      case "fight": aL = aR = -1.5; zL = -0.3; zR = 0.3; y = Math.abs(Math.sin(t * 6 + this.phase)) * 0.08; lL = 0.25; lR = -0.25; break;
      case "punch": { const k = Math.sin(Math.min(1, p) * Math.PI); aL = -1.5; aR = -1.5 - k * 0.1; zR = 0.3 - k * 0.3; fwd = k * 0.6; lL = 0.4; lR = -0.3; break }
      case "block": { const k = this.override.hit ? Math.sin(Math.min(1, p) * Math.PI) : 0; aL = aR = -2.5; zL = 0.35; zR = -0.35; fwd = -k * 0.9; tilt = -k * 0.35; break }
      case "fall": { const down = p < 0.15 ? p / 0.15 : p > 0.75 ? Math.max(0, 1 - (p - 0.75) / 0.2) : 1; tilt = -Math.PI / 2 * down; aL = aR = -2.8 * down; break }
      default: y = Math.sin(t * 2 + this.phase) * 0.025; head = Math.sin(t * 0.6 + this.phase) * 0.2;
    }
    this.legL.rotation.x = lL; this.legR.rotation.x = lR;
    this.armL.rotation.set(aL, 0, zL); this.armR.rotation.set(aR, 0, zR);
    this.head.rotation.y = head;
    this.rig.position.set(0, y, fwd);
    this.rig.rotation.x = tilt;
    // bubbles: one at a time, queued posts wait their turn
    if (this.bubbleUntil && t > this.bubbleUntil) { this.bubble.classList.remove("on"); this.bubbleUntil = 0 }
    if (!this.bubbleUntil && this.queue.length) this.say(this.queue.shift(), 6);
  }
}

// a walk between zones: out of the current zone's door, round the plaza ring, in at the target's door
function route(from, fromZone, toZone, slot) {
  if (fromZone === toZone || !ZONES[fromZone] || !ZONES[toZone]) return [slot];
  const a = ZONES[fromZone].door, b = ZONES[toZone].door, r = 13;
  let a0 = Math.atan2(a.z, a.x), a1 = Math.atan2(b.z, b.x), d = ((a1 - a0 + 3 * Math.PI) % (2 * Math.PI)) - Math.PI;
  const pts = [a];
  const n = Math.max(1, Math.ceil(Math.abs(d) / 0.6));
  for (let i = 0; i <= n; i++) { const ang = a0 + d * i / n; pts.push(V(Math.cos(ang) * r, 0, Math.sin(ang) * r)) }
  pts.push(b, slot);
  return pts;
}

// ---------------------------------------------------------------- dynamic things
const clock = new THREE.Clock();
const chars = new Map();     // character id -> Figure
const npcs = new Map();      // "job:<id>", "bout:<party>", "crier", "judge" -> Figure
let S = null, first = true, selected = null;
const seenPosts = new Set(), bugState = new Map();

const crier = new Figure({id: "organiser", color: "#faa61a", name: "Organisers", npc: true});
crier.place(V(5.6, 0, 5.8), 0.7, "idle");
crier.setTag(`<span class="ic">📣</span>Organisers`);
npcs.set("crier", crier);
const judge = new Figure({id: "judge", color: "#1f2430", name: "Judge", npc: true});
judge.place(V(ARENA.x + 7.4, 2.9, ARENA.z - 3.4), -Math.PI / 2 - 0.4, "idle");
judge.setTag(`<span class="ic">⚖️</span>Judge`);
npcs.set("judge", judge);

function charZone(c, bout) {
  if (bout && (bout.a === c.id || bout.b === c.id)) return "arena";
  return {testing: "product", rechecking: "product", waiting: "bench", blocked: "bench", judged: "court", done: "houses", fixing: "workshop"}[c.status] || "bench";
}
const ANIM = {product: "type", bench: "sit", court: "wait", houses: "idle", workshop: "hammer", booth: "inspect"};

function npc(key, opts) {
  let f = npcs.get(key);
  if (!f) { f = new Figure({npc: true, ...opts}); npcs.set(key, f) }
  return f;
}

// arena: one bout on stage (a running one first, else the latest), parties are characters or helper figures
const arena = {bout: null, t0: 0, figs: [null, null]};
function partyFig(id, side, live) {
  if (live && chars.has(id)) return chars.get(id);
  const c = chars.get(id) && chars.get(id).data;
  const f = npc("bout:" + id, {id, color: c ? c.color : ["#e2574c", "#3b6fd8"][side], name: id, npc: !c});
  f.setTag(`<span class="ic">${side ? "🛡️" : "🥊"}</span>${esc(clip(c ? String(c.name).split(" ")[0] : id, 24))}`);
  return f;
}
function setBout(b, live) {
  const key = b ? b.id + ":" + b.rounds.length + ":" + b.winner + ":" + b.status : "";
  for (const k of [...npcs.keys()]) if (k.startsWith("bout:") && (!b || ![b.a, b.b].includes(k.slice(5)) || (live && chars.has(k.slice(5))))) { npcs.get(k).dispose(); npcs.delete(k) }
  for (const f of arena.figs) if (f) f.override = null;
  arena.bout = b;
  arena.figs = [null, null];
  if (!b) { judge.setTag(`<span class="ic">⚖️</span>Judge`); return }
  arena.figs = [partyFig(b.a, 0, live), partyFig(b.b, 1, live)];
  arena.figs.forEach((f, i) => {
    const pos = V(ARENA.x + (i ? 2.2 : -2.2), 0.8, ARENA.z), yaw = i ? -Math.PI / 2 : Math.PI / 2;
    if (!f.data && !f._placed) { f.place(pos, yaw, "fight"); f._placed = true }
  });
  if (key !== arena.key) arena.t0 = clock.elapsedTime;
  arena.key = key;
  judge.setTag(`<span class="ic">⚖️</span>Judge`);
}
const ROUND_S = 3.4, FINALE_S = 5;
function tickArena(t) {
  const b = arena.bout;
  if (!b) return;
  const [fa, fb] = arena.figs;
  if (!fa || !fb || fa.path.length || fb.path.length) return;   // still walking in
  const rounds = b.rounds.length ? b.rounds : [{by: b.a, text: b.subject || "…", severity: "", reply: ""}];
  const done = !!b.winner;
  const total = rounds.length * ROUND_S + (done ? FINALE_S : 0);
  const e = (t - arena.t0) % total, ri = Math.floor(e / ROUND_S);
  [fa, fb].forEach((f, i) => { f.faceYaw = i ? -Math.PI / 2 : Math.PI / 2; f.targetY = 0.8 });
  if (ri < rounds.length) {
    const r = rounds[ri], p = (e % ROUND_S) / ROUND_S;
    const atk = r.by === b.b ? fb : fa, def = atk === fa ? fb : fa;
    const hit = /high|critical|major|strong/i.test(r.severity || "");
    const swing = p > 0.25 && p < 0.6 ? (p - 0.25) / 0.35 : -1;
    atk.override = swing >= 0 ? {anim: "punch", p: swing} : {anim: "fight"};
    def.override = swing >= 0 ? {anim: "block", p: swing, hit} : {anim: "fight"};
    if (arena.shown !== ri + ":" + arena.t0 + ":" + Math.floor((t - arena.t0) / total)) {
      arena.shown = ri + ":" + arena.t0 + ":" + Math.floor((t - arena.t0) / total);
      atk.say(`<span class="k hit">round ${ri + 1}${r.severity ? " · " + esc(r.severity) : ""}</span>${esc(clip(r.text, 140))}`, ROUND_S * 0.55);
      if (r.reply) setTimeout(() => def.say(`<span class="k reply">reply</span>${esc(clip(r.reply, 120))}`, ROUND_S * 0.4), ROUND_S * 550);
    }
  } else {
    const p = (e - rounds.length * ROUND_S) / FINALE_S;
    const win = b.winner === b.b ? fb : fa, lose = win === fa ? fb : fa;
    win.override = {anim: "cheer", p};
    lose.override = {anim: "fall", p};
    if (arena.shown !== "end:" + Math.floor((t - arena.t0) / total)) {
      arena.shown = "end:" + Math.floor((t - arena.t0) / total);
      const sc = Object.entries(b.scores || {}).map(([k, v]) => `${esc(clip(k, 16))} ${esc(typeof v === "object" ? JSON.stringify(v) : v)}`).join(" · ");
      judge.say(`<span class="k judge">verdict</span>${esc(clip(b.winner, 24))} wins${sc ? "<br>" + sc : ""}`, FINALE_S * 0.9);
    }
  }
}

// boats
const boats = new Map();
function boat(pr, i) {
  let b = boats.get(pr.area);
  if (!b) {
    const g = new THREE.Group(), h = hash(pr.area);
    const hullM = mat("#ffffff"), stripe = mat(["#e2574c", "#3b6fd8", "#23a55a", "#f0b232"][h % 4]);
    const add = (geo, m, s, p, r = [0, 0, 0]) => { const x = new THREE.Mesh(geo, m); x.scale.set(...s); x.position.set(...p); x.rotation.set(...r); x.castShadow = true; x.userData.boat = pr.area; g.add(x); pickables.push(x); return x };
    add(BOX, hullM, [1.8, 0.8, 4], [0, 0.4, 0]);
    add(BOX, stripe, [1.84, 0.25, 4.04], [0, 0.55, 0]);
    add(CONE4, hullM, [1.8, 1.4, 0.8], [0, 0.4, 2.4], [Math.PI / 2, Math.PI / 4, 0]);
    add(CYL, mat("#8a5a34"), [0.15, 4, 0.15], [0, 2.6, 0]);
    const sail = add(BOX, mat("#ffffff"), [0.08, 2.8, 2.2], [0, 2.9, -0.3]);
    const flag = add(BOX, mat("#cccccc"), [0.05, 0.5, 0.9], [0, 4.5, -0.45]);
    const lbl = label(g, "", "boatlbl", 5.6);
    lbl.element.addEventListener("click", ev => { ev.stopPropagation(); showBoat(pr.area) });
    scene.add(g);
    b = {g, sail, flag, lbl, pos: V(0, 0, 0), from: V(0, 0, 0), to: V(0, 0, 0), t0: -99, phase: h % 7};
    boats.set(pr.area, b);
  }
  b.pr = pr;
  const st = String(pr.status || "").toLowerCase();
  const out = ["open", "draft", "merged", "ready"].includes(st);
  // docked boats: two staggered columns; at sea: three lanes, each boat its own row, so no two tags meet
  const dock = V(55.2 + (i % 2) * 5, 0, -6 + Math.floor(i / 2) * 4.4 + (i % 2) * 2.2),
    sea = V(st === "merged" ? 80 : 60 + (i % 3) * 7, 0, -24 + i * 4.2);
  const to = out ? sea : dock;
  b.flag.material = mat({open: "#23a55a", draft: "#9aa1b1", merged: "#8957e5", closed: "#e5484d"}[st] || "#cccccc");
  b.sail.material = mat(st === "draft" ? "#e6e8ee" : "#ffffff");
  const rc = pr.recheck || {};
  const stageIc = {fixing: "🔨", reviewing: "🔍", rechecking: "🔁", done: "✅", "needs-human": "⚠️"}[pr.stage] || "";
  b.lbl.element.title = `${pr.area} · ${pr.stage || st || "?"}`;
  b.lbl.element.innerHTML = `${pr.number ? "#" + esc(pr.number) : esc(clip(pr.area, 10))} ${stageIc}${(rc.pass || rc.partial || rc.fail) ? ` · ✅${rc.pass || 0} 🟨${rc.partial || 0} ❌${rc.fail || 0}` : ""}`;
  if (!b.to.equals(to)) {
    if (first) { b.g.position.copy(to); b.from.copy(to) } else b.from.copy(b.g.position);
    b.to.copy(to);
    b.t0 = clock.elapsedTime;
  }
}
function tickBoats(t) {
  for (const b of boats.values()) {
    const k = Math.min(1, (t - b.t0) / 7), e = k * k * (3 - 2 * k);
    b.g.position.lerpVectors(b.from, b.to, e);
    b.g.position.y = Math.sin(t * 1.6 + b.phase) * 0.12 + 0.1;
    b.g.rotation.z = Math.sin(t * 1.2 + b.phase) * 0.05;
    b.g.rotation.y = k < 1 ? Math.atan2(b.to.x - b.from.x, b.to.z - b.from.z) : lerpAngle(b.g.rotation.y, Math.PI / 2, 0.02);
  }
}

// bug sprites
const bugs = [];
const BUGBODY = mat("#3a2a1a"), BUGWING = mat("#ffffff", {transparent: true, opacity: 0.8});
function flyBug(from, to, delay = 0) {
  const g = new THREE.Group();
  const body = new THREE.Mesh(SPHERE, BUGBODY); body.scale.set(0.5, 0.4, 0.75); g.add(body);
  const spots = new THREE.Mesh(SPHERE, mat("#e2574c")); spots.scale.set(0.42, 0.3, 0.5); spots.position.set(0, 0.12, -0.05); g.add(spots);
  const wl = new THREE.Mesh(BOX, BUGWING); wl.scale.set(0.6, 0.04, 0.35); wl.position.set(-0.35, 0.2, 0); g.add(wl);
  const wr = wl.clone(); wr.position.x = 0.35; g.add(wr);
  g.scale.setScalar(2);
  g.visible = false;
  scene.add(g);
  bugs.push({g, wl, wr, from: from.clone().setY(from.y + 3.2), to: to.clone().setY(to.y + 2.5), t0: clock.elapsedTime + delay, dur: 2.6});
}
function tickBugs(t) {
  for (let i = bugs.length - 1; i >= 0; i--) {
    const b = bugs[i], k = (t - b.t0) / b.dur;
    if (k < 0) continue;
    if (k > 1.25) { scene.remove(b.g); bugs.splice(i, 1); continue }
    b.g.visible = true;
    const e = Math.min(1, k);
    b.g.position.lerpVectors(b.from, b.to, e);
    b.g.position.y += Math.sin(e * Math.PI) * 9 + Math.sin(t * 20) * 0.1;
    b.g.lookAt(b.to.x, b.g.position.y, b.to.z);
    b.wl.rotation.z = Math.sin(t * 40) * 0.6; b.wr.rotation.z = -b.wl.rotation.z;
    b.g.scale.setScalar(2 * (k > 1 ? Math.max(0.01, 1 - (k - 1) * 4) : 1));
  }
}

// ---------------------------------------------------------------- applying a state
function stepNow(S) {
  const st = S.run.stage;
  if (st === "done") return STEPS.length;
  if (st !== "run") return Math.max(0, STEPS.findIndex(s => s[0] === st));
  const cs = S.characters;
  return !cs.some(c => ["testing", "waiting", "blocked"].includes(c.status)) && cs.some(c => c.status === "judged") ? 2 : 1;
}
function hud(S) {
  $("#run-name").textContent = S.run.name || "crowd";
  document.title = (S.run.name || "crowd") + " · world";
  $("#updated").textContent = S.run.updated ? "updated " + S.run.updated.slice(11, 16) : "";
  const now = stepNow(S);
  $("#steps").innerHTML = STEPS.map(([k, l], i) => `<li class="${i < now ? "past" : i === now ? "now" : ""}">${l}</li>`).join("");
  const n = s => S.characters.filter(c => c.status === s).length;
  const filed = S.bugs.filter(b => ["filed", "in-pr"].includes(b.state)).length;
  $("#counters").innerHTML = [[n("testing"), "testing"], [S.posts.length, "posts"], [filed, "bugs filed"], [S.prs.length, "PRs"]]
    .map(([v, l]) => `<div class="ctr"><b>${v}</b><span>${l}</span></div>`).join("");
}
function zoneCounts(S, liveBout, bout) {
  const by = {};
  for (const c of S.characters) { const z = charZone(c, liveBout); by[z] = (by[z] || 0) + 1 }
  const jobs = r => {
    const of = st => S.jobs.filter(j => j.role === r && String(j.status || "").toLowerCase() === st).length;
    return [of("running"), of("queued"), of("done")];
  };
  const [fr, fq, fd] = jobs("fix"), [rr, rq, rd] = jobs("review");
  const n = st => S.characters.filter(c => c.status === st).length;
  const sub = {product: `${n("testing")} testing${n("rechecking") ? ` · ${n("rechecking")} re-testing` : ""}`, bench: `${by.bench || 0} waiting`, court: `${by.court || 0} being judged`,
    houses: `${by.houses || 0} done for the day`, bank: Object.entries((S.usage || {}).engines || {}).map(([e, x]) => `${e} ${x.paused ? "💤 paused" : x.primary}`).join(" · ") || "no quota guard",
    playground: (() => {
      const fin = S.jobs.filter(j => ["fix", "review"].includes(j.role) && ["done", "failed"].includes(String(j.status || "").toLowerCase()));
      const bad = fin.filter(j => String(j.status).toLowerCase() === "failed").length, done = fin.length - bad;
      return fin.length ? `${done} done${done > 2 ? ` · ${Math.floor(done / 3)} napping` : ""}${bad ? ` · ${bad} failed` : ""}` : "nobody finished yet";
    })(),
    watch: (S.watch || {}).on ? `${((S.watch || {}).problems || []).length || "no"} open problems` : "off duty",
    workshop: `${fr} fixing${fq ? ` · ${fq} waiting` : ""}${fd ? ` · ${fd} done` : ""}`,
    booth: `${rr} reviewing${rq ? ` · ${rq} waiting` : ""}${rd ? ` · ${rd} done` : ""}`,
    arena: bout ? (bout.winner ? "last bout: " + clip(bout.winner, 16) + " won" : clip(bout.subject || bout.kind || "bout", 26)) : "no bout", harbour: `${S.prs.length} PR${S.prs.length === 1 ? "" : "s"}`,
    post: `${S.bugs.filter(b => ["filed", "in-pr"].includes(b.state)).length} filed`, plaza: `${S.posts.length} posts`};
  for (const [k, z] of Object.entries(ZONES)) { const el = z.lbl.element.querySelector("small"); if (el) el.textContent = sub[k] || "" }

}
function postHtml(p) {
  return `${p.kind !== "post" ? `<span class="k ${esc(p.kind)}">${esc(p.kind.replace("metoo", "me too").replace("cantrepro", "can't repro"))}</span>` : ""}${esc(clip(p.text, 120))}`;
}

function apply(next) {
  if (!next || !next.run) return;
  S = Object.assign({characters: [], posts: [], bugs: [], jobs: [], bouts: [], prs: []}, next);
  hud(S);
  const liveBout = [...S.bouts].reverse().find(b => active(b.status) && !b.winner) || null;
  const bout = liveBout || S.bouts[S.bouts.length - 1] || null;   // a decided bout replays with stand-ins
  // characters
  const ids = new Set(S.characters.map(c => c.id));
  for (const [id, f] of chars) if (!ids.has(id)) { f.dispose(); chars.delete(id) }
  const recheck = {};
  for (const j of S.jobs) if (j.role === "recheck" && j.persona) recheck[j.persona] = j;
  const slots = {};
  S.characters.forEach(c => {
    let f = chars.get(c.id);
    if (!f) { f = new Figure({id: c.id, color: c.color || "#5865f2", name: c.name}); chars.set(c.id, f) }
    f.data = c;
    const rj = recheck[c.id], badge = rj && rj.result ? `<span class="badge ${esc(rj.result)}">${esc(rj.result.toUpperCase())}</span>` : "";
    f.setTag(`<i style="background:${esc(c.color)}"></i>${esc(String(c.name).split(" ")[0])}<span class="ic">${ICON[c.status] || ""}</span>${c.bugs ? `<span class="ic">🐞${c.bugs}</span>` : ""}${badge}`);
    const z = charZone(c, liveBout);
    const i = slots[z] = (slots[z] ?? -1) + 1;
    const key = z + ":" + (z === "arena" ? (c.id === liveBout.a ? 0 : 1) : i);
    if (f.slotKey === key) return;
    let {pos, yaw} = ZONES[z].slot(i);
    if (z === "arena") { const side = c.id === liveBout.a ? 0 : 1; pos = V(ARENA.x + (side ? 2.2 : -2.2), 0.8, ARENA.z); yaw = side ? -Math.PI / 2 : Math.PI / 2 }
    if (first || !f.zone) f.place(pos, yaw, ANIM[z] || "fight");
    else f.walkTo(route(f.g.position, f.zone, z, pos), yaw, ANIM[z] || "fight");
    if (z !== "arena") f.override = null;
    f.zone = z;
    f.slotKey = key;
  });
  // fix / review helpers, and recheck runs for personas that are not characters
  const live = new Set();
  const byRole = {fix: [], review: [], recheck: []};
  for (const j of S.jobs) if (byRole[j.role] && (j.role !== "recheck" || !chars.has(j.persona))) byRole[j.role].push(j);
  // only running jobs stand at the station; queued ones line up in front of it (QUEUE_SHOWN at most, the zone
  // label counts the rest); done and failed jobs leave: their work is a boat in the harbour
  let played = 0, benched = 0;
  const PLAYS = ["cheer", "wait", "sit", "inspect"];
  const playSlot = (n, failed) => {   // finished jobs go to the playground (one counter for fixers and reviewers)
    if (failed) return {pos: V(PLAY.x - 1.8 + (n % 4) * 1.2, 0, PLAY.z + 6.2 + Math.floor(n / 4) * 1.6), yaw: Math.PI, anim: "crossed"};
    if (n % 3 === 2) {                // every third one naps on a mat
      const k = Math.floor(n / 3);
      return {pos: V(PLAY.x - 7.8 + (k % 7) * 2.6, 0, PLAY.z + 8.5 + Math.floor(k / 7) * 1.8), yaw: Math.PI, anim: "sleep"};
    }
    const k = n - Math.floor(n / 3), [x, z] = PLAY_SPOTS[k] || [PLAY.x - 9 + (k % 8) * 2.6, PLAY.z + 11 + Math.floor((k - PLAY_SPOTS.length) / 8) * 2.4];
    return {pos: V(x, 0, z), yaw: (k * 1.3) % (Math.PI * 2), anim: PLAYS[k % PLAYS.length]};
  };
  const queueSlot = (zk, q) => {
    const z = ZONES[zk], row = Math.floor(q / 4), col = q % 4;
    return {pos: V(z.x - 5.1 + col * 3.4, 0, z.z + 7 + row * 3), yaw: Math.PI};
  };
  for (const [role, zk] of [["fix", "workshop"], ["review", "booth"], ["recheck", "product"]]) {
    let w = 0, q = 0;
    byRole[role].forEach(j => {
      const st0 = String(j.status || "").toLowerCase();
      const finished = st0 === "done" || st0 === "failed";
      if (finished && role === "recheck") return;   // a finished re-check shows as a badge on its character
      const queued = st0 === "queued";
      if (queued && q >= QUEUE_SHOWN) return;
      const i = finished ? (st0 === "failed" ? benched++ : played++) : queued ? q++ : w++;
      const k = "job:" + j.id;
      live.add(k);
      const f = npc(k, {id: j.persona || j.id, color: {fix: "#e08a3c", review: "#f4b942", recheck: "#8957e5"}[role], name: j.area});
      f.job = j;
      const st = String(j.status || "").toLowerCase();
      const ic = st === "done" ? (j.result ? {pass: "✅", partial: "🟨", fail: "❌"}[j.result] : "✅") : st === "failed" ? "❌" : st === "queued" ? "⏳" : {fix: "🔨", review: "🔍", recheck: "🔁"}[role];
      // running jobs carry their area; queued and finished ones only an icon (click any figure for its card)
      f.setTag(`<span class="ic">${ic}</span>${queued || finished ? "" : esc(clip(j.area || j.id, 16))}`);
      const idx = role === "recheck" ? 19 - i : i;
      const spot = finished ? playSlot(i, st0 === "failed") : queued && role !== "recheck" ? queueSlot(zk, i) : ZONES[zk].slot(idx);
      const {pos, yaw} = spot;
      const anim = st === "running" ? ANIM[zk] : queued ? "wait" : spot.anim || "idle";
      const key = (finished ? "play:" + (st0 === "failed" ? "b" : "p") : zk + (queued ? ":q" : ":w")) + i;
      if (f.slotKey !== key) { f.place(pos, yaw, anim); f.slotKey = key }
      f.anim = anim;
    });
  }
  for (const k of [...npcs.keys()]) if (k.startsWith("job:") && !live.has(k)) { npcs.get(k).dispose(); npcs.delete(k) }
  { // the lookout
    const w = S.watch || {}, n = (w.problems || []).length;
    const f = npc("watch", {id: "watch", color: "#2d9cdb", name: "Watch"});
    f.watch = true;
    f.setTag(`<span class="ic">${w.on ? (n ? "⚠️" : "👁️") : "💤"}</span>Watch${n ? ` · ${n}` : w.on ? " · all quiet" : " · off"}`);
    if (f.slotKey !== "tower") { const s = ZONES.watch.slot(0); f.place(s.pos, s.yaw, "idle"); f.slotKey = "tower" }
    f.anim = n ? "wait" : "idle";
  }
  // an engine paused at its usage limit: everyone working on it sleeps until it is back
  const eng = (S.usage || {}).engines || {}, asleep = e => !!(eng[e] && eng[e].paused);
  for (const f of chars.values()) {
    const z = f.data && asleep(f.data.engine) && f.data.status === "testing";
    if (z) { f.anim = "sleep"; f.tag.element.classList.add("zz") } else f.tag.element.classList.remove("zz");
  }
  for (const [k, f] of npcs) {
    if (!k.startsWith("job:") || !f.job) continue;
    const e = String(f.job.model || "claude").split(":")[0], z = f.job.status === "running" && asleep(e);
    if (z) f.anim = "sleep";
    f.tag.element.classList.toggle("zz", z || f.anim === "sleep");   // nappers in the playground too
  }
  setBout(bout, bout === liveBout);
  S.prs.forEach((pr, i) => boat(pr, i));
  for (const [area, b] of boats) if (!S.prs.some(p => p.area === area)) { scene.remove(b.g); b.lbl.element.remove(); boats.delete(area) }
  zoneCounts(S, liveBout, bout);
  // new posts pop over their author
  const fresh = S.posts.filter(p => p.id && !seenPosts.has(p.id));
  fresh.forEach(p => seenPosts.add(p.id));
  (first ? fresh.slice(-4) : fresh).forEach(p => {
    const f = chars.get(p.author) || (p.author === "organiser" ? crier : null);
    if (f) f.queue.push(postHtml(p));
  });
  // bugs: found -> courthouse, filed -> post office
  let d = 0;
  for (const b of S.bugs) {
    const prev = bugState.get(b.key);
    bugState.set(b.key, b.state);
    if (first || prev === b.state) continue;
    const rep = chars.get(b.reporter);
    const court = ZONES.court.door, post = POST_OFFICE.clone().setZ(POST_OFFICE.z + 3);
    if (!prev && ["found", "verified"].includes(b.state) && rep) flyBug(rep.g.position, court, (d++) * 0.4);
    else if (["filed", "in-pr"].includes(b.state) && !["filed", "in-pr"].includes(prev)) flyBug(prev ? court : rep ? rep.g.position : court, post, (d++) * 0.4);
    if (d > 8) break;
  }
  if (selected) select(selected, true);
  first = false;
}

// ---------------------------------------------------------------- cards
const card = $("#card");
function openCard(html) { card.querySelector(".body").innerHTML = html; card.hidden = false }
card.querySelector(".x").addEventListener("click", () => { card.hidden = true; select(null) });
function select(f, refresh) {
  if (selected && selected.tag) selected.tag.element.classList.remove("sel");
  selected = f;
  if (!f) return;
  if (f.tag) f.tag.element.classList.add("sel");
  if (f.data) return showChar(f.data);
  if (f.job) return showJob(f.job);
  if (f.watch) return showWatch();
  if (f === judge && arena.bout) return showBout(arena.bout);
  if (f.id && !refresh && S) { const b = S.bouts.find(x => x.a === f.id || x.b === f.id); if (b) return showBout(b) }
  if (f === crier) return openCard(`<h2><i style="background:#faa61a"></i>Organisers</h2><div class="sub">Notices to the whole town</div>` +
    S.posts.filter(p => p.author === "organiser").slice(-5).reverse().map(p => `<div class="post"><span class="t">${esc((p.ts || "").slice(11, 16))}</span>${esc(clip(p.text, 220))}</div>`).join(""));
}
function showChar(c) {
  const mine = S.posts.filter(p => p.author === c.id).slice(-5).reverse();
  const myBugs = S.bugs.filter(b => b.reporter === c.id);
  openCard(`<h2><i style="background:${esc(c.color)}"></i>${esc(c.name)}</h2><div class="sub">${esc(c.role || "character")}${c.surface ? " · " + esc(c.surface) : ""}</div>
    <div class="chips"><span class="chip">${ICON[c.status] || ""} ${esc(STATUS_TEXT[c.status] || c.status)}</span><span class="chip">day ${esc(c.day)}${S.run.days ? " of " + esc(S.run.days) : ""}</span>
    <span class="chip">${esc(c.done)}/${esc(c.total)} scenarios</span><span class="chip">🐞 ${esc(c.bugs)}</span></div>
    ${c.shot ? `<a href="${esc(c.shot)}" target="_blank" rel="noopener"><img class="shot" src="${esc(c.shot)}" alt="latest screenshot"></a>` : ""}
    ${myBugs.length ? `<h3>Bugs</h3>` + myBugs.slice(0, 5).map(b => `<div class="post"><span class="k">${esc(b.state)}</span>${b.url ? `<a href="${esc(b.url)}" target="_blank" rel="noopener">${esc(clip(b.title, 90))}</a>` : esc(clip(b.title, 90))}</div>`).join("") : ""}
    <h3>Last posts</h3>${mine.map(p => `<div class="post"><span class="t">${esc((p.ts || "").slice(11, 16))}</span>${p.kind !== "post" ? `<span class="k">${esc(p.kind)}</span>` : ""}${esc(clip(p.text, 200))}</div>`).join("") || `<div class="post">Nothing posted yet.</div>`}
    <div class="act"><button class="go" data-square="${esc(c.id)}">Open in square</button></div>`);
}
function showJob(j) {
  const url = threadUrl(j.thread);
  openCard(`<h2><i style="background:#e08a3c"></i>${esc(j.role)} · ${esc(j.area || j.id)}</h2><div class="sub">${esc(j.status)}${j.result ? " · " + esc(j.result.toUpperCase()) : ""}</div>
    <div class="kv"><span>Job</span><span>${esc(j.id)}</span><span>Model</span><span>${esc(j.model || "—")}</span><span>Runner</span><span>${esc(j.runner || "—")}</span>
    <span>Persona</span><span>${esc(j.persona || "—")}</span><span>Started</span><span>${esc(j.started || "—")}</span></div>
    <div class="act">${url ? `<a class="go" href="${esc(url)}" target="_blank" rel="noopener">Open thread</a>` : `<button disabled>No thread yet</button>`}</div>`);
}
function showBank() {
  const u = S.usage || {}, eng = u.engines || {};
  const bar = v => { const n = parseFloat(v); return isNaN(n) ? `<span class="t">—</span>` : `<span class="bar"><i style="width:${Math.min(100, n)}%;background:${n >= 90 ? "#e5484d" : n >= 70 ? "#f0b232" : "#23a55a"}"></i></span><span class="t">${n}%</span>` };
  const engHtml = Object.entries(eng).map(([e, x]) => `<h3>${esc(e)}${x.paused ? " · paused, its people are asleep 💤" : ""} · ${esc(x.at || "")}</h3>
    ${x.accounts.map(a => `<div class="acct"><b>${esc(a.name)}${a.name === x.active ? " ●" : ""}</b><span>5 h</span>${bar(a.primary)}<span>week</span>${bar(a.weekly)}</div>`).join("")}`).join("") || `<div class="post">No quota guard is reporting.</div>`;
  const top = [...S.jobs].filter(j => j.tokens || j.usd).sort((a, b) => (b.usd || 0) - (a.usd || 0) || (b.tokens || 0) - (a.tokens || 0)).slice(0, 10);
  openCard(`<h2><i style="background:#d4a017"></i>Bank</h2><div class="sub">$${(u.total_usd || 0).toFixed(2)} of model steps and cli jobs${u.fix_tokens ? ` · ${(u.fix_tokens / 1e6).toFixed(2)}M tokens in subagent jobs` : ""}</div>
    ${engHtml}
    <h3>Spent by step</h3><div class="kv">${Object.entries(u.steps || {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => `<span>${esc(k)}</span><span>$${v.toFixed(2)}</span>`).join("")}${u.fix_usd ? `<span>fix jobs (cli)</span><span>$${u.fix_usd.toFixed(2)}</span>` : ""}</div>
    <h3>Biggest jobs</h3>${top.map(j => `<div class="post"><span class="k">${esc(j.role)}</span>${esc(j.area || j.id)} · ${j.usd ? "$" + j.usd.toFixed(2) : ""}${j.usd && j.tokens ? " · " : ""}${j.tokens ? (j.tokens / 1000).toFixed(0) + "k tokens" : ""}</div>`).join("") || `<div class="post">No job usage recorded yet.</div>`}`);
}
function showWatch() {
  const w = S.watch || {};
  openCard(`<h2><i style="background:#2d9cdb"></i>The watch</h2><div class="sub">${w.on ? "on duty, checks every minute without a model call" : "off duty: fix-watch.py is not running"}</div>
    <h3>Open problems · ${(w.problems || []).length}</h3>${(w.problems || []).map(p => `<div class="post"><span class="k">${esc(p.kind)}</span><span class="t">${esc(p.at)}</span>${esc(clip(p.text, 200))}</div>`).join("") || `<div class="post">All quiet.</div>`}
    <h3>Last reports</h3>${(w.recent || []).slice().reverse().map(l => `<div class="post">${esc(clip(l, 220))}</div>`).join("") || `<div class="post">Nothing reported yet.</div>`}`);
}
function showBoat(area) {
  const b = boats.get(area);
  if (!b) return;
  const pr = b.pr, rc = pr.recheck || {};
  const titles = pr.bugs.map(k => (S.bugs.find(x => x.key === k) || {}).title || k);
  openCard(`<h2><i style="background:#3b6fd8"></i>PR ${pr.number ? "#" + esc(pr.number) : ""} · ${esc(pr.area)}</h2><div class="sub">${esc(pr.status || "not opened")}</div>
    <div class="chips"><span class="chip">✅ ${rc.pass || 0} pass</span><span class="chip">🟨 ${rc.partial || 0} partial</span><span class="chip">❌ ${rc.fail || 0} fail</span></div>
    <h3>Bugs in this PR · ${titles.length}</h3>${titles.map(t => `<div class="post">${esc(clip(t, 120))}</div>`).join("")}
    <div class="act">${pr.url ? `<a class="go" href="${esc(pr.url)}" target="_blank" rel="noopener">Open PR</a>` : `<button disabled>No PR link yet</button>`}</div>`);
}
// the post office: every bug that reached GitHub, newest issue first, drawn like GitHub's own issue list
function showPost() {
  const nm = id => (S.characters.find(c => c.id === id) || {}).name || "";
  const gh = u => { const m = /github\.com\/([^/]+\/[^/]+)\/(issues|pull)\/(\d+)(#issuecomment)?/.exec(u || ""); return m ? {repo: m[1].split("/")[1], n: +m[3], comment: !!m[4]} : null };
  const rows = S.bugs.filter(b => b.url && ["filed", "in-pr"].includes(b.state)).map(b => ({b, g: gh(b.url)})).filter(r => r.g)
    .sort((x, y) => (x.g.comment - y.g.comment) || y.g.n - x.g.n);
  const created = rows.filter(r => !r.g.comment).length;
  const row = ({b, g}) => `<a class="gh" href="${esc(b.url)}" target="_blank" rel="noopener">
      <span class="st ${b.state === "in-pr" ? "pr" : g.comment ? "cm" : "op"}">${b.state === "in-pr" ? "⇄" : g.comment ? "💬" : "◉"}</span>
      <span class="body"><b>${esc(clip(b.title, 110))}</b>
      <span class="meta">${esc(g.repo)} #${g.n}${g.comment ? " · comment" : ""}${nm(b.reporter) ? " · by " + esc(nm(b.reporter)) : ""}${b.area ? " · " + esc(b.area) : ""}</span>
      ${b.severity ? `<span class="lb ${esc(b.severity)}">${esc(b.severity)}</span>` : ""}${b.state === "in-pr" ? `<span class="lb pr">in a PR</span>` : ""}</span></a>`;
  openCard(`<h2><i style="background:#c23f33"></i>Post office</h2><div class="sub">${created} issues filed · ${rows.length - created} comments on existing issues</div>
    ${rows.map(row).join("") || `<div class="post">Nothing filed yet.</div>`}`);
}
function showBout(b) {
  const nm = id => (S.characters.find(c => c.id === id) || {}).name || id;
  openCard(`<h2><i style="background:#e2574c"></i>Bout · ${esc(b.kind || "dispute")}</h2><div class="sub">${esc(nm(b.a))} vs ${esc(nm(b.b))}${b.subject ? " · " + esc(clip(b.subject, 80)) : ""}</div>
    <div class="chips"><span class="chip">${esc(b.status || "?")}</span>${b.winner ? `<span class="chip">🏆 ${esc(nm(b.winner))}</span>` : ""}
    ${Object.entries(b.scores || {}).map(([k, v]) => `<span class="chip">${esc(nm(k))}: ${esc(typeof v === "object" ? JSON.stringify(v) : v)}</span>`).join("")}</div>
    ${b.rounds.map((r, i) => `<div class="round"><b>Round ${i + 1} · ${esc(nm(r.by))}${r.severity ? " · " + esc(r.severity) : ""}</b><div>${esc(clip(r.text, 300))}</div>${r.reply ? `<div style="color:var(--muted)">↪ ${esc(clip(r.reply, 240))}</div>` : ""}</div>`).join("")}`);
}

// ---------------------------------------------------------------- square panel
const frame = $("#sq-frame");
function toggleSquare(open = !document.body.classList.contains("sq-open"), hash = "") {
  if (open && (!frame.src || hash)) frame.src = "square.html" + (hash ? "#" + hash : "");
  document.body.classList.toggle("sq-open", open);
  $("#sq").setAttribute("aria-hidden", String(!open));
  $("#sq-toggle").textContent = open ? "Close square" : "Square";
}
$("#sq-toggle").addEventListener("click", () => toggleSquare());
card.addEventListener("click", ev => { const b = ev.target.closest("[data-square]"); if (b) toggleSquare(true, b.dataset.square) });
addEventListener("keydown", ev => {
  if (ev.target.closest && ev.target.closest("input,select,textarea")) return;
  if (ev.key === "s" || ev.key === "S") toggleSquare();
  if (ev.key === "Escape") { card.hidden = true; select(null) }
});

// ---------------------------------------------------------------- picking
const ray = new THREE.Raycaster(), ptr = new THREE.Vector2();
let down = null;
renderer.domElement.addEventListener("pointerdown", e => { down = [e.clientX, e.clientY] });
renderer.domElement.addEventListener("pointerup", e => {
  if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 5) return;
  ptr.set(e.clientX / innerWidth * 2 - 1, -(e.clientY / innerHeight) * 2 + 1);
  ray.setFromCamera(ptr, camera);
  const hit = ray.intersectObjects(pickables, false)[0];
  if (!hit) { card.hidden = true; select(null); return }
  if (hit.object.userData.boat) showBoat(hit.object.userData.boat);
  else if (hit.object.userData.post) showPost();
  else if (hit.object.userData.bank) showBank();
  else select(hit.object.userData.fig);
});
renderer.domElement.addEventListener("pointermove", e => {
  if (e.buttons) return;
  ptr.set(e.clientX / innerWidth * 2 - 1, -(e.clientY / innerHeight) * 2 + 1);
  ray.setFromCamera(ptr, camera);
  renderer.domElement.style.cursor = ray.intersectObjects(pickables, false).length ? "pointer" : "";
});

// ---------------------------------------------------------------- loading state: state.json, else state.js (works from file://)
function inline() { const el = document.getElementById("state"); try { return el ? JSON.parse(el.textContent) : null } catch (e) { return null } }
async function load() {
  if (location.protocol !== "file:") {
    try { const r = await fetch("state.json?t=" + Date.now(), {cache: "no-store"}); if (r.ok) return await r.json() } catch (e) {}
  }
  return new Promise(res => {
    const s = document.createElement("script");
    s.src = "state.js?t=" + Date.now();
    s.onload = () => { s.remove(); res(window.CROWD_STATE || null) };
    s.onerror = () => { s.remove(); res(null) };
    document.head.appendChild(s);
  });
}
let lastUpdated = "";
async function poll() {
  const st = await load();
  if (st && st.run && st.run.updated !== lastUpdated) { lastUpdated = st.run.updated; apply(st) }
}

// ---------------------------------------------------------------- loop
addEventListener("resize", () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
  labels.setSize(innerWidth, innerHeight);
});
function frameLoop() {
  const dt = Math.min(clock.getDelta(), 0.05), t = clock.elapsedTime;
  for (const f of chars.values()) f.update(dt, t);
  for (const f of npcs.values()) f.update(dt, t);
  tickArena(t);
  tickBoats(t);
  tickBugs(t);
  controls.update();
  renderer.render(scene, camera);
  labels.render(scene, camera);
}

const boot = inline();
if (boot) { lastUpdated = boot.run && boot.run.updated; apply(boot) }
else await poll();
renderer.setAnimationLoop(frameLoop);
window.__worldReady = true;
window.__world = {scene, chars, npcs, boats, apply, renderer};
$("#loading").classList.add("gone");
setInterval(poll, POLL_MS);
