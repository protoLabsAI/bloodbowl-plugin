"""Auto mode: the one thing that fires a turn.

Turns used to be driven by an event that several routes published, turned into a
nudge, and retried by the scheduler when a fire failed. Every part was reasonable
and together they produced nine concurrent tasks for one handover, seats woken
with nothing to do, and retry storms that made a stuck match look like a slow one.

A loop does not need any of that: whose move is it, fire that seat, wait for the
board to change hands. One turn in flight, by construction.
"""

from __future__ import annotations

import threading
import time

import pytest
from bloodbowl import runner


@pytest.fixture(autouse=True)
def _a_clean_driver():
    """ONE RUNNER, AND NOT ONE LEFT OVER FROM ANOTHER TEST.

    `start` is idempotent and keeps the driver it already has — correct in
    production, where two drivers is the bug this file removes, and a trap in a
    suite where an earlier test (or `register()`) left one alive. These tests
    passed alone and failed together until this existed.
    """
    runner.stop()
    yield
    runner.stop()


def test_it_fires_the_side_that_is_owed_and_only_once(monkeypatch):
    """The failure this replaces: one handover, nine fires."""
    fired: list = []
    owed = {"side": "away", "controller": "agent", "half": 1, "turn": 2, "why": "turn"}

    class _Match:
        over = False
        controllers = {"home": "agent", "away": "agent"}

    monkeypatch.setattr(runner, "IDLE_POLL_S", 0.01)
    monkeypatch.setattr("bloodbowl.store.load_match", lambda: _Match())
    monkeypatch.setattr("bloodbowl.engine.handover.owed", lambda m: owed)

    t = runner.start(lambda o: fired.append(o["side"]))
    time.sleep(0.3)
    runner.stop()
    t.join(timeout=2)

    assert fired, "nobody was fired"
    assert len(fired) == 1, f"one handover fired {len(fired)} times — that is the bug it replaces"
    assert fired[0] == "away"


def test_it_does_not_fire_a_human_seat(monkeypatch):
    """Driving a person's turn for them is not auto mode, it is taking their go."""
    fired: list = []

    class _Match:
        over = False
        controllers = {"home": "human", "away": "agent"}

    monkeypatch.setattr(runner, "IDLE_POLL_S", 0.01)
    monkeypatch.setattr("bloodbowl.store.load_match", lambda: _Match())
    monkeypatch.setattr(
        "bloodbowl.engine.handover.owed",
        lambda m: {"side": "home", "controller": "human", "half": 1, "turn": 1, "why": "turn"},
    )

    t = runner.start(lambda o: fired.append(o))
    time.sleep(0.2)
    runner.stop()
    t.join(timeout=2)
    assert not fired, "it fired a turn belonging to a person"


def test_a_handover_wakes_it_rather_than_making_anyone_wait(monkeypatch):
    """ "im not waiting for it to schedule a loop to play." Ending your turn is a
    SIGNAL, not a schedule — the poll underneath is a backstop for a signal that
    never arrives, not the normal path."""
    monkeypatch.setattr(runner, "_WAKE", threading.Event())
    started = time.monotonic()

    def waker():
        time.sleep(0.05)
        runner.wake()

    threading.Thread(target=waker, daemon=True).start()
    runner._rest(5.0)
    assert time.monotonic() - started < 1.0, "a wake must not wait out the poll"


def test_the_match_being_over_stops_it_firing(monkeypatch):
    fired: list = []

    class _Over:
        over = True
        controllers = {"home": "agent", "away": "agent"}

    monkeypatch.setattr(runner, "IDLE_POLL_S", 0.01)
    monkeypatch.setattr("bloodbowl.store.load_match", lambda: _Over())
    monkeypatch.setattr("bloodbowl.engine.handover.owed", lambda m: {"side": "away", "controller": "agent"})

    t = runner.start(lambda o: fired.append(o))
    time.sleep(0.15)
    runner.stop()
    t.join(timeout=2)
    assert not fired, "a finished match must not be played on"


def test_a_flickering_handover_does_not_fire_the_same_turn_twice(monkeypatch):
    """THE DUPLICATE TASK, AND THE "turn is not mine" THAT CAME WITH IT.

    Remembering only the PREVIOUS key is not enough. `owed` can flicker — a
    question appears and is answered, a read lands mid-write — and an A -> B -> A
    sequence re-fires A. Observed live: "firing away for H1t1" twice, two tasks in
    one session, and a seat waking to a turn that had already moved on.
    """
    fired: list = []
    seq = [
        {"side": "away", "controller": "agent", "half": 1, "turn": 1, "why": "turn"},
        {"side": "away", "controller": "agent", "half": 1, "turn": 1, "why": "answer"},
        {"side": "away", "controller": "agent", "half": 1, "turn": 1, "why": "turn"},  # back to A
    ]
    calls = {"n": 0}

    class _Match:
        over = False
        controllers = {"home": "agent", "away": "agent"}
        session_ids = {"home": "s:home", "away": "s:away"}
        session_id = "s"

    def owed(_m):
        i = min(calls["n"] // 3, len(seq) - 1)
        calls["n"] += 1
        return seq[i]

    monkeypatch.setattr(runner, "IDLE_POLL_S", 0.01)
    monkeypatch.setattr(runner, "TURN_TIMEOUT_S", 0.05)
    monkeypatch.setattr("bloodbowl.store.load_match", lambda: _Match())
    monkeypatch.setattr("bloodbowl.engine.handover.owed", owed)

    t = runner.start(lambda o: fired.append((o["side"], o["why"])))
    time.sleep(0.5)
    runner.stop()
    t.join(timeout=2)

    turns = [f for f in fired if f == ("away", "turn")]
    assert len(turns) <= 1, f"the same turn fired {len(turns)} times after a flicker: {fired}"


def test_a_new_match_starts_with_a_clean_sheet(monkeypatch):
    """The same half/turn/side belongs to a different game — a second match must
    not be refused its first turn because the first match already played one."""
    fired: list = []
    state = {"sessions": {"home": "a:home", "away": "a:away"}}

    class _Match:
        over = False
        controllers = {"home": "agent", "away": "agent"}
        session_id = "a"

        @property
        def session_ids(self):
            return state["sessions"]

    monkeypatch.setattr(runner, "IDLE_POLL_S", 0.01)
    # Long on purpose: the driver must notice the new match because the MATCH
    # changed, not because it gave up waiting for the old turn.
    monkeypatch.setattr(runner, "TURN_TIMEOUT_S", 60.0)
    monkeypatch.setattr("bloodbowl.store.load_match", lambda: _Match())
    monkeypatch.setattr(
        "bloodbowl.engine.handover.owed",
        lambda m: {"side": "home", "controller": "agent", "half": 1, "turn": 1, "why": "turn"},
    )

    t = runner.start(lambda o: fired.append(o["side"]))
    time.sleep(0.15)
    assert len(fired) == 1
    state["sessions"] = {"home": "b:home", "away": "b:away"}  # a new match
    time.sleep(0.15)
    runner.stop()
    t.join(timeout=2)
    assert len(fired) == 2, "a new match must get its first turn fired"
