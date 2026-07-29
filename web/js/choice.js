/* The board's half of a Kick-off Event that asks the Coach a question.
 *
 * Four of the eleven stop and ask: High Kick picks one player, Quick Snap! moves
 * up to D3+3 of them one square each, Solid Defence sets up to D3+3 of them up
 * again, and Charge! picks up to D3+3 of them to send in. Until one is answered
 * the engine refuses every other action, so a board that ignored `pending` would
 * look broken — every click would come back "the Kick-off Event is waiting on
 * home".
 *
 * The interaction is deliberately the same as the rest of play: click a player,
 * click a square. What differs is that the moves are STAGED — they are shown on
 * the board before they are sent, because "up to D3+3 players" is one answer, not
 * D3+3 answers, and half of it committed is a formation nobody chose.
 *
 * A CHARGE IS THE ODD ONE OUT twice over: its answer names players and no squares,
 * and answering it only STARTS the thing — the selected players then take their
 * free Actions with the ordinary controls, so afterwards this module gets out of
 * the way and the bar only reports what is left and offers the exit.
 */

import { $, api, esc, fail, json, ok } from "./api.js";
import { at } from "./board.js";

/* Not `clearMarks` — that also strips the `.odds` badges the legal-move painter
 * puts on cells, and this runs inside every render. Classes only. */
function unmark() {
  document
    .querySelectorAll(".cell.choosable, .cell.chosen, .cell.picking")
    .forEach((c) => c.classList.remove("choosable", "chosen", "picking"));
}

/** id -> [x, y] the coach has staged but not yet sent. */
const staged = new Map();
let mounted = false;

export function pendingOf(match) {
  const p = match && match.pending;
  return p && p.choice ? p : null;
}

/* A Charge is not a question — it is a free turn the kicking team plays with the
 * ordinary controls. The bar stays up to say whose it is, what is left, and to
 * offer the one thing the board cannot express: ending it early. */
function chargeOf(match) {
  const c = match && match.charge;
  return c && c.players ? c : null;
}

/** Is the board's normal interaction suspended? A pending question suspends it;
 *  a Charge under way deliberately does not. */
export function active(match) {
  return !!pendingOf(match);
}

/** Does this click land on the choice? Returns true if the choice consumed it. */
export function clickPlayer(match, p, redraw) {
  const q = pendingOf(match);
  // A Charge is played with the normal controls, so clicks pass straight through.
  if (!q) return false;
  if (!(q.eligible || []).includes(p.id)) return true; // ineligible: swallow it, say nothing new
  if (q.choice === "high_kick") {
    send(match, { player: p.id }, redraw);
    return true;
  }
  // Charge! picks players and no squares: the second click never comes, so the
  // selection is complete the moment they are named.
  staged.set(p.id, q.choice === "charge" ? ["picked"] : staged.get(p.id) || null);
  paint(match);
  return true;
}

export function clickSquare(match, x, y, redraw) {
  const q = pendingOf(match);
  if (!q) return false;
  // The most recently picked player without a destination gets this square.
  const waiting = [...staged.entries()].filter(([, sq]) => !sq).map(([id]) => id);
  if (!waiting.length) return true;
  staged.set(waiting[waiting.length - 1], [x, y]);
  paint(match);
  return true;
}

function moves() {
  return [...staged.entries()].filter(([, sq]) => sq).map(([id, sq]) => ({ id, x: sq[0], y: sq[1] }));
}

async function send(match, answer, redraw) {
  try {
    const r = await api("/game/choose", json(answer));
    if (r.ok === false) return fail(new Error(r.error || "refused"));
    staged.clear();
    ok();
    redraw(r.match);
  } catch (e) {
    fail(e);
  }
}

/** Draw the question, the eligible players and whatever is staged so far. */
export function paint(match) {
  const bar = $("#choice");
  const q = pendingOf(match);
  unmark();
  if (!q) {
    staged.clear();
    const ch = chargeOf(match);
    bar.hidden = !ch;
    if (!ch) return;
    // Mid-Charge: the selected players play on with the normal controls, so the
    // bar only reports and offers the exit.
    const left = (ch.players || []).filter((id) => {
      const p = (match.players || []).find((x) => x.id === id);
      return p && !p.done;
    });
    const spent = ch.used || [];
    $("#choiceText").innerHTML = esc(
      `Charge! ${ch.side} — ${left.length} of ${ch.players.length} still to activate.` +
        (spent.length ? ` Used: ${spent.join(", ")}.` : " A Blitz, a Throw Team-mate and a Kick Team-mate are free."),
    );
    for (const id of left) {
      const p = (match.players || []).find((x) => x.id === id);
      if (p && p.place === "pitch") at(p.x, p.y).classList.add("choosable");
    }
    $("#choicePicked").textContent = "";
    $("#choiceConfirm").hidden = true;
    $("#choiceUndo").hidden = true;
    $("#choiceDecline").textContent = "End the Charge";
    return;
  }
  $("#choiceDecline").textContent = "Decline";
  bar.hidden = false;
  $("#choiceText").innerHTML = esc(q.text || q.choice);

  for (const p of match.players || []) {
    if (p.place === "pitch" && (q.eligible || []).includes(p.id)) at(p.x, p.y).classList.add("choosable");
  }
  if (q.choice === "high_kick" && q.square) at(q.square[0], q.square[1]).classList.add("chosen");
  for (const [id, sq] of staged) {
    if (q.choice === "charge") {
      // Charge stages the PLAYER, not a destination — mark where they stand.
      const p = (match.players || []).find((q2) => q2.id === id);
      if (p && p.place === "pitch") at(p.x, p.y).classList.add("chosen");
      continue;
    }
    if (sq) {
      at(sq[0], sq[1]).classList.add("chosen");
      continue;
    }
    // Picked, but with nowhere to go yet. Without a mark of its own there is no
    // feedback at all between the two clicks, and the board looks like it ate the
    // first one — which is how this got noticed.
    const p = (match.players || []).find((q2) => q2.id === id);
    if (p && p.place === "pitch") at(p.x, p.y).classList.add("picking");
  }

  const charging = q.choice === "charge";
  const picked = charging ? staged.size : moves().length;
  const waiting = charging ? 0 : staged.size - picked;
  const limit = q.limit ? ` of up to ${q.limit}` : "";
  const note = waiting ? " — now click a square" : "";
  const verb = charging ? "selected" : "placed";
  $("#choicePicked").textContent = q.choice === "high_kick" ? "" : `${picked}${limit} ${verb}${note}`;
  $("#choiceConfirm").hidden = q.choice === "high_kick" || !picked;
  $("#choiceConfirm").textContent = q.choice === "charge" ? "Send them in" : "Confirm";
  $("#choiceUndo").hidden = !staged.size;
}

export function mount(redraw, matchOf) {
  if (mounted) return;
  mounted = true;
  $("#choiceDecline").addEventListener("click", () => send(matchOf(), { decline: true }, redraw));
  $("#choiceConfirm").addEventListener("click", () => {
    const q = pendingOf(matchOf());
    const answer = q && q.choice === "charge" ? { players: [...staged.keys()] } : { moves: moves() };
    send(matchOf(), answer, redraw);
  });
  $("#choiceUndo").addEventListener("click", () => {
    const last = [...staged.keys()].pop();
    if (last) staged.delete(last);
    paint(matchOf());
  });
}
