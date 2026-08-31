"""Auto mode: one thing whose job is to run a self-playing match.

WHY THIS EXISTS, and why the thing it replaces was the wrong shape.

Turns used to be driven by an EVENT. Several routes published a handover
(`act`, `choose`, `end-turn`), a bus event turned into a nudge, the nudge
enqueued a turn, and a failed fire was retried by the scheduler. Every part of
that is reasonable on its own and together they produced:

* **nine nudges for one handover** — each route legitimately saw news, and once
  the job id became unique per attempt nothing collapsed them. Nine concurrent
  tasks for one seat, all of which stalled;
* **seats woken with nothing to do**, having to spend a whole model call working
  out that it was not their turn;
* **retry storms**, where an interrupted turn was restarted into the same session
  it had already half-filled, so each attempt was slower than the last;
* and no single place that knew whether the match was actually progressing.

A self-playing match does not need any of that. It needs a loop: whose move is
it, fire that seat, WAIT for the board to change hands, go again. One turn in
flight at a time, by construction — which is also how the game itself works.

**IT DRIVES EVERY AGENT TURN, head-to-head included.** That is the point: one
thing fires turns, so a turn cannot be fired twice, and there is one place to look
when one does not happen.

**And it does not make you wait for a poll.** Ending your turn WAKES it — the
handover is a signal, not a schedule — so the agent starts immediately. The poll
underneath is a backstop for a signal that never arrives, not the normal path.

The loop is deliberately dumb about Blood Bowl. It asks `handover.owed` who is
waited on — which already accounts for an unanswered Kick-off question outranking
the clock — fires that side, and watches for the answer to change. All the rules
stay in the engine.
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger("protoagent.plugins.bloodbowl")

#: How often to look at the board when nothing is in flight.
IDLE_POLL_S = 5.0

#: How long to let one turn run before deciding it is not coming back. A turn at
#: eleven-a-side takes minutes; this is the point at which waiting longer is worse
#: than trying again.
TURN_TIMEOUT_S = 600.0

#: A stopped runner must not spin.
_STOP = threading.Event()
#: Set when the board changes hands, so the loop looks NOW instead of on the next
#: poll. A person who has just ended their turn should not wait five seconds to
#: find out the agent noticed.
_WAKE = threading.Event()
_THREAD: threading.Thread | None = None


def wake() -> None:
    """The board changed hands — look now."""
    _WAKE.set()


def _rest(seconds: float) -> None:
    """Sleep, but come back early if something wakes us."""
    if _WAKE.wait(seconds):
        _WAKE.clear()


def _owed_key(owed: dict) -> tuple:
    """What counts as "the same thing to do" — so we can tell a real handover from
    the same one seen twice."""
    return (owed.get("side"), owed.get("why"), owed.get("half"), owed.get("turn"))


def _loop(run_turn) -> None:
    """Fire one seat at a time until the match ends.

    `run_turn(owed) -> None` does the host-side work of actually running a turn;
    it is injected so this loop can be tested without a host.
    """
    from .engine import handover
    from .store import load_match

    last_fired: tuple | None = None
    while not _STOP.is_set():
        try:
            match = load_match()
            if match is None or match.over:
                last_fired = None
                _rest(IDLE_POLL_S)
                continue

            owed = handover.owed(match)
            if not owed or owed.get("controller") != "agent":
                _rest(IDLE_POLL_S)
                continue

            key = _owed_key(owed)
            if key == last_fired:
                # Already fired this exact turn and the board has not moved on.
                # Waiting is right: the seat is still playing it.
                _rest(IDLE_POLL_S)
                continue

            log.info(
                "[bloodbowl] auto: firing %s for H%st%s (%s)",
                owed.get("side"),
                owed.get("half"),
                owed.get("turn"),
                owed.get("why"),
            )
            last_fired = key
            run_turn(owed)

            # WAIT FOR THE BOARD TO CHANGE HANDS, rather than firing again on a
            # timer. One turn in flight at a time is the whole point; a watchdog
            # that re-fires on a schedule is what produced the pile-up before.
            waited = 0.0
            while not _STOP.is_set() and waited < TURN_TIMEOUT_S:
                _STOP.wait(IDLE_POLL_S)
                waited += IDLE_POLL_S
                now = load_match()
                if now is None or now.over:
                    break
                if _owed_key(handover.owed(now)) != key:
                    log.info(
                        "[bloodbowl] auto: %s finished H%st%s", owed.get("side"), owed.get("half"), owed.get("turn")
                    )
                    break
            else:
                # Fell out on the timeout: the turn never handed over. Say so
                # loudly — a silent retry here is what made a stuck match look
                # like a slow one for hours.
                log.warning(
                    "[bloodbowl] auto: %s did not finish H%st%s within %.0fs — firing it again",
                    owed.get("side"),
                    owed.get("half"),
                    owed.get("turn"),
                    TURN_TIMEOUT_S,
                )
                last_fired = None
        except Exception:  # noqa: BLE001 — the runner must outlive any one turn
            log.exception("[bloodbowl] auto: loop error; continuing")
            _rest(IDLE_POLL_S)


def start(run_turn) -> threading.Thread:
    """Begin driving matches. Idempotent — one runner, never two.

    ⚠️ IT KEEPS THE RUNNER IT ALREADY HAS, including the `run_turn` it was built
    with. Starting a second would be two drivers, which is the entire class of bug
    this file exists to remove. A caller that needs a different one calls `stop()`
    first — and a test that forgets is the reason this note is here.
    """
    global _THREAD
    if _THREAD is not None and _THREAD.is_alive():
        log.debug("[bloodbowl] auto: already running; keeping the existing driver")
        return _THREAD
    _STOP.clear()
    _THREAD = threading.Thread(target=_loop, args=(run_turn,), name="bloodbowl-auto", daemon=True)
    _THREAD.start()
    log.info("[bloodbowl] auto mode running — it will play any match where both seats are the agent")
    return _THREAD


def stop() -> None:
    """Stop the driver and wait for it, so a caller can start a different one."""
    global _THREAD
    _STOP.set()
    _WAKE.set()
    t = _THREAD
    if t is not None and t.is_alive():
        t.join(timeout=5)
    _THREAD = None
