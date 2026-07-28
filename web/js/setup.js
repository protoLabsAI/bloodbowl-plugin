/* Setup mode: the practice board, unchanged in behaviour.
 *
 * Permissive by design — an illegal position is a legitimate thing to want while
 * working a shape out, so nothing here blocks. The engine is the strict half, and
 * only once a match has started.
 *
 * The defects this file is shaped around, all found by driving the board rather
 * than reading it: opening the view must never WRITE (an earlier version POSTed
 * both teams on load and stomped the agent's setup); the poller must not rebuild
 * a node mid-drag; and removal is an explicit target, because a document-level
 * drop handler silently deleted anyone dropped on the palette.
 */

import { $, $$, api, esc, fail, json, ok } from "./api.js";
import { at, clearMarks, key } from "./board.js";
import { hideCard, posCard, showCard } from "./card.js";

export const state = {
  scenario: null,
  roster: {},
  nodes: new Map(),
  armed: null,
  selected: null,
  dragging: false,
  undo: [],
};

export function setScenario(sc) {
  state.scenario = sc;
}

function snapshot() {
  state.undo.push(JSON.parse(JSON.stringify(state.scenario)));
  if (state.undo.length > 50) state.undo.shift();
}

async function commit(fn) {
  snapshot();
  try {
    state.scenario = await fn();
    ok();
  } catch (e) {
    state.undo.pop();
    fail(e);
  }
  render();
}

/* Incremental render: touch only what changed, so a poll landing mid-interaction
   cannot tear out the element under the cursor. */
export function render() {
  const sc = state.scenario;
  if (!sc) return;
  const want = new Map();
  for (const p of sc.players || []) want.set(key(p.x, p.y), p);
  for (const [k, node] of state.nodes) {
    if (!want.has(k)) {
      node.remove();
      state.nodes.delete(k);
    }
  }
  for (const [k, p] of want) {
    let node = state.nodes.get(k);
    const sig = [p.side, p.badge, p.position].join("|");
    if (node && node.dataset.sig === sig) continue;
    if (node) node.remove();
    node = document.createElement("div");
    node.className = `pc ${p.side === "home" ? "home" : "away"}`;
    node.dataset.sig = sig;
    node.draggable = true;
    node.textContent = p.badge || "?";
    node.title = `${p.position || "player"} (${p.x},${p.y})`;
    node.addEventListener("dragstart", (ev) => {
      state.dragging = true;
      ev.dataTransfer.setData("text/plain", JSON.stringify({ move: { x: p.x, y: p.y } }));
    });
    node.addEventListener("dragend", () => {
      state.dragging = false;
    });
    node.addEventListener("mouseenter", (ev) => showCard(ev, p));
    node.addEventListener("mousemove", posCard);
    node.addEventListener("mouseleave", hideCard);
    node.addEventListener("click", (ev) => {
      ev.stopPropagation();
      state.selected = { x: p.x, y: p.y };
      state.armed = null;
      paintArmed();
      render();
    });
    at(p.x, p.y).appendChild(node);
    state.nodes.set(k, node);
  }
  const h = (sc.players || []).filter((p) => p.side === "home").length;
  const a = (sc.players || []).filter((p) => p.side === "away").length;
  $("#counts").textContent = `home ${h} · away ${a}`;
  $("#undo").disabled = state.undo.length === 0;
  $$(".pc.sel").forEach((n) => n.classList.remove("sel"));
  if (state.selected) {
    const n = state.nodes.get(key(state.selected.x, state.selected.y));
    if (n) n.classList.add("sel");
  }
}

export function teardown() {
  for (const [, node] of state.nodes) node.remove();
  state.nodes.clear();
  state.selected = null;
  state.armed = null;
  clearMarks("armed", "target");
}

export async function onDrop(ev, x, y) {
  let p;
  try {
    p = JSON.parse(ev.dataTransfer.getData("text/plain"));
  } catch {
    return;
  }
  state.dragging = false;
  if (p.move) await commit(() => api("/move", json({ from: p.move, to: { x, y } })));
  else await commit(() => api("/place", json({ side: p.side, team: p.team, position: p.position, x, y })));
}

export async function onCellClick(x, y) {
  if (state.armed) return commit(() => api("/place", json({ ...state.armed, x, y })));
  if (state.selected) {
    const s = state.selected;
    state.selected = null;
    if (s.x === x && s.y === y) return render();
    return commit(() => api("/move", json({ from: s, to: { x, y } })));
  }
}

function paintArmed() {
  $$(".pi").forEach((n) =>
    n.classList.toggle("armed", !!state.armed && n.dataset.side === state.armed.side && n.dataset.pos === state.armed.position),
  );
  $$(".cell").forEach((n) => n.classList.toggle("armed", !!state.armed));
}

export function buildPalette() {
  const sc = state.scenario;
  for (const side of ["home", "away"]) {
    const host = $(side === "home" ? "#palHome" : "#palAway");
    const team = side === "home" ? sc.home_team : sc.away_team;
    const r = state.roster[team];
    host.innerHTML = `<h5>${side} — ${esc(team || "(none)")}</h5>`;
    if (!r) continue;
    for (const p of r.positionals) {
      const el = document.createElement("div");
      el.className = "pi";
      el.draggable = true;
      el.dataset.side = side;
      el.dataset.pos = p.position;
      el.innerHTML =
        `<span>${esc(p.position)}</span>` +
        `<span class="st">${esc(p.MA)}/${esc(p.ST)}/${esc(p.AG)}/${esc(p.AV)} · ${esc(p.cost || "")}</span>`;
      el.addEventListener("dragstart", (ev) => {
        state.dragging = true;
        ev.dataTransfer.setData("text/plain", JSON.stringify({ side, team, position: p.position }));
      });
      el.addEventListener("dragend", () => {
        state.dragging = false;
      });
      el.addEventListener("click", () => {
        state.armed =
          state.armed && state.armed.position === p.position && state.armed.side === side
            ? null
            : { side, team, position: p.position };
        state.selected = null;
        paintArmed();
      });
      host.appendChild(el);
    }
  }
  paintArmed();
}

export async function ensureRoster(name) {
  if (name && !state.roster[name]) state.roster[name] = await api(`/roster?team=${encodeURIComponent(name)}`);
}

export function wire() {
  for (const which of ["home", "away"]) {
    $(which === "home" ? "#homeTeam" : "#awayTeam").addEventListener("change", async (ev) => {
      const name = ev.target.value;
      await commit(async () => {
        await ensureRoster(name);
        return api("/teams", json({ [`${which}_team`]: name }));
      });
      buildPalette();
    });
  }
  for (const [id, body] of [
    ["#clearAll", {}],
    ["#clearHome", { side: "home" }],
    ["#clearAway", { side: "away" }],
  ]) {
    $(id).addEventListener("click", () => commit(() => api("/clear", json(body))));
  }
  $("#undo").addEventListener("click", async () => {
    const prev = state.undo.pop();
    if (!prev) return;
    try {
      state.scenario = await api("/replace", json(prev));
      ok();
    } catch (e) {
      fail(e);
    }
    render();
  });
  document.addEventListener("keydown", (ev) => {
    if ((ev.key === "Delete" || ev.key === "Backspace") && state.selected) {
      const s = state.selected;
      state.selected = null;
      commit(() => api("/remove", json(s)));
    }
    if (ev.key === "Escape") {
      state.armed = null;
      state.selected = null;
      paintArmed();
      render();
    }
  });
  const trash = $("#trash");
  trash.addEventListener("dragover", (ev) => {
    ev.preventDefault();
    trash.classList.add("over");
  });
  trash.addEventListener("dragleave", () => trash.classList.remove("over"));
  trash.addEventListener("drop", async (ev) => {
    ev.preventDefault();
    trash.classList.remove("over");
    state.dragging = false;
    let p;
    try {
      p = JSON.parse(ev.dataTransfer.getData("text/plain"));
    } catch {
      return;
    }
    if (p.move) await commit(() => api("/remove", json(p.move)));
  });
}

/** The poller's setup-mode half. Returns true when the palette needs rebuilding. */
export async function poll() {
  const s = await api("/state");
  if (JSON.stringify(s) === JSON.stringify(state.scenario)) return false;
  const teamsChanged = s.home_team !== state.scenario.home_team || s.away_team !== state.scenario.away_team;
  state.scenario = s;
  if (teamsChanged) {
    $("#homeTeam").value = state.scenario.home_team || $("#homeTeam").value;
    $("#awayTeam").value = state.scenario.away_team || $("#awayTeam").value;
    await ensureRoster(state.scenario.home_team);
    await ensureRoster(state.scenario.away_team);
    buildPalette();
  }
  render();
  return teamsChanged;
}
