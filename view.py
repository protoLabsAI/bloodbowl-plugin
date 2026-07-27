"""The console view: a real 26x15 pitch, drawn as a CSS grid with rulers and an
SVG zone overlay.

Four-rules compliant: the page is served PUBLIC at /plugins/bloodbowl/view, all data
comes from the GATED /api/plugins/bloodbowl/* via the DS kit's slug-aware authed
fetch, every asset is prefixed with the slug-derived BASE, and theming comes from the
kit's --pl-* tokens.

Orientation: the pitch is 26 long by 15 wide, drawn with the LONG axis horizontal —
26 columns across, 15 rows down. So the grid column is the pitch's `y` and the grid
row is its `x`.

Sizing note: cell size is measured with a ResizeObserver and published as `--cell`,
so the badge font scales with the BOARD. An earlier version scaled it off `vw`, which
in a rail panel resolved to ~7px and made every player unreadable.
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
  .wrap{padding:10px 12px 16px;max-width:1600px;margin:0 auto}
  .bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px}
  .bar .grow{flex:1}
  label{color:var(--pl-color-fg-muted);font-size:12px;display:inline-flex;gap:4px;align-items:center}
  select,button{font:inherit;color:var(--pl-color-fg);background:var(--pl-color-bg-raised);
    border:1px solid var(--pl-color-border);border-radius:6px;padding:4px 8px}
  button{cursor:pointer}
  button:hover:not(:disabled){background:var(--pl-color-bg-hover)}
  button:disabled{opacity:.45;cursor:default}
  button.primary{background:var(--pl-color-accent);color:var(--pl-color-fg-on-accent);border-color:transparent}
  :focus-visible{outline:2px solid var(--pl-color-focus);outline-offset:1px}
  .mono{font-family:var(--pl-font-mono,ui-monospace,monospace)}
  .muted{color:var(--pl-color-fg-subtle);font-size:11px}

  /* ---- board + rulers ------------------------------------------------- */
  .stage{display:grid;grid-template-columns:auto 1fr;grid-template-rows:auto 1fr;
    gap:2px;align-items:stretch}
  .corner{}
  .ruler{display:grid;color:var(--pl-color-fg-subtle);
    font-size:max(7px,calc(var(--cell,26px) * .34));line-height:1;
    font-family:var(--pl-font-mono,ui-monospace,monospace)}
  .ruler-top{grid-template-columns:repeat(26,1fr)}
  .ruler-left{grid-template-rows:repeat(15,1fr)}
  .ruler div{display:flex;align-items:center;justify-content:center;padding:1px 0}
  .ruler-left div{padding:0 3px}
  .ruler div.hot{color:var(--pl-color-accent-fg);font-weight:700}

  .board-outer{position:relative;border:1px solid var(--pl-color-border-strong);
    border-radius:6px;overflow:hidden;background:var(--pl-color-bg-inset)}
  .board{display:grid;grid-template-columns:repeat(26,1fr);grid-template-rows:repeat(15,1fr);
    aspect-ratio:26/15;width:100%}
  .cell{position:relative;
    border-right:1px solid rgba(150,150,150,.30);
    border-bottom:1px solid rgba(150,150,150,.30)}
  .cell.awayhalf{background:rgba(255,255,255,.055)}
  /* Each End Zone is tinted by WHOSE it is. Tinting both with the accent made
     both ends read as home territory. */
  .cell.ez-home{background:color-mix(in oklab,var(--pl-color-accent) 22%,transparent)}
  .cell.ez-away{background:color-mix(in oklab,var(--pl-color-fg) 14%,transparent)}
  .cell.wide{box-shadow:inset 0 0 0 99px rgba(0,0,0,.22)}
  .cell.xhot,.cell.yhot{background:rgba(255,255,255,.07)}
  .cell.target{outline:2px solid var(--pl-color-focus);outline-offset:-2px;z-index:4}
  .cell.armed{cursor:copy}

  .pc{position:absolute;inset:6%;border-radius:4px;display:flex;align-items:center;
    justify-content:center;font-weight:700;line-height:1;cursor:grab;user-select:none;
    font-size:calc(var(--cell,26px) * .40);
    box-shadow:0 1px 3px rgba(0,0,0,.5)}
  .pc.home{background:var(--pl-color-accent);color:var(--pl-color-fg-on-accent)}
  .pc.away{background:var(--pl-color-fg);color:var(--pl-color-bg)}
  .pc.sel{outline:2px solid var(--pl-color-focus);outline-offset:2px}
  .pc:active{cursor:grabbing}
  .overlay{position:absolute;inset:0;pointer-events:none}
  .ezlab{position:absolute;top:50%;font-size:10px;letter-spacing:.08em;
    color:var(--pl-color-fg-subtle);transform:translateY(-50%);
    writing-mode:vertical-rl;pointer-events:none}

  /* ---- palette + trash ------------------------------------------------ */
  .cols{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px}
  .pal h5{margin:0 0 5px;font-size:11px;color:var(--pl-color-fg-subtle);
    text-transform:uppercase;letter-spacing:.06em}
  .pi{display:flex;justify-content:space-between;gap:8px;align-items:center;
    border:1px solid var(--pl-color-border);border-radius:6px;padding:4px 7px;
    cursor:pointer;background:var(--pl-color-bg-raised);font-size:12px;margin-bottom:4px}
  .pi:hover{border-color:var(--pl-color-border-strong)}
  .pi.armed{border-color:var(--pl-color-accent);background:var(--pl-color-bg-hover)}
  .pi .st{color:var(--pl-color-fg-subtle);font-size:10px;white-space:nowrap}
  #trash{border:1px dashed var(--pl-color-border-strong);border-radius:6px;
    padding:6px 10px;color:var(--pl-color-fg-subtle);font-size:11px}
  #trash.over{border-color:var(--pl-color-status-error);color:var(--pl-color-status-error)}

  /* ---- hover stat card ------------------------------------------------ */
  #card{position:fixed;z-index:60;min-width:196px;max-width:270px;padding:8px 10px;
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
  #err{margin-bottom:8px}
</style>
</head><body><div class="wrap">

<div id="err" class="pl-callout pl-callout--error" hidden></div>

<div class="bar">
  <label>Home <select id="homeTeam"></select></label>
  <label>Away <select id="awayTeam"></select></label>
  <span id="coord" class="mono muted" style="min-width:74px"></span>
  <span class="grow"></span>
  <span id="counts" class="muted"></span>
  <button id="undo" disabled>Undo</button>
  <button id="clearHome">Clear home</button>
  <button id="clearAway">Clear away</button>
  <button id="clearAll">Clear pitch</button>
</div>

<div class="stage">
  <div class="corner"></div>
  <div class="ruler ruler-top" id="rtop"></div>
  <div class="ruler ruler-left" id="rleft"></div>
  <div class="board-outer">
    <div class="board" id="board"></div>
    <svg class="overlay" id="ov" viewBox="0 0 26 15" preserveAspectRatio="none"></svg>
    <div class="ezlab" style="left:2px">HOME END ZONE</div>
    <div class="ezlab" style="right:2px">AWAY END ZONE</div>
  </div>
</div>

<div class="cols">
  <div class="pal" id="palHome"></div>
  <div class="pal" id="palAway"></div>
</div>
<div class="bar" style="margin-top:8px">
  <div id="trash">Drag a player here to remove them</div>
  <span class="muted">Click a position then click a square, or drag. Click a player to select; Delete removes.</span>
</div>

</div>
<div id="card"></div>

<script type="module">
let kit;
try { kit = await import(BASE + "/_ds/plugin-kit.js"); }
catch (e) { kit = { initPluginView(){}, apiFetch: (p,i) => fetch(BASE + p, i) }; }

const API = "/api/plugins/bloodbowl";
const $ = s => document.querySelector(s);
const esc = s => String(s??"").replace(/[&<>"]/g, m => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[m]));

let GEO=null, TEAMS=[], STATE=null, ROSTER={}, CELLS=[], NODES=new Map();
let armed=null, selected=null, dragging=false, undoStack=[];

function fail(e){ const el=$("#err"); el.hidden=false; el.textContent=String(e&&e.message||e); }
function ok(){ $("#err").hidden = true; }

async function api(path, init){
  const r = await kit.apiFetch(API + path, init);
  if (!r.ok){ let d=""; try{ d=(await r.json()).detail||""; }catch{} throw new Error(d || (path+" -> "+r.status)); }
  return r.json();
}
const json = body => ({method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});

/* pitch y (1..26) is the grid COLUMN; pitch x (1..15) is the grid ROW */
const at = (x,y) => CELLS[(x-1)*GEO.length + (y-1)];
const key = (x,y) => x + ":" + y;

function buildBoard(){
  const b=$("#board"); b.innerHTML=""; CELLS=[];
  const wz=GEO.wide_zone_width, ez=GEO.end_zone_depth, los=GEO.los_rows[0];
  for (let x=1; x<=GEO.width; x++){
    for (let y=1; y<=GEO.length; y++){
      const c=document.createElement("div");
      c.className="cell";
      if (y<=ez) c.classList.add("ez-home");
      else if (y>GEO.length-ez) c.classList.add("ez-away");
      else if (y>los) c.classList.add("awayhalf");
      if (x<=wz || x>GEO.width-wz) c.classList.add("wide");
      c.dataset.x=x; c.dataset.y=y;
      c.addEventListener("mouseenter", () => hot(x,y));
      c.addEventListener("dragover", ev => { ev.preventDefault(); c.classList.add("target"); });
      c.addEventListener("dragleave", () => c.classList.remove("target"));
      c.addEventListener("drop", ev => { ev.preventDefault(); c.classList.remove("target"); drop(ev,x,y); });
      c.addEventListener("click", () => cellClick(x,y));
      CELLS.push(c); b.appendChild(c);
    }
  }
  $("#rtop").innerHTML  = Array.from({length:GEO.length}, (_,i)=>`<div data-c="${i+1}">${i+1}</div>`).join("");
  $("#rleft").innerHTML = Array.from({length:GEO.width },(_,i)=>`<div data-r="${i+1}">${i+1}</div>`).join("");
  $("#ov").innerHTML = `
    <line x1="${los}" y1="0" x2="${los}" y2="15" stroke="var(--pl-color-accent)" stroke-width=".10"/>
    <line x1="0" y1="${wz}" x2="26" y2="${wz}" stroke="currentColor" stroke-width=".05" opacity=".5"/>
    <line x1="0" y1="${15-wz}" x2="26" y2="${15-wz}" stroke="currentColor" stroke-width=".05" opacity=".5"/>
    <line x1="${ez}" y1="0" x2="${ez}" y2="15" stroke="currentColor" stroke-width=".05" opacity=".5"/>
    <line x1="${26-ez}" y1="0" x2="${26-ez}" y2="15" stroke="currentColor" stroke-width=".05" opacity=".5"/>`;
  // Publish cell size so type scales with the BOARD, not the viewport.
  new ResizeObserver(es => {
    const w = es[0].contentRect.width;
    document.documentElement.style.setProperty("--cell", (w / GEO.length) + "px");
  }).observe($("#board"));
}

let hotX=0, hotY=0;
function hot(x,y){
  if (x===hotX && y===hotY) return;
  hotX=x; hotY=y;
  for (const c of CELLS) c.classList.remove("xhot","yhot");
  for (let i=1;i<=GEO.length;i++) at(x,i).classList.add("xhot");
  for (let i=1;i<=GEO.width;i++)  at(i,y).classList.add("yhot");
  $("#rtop").querySelectorAll("div").forEach(d=>d.classList.toggle("hot", +d.dataset.c===y));
  $("#rleft").querySelectorAll("div").forEach(d=>d.classList.toggle("hot", +d.dataset.r===x));
  $("#coord").textContent = `(${x},${y})`;
}

/* Incremental render: touch only what changed, so a poll landing mid-interaction
   cannot tear down the element under the cursor. */
function render(){
  const want=new Map();
  for (const p of (STATE.players||[])) want.set(key(p.x,p.y), p);
  for (const [k,node] of NODES) if (!want.has(k)) { node.remove(); NODES.delete(k); }
  for (const [k,p] of want){
    let node=NODES.get(k);
    const sig=[p.side,p.badge,p.position].join("|");
    if (node && node.dataset.sig===sig) continue;
    if (node) node.remove();
    node=document.createElement("div");
    node.className="pc "+(p.side==="home"?"home":"away");
    node.dataset.sig=sig; node.draggable=true;
    node.textContent=p.badge||"?";
    node.title=`${p.position||"player"} (${p.x},${p.y})`;
    node.addEventListener("dragstart", ev=>{ dragging=true;
      ev.dataTransfer.setData("text/plain", JSON.stringify({move:{x:p.x,y:p.y}})); });
    node.addEventListener("dragend", ()=>{ dragging=false; });
    node.addEventListener("mouseenter", ev=>showCard(ev,p));
    node.addEventListener("mousemove", posCard);
    node.addEventListener("mouseleave", hideCard);
    node.addEventListener("click", ev=>{ ev.stopPropagation(); selectPlayer(p); });
    at(p.x,p.y).appendChild(node); NODES.set(k,node);
  }
  const h=(STATE.players||[]).filter(p=>p.side==="home").length;
  const a=(STATE.players||[]).filter(p=>p.side==="away").length;
  $("#counts").textContent=`home ${h} · away ${a}`;
  $("#undo").disabled = undoStack.length===0;
  document.querySelectorAll(".pc.sel").forEach(n=>n.classList.remove("sel"));
  if (selected){ const n=NODES.get(key(selected.x,selected.y)); if (n) n.classList.add("sel"); }
}

function selectPlayer(p){ selected={x:p.x,y:p.y}; armed=null; paintArmed(); render(); }

/* --- mutations (each snapshots for undo) ------------------------------- */
function snapshot(){ undoStack.push(JSON.parse(JSON.stringify(STATE))); if (undoStack.length>50) undoStack.shift(); }
async function commit(fn){ snapshot(); try { STATE=await fn(); ok(); } catch(e){ undoStack.pop(); fail(e); } render(); }

async function drop(ev,x,y){
  let p; try{ p=JSON.parse(ev.dataTransfer.getData("text/plain")); }catch{ return; }
  dragging=false;
  if (p.move) await commit(()=>api("/move", json({from:p.move,to:{x,y}})));
  else await commit(()=>api("/place", json({side:p.side,team:p.team,position:p.position,x,y})));
}

async function cellClick(x,y){
  if (armed) return commit(()=>api("/place", json({...armed,x,y})));
  if (selected){ const s=selected; selected=null;
    if (s.x===x && s.y===y) return render();
    return commit(()=>api("/move", json({from:s,to:{x,y}})));
  }
}

/* --- stat card --------------------------------------------------------- */
function showCard(ev,p){
  const st=[["MA",p.MA],["ST",p.ST],["AG",p.AG],["PA",p.PA],["AV",p.AV]]
    .map(([k,v])=>`<div class="stat"><b>${esc(v||"–")}</b><span>${k}</span></div>`).join("");
  $("#card").innerHTML=`<h4>${esc(p.position||"Player")}</h4>
    <div class="sub">${esc(p.team||"")}${p.role?" · "+esc(p.role):""}${p.cost?" · "+esc(p.cost):""} · (${p.x},${p.y})</div>
    <div class="stats">${st}</div>
    ${(p.skills&&p.skills.length)?`<div class="sk">${p.skills.map(esc).join(" · ")}</div>`:""}`;
  $("#card").style.display="block"; posCard(ev);
}
function posCard(ev){
  const c=$("#card"), pad=14, r=c.getBoundingClientRect();
  let x=ev.clientX+pad, y=ev.clientY+pad;
  if (x+r.width>innerWidth) x=ev.clientX-r.width-pad;
  if (y+r.height>innerHeight) y=ev.clientY-r.height-pad;
  c.style.left=x+"px"; c.style.top=y+"px";
}
function hideCard(){ $("#card").style.display="none"; }

/* --- palette ----------------------------------------------------------- */
function paintArmed(){
  document.querySelectorAll(".pi").forEach(n=>n.classList.toggle("armed",
    !!armed && n.dataset.side===armed.side && n.dataset.pos===armed.position));
  document.querySelectorAll(".cell").forEach(n=>n.classList.toggle("armed", !!armed));
}
function buildPalette(){
  for (const side of ["home","away"]){
    const host=$(side==="home"?"#palHome":"#palAway");
    const team=side==="home"?STATE.home_team:STATE.away_team;
    const r=ROSTER[team];
    host.innerHTML=`<h5>${side} — ${esc(team||"(none)")}</h5>`;
    if (!r) continue;
    for (const p of r.positionals){
      const el=document.createElement("div");
      el.className="pi"; el.draggable=true; el.dataset.side=side; el.dataset.pos=p.position;
      el.innerHTML=`<span>${esc(p.position)}</span><span class="st">${esc(p.MA)}/${esc(p.ST)}/${esc(p.AG)}/${esc(p.AV)} · ${esc(p.cost||"")}</span>`;
      el.addEventListener("dragstart", ev=>{ dragging=true;
        ev.dataTransfer.setData("text/plain", JSON.stringify({side,team,position:p.position})); });
      el.addEventListener("dragend", ()=>{ dragging=false; });
      el.addEventListener("click", ()=>{
        armed = (armed && armed.position===p.position && armed.side===side)
          ? null : {side, team, position:p.position};
        selected=null; paintArmed();
      });
      host.appendChild(el);
    }
  }
  paintArmed();
}

async function ensureRoster(name){
  if (name && !ROSTER[name]) ROSTER[name]=await api("/roster?team="+encodeURIComponent(name));
}

/* --- boot -------------------------------------------------------------- */
async function boot(){
  try{
    const meta=await api("/meta");
    GEO=meta.geometry; TEAMS=meta.teams; STATE=meta.scenario;
    buildBoard();
    for (const id of ["#homeTeam","#awayTeam"]) $(id).innerHTML=TEAMS.map(t=>`<option>${esc(t)}</option>`).join("");
    // Reflect the board; NEVER write to it. Opening a view must not mutate state —
    // an earlier version POSTed both teams on load and stomped the agent's setup.
    if (STATE.home_team) $("#homeTeam").value=STATE.home_team;
    if (STATE.away_team) $("#awayTeam").value=STATE.away_team;
    await ensureRoster(STATE.home_team); await ensureRoster(STATE.away_team);
    buildPalette(); render(); ok();
  }catch(e){ fail(e); }
}

for (const which of ["home","away"]){
  $(which==="home"?"#homeTeam":"#awayTeam").addEventListener("change", async ev=>{
    const name=ev.target.value;
    await commit(async()=>{ await ensureRoster(name); return api("/teams", json({[which+"_team"]:name})); });
    buildPalette();
  });
}
for (const [id,body] of [["#clearAll",{}],["#clearHome",{side:"home"}],["#clearAway",{side:"away"}]]){
  $(id).addEventListener("click", ()=>commit(()=>api("/clear", json(body))));
}
$("#undo").addEventListener("click", async ()=>{
  const prev=undoStack.pop(); if (!prev) return;
  try{ STATE=await api("/replace", json(prev)); ok(); }catch(e){ fail(e); }
  render();
});
document.addEventListener("keydown", ev=>{
  if ((ev.key==="Delete"||ev.key==="Backspace") && selected){
    const s=selected; selected=null; commit(()=>api("/remove", json(s)));
  }
  if (ev.key==="Escape"){ armed=null; selected=null; paintArmed(); render(); }
});
// Removal is an explicit target, not "anywhere off the board" — the old
// document-level handler deleted a player if you dropped on the palette.
const trash=$("#trash");
trash.addEventListener("dragover", ev=>{ ev.preventDefault(); trash.classList.add("over"); });
trash.addEventListener("dragleave", ()=>trash.classList.remove("over"));
trash.addEventListener("drop", async ev=>{
  ev.preventDefault(); trash.classList.remove("over"); dragging=false;
  let p; try{ p=JSON.parse(ev.dataTransfer.getData("text/plain")); }catch{ return; }
  if (p.move) await commit(()=>api("/remove", json(p.move)));
});

// Poll for the agent's edits — but never while the operator is mid-drag, and only
// re-render when something actually changed.
setInterval(async ()=>{
  if (dragging || document.hidden) return;
  try{
    const s=await api("/state");
    if (JSON.stringify(s)===JSON.stringify(STATE)) return;
    const teamsChanged = s.home_team!==STATE.home_team || s.away_team!==STATE.away_team;
    STATE=s;
    if (teamsChanged){
      $("#homeTeam").value=STATE.home_team||$("#homeTeam").value;
      $("#awayTeam").value=STATE.away_team||$("#awayTeam").value;
      await ensureRoster(STATE.home_team); await ensureRoster(STATE.away_team);
      buildPalette();
    }
    render();
  }catch{ /* transient */ }
}, 2500);

let booted=false;
function go(){ if (booted) return; booted=true; boot(); }
kit.initPluginView(go);
setTimeout(go, 800);
</script>
</body></html>
"""
