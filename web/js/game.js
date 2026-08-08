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
import * as choice from "./choice.js";
import * as drag from "./drag.js";

/* Every mark `paintLegal` can put on a square, in one place because it was in
 * SEVEN and they had already drifted: `passable` was in none of them, so arming
 * a pass and cancelling it left the throw targets lit up across the pitch. */
const MARKS = [
  "legal",
  "needsroll",
  "blockable",
  "blitzable",
  "foulable",
  "securable",
  "handoffable",
  "passable",
  "droptarget",
];

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
  clearMarks(...MARKS);
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
      if (choice.clickPlayer(state.match, p, adopt)) return;
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
    // Drag to move. Click-then-click still works and is still the accessible
    // path; this is the direct one. Only a player who could be activated is
    // draggable — dragging the opposition, or anyone on a board that is not
    // yours to move, would be a gesture the engine can only refuse.
    drag.draggable(node, {
      canDrag: () => canActivate(p),
      onStart: () => {
        hideCard();
        clearPath();
        if (state.selected !== p.id) select(p);
      },
      onEnter: (sq) => {
        markDropTarget(sq);
        // The budget is what the engine says is left, plus the two Rushes anyone
        // may attempt. Beyond that the trail would be drawing squares the player
        // cannot reach however the dice fall.
        const left = (state.legal && state.legal.ok && state.legal.movement_left) || 0;
        extendPath(sq, { x: p.x, y: p.y }, left + 2);
      },
      onDrop: (sq) => dropOn(p, sq),
      onEnd: () => {
        markDropTarget(null);
        clearPath();
      },
    });
    const cell = at(p.x, p.y);
    if (!cell) continue; // off the pitch — nothing to draw into
    cell.appendChild(node);
    state.nodes.set(k, node);
  }

  // A loose ball is drawn on its square; a carried one rides its carrier's badge.
  const loose = document.querySelector(".ball");
  if (loose) loose.remove();
  if (m.ball && m.ball.in_play && !m.ball.carrier) {
    const b = document.createElement("div");
    // A ball still in the air is on its way to that square, not on it — during a
    // Kick-off Event that stopped to ask the Coach something it can sit there for
    // several calls, and drawn solid it reads as landed.
    b.className = m.ball.in_air ? "ball air" : "ball";
    b.title = m.ball.in_air ? "still in the air — the Kick-off Event is not resolved yet" : "";
    // The ball can legitimately be OFF the pitch: it sits out of bounds between leaving
    // the field and the crowd throwing it back. Skip it rather than crash — a renderer
    // must tolerate any state the engine can produce.
    const cell = at(m.ball.x, m.ball.y);
    if (cell) cell.appendChild(b);
  }

  const c = m.clock || {};
  $("#clock").textContent = `H${c.half} · turn ${c.turn}/${c.turns_per_half} · drive ${m.drive || 1}`;
  $("#score").textContent = `${m.score.home} – ${m.score.away}`;
  const active = $("#activeSide");
  active.textContent = `${c.active} to act`;
  active.classList.toggle("home", c.active === "home");

  // Whose game it is. In a head-to-head the board may only move ONE side, so it
  // has to say which — a board that silently refuses half your clicks reads as
  // broken, and that is the whole failure this chip exists to prevent.
  const who = m.controllers || {};
  const mine = Object.keys(who).find((s) => who[s] === "human");
  const chip = $("#whose");
  chip.hidden = !mine;
  if (mine) {
    const yours = c.active === mine;
    chip.textContent = yours ? `your move — you are ${mine}` : `the agent is playing ${c.active}…`;
    chip.classList.toggle("waiting", !yours);
  }
  document.body.classList.toggle("not-my-turn", !!mine && c.active !== mine);
  $("#counts").textContent = m.over ? "match over" : "";

  // After the marks, not before: paint() adds its own, and clearMarks elsewhere
  // in this render would wipe them.
  choice.paint(m);

  document.querySelectorAll(".pc.sel").forEach((n) => n.classList.remove("sel"));
  if (state.selected) {
    const n = [...state.nodes.values()].find((x) => x.dataset.id === state.selected);
    if (n) n.classList.add("sel");
  }
}

/** Could this player be activated right now? The gate on dragging them. */
function canActivate(p) {
  const m = state.match;
  if (!m || m.over) return false;
  if (p.side !== m.clock.active) return false;
  // A head-to-head board may only move one side. `not-my-turn` is already the
  // rule the rest of the view obeys; a draggable opposition player would just be
  // a refusal dressed up as an affordance.
  const who = m.controllers || {};
  const mine = Object.keys(who).find((s) => who[s] === "human");
  if (mine && m.clock.active !== mine) return false;
  return !p.acted;
}

/* THE DRAGGED PATH.
 *
 * A Blood Bowl move is not a move, it is a SEQUENCE OF SINGLE SQUARES, each of
 * which can demand a Dodge or a Rush and any of which can end the activation on
 * the spot. So a drag from here to there cannot be one call, and — this is the
 * part that shapes the whole design — it cannot be fully previewed either:
 * `/game/legal` costs the squares around where the player IS, and step two's
 * legality depends on step one's dice having already been rolled.
 *
 * What is honest, then: the squares adjacent to the player keep the real odds
 * `paintLegal` gave them, and the rest of the trail is drawn as a trail and
 * claims nothing. The engine adjudicates each step as it is taken, and the walk
 * stops the moment one of them does not go to plan.
 */
let path = [];
let pathCells = [];
let lastDropTarget = null;

const adjacent = (a, b) => Math.max(Math.abs(a.x - b.x), Math.abs(a.y - b.y)) === 1;
const same = (a, b) => a && b && a.x === b.x && a.y === b.y;

function clearPath() {
  for (const c of pathCells) {
    c.classList.remove("path");
    const s = c.querySelector(".step");
    if (s) s.remove();
  }
  pathCells = [];
  path = [];
}

function paintPath() {
  for (const c of pathCells) {
    c.classList.remove("path");
    const s = c.querySelector(".step");
    if (s) s.remove();
  }
  pathCells = [];
  path.forEach((sq, i) => {
    const cell = at(sq.x, sq.y);
    if (!cell) return;
    cell.classList.add("path");
    const n = document.createElement("span");
    n.className = "step";
    n.textContent = String(i + 1);
    cell.appendChild(n);
    pathCells.push(cell);
  });
}

/**
 * Grow the trail towards `sq`.
 *
 * FILLS IN THE SQUARES BETWEEN, because a pointer does not visit every cell it
 * crosses — move quickly and the events arrive several squares apart, which
 * would otherwise produce a "path" of disconnected hops that the engine refuses
 * one at a time. King moves, so the fill is just a walk towards the target.
 *
 * Dragging back over the previous square pops it, which is how anybody expects
 * to undo a step mid-gesture.
 */
function extendPath(sq, origin, budget) {
  if (!sq) return;
  let head = path.length ? path[path.length - 1] : origin;
  if (same(sq, head)) return;
  // Backtracking: the pointer has returned to where it came from.
  if (path.length && same(sq, path.length > 1 ? path[path.length - 2] : origin)) {
    path.pop();
    paintPath();
    return;
  }
  let guard = 0;
  while (!same(head, sq) && path.length < budget && guard++ < 40) {
    const next = {
      x: head.x + Math.sign(sq.x - head.x),
      y: head.y + Math.sign(sq.y - head.y),
    };
    // Never let the trail cross itself into a loop; a coach who drags in a circle
    // means the last square they touched, not the whole circle.
    if (path.some((q) => same(q, next)) || same(next, origin)) break;
    path.push(next);
    head = next;
  }
  paintPath();
}

/**
 * Walk the trail, one real move per square, and stop the moment it goes wrong.
 *
 * Each step is adjudicated by the engine — a failed Dodge, a Rush that fails, a
 * Turnover, or simply a refusal — and any of those ends the run where it
 * happened rather than pressing on with the rest of a plan that no longer
 * applies. A refusal answers 200 with `ok:false`, so the status code is not
 * enough to tell a played move from a rejected one.
 */
async function walkPath(p, squares) {
  for (const sq of squares) {
    let report;
    try {
      report = await api("/game/act", json({ action: "move", player: p.id, x: sq.x, y: sq.y }));
    } catch (e) {
      fail(e);
      break;
    }
    if (!report || report.ok === false || !report.match) break;
    state.match = report.match;
    ok();
    render();
    const still = (state.match.players || []).find((q) => q.id === p.id);
    if (report.turnover || !still || still.down !== "standing") break;
    // Pushed, or stopped short: the plan is stale either way.
    if (still.x !== sq.x || still.y !== sq.y) break;
    // Slow enough to watch. A run of six squares resolved instantly is a jump
    // cut — you cannot see which step cost a Dodge, which is the one thing worth
    // watching a move for.
    if (squares.length > 1) await new Promise((r) => setTimeout(r, 130));
  }
  await renderLog();
}

/**
 * Light the square under the pointer.
 *
 * Keeps its own cell reference rather than calling `clearMarks("droptarget")`,
 * which now walks all ~390 squares — cheap once, but this runs on every pointer
 * move. (It also used to strip every odds badge on the board whatever classes it
 * was handed, which is what originally forced the reference; that trap is fixed
 * in `board.js`, and this stays for the cost rather than the correctness.)
 */
function markDropTarget(sq) {
  if (lastDropTarget) lastDropTarget.classList.remove("droptarget");
  lastDropTarget = null;
  if (!sq) return;
  const cell = at(sq.x, sq.y);
  if (!cell) return;
  cell.classList.add("droptarget");
  lastDropTarget = cell;
}

/**
 * A player was dropped on a square.
 *
 * Routed through `onCellClick` rather than reimplemented, so a drop and a click
 * can never disagree about what a square MEANS — the dispatch that decides
 * between Secure, a pass and a move lives in exactly one place.
 *
 * The selection is ensured first: `onStart` fires `select` without awaiting it
 * (a pointer gesture cannot wait on a fetch), so a fast drag can arrive here
 * before the move list does.
 */
/** Whoever is standing on a square, or null. */
function playerAt(sq) {
  return (state.match.players || []).find((q) => q.place === "pitch" && q.x === sq.x && q.y === sq.y) || null;
}

/**
 * Dropped ON somebody. The drop target already knows what the gesture means.
 *
 * The board has been painting `blockable`, `blitzable`, `foulable` and
 * `handoffable` on these squares all along — the engine decided which, and it is
 * the only thing that can. So a drop reads the answer rather than working it out
 * again, exactly as the click handler does.
 *
 * DISTANCE IS THE VERB. Dropping on an opponent you are already touching is a
 * Block. Dragging ACROSS the pitch onto one is a Blitz — declare, walk, hit —
 * which is precisely what a Blitz is, and it is the only action in the game that
 * a drag describes better than a click ever could.
 */
async function dropOnPlayer(p, foe, route) {
  const legal = state.legal || {};
  const block = (legal.blocks || []).find((b) => b.target === foe.id);
  if (block && !route.length) return throwBlock(block);

  const blitz = ((legal.blitz || {}).targets || []).find((b) => b.target === foe.id);
  if (blitz && !(legal.blitz || {}).declared) {
    await declareBlitz(blitz);
    if (route.length) await walkPath(p, route);
    // Re-ask: whether the Block is on now is the ENGINE's answer, not a guess
    // from having arrived — a failed Rush on the way leaves them on the floor.
    state.legal = await apiOrNull(`/game/legal?player=${encodeURIComponent(p.id)}`);
    const now = ((state.legal || {}).blocks || []).find((b) => b.target === foe.id);
    if (now) return throwBlock(now);
    paintLegal();
    describeSelection(
      (state.match.players || []).find((q) => q.id === p.id),
      state.legal,
    );
    render();
    return;
  }
  if (block) return throwBlock(block);

  const hand = (legal.ball_actions || []).find((b) => b.action === "handoff" && b.target === foe.id);
  if (hand) return ballAction("handoff", { target: hand.target });

  const foul = (legal.fouls || []).find((f) => f.target === foe.id);
  if (foul) return putTheBootIn(foul);
}

async function dropOn(p, sq) {
  let route = path.slice();
  if (state.selected !== p.id || !state.legal) await select(p);
  if (!state.legal || !state.legal.ok) return;

  // A square with somebody on it is never a square to walk to, so the trail's
  // last step is trimmed: you cannot stand where they are standing, you act on
  // them from next door.
  const onIt = playerAt(sq);
  if (onIt && route.length && same(route[route.length - 1], sq)) route = route.slice(0, -1);
  if (onIt && onIt.id !== p.id) return dropOnPlayer(p, onIt, route);

  // A drag of more than one square is a RUN, walked a step at a time. One square
  // goes through `onCellClick` instead, so a short drag and a click stay exactly
  // the same thing — including Secure the Ball and a thrown pass, which are
  // squares that mean something other than "walk here".
  if (route.length > 1 && same(route[route.length - 1], sq)) {
    await walkPath(p, route);
    const still = (state.match.players || []).find((q) => q.id === p.id);
    if (still && still.down === "standing" && state.selected === p.id) {
      state.legal = await apiOrNull(`/game/legal?player=${encodeURIComponent(p.id)}`);
      paintLegal();
      describeSelection(still, state.legal);
    } else {
      state.selected = null;
      state.legal = null;
      clearMarks(...MARKS);
      describeSelection(null, null);
    }
    render();
    return;
  }
  await onCellClick(sq.x, sq.y);
}

/** Take a board the server just handed back, and redraw everything from it. */
async function adopt(match) {
  state.match = match;
  state.selected = null;
  state.legal = null;
  clearMarks(...MARKS);
  describeSelection(null, null);
  render();
  await renderLog();
}

async function select(p) {
  if (p.side !== state.match.clock.active) {
    // Not an error — you are allowed to look at the opposition. Just no move list.
    state.selected = p.id;
    state.legal = null;
    clearMarks(...MARKS);
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
  clearMarks(...MARKS);
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
  clearMarks(...MARKS);
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
    // NO `choice`. It indexes the faces the roll shows, and the roll has not
    // happened yet at the moment of asking — so the only correct value is no
    // value, and `choice: 0` was a blind pre-commitment to the FIRST DIE dressed
    // up as a decision. Left out, the engine applies the best face for whoever is
    // entitled to choose, which is the coach the board is being played by.
    const report = await api("/game/act", json({ action: "block", player: state.selected, target: block.target }));
    state.match = report.match;
    ok();
    // Whether the player may do anything else is the ENGINE's answer, not a
    // guess from the result: an ordinary Block ends the activation, a Blitz's
    // Block does not, and a Player Down ends the turn under everybody.
    const still = (state.match.players || []).find((q) => q.id === state.selected);
    if (report.turnover || !still || still.done || still.down !== "standing") {
      state.selected = null;
      state.legal = null;
      clearMarks(...MARKS);
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
  // A pending Kick-off Event owns the board until it is answered — the engine
  // refuses everything else, so the click has to mean the question or nothing.
  if (choice.clickSquare(state.match, x, y, adopt)) return;
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
      clearMarks(...MARKS);
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
  choice.mount(adopt, () => state.match);
  $("#newMatch").addEventListener("click", async () => {
    try {
      const seed = Math.floor(Math.random() * 100000);
      // `you` claims a side and gives the agent the other one. Left out, nobody
      // owns anything and the board stays permissive — the practice match.
      const versus = $("#versus").checked ? { you: $("#mySide").value } : {};
      const r = await api("/game/new", json({ seed, ...versus }));
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
      clearMarks(...MARKS);
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
  // NOT WHILE A DRAG IS IN FLIGHT. `render()` replaces a player's node whenever
  // its signature changes, which tears the node out from under the pointer and
  // kills the gesture with no error at all. The setup board shipped exactly this
  // bug; standing the poller down is how it was fixed there too.
  if (drag.state.active) return false;
  const r = await apiOrNull("/game");
  const next = r && r.ok ? r.match : null;
  if (JSON.stringify(next) === JSON.stringify(state.match)) return false;
  state.match = next;
  render();
  await renderLog();
  return true;
}
