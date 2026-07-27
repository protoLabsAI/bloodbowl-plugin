"""The console view: a real 26x15 pitch, drawn as a CSS grid with an SVG overlay.

Four-rules compliant (see the protoAgent plugin-views guide): the page is served
PUBLIC at /plugins/bloodbowl/view, all data comes from the GATED
/api/plugins/bloodbowl/* via the DS kit's slug-aware authed fetch, every asset is
prefixed with the slug-derived BASE, and theming comes from the kit's --pl-* tokens
rather than a hand-rolled :root map.

Orientation: the pitch is 26 long by 15 wide, drawn with the LONG axis horizontal —
26 columns across, 15 rows down — which is how a Blood Bowl board is read. So the
grid column is the pitch's `y` and the grid row is its `x`.
"""

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Blood Bowl Pitch</title>
<script>
  var BASE = location.pathname.split("/plugins/")[0];
  (function(){ var l=document.createElement("link"); l.rel="stylesheet";
    l.href=BASE+"/_ds/plugin-kit.css"; document.head.appendChild(l); })();
</script>
<style>
  html,body{margin:0;background:var(--pl-color-bg);color:var(--pl-color-fg);
    font-family:var(--pl-font-sans,system-ui);font-size:13px}
  .wrap{padding:12px 14px;max-width:1400px;margin:0 auto}
  .bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
  .bar .grow{flex:1}
  label{color:var(--pl-color-fg-muted);font-size:12px}
  select,button,input{font:inherit;color:var(--pl-color-fg);
    background:var(--pl-color-bg-raised);border:1px solid var(--pl-color-border);
    border-radius:6px;padding:4px 8px}
  button{cursor:pointer}
  button:hover{background:var(--pl-color-bg-hover)}
  button.primary{background:var(--pl-color-accent);color:var(--pl-color-fg-on-accent);
    border-color:transparent}
  :focus-visible{outline:2px solid var(--pl-color-focus);outline-offset:1px}

  /* ---- board ---------------------------------------------------------- */
  .board-outer{position:relative;border:1px solid var(--pl-color-border-strong);
    border-radius:8px;overflow:hidden;background:var(--pl-color-bg-inset)}
  .board{display:grid;grid-template-columns:repeat(26,1fr);grid-template-rows:repeat(15,1fr);
    aspect-ratio:26/15;width:100%}
  .cell{position:relative;border-right:1px solid rgba(128,128,128,.16);
    border-bottom:1px solid rgba(128,128,128,.16);display:flex;align-items:center;
    justify-content:center}
  .cell.ez{background:color-mix(in oklab,var(--pl-color-accent) 12%,transparent)}
  .cell.wide{background:rgba(128,128,128,.07)}
  .cell.drop{outline:2px solid var(--pl-color-focus);outline-offset:-2px;z-index:3}

  .pc{position:absolute;inset:8%;border-radius:5px;display:flex;align-items:center;
    justify-content:center;font-weight:650;font-size:clamp(7px,.85vw,13px);
    cursor:grab;user-select:none;line-height:1;
    box-shadow:0 1px 2px rgba(0,0,0,.45)}
  .pc.home{background:var(--pl-color-accent);color:var(--pl-color-fg-on-accent)}
  .pc.away{background:var(--pl-color-fg-muted);color:var(--pl-color-bg)}
  .pc:active{cursor:grabbing}

  .overlay{position:absolute;inset:0;pointer-events:none}

  /* ---- hover stat card ------------------------------------------------ */
  #card{position:fixed;z-index:50;min-width:190px;max-width:260px;padding:8px 10px;
    border-radius:8px;background:var(--pl-color-bg-raised);
    border:1px solid var(--pl-color-border-strong);
    box-shadow:var(--pl-shadow-popover,0 8px 28px rgba(0,0,0,.5));display:none}
  #card h4{margin:0 0 2px;font-size:13px}
  #card .sub{color:var(--pl-color-fg-subtle);font-size:11px;margin-bottom:6px}
  #card .stats{display:grid;grid-template-columns:repeat(5,1fr);gap:3px;margin-bottom:6px}
  #card .stat{text-align:center;background:var(--pl-color-bg-subtle);border-radius:4px;padding:3px 0}
  #card .stat b{display:block;font-size:12px}
  #card .stat span{font-size:9px;color:var(--pl-color-fg-subtle);letter-spacing:.04em}
  #card .sk{font-size:11px;color:var(--pl-color-fg-muted)}

  /* ---- palette -------------------------------------------------------- */
  .pal{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}
  .pi{border:1px solid var(--pl-color-border);border-radius:6px;padding:5px 8px;
    cursor:grab;background:var(--pl-color-bg-raised);font-size:12px}
  .pi:hover{border-color:var(--pl-color-border-strong)}
  .pi small{color:var(--pl-color-fg-subtle);margin-left:5px}
  .hint{color:var(--pl-color-fg-subtle);font-size:11px;margin-top:8px}
  #err{margin-bottom:8px}
</style>
</head><body><div class="wrap">

<div id="err" class="pl-callout pl-callout--error" hidden></div>

<div class="bar">
  <label>Home <select id="homeTeam"></select></label>
  <label>Away <select id="awayTeam"></select></label>
  <span class="grow"></span>
  <span id="counts" class="hint" style="margin:0"></span>
  <button id="clearHome">Clear home</button>
  <button id="clearAway">Clear away</button>
  <button id="clearAll" class="primary">Clear pitch</button>
</div>

<div class="board-outer">
  <div class="board" id="board"></div>
  <svg class="overlay" id="ov" viewBox="0 0 26 15" preserveAspectRatio="none"></svg>
</div>

<div class="pal" id="palette"></div>
<div class="hint">Drag a position onto the pitch. Drag a player to move them; drag one off the board to remove. Hover for stats.</div>

</div>
<div id="card"></div>

<script type="module">
let kit;
try { kit = await import(BASE + "/_ds/plugin-kit.js"); }
catch (e) { kit = { initPluginView(){}, apiFetch: (p,i) => fetch(BASE + p, i) }; }

const API = "/api/plugins/bloodbowl";
const $ = s => document.querySelector(s);
const errEl = $("#err");

function fail(e){ errEl.hidden = false; errEl.textContent = String(e && e.message || e); }
function ok(){ errEl.hidden = true; }

let GEO = null, TEAMS = [], STATE = null, ROSTER = {};

async function api(path, init){
  const r = await kit.apiFetch(API + path, init);
  if (!r.ok) throw new Error(path + " -> " + r.status);
  return r.json();
}

/* The pitch's y (length, 26) is the grid COLUMN; its x (width, 15) is the ROW. */
const idx = (x,y) => (x-1) * GEO.width_cols + (y-1);

function buildBoard(){
  GEO.width_cols = GEO.length;                       // 26 columns
  const b = $("#board"); b.innerHTML = "";
  const wz = GEO.wide_zone_width, ez = GEO.end_zone_depth;
  for (let x = 1; x <= GEO.width; x++){
    for (let y = 1; y <= GEO.length; y++){
      const c = document.createElement("div");
      c.className = "cell";
      if (y <= ez || y > GEO.length - ez) c.classList.add("ez");
      else if (x <= wz || x > GEO.width - wz) c.classList.add("wide");
      c.dataset.x = x; c.dataset.y = y;
      c.title = `(${x},${y})`;
      c.addEventListener("dragover", ev => { ev.preventDefault(); c.classList.add("drop"); });
      c.addEventListener("dragleave", () => c.classList.remove("drop"));
      c.addEventListener("drop", ev => { ev.preventDefault(); c.classList.remove("drop"); onDrop(ev, x, y); });
      b.appendChild(c);
    }
  }
  // SVG overlay: zone boundaries + the Line of Scrimmage, in pitch units so the
  // lines can never drift out of step with the grid.
  const los = GEO.los_rows[0];
  $("#ov").innerHTML = `
    <line x1="${los}" y1="0" x2="${los}" y2="15" stroke="var(--pl-color-accent)"
          stroke-width=".08" opacity=".9"/>
    <line x1="0" y1="${wz}" x2="26" y2="${wz}" stroke="currentColor" stroke-width=".05" opacity=".45"/>
    <line x1="0" y1="${15-wz}" x2="26" y2="${15-wz}" stroke="currentColor" stroke-width=".05" opacity=".45"/>
    <line x1="${ez}" y1="0" x2="${ez}" y2="15" stroke="currentColor" stroke-width=".05" opacity=".45"/>
    <line x1="${26-ez}" y1="0" x2="${26-ez}" y2="15" stroke="currentColor" stroke-width=".05" opacity=".45"/>`;
}

function render(){
  document.querySelectorAll(".pc").forEach(n => n.remove());
  const cells = $("#board").children;
  for (const p of (STATE.players || [])){
    const cell = cells[idx(p.x, p.y)];
    if (!cell) continue;
    const el = document.createElement("div");
    el.className = "pc " + (p.side === "home" ? "home" : "away");
    el.textContent = p.badge || "?";
    el.draggable = true;
    el.addEventListener("dragstart", ev => {
      ev.dataTransfer.setData("text/plain", JSON.stringify({move:{x:p.x, y:p.y}}));
    });
    el.addEventListener("mouseenter", ev => showCard(ev, p));
    el.addEventListener("mousemove", ev => posCard(ev));
    el.addEventListener("mouseleave", hideCard);
    cell.appendChild(el);
  }
  const h = (STATE.players||[]).filter(p=>p.side==="home").length;
  const a = (STATE.players||[]).filter(p=>p.side==="away").length;
  $("#counts").textContent = `home ${h} · away ${a}`;
}

function showCard(ev, p){
  const c = $("#card");
  const st = [["MA",p.MA],["ST",p.ST],["AG",p.AG],["PA",p.PA],["AV",p.AV]]
    .map(([k,v]) => `<div class="stat"><b>${v||"–"}</b><span>${k}</span></div>`).join("");
  c.innerHTML = `<h4>${esc(p.position || "Player")}</h4>
    <div class="sub">${esc(p.team||"")}${p.role ? " · " + esc(p.role) : ""}${p.cost ? " · " + esc(p.cost) : ""}</div>
    <div class="stats">${st}</div>
    ${(p.skills&&p.skills.length) ? `<div class="sk">${p.skills.map(esc).join(" · ")}</div>` : ""}`;
  c.style.display = "block"; posCard(ev);
}
function posCard(ev){
  const c = $("#card"), pad = 14;
  let x = ev.clientX + pad, y = ev.clientY + pad;
  const r = c.getBoundingClientRect();
  if (x + r.width > innerWidth) x = ev.clientX - r.width - pad;
  if (y + r.height > innerHeight) y = ev.clientY - r.height - pad;
  c.style.left = x + "px"; c.style.top = y + "px";
}
function hideCard(){ $("#card").style.display = "none"; }
function esc(s){ return String(s??"").replace(/[&<>"]/g, m => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[m])); }

async function onDrop(ev, x, y){
  let payload;
  try { payload = JSON.parse(ev.dataTransfer.getData("text/plain")); } catch { return; }
  try {
    if (payload.move){
      STATE = await api("/move", {method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({from: payload.move, to: {x, y}})});
    } else {
      STATE = await api("/place", {method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({side: payload.side, team: payload.team, position: payload.position, x, y})});
    }
    ok(); render();
  } catch (e) { fail(e); }
}

function buildPalette(){
  const pal = $("#palette"); pal.innerHTML = "";
  for (const side of ["home","away"]){
    const team = side === "home" ? $("#homeTeam").value : $("#awayTeam").value;
    const r = ROSTER[team];
    if (!r) continue;
    const hdr = document.createElement("div");
    hdr.style.cssText = "width:100%;color:var(--pl-color-fg-subtle);font-size:11px;margin-top:4px";
    hdr.textContent = `${side} — ${team}`;
    pal.appendChild(hdr);
    for (const p of r.positionals){
      const el = document.createElement("div");
      el.className = "pi"; el.draggable = true;
      el.innerHTML = `${esc(p.position)}<small>${esc(p.qty||"")} · ${esc(p.cost||"")}</small>`;
      el.addEventListener("dragstart", ev => {
        ev.dataTransfer.setData("text/plain", JSON.stringify({side, team, position: p.position}));
      });
      pal.appendChild(el);
    }
  }
}

async function pickTeam(which){
  const sel = which === "home" ? $("#homeTeam") : $("#awayTeam");
  const name = sel.value;
  if (!ROSTER[name]) ROSTER[name] = await api("/roster?team=" + encodeURIComponent(name));
  STATE = await api("/teams", {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({[which + "_team"]: name})});
  buildPalette(); render();
}

async function boot(){
  try {
    const meta = await api("/meta");
    GEO = meta.geometry; TEAMS = meta.teams; STATE = meta.scenario;
    buildBoard();
    for (const id of ["#homeTeam","#awayTeam"]){
      $(id).innerHTML = TEAMS.map(t => `<option>${esc(t)}</option>`).join("");
    }
    if (STATE.home_team) $("#homeTeam").value = STATE.home_team;
    if (STATE.away_team) $("#awayTeam").value = STATE.away_team;
    else if (TEAMS.length > 1) $("#awayTeam").value = TEAMS[1];
    await pickTeam("home"); await pickTeam("away");
    ok();
  } catch (e) { fail(e); }
}

$("#homeTeam").addEventListener("change", () => pickTeam("home").catch(fail));
$("#awayTeam").addEventListener("change", () => pickTeam("away").catch(fail));
for (const [id, body] of [["#clearAll",{}],["#clearHome",{side:"home"}],["#clearAway",{side:"away"}]]){
  $(id).addEventListener("click", async () => {
    try { STATE = await api("/clear", {method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify(body)}); ok(); render(); } catch(e){ fail(e); }
  });
}
// Dragging a player off the board removes them.
document.addEventListener("dragover", ev => ev.preventDefault());
document.addEventListener("drop", async ev => {
  if (ev.target.closest && ev.target.closest(".board")) return;
  let payload; try { payload = JSON.parse(ev.dataTransfer.getData("text/plain")); } catch { return; }
  if (!payload || !payload.move) return;
  ev.preventDefault();
  try { STATE = await api("/remove", {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify(payload.move)}); ok(); render(); } catch(e){ fail(e); }
});

// Refresh when the agent moves pieces out from under us.
setInterval(async () => {
  try { const s = await api("/state");
    if (JSON.stringify(s) !== JSON.stringify(STATE)) { STATE = s; render(); }
  } catch { /* transient */ }
}, 3000);

let booted = false;
function go(){ if (booted) return; booted = true; boot(); }
kit.initPluginView(go);
setTimeout(go, 800);
</script>
</body></html>
"""
