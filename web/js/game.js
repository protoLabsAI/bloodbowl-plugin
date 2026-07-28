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
  clearMarks("legal", "needsroll", "blockable", "blitzable", "foulable", "securable", "handoffable");
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
      // An adjacent opponent is a plain Block. Blitzing one you can already reach
      // is legal but spends the team's only Blitz for the turn, so it is not what
      // a click should mean — the tool can still do it deliberately.
      if (block) return throwBlock(block);
      const hand = acts.find((b) => b.action === "handoff" && b.target === p.id);
      if (hand) return ballAction("handoff", { target: hand.target });
      const foul = ((state.legal || {}).fouls || []).find((f) => f.target === p.id);
      if (foul) return putTheBootIn(foul);
      const blitz = (((state.legal || {}).blitz || {}).targets || []).find((b) => b.target === p.id);
      if (blitz) return declareBlitz(blitz);
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
    clearMarks("legal", "needsroll", "blockable", "blitzable", "foulable", "securable", "handoffable");
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
  clearMarks("legal", "needsroll", "blockable", "blitzable", "foulable", "securable", "handoffable");
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

  // Players already on the floor, whom this player could Foul. Marked distinctly
  // from a Block because the decision is a different one: the question is not the
  // odds of hurting them but the odds of the referee noticing.
  for (const f of state.legal.fouls || []) {
    const cell = at(f.x, f.y);
    cell.classList.add("foulable");
    const o = document.createElement("span");
    o.className = "odds";
    o.textContent = f.armour_modifier ? `F${f.armour_modifier > 0 ? "+" : ""}${f.armour_modifier}` : "F";
    o.title = `Foul — Armour ${f.armour_target}${f.armour_modifier ? ` (${f.armour_modifier > 0 ? "+" : ""}${f.armour_modifier} from assists)` : ""}. Sent off on ${f.sending_off_on}.`;
    cell.appendChild(o);
  }

  // Blitz targets, tagged with how far away they are. Only the ones out of
  // arm's reach are marked: an adjacent opponent is already offered as a plain
  // Block, and two marks on one square would be a choice nobody asked for.
  for (const b of ((state.legal || {}).blitz || {}).targets || []) {
    if ((state.legal.blocks || []).some((k) => k.target === b.target)) continue;
    const cell = at(b.x, b.y);
    cell.classList.add("blitzable");
    const o = document.createElement("span");
    o.className = "odds";
    o.textContent = `B${b.steps}`;
    o.title = b.can_block
      ? `Blitz — ${b.steps} squares away, ${b.budget} available`
      : `Blitz — ${b.steps} squares of ${b.budget}, nothing left to pay for the Block`;
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
            `${esc(b.name || b.position || b.target)} — <b>${b.dice} dice</b>, ${esc(b.chooser)} chooses` +
            ` <span class="muted">(ST ${b.attacker_strength} v ${b.defender_strength})</span></div>`,
        )
        .join("");
  }
  if (legal && legal.ok && (legal.fouls || []).length) {
    html +=
      `<div class="muted" style="margin-top:6px">Foul <span class="muted">(one per turn)</span></div>` +
      legal.fouls
        .map(
          (f) =>
            `<div class="blockrow foulrow">${esc(f.name || f.position || f.target)} — Armour <b>${esc(f.armour_target)}</b>` +
            (f.armour_modifier ? ` ${f.armour_modifier > 0 ? "+" : ""}${f.armour_modifier}` : "") +
            ` <span class="muted">· sent off on a natural double${f.may_argue ? "" : ", and your Coach may not argue"}</span></div>`,
        )
        .join("");
  }
  if (legal && legal.ok && legal.blitz) {
    const bz = legal.blitz;
    if (bz.declared && bz.declared.player === p.id) {
      const foe = (state.match.players || []).find((q) => q.id === bz.declared.target);
      html +=
        `<div class="blockrow blitzrow">Blitzing <b>${esc((foe && foe.position) || bz.declared.target)}</b> — ` +
        (bz.declared.blocked ? "Block thrown; keep moving if you have the squares" : "Block legal once adjacent") +
        `</div>`;
    } else if (bz.declared) {
      html += `<div class="blockrow muted">Blitz already used this turn</div>`;
    } else if ((bz.targets || []).length) {
      // Everything reachable is marked on the BOARD; the panel lists the nearest
      // few and says how many it is not showing, because a silent cap reads as
      // "these are all of them".
      const shown = bz.targets.slice(0, 6);
      html +=
        `<div class="muted" style="margin-top:6px">Blitz <span class="muted">(one per turn)</span></div>` +
        shown
          .map(
            (b) =>
              `<div class="blockrow blitzrow${b.can_block ? "" : " bad"}">` +
              `${esc(b.name || b.position || b.target)} — ` +
              // An adjacent target is already blockable for free. Blitzing one is
              // legal and occasionally right — it is the only way to hit someone
              // and then keep running — but "0 sq" reads as a glitch, so say it.
              (b.steps === 0 ? `<b>adjacent</b> <span class="muted">(only to move on after)</span>` : `<b>${b.steps} sq</b> of ${b.budget}`) +
              (b.can_block ? "" : ` <span class="muted">(nothing left for the Block)</span>`) +
              `</div>`,
          )
          .join("") +
        (bz.targets.length > shown.length
          ? `<div class="blockrow muted">+${bz.targets.length - shown.length} further away, all marked on the board</div>`
          : "");
    }
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
  clearMarks("legal", "needsroll", "blockable", "blitzable", "foulable", "securable", "handoffable");
  describeSelection(null, null);
  render();
  await renderLog();
}

async function putTheBootIn(foul) {
  // Always ends the activation, and may end the turn — so the selection goes
  // either way and the coach reads the log for what the referee made of it.
  return ballAction("foul", { target: foul.target });
}

async function declareBlitz(target) {
  // Declaring rolls nothing, so the player stays selected and the move list is
  // simply refreshed — the coach then walks over and clicks them again.
  try {
    const report = await api("/game/act", json({ action: "blitz", player: state.selected, target: target.target }));
    state.match = report.match;
    ok();
    state.legal = await api(`/game/legal?player=${encodeURIComponent(state.selected)}`);
    paintLegal();
    describeSelection(
      (state.match.players || []).find((q) => q.id === state.selected),
      state.legal,
    );
  } catch (e) {
    fail(e);
  }
  render();
  await renderLog();
}

async function throwBlock(block) {
  try {
    const report = await api(
      "/game/act",
      json({ action: "block", player: state.selected, target: block.target, choice: 0 }),
    );
    state.match = report.match;
    ok();
    // Whether the player may do anything else is the ENGINE's answer, not a
    // guess from the result: an ordinary Block ends the activation, a Blitz's
    // Block does not, and a Player Down ends the turn under everybody.
    const still = (state.match.players || []).find((q) => q.id === state.selected);
    if (report.turnover || !still || still.done || still.down !== "standing") {
      state.selected = null;
      state.legal = null;
      clearMarks("legal", "needsroll", "blockable", "blitzable", "foulable", "securable", "handoffable");
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
      clearMarks("legal", "needsroll", "blockable", "blitzable", "foulable", "securable", "handoffable");
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
      clearMarks("legal", "needsroll", "blockable", "blitzable", "foulable", "securable", "handoffable");
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
