/* Dragging a player across the pitch.
 *
 * POINTER EVENTS, NOT HTML5 DRAG-AND-DROP — and the divergence from `setup.js`,
 * which uses HTML5, is deliberate. Two reasons, both practical:
 *
 *   TOUCH. HTML5 `draggable` does not fire on a touchscreen at all. Setup is a
 *   desk activity; moving a player during a game is the thing somebody will try
 *   on a tablet, and a gesture that silently does nothing is worse than a click.
 *
 *   TESTABILITY. `harness.py` is the only thing that can tell whether a drag
 *   LANDS — a server-side 200 says nothing about it. Playwright drives pointer
 *   events natively (`mouse.move`/`down`/`up`) and drives HTML5 DnD badly. A
 *   gesture I cannot test is a gesture that breaks silently, which is exactly
 *   the defect class this view keeps producing.
 *
 * A DRAG IS NOT A CLICK, AND BOTH MUST KEEP WORKING. Click-then-click stays the
 * accessible path and is what most of the harness drives, so this only takes over
 * once the pointer has actually travelled — under `SLOP` pixels it stays a click
 * and the browser's own `click` event fires untouched. Above it, the click is
 * suppressed on the way up, because a drag that also selects the player it just
 * moved would repaint the board out from under the result.
 *
 * The hit test is `elementFromPoint`, so the thing under the pointer must not BE
 * the pointer: the follower is `pointer-events: none` and the original stays put,
 * dimmed, marking where the move started.
 */

// How far the pointer must travel before this is a drag rather than a click.
// Small enough that a deliberate drag is never mistaken for a click; large enough
// that a shaky click is never mistaken for a drag.
const SLOP = 6;

export const state = {
  // Read by the poller, which must not rebuild the node being dragged. The
  // setup board shipped this bug: a re-render mid-drag tore out the drag target
  // and the gesture died with no error.
  active: false,
};

let follower = null;

/** The pitch square under the pointer, or null. */
function cellAt(ev) {
  const el = document.elementFromPoint(ev.clientX, ev.clientY);
  const cell = el && el.closest && el.closest(".cell");
  if (!cell) return null;
  return { x: +cell.dataset.x, y: +cell.dataset.y };
}

function makeFollower(node) {
  const f = node.cloneNode(true);
  f.classList.add("follower");
  f.classList.remove("sel");
  document.body.appendChild(f);
  return f;
}

function moveFollower(ev) {
  if (!follower) return;
  follower.style.left = `${ev.clientX}px`;
  follower.style.top = `${ev.clientY}px`;
}

/**
 * Make one player node draggable.
 *
 * `handlers.canDrag()`   — false to leave it a click-only node (the opposition,
 *                          a player who has acted, a match that is not yours).
 * `handlers.onStart()`   — the drag has passed the slop threshold.
 * `handlers.onEnter(sq)` — the pointer has entered a new square (or null, off-board).
 * `handlers.onDrop(sq)`  — released over `sq`. Not called for a release off-board.
 * `handlers.onEnd()`     — always, after drop or cancel. Put the board back.
 */
export function draggable(node, handlers) {
  node.addEventListener("pointerdown", (ev) => {
    // Left button / touch / pen only: a right-click is not a drag, and on a
    // trackpad it is how somebody opens a context menu.
    if (ev.button !== 0) return;
    if (handlers.canDrag && !handlers.canDrag()) return;

    const from = { x: ev.clientX, y: ev.clientY };
    let started = false;
    let last = null;

    // Capture so the gesture survives the pointer leaving the node — which it
    // does immediately, because the node is one square wide.
    node.setPointerCapture(ev.pointerId);

    const move = (e) => {
      if (!started) {
        if (Math.hypot(e.clientX - from.x, e.clientY - from.y) < SLOP) return;
        started = true;
        state.active = true;
        // CLONE FIRST, dim second. The other order copies `ghost` onto the
        // follower, so the thing under the pointer is drawn at a third of its
        // opacity and there are two ghosts on the board instead of one.
        follower = makeFollower(node);
        node.classList.add("ghost");
        handlers.onStart && handlers.onStart();
      }
      moveFollower(e);
      const sq = cellAt(e);
      const same = sq && last && sq.x === last.x && sq.y === last.y;
      if (!same) {
        last = sq;
        handlers.onEnter && handlers.onEnter(sq);
      }
    };

    const finish = (e, dropped) => {
      node.removeEventListener("pointermove", move);
      node.removeEventListener("pointerup", up);
      node.removeEventListener("pointercancel", cancel);
      window.removeEventListener("keydown", esc);
      try {
        node.releasePointerCapture(ev.pointerId);
      } catch {
        /* the pointer may already be gone */
      }
      if (!started) return; // it was a click; let the click handler have it
      if (follower) follower.remove();
      follower = null;
      node.classList.remove("ghost");
      state.active = false;
      // Suppress the click this pointerup is about to synthesise. Without it a
      // drag ends by selecting the player, repainting the board over the result.
      const swallow = (c) => c.stopPropagation();
      node.addEventListener("click", swallow, { capture: true, once: true });
      setTimeout(() => node.removeEventListener("click", swallow, { capture: true }), 0);

      const sq = dropped ? cellAt(e) : null;
      if (sq && handlers.onDrop) handlers.onDrop(sq);
      handlers.onEnd && handlers.onEnd();
    };

    const up = (e) => finish(e, true);
    const cancel = (e) => finish(e, false);
    const esc = (e) => {
      if (e.key === "Escape") finish(e, false);
    };

    node.addEventListener("pointermove", move);
    node.addEventListener("pointerup", up);
    node.addEventListener("pointercancel", cancel);
    window.addEventListener("keydown", esc);
  });
}
