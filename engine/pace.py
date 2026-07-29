"""How fast the agent is allowed to play.

A model can take eight activations in under a second. That is not a game — it is a
diff. The board polls every 2.5 seconds, so a turn played at full speed arrives as
one jump: the ball is somewhere else, two of your players are down, and nothing you
can watch explains how. The point of playing an opponent is seeing them play.

So the agent's own actions are PACED: a minimum wall-clock gap between them,
enforced where the agent's tools enter the engine and nowhere else. The human's
clicks are never paced — a person is already as slow as a person.

Three things this is deliberately not:

* Not a rate limit. It does not refuse anything; it waits and then proceeds. A
  refusal would just teach the agent to retry, and a retry loop is a worse
  experience than a pause.
* Not in the engine. `engine.game.act` knows nothing about it. The pace belongs to
  the SURFACE the agent acts through, which is why it lives beside the tools rather
  than inside the rules.
* Not in tests. `configure(0)` turns it off, and the suite does exactly that — a
  test that sleeps for real is a test nobody runs twice.

The default is a guess about reading speed rather than about rules, which is why it
is config (`bloodbowl.agent_pace_s`) and not a constant in the middle of a module.
"""

from __future__ import annotations

import threading
import time

# Seconds between two consecutive agent actions. About the time it takes to read a
# line of the log and look at where the piece went.
DEFAULT_PACE_S = 2.0

_pace_s = DEFAULT_PACE_S
_last = 0.0
_lock = threading.Lock()


def configure(cfg) -> None:
    """Read `bloodbowl.agent_pace_s`. Accepts a plain number for the tests."""
    global _pace_s
    if isinstance(cfg, int | float):
        _pace_s = max(0.0, float(cfg))
        return
    try:
        _pace_s = max(0.0, float((cfg or {}).get("agent_pace_s", DEFAULT_PACE_S)))
    except (TypeError, ValueError):
        _pace_s = DEFAULT_PACE_S


def seconds() -> float:
    return _pace_s


def wait() -> float:
    """Block until enough time has passed since the last agent action.

    Returns how long it actually waited, so the caller can say so. Thread-safe
    because the tools may be called from a worker: two actions that raced would
    both see an old timestamp and neither would wait.
    """
    global _last
    if _pace_s <= 0:
        return 0.0
    with _lock:
        now = time.monotonic()
        gap = now - _last
        delay = max(0.0, _pace_s - gap)
        # Reserve the slot BEFORE sleeping, so a second caller waiting on the lock
        # queues behind this action rather than beside it.
        _last = now + delay
    if delay:
        time.sleep(delay)
    return delay


def reset() -> None:
    """Forget the last action — used when a match starts, so the first move of a
    game is not made to wait for something that happened in a previous one."""
    global _last
    _last = 0.0
