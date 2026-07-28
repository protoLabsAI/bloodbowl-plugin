/* Play mode: a match on the board.
 *
 * The interaction is the engine's own division of labour made visible. Clicking
 * one of your players asks the engine what it may do — every neighbour, which
 * need a Dodge, at what modifier, which need a Rush — and paints the answer. Then
 * clicking a square commits. Nothing here works out whether a move is legal or
 * what the odds are; asking is cheap and the engine is the only thing that knows.
 *
 * The log shows the rolls as they were made. It is the same text the coach reads,
 * for the same reason: a described outcome can drift from the one that happened,
 * a quoted roll cannot.
 */

import { $, api, apiOrNull, esc, fail, json, ok } from "./api.js";
import { at, clearMarks, key } from "./board.js";
import { hideCard, posCard, showCard } from "./card.js";

export const state = {
  match: null,
  nodes: new Map(),
  selected: null,
  legal: null,
  passing: false,
};

export function has() {
  return !!state.match;
}

export async function refresh() {
  const r = await apiOrNull("/game");
  state.match = r && r.ok ? r.match : null;
  return state.match;
}

export function teardown() {
  for (const [, node] of state.nodes) node.remove();
  state.nodes.clear();
  const ball = document.querySelector(".ball");
  if (ball) ball.remove();
  state.selected = null;
  state.legal = null;
  clearMarks("legal", "needsroll", "blockable", "securable", "handoffable");
}

export function render() {
  const m = state.match;
  if (!m) return;

  const want = new Map();
  for (const p of m.players || []) if (p.place === "pitch") want.set(key(p.x, p.y), p);

  for (const [k, node] of state.nodes) {
    if (!want.has(k)) {
      node.remove();
      state.nodes.delete(k);
    }
  }
  for (const [k, p] of want) {
    const carrying = m.ball && m.ball.carrier === p.id;
    const sig = [p.side, p.badge, p.down, p.acted, p.distracted, carrying].join("|");
    let node = state.nodes.get(k);
    if (node && node.dataset.sig === sig) continue;
    if (node) node.remove();
    node = document.createElement("div");
    node.className =
      `pc ${p.side === "home" ? "home" : "away"}` +
      (p.down === "prone" ? " prone" : "") +
      (p.down === "stunned" ? " stunned" : "") +
      (p.acted ? " acted" : "") +
      (carrying ? " hasball" : "");
    node.dataset.sig = sig;
    node.dataset.id = p.id;
    node.textContent = p.badge || "?";
    node.title = `${p.position || "player"} — ${p.down}${p.acted ? ", has acted" : ""}`;
    node.addEventListener("mouseenter", (ev) => showCard(ev, p));
    node.addEventListener("mousemove", posCard);
    node.addEventListener("mouseleave", hideCard);
    node.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const acts = (state.legal && state.legal.ball_actions) || [];
      const block = ((state.legal && state.legal.blocks) || []).find((b) => b.target === p.id);
      if (block) return throwBlock(block);
      const hand = acts.find((b) => b.action === "handoff" && b.target === p.id);
      if (hand) return ballAction("handoff", { target: hand.target });
      select(p);
    });
    at(p.x, p.y).appendChild(node);
    state.nodes.set(k, node);
  }

  // A loose ball is drawn on its square; a carried one rides its carrier's badge.
  const loose = document.querySelector(".ball");
  if (loose) loose.remove();
  if (m.ball && m.ball.in_play && !m.ball.carrier) {
    const b = document.createElement("div");
    b.className = "ball";
    at(m.ball.x, m.ball.y).appendChild(b);
  }

  const c = m.clock || {};
  $("#clock").textContent = `H${c.half} · turn ${c.turn}/${c.turns_per_half} · drive ${m.drive || 1}`;
  $("#score").textContent = `${m.score.home} – ${m.score.away}`;
  const active = $("#activeSide");
  active.textContent = `${c.active} to act`;
  active.classList.toggle("home", c.active === "home");
  $("#counts").textContent = m.over ? "match over" : "";

  document.querySelectorAll(".pc.sel").forEach((n) => n.classList.remove("sel"));
  if (state.selected) {
    const n = [...state.nodes.values()].find((x) => x.dataset.id === state.selected);
    if (n) n.classList.add("sel");
  }
}

async function select(p) {
  if (p.side !== state.match.clock.active) {
    // Not an error — you are allowed to look at the opposition. Just no move list.
    state.selected = p.id;
    state.legal = null;
    clearMarks("legal", "needsroll", "blockable", "securable", "handoffable");
    describeSelection(p, null);
    render();
    return;
  }
  state.selected = p.id;
  try {
    state.legal = await api(`/game/legal?player=${encodeURIComponent(p.id)}`);
    ok();
  } catch (e) {
    state.legal = null;
    fail(e);
  }
  paintLegal();
  describeSelection(p, state.legal);
  render();
}

function paintLegal() {
  clearMarks("legal", "needsroll", "blockable", "securable", "handoffable");
  if (!state.legal || !state.legal.ok) return;

  // Blockable opponents, labelled with the dice and — the part that decides
  // whether it is a good idea — who gets to choose them.
  for (const b of state.legal.blocks || []) {
    const cell = at(b.x, b.y);
    cell.classList.add("blockable");
    const o = document.createElement("span");
    o.className = "odds";
    o.textContent = `${b.dice}D${b.chooser === "attacker" ? "" : "!"}`;
    cell.appendChild(o);
  }

  // Ball actions the engine says are available. Secure the Ball especially:
  // it is new in S3 and nobody will think to try it unless it is offered.
  for (const b of state.legal.ball_actions || []) {
    const cell = at(b.x, b.y);
    if (b.action === "pass") {
      if (!state.passing) continue;
      cell.classList.add("passable");
      const o = document.createElement("span");
      o.className = "odds";
      // The band's modifier, which is what actually decides the throw.
      o.textContent = b.modifier ? `${b.modifier}` : "0";
      o.title = `${b.range} (${b.modifier >= 0 ? "+" : ""}${b.modifier})`;
      cell.appendChild(o);
      continue;
    }
    cell.classList.add(b.action === "secure" ? "securable" : "handoffable");
    const o = document.createElement("span");
    o.className = "odds";
    o.textContent = b.action === "secure" ? "2+" : "H";
    cell.appendChild(o);
  }

  for (const s of state.legal.squares) {
    if (!s.legal) continue;
    const cell = at(s.x, s.y);
    cell.classList.add("legal");
    const needs = s.dodge || s.rush;
    if (needs) cell.classList.add("needsroll");
    const tag = [];
    if (s.rush) tag.push("R");
    if (s.dodge) tag.push(s.dodge_modifier ? `D${s.dodge_modifier}` : "D");
    if (tag.length) {
      const o = document.createElement("span");
      o.className = "odds";
      o.textContent = tag.join(" ");
      cell.appendChild(o);
    }
  }
}

function describeSelection(p, legal) {
  const host = $("#sel");
  if (!p) {
    host.innerHTML = '<span class="muted">Click one of your players to see where they can go.</span>';
    return;
  }
  const left = legal && legal.ok ? legal.movement_left : Math.max(0, (p.movement || 0) - (p.ma_used || 0));
  let html =
    `<b>${esc(p.position || "Player")}</b> <span class="muted">${esc(p.side)} · ${esc(p.down)}</span>` +
    `<div class="muted">MA ${esc(p.MA)} · moved ${p.ma_used || 0} · ${left} left</div>`;

  if (legal && legal.ok) {
    // A 3x3 mirror of the board around the player: legal squares, and which of
    // them cost a roll. Reading it should take a glance, not arithmetic.
    const grid = [];
    for (let dx = -1; dx <= 1; dx++) {
      for (let dy = -1; dy <= 1; dy++) {
        if (dx === 0 && dy === 0) {
          grid.push('<div class="no">·</div>');
          continue;
        }
        const s = legal.squares.find((q) => q.x === p.x + dx && q.y === p.y + dy);
        if (!s || !s.legal) {
          grid.push('<div class="no">×</div>');
          continue;
        }
        const tag = [s.rush ? "Rush" : "", s.dodge ? `Dodge ${s.dodge_modifier || 0}` : ""].filter(Boolean).join(" ");
        grid.push(`<div class="${tag ? "roll" : ""}">${tag || "free"}</div>`);
      }
    }
    html += `<div class="moves">${grid.join("")}</div>`;
  }
  if (legal && legal.ok && (legal.blocks || []).length) {
    html +=
      `<div class="muted" style="margin-top:6px">Blocks</div>` +
      (legal.blocks || [])
        .map(
          (b) =>
            `<div class="blockrow${b.chooser === "attacker" ? "" : " bad"}">` +
            `${esc(b.position)} — <b>${b.dice} dice</b>, ${esc(b.chooser)} chooses` +
            ` <span class="muted">(ST ${b.attacker_strength} v ${b.defender_strength})</span></div>`,
        )
        .join("");
  }
  if (legal && legal.ok && (legal.ball_actions || []).length) {
    html +=
      `<div class="muted" style="margin-top:6px">Ball</div>` +
      (legal.ball_actions || [])
        .map((b) =>
          b.action === "secure"
            ? `<div class="blockrow">Secure the Ball — <b>2+</b>, ends the activation</div>`
            : b.action === "pass"
              ? ""
              : `<div class="blockrow">Hand-off to a team-mate at (${b.x},${b.y})</div>`,
        )
        .join("");
    const passes = (legal.ball_actions || []).filter((b) => b.action === "pass");
    if (passes.length) {
      const byBand = {};
      for (const b of passes) byBand[b.range] = (byBand[b.range] || 0) + 1;
      html +=
        `<div class="blockrow">Pass — ` +
        Object.entries(byBand)
          .map(([r, n]) => `${esc(r)} ${n} sq`)
          .join(", ") +
        ` <button id="passArm" class="mini">${state.passing ? "cancel" : "throw…"}</button></div>`;
    }
  }
  if (p.skills && p.skills.length) html += `<div class="sk muted">${p.skills.map(esc).join(" · ")}</div>`;
  host.innerHTML = html;
  const arm = $("#passArm");
  if (arm) {
    arm.addEventListener("click", () => {
      // Arming is explicit: with the ball in hand, most of the pitch is a legal
      // pass target, and an un-armed click would throw the ball at the first
      // square a coach touched.
      state.passing = !state.passing;
      paintLegal();
      describeSelection(p, legal);
    });
  }
}

async function ballAction(action, extra) {
  try {
    const report = await api("/game/act", json({ action, player: state.selected, ...extra }));
    state.match = report.match;
    ok();
  } catch (e) {
    fail(e);
  }
  // Both of these end the activation or change possession, so the previous
  // move list is stale either way.
  state.selected = null;
  state.legal = null;
  clearMarks("legal", "needsroll", "blockable", "securable", "handoffable");
  describeSelection(null, null);
  render();
  await renderLog();
}

async function throwBlock(block) {
  const chooses = block.chooser === "attacker";
  try {
    const report = await api(
      "/game/act",
      json({ action: "block", player: state.selected, target: block.target, choice: 0 }),
    );
    state.match = report.match;
    ok();
    if (report.turnover || !chooses) {
      state.selected = null;
      state.legal = null;
      clearMarks("legal", "needsroll", "blockable", "securable", "handoffable");
      describeSelection(null, null);
    } else {
      state.legal = await api(`/game/legal?player=${encodeURIComponent(state.selected)}`);
      paintLegal();
    }
  } catch (e) {
    fail(e);
  }
  render();
  await renderLog();
}

export async function onCellClick(x, y) {
  if (!state.selected || !state.legal) return;
  const acts = (state.legal && state.legal.ball_actions) || [];
  const secure = acts.find((b) => b.action === "secure" && b.x === x && b.y === y);
  if (secure) return ballAction("secure", {});
  const throwTo = acts.find((b) => b.action === "pass" && b.x === x && b.y === y);
  if (throwTo && state.passing) return ballAction("pass", { x, y });
  const square = state.legal.squares.find((s) => s.x === x && s.y === y);
  if (!square || !square.legal) return;
  try {
    const report = await api("/game/act", json({ action: "move", player: state.selected, x, y }));
    state.match = report.match;
    ok();
    if (report.unmodelled_skills && report.unmodelled_skills.length) {
      $("#sel").insertAdjacentHTML(
        "beforeend",
        `<div class="unmodelled">Not modelled: ${report.unmodelled_skills.map(esc).join(", ")}</div>`,
      );
    }
    // The player may have fallen, or the turn may have ended under them.
    const still = (state.match.players || []).find((p) => p.id === state.selected);
    if (report.turnover || !still || still.down !== "standing") {
      state.selected = null;
      state.legal = null;
      clearMarks("legal", "needsroll", "blockable", "securable", "handoffable");
      describeSelection(null, null);
    } else {
      state.legal = await api(`/game/legal?player=${encodeURIComponent(state.selected)}`);
      paintLegal();
      describeSelection(still, state.legal);
    }
  } catch (e) {
    fail(e);
  }
  render();
  await renderLog();
}

export async function renderLog() {
  const r = await apiOrNull("/game/log?last=40");
  const host = $("#log");
  if (!r || !r.ok) {
    host.innerHTML = '<span class="muted">No match in progress.</span>';
    return;
  }
  host.innerHTML = r.log
    .slice()
    .reverse()
    .map(
      (e) =>
        `<div><span class="${e.kind === "turnover" ? "turnover" : ""}">${esc(e.text)}</span>` +
        (e.rolls && e.rolls.length ? `<div class="roll">${e.rolls.map(esc).join(" · ")}</div>` : "") +
        `</div>`,
    )
    .join("");
}

export function wire(onChanged) {
  $("#newMatch").addEventListener("click", async () => {
    try {
      const seed = Math.floor(Math.random() * 100000);
      const r = await api("/game/new", json({ seed }));
      state.match = r.match;
      ok();
    } catch (e) {
      fail(e);
    }
    teardown();
    render();
    await renderLog();
    onChanged && onChanged();
  });
  $("#endTurn").addEventListener("click", async () => {
    try {
      const r = await api("/game/end-turn", json({}));
      state.match = r.match;
      state.selected = null;
      state.legal = null;
      clearMarks("legal", "needsroll", "blockable", "securable", "handoffable");
      describeSelection(null, null);
      ok();
    } catch (e) {
      fail(e);
    }
    render();
    await renderLog();
  });
  $("#kickoff").addEventListener("click", async () => {
    try {
      const r = await api("/game/kickoff", json({}));
      state.match = r.match;
      ok();
    } catch (e) {
      fail(e);
    }
    teardown();
    render();
    await renderLog();
  });
  $("#abandon").addEventListener("click", async () => {
    try {
      await api("/game/abandon", json({}));
      ok();
    } catch (e) {
      fail(e);
    }
    state.match = null;
    teardown();
    await renderLog();
    onChanged && onChanged();
  });
}

/** The poller's play-mode half. Re-renders only when the match actually moved. */
export async function poll() {
  const r = await apiOrNull("/game");
  const next = r && r.ok ? r.match : null;
  if (JSON.stringify(next) === JSON.stringify(state.match)) return false;
  state.match = next;
  render();
  await renderLog();
  return true;
}
