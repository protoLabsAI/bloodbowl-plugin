"""Dice as a service, so rules can be tested without randomness and matches replay.

Every roll in the engine goes through one of these, and every roll is RECORDED
into the event that caused it. That recording is the thing that makes replay
real, and it is worth being precise about why a seed alone is not enough:

* ``SeededDice`` gives REPRODUCIBLE GENERATION — same seed plus the same commands
  produces the same match. That holds only while the engine draws dice in exactly
  the same order, so the moment a skill is added that rolls one extra die, every
  later draw in the shared stream shifts and an old match regenerates differently.
* ``ReplayDice`` gives FAITHFUL REPLAY — it hands back the results recorded in the
  log, so a saved match resolves the same way it originally did no matter how the
  rules have moved on since. It refuses to invent a roll the log does not hold,
  which turns "the engine now rolls more than it used to" into a loud failure
  instead of a quietly different game.

Keep both. The seed is stored on the match so a match can be regenerated; the log
is what guarantees it can be re-watched.

The recording is also what the agent narrates from — a coach reading "Dodge needed
3+, rolled 2" is reading the engine's own arithmetic rather than reconstructing it
from a board that changed underneath. That is the whole reason this plugin exists.

DETERMINISM RULES for anything in this package: no wall-clock, no unseeded random,
no iteration over a set where order reaches a roll, and no dict ordering assumed
beyond insertion. Player lists stay in a stable order. Every one of those is a way
a replay drifts without anything looking broken.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Protocol

# The six faces of a Block die, using S3's OWN names. The earlier spelling here
# was the previous edition's ("attacker down", "defender stumbles", "defender
# down"); S3 calls them Player Down, Stumble and POW, and the names end up in the
# log a coach reads, so they should be the ones printed on the die.
#
# VERIFIED from source: the five result types and what each does.
# ASSUMED (standard across every edition, not re-read from S3): the distribution,
# two of the six faces being Push Back. If that ever proves wrong, this tuple is
# the single place it is wrong.
BLOCK_FACES = (
    "player_down",
    "both_down",
    "push_back",
    "push_back",
    "stumble",
    "pow",
)

BLOCK_LABELS = {
    "player_down": "Player Down",
    "both_down": "Both Down",
    "push_back": "Push Back",
    "stumble": "Stumble",
    "pow": "POW!",
}


@dataclass
class Roll:
    """One recorded roll. ``target`` and ``passed`` are set for tests against a
    number; a raw roll (scatter direction, block dice) leaves them None."""

    kind: str  # "dodge" | "gfi" | "armour" | "block" | "d6" | "d8" ...
    dice: list[int | str]
    total: int | None = None
    target: int | None = None
    modifier: int = 0
    passed: bool | None = None
    note: str = ""

    def describe(self) -> str:
        """The line a coach reads, so it has to survive being read literally.

        It used to print the POST-modifier total followed by the modifier —
        "rolled 3-3 — passed" — which reads as 3 minus 3 equals 0, and passing on
        0 against a 3+ looks like a broken engine. It was a natural 6 with a -3.
        Show the raw dice, then the modifier, then the total they make.
        """
        raw = "+".join(str(d) for d in self.dice)
        if self.target is None:
            return f"{self.kind}: {raw}"
        verdict = "passed" if self.passed else "FAILED"
        if self.modifier:
            return f"{self.kind}: needed {self.target}+, rolled {raw} {self.modifier:+d} = {self.total} — {verdict}"
        return f"{self.kind}: needed {self.target}+, rolled {raw} — {verdict}"

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "dice": list(self.dice),
            "total": self.total,
            "target": self.target,
            "modifier": self.modifier,
            "passed": self.passed,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Roll:
        return cls(
            kind=str(d.get("kind") or "d6"),
            dice=list(d.get("dice") or []),
            total=d.get("total"),
            target=d.get("target"),
            modifier=int(d.get("modifier") or 0),
            passed=d.get("passed"),
            note=str(d.get("note") or ""),
        )


class Dice(Protocol):
    def d6(self) -> int: ...
    def d8(self) -> int: ...
    def block(self, count: int) -> list[str]: ...

    def dn(self, sides: int) -> int:
        """Any other die the rules call for.

        The table needs a D3 (Pitch Invasion) and a D16 (the Casualty Table), and
        several rules say "randomly select one of their players", which is a die
        with as many sides as they have players. Adding those one at a time would
        have meant three near-identical methods across four implementations, so
        there is one — and d6/d8 stay because they are what the rules say
        everywhere else, and a log reading "d6: 4" is worth more than "dn(6): 4".
        """
        ...


@dataclass
class SeededDice:
    """Real randomness, reproducible. Same seed + same actions = same match."""

    seed: int = 0
    rolls: list[Roll] = field(default_factory=list)
    _rng: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def d6(self) -> int:
        return self._rng.randint(1, 6)

    def d8(self) -> int:
        return self._rng.randint(1, 8)

    def dn(self, sides: int) -> int:
        return self._rng.randint(1, max(1, sides))

    def block(self, count: int) -> list[str]:
        return [BLOCK_FACES[self._rng.randint(0, 5)] for _ in range(max(1, count))]


@dataclass
class ScriptedDice:
    """Dice that come out in a stated order — the test author's dice.

    Exhausting the script is an ERROR rather than a fallback to random. A rules
    test that quietly starts rolling for real stops testing what it claims to.
    """

    script: list[int] = field(default_factory=list)
    block_script: list[list[str]] = field(default_factory=list)
    rolls: list[Roll] = field(default_factory=list)
    _i: int = 0
    _b: int = 0

    def _next(self, sides: int) -> int:
        if self._i >= len(self.script):
            raise AssertionError(
                f"ScriptedDice ran out after {self._i} rolls — the code under test "
                "rolled more dice than the test scripted."
            )
        v = self.script[self._i]
        self._i += 1
        if not 1 <= v <= sides:
            raise AssertionError(f"scripted {v} is not a valid d{sides} result")
        return v

    def d6(self) -> int:
        return self._next(6)

    def d8(self) -> int:
        return self._next(8)

    def dn(self, sides: int) -> int:
        return self._next(max(1, sides))

    def block(self, count: int) -> list[str]:
        if self._b >= len(self.block_script):
            raise AssertionError("ScriptedDice ran out of block dice")
        faces = self.block_script[self._b]
        self._b += 1
        return faces


@dataclass
class ReplayDice:
    """The original dice of a recorded match, served back in order.

    NOT how a match is re-watched. Re-watching is ``fold(events)`` — replaying a
    recorded log needs no dice and no rules, so it is exact by construction and
    cannot drift. That is the primary mechanism and it lives in state.py.

    This class is for RE-EXECUTION: feed a saved match's raw dice back through
    today's rules and compare the events that come out against the events that
    were recorded. Identical means the engine still plays that match the same way;
    different means a rules change altered a real game, and the diff says exactly
    where. A corpus of saved matches then becomes a regression suite that no
    hand-written test could match for coverage.

    Running out of recorded dice means the engine now asks for more than it did,
    which is itself the signal — so it raises rather than inventing a roll.
    """

    recorded: list[Roll] = field(default_factory=list)
    rolls: list[Roll] = field(default_factory=list)
    _i: int = 0

    @classmethod
    def from_events(cls, events: list) -> ReplayDice:
        out: list[Roll] = []
        for e in events:
            out.extend(getattr(e, "rolls", ()) or ())
        return cls(recorded=out)

    def _next(self, kind: str) -> Roll:
        if self._i >= len(self.recorded):
            raise ReplayDivergence(
                f"replay exhausted after {self._i} recorded rolls but the engine asked for another "
                f"({kind}) — this match was played under different rules."
            )
        r = self.recorded[self._i]
        self._i += 1
        return r

    def d6(self) -> int:
        r = self._next("d6")
        return int(r.dice[0])

    def d8(self) -> int:
        r = self._next("d8")
        return int(r.dice[0])

    def dn(self, sides: int) -> int:
        r = self._next(f"d{sides}")
        return int(r.dice[0])

    def block(self, count: int) -> list[str]:
        r = self._next("block")
        return [str(f) for f in r.dice]


class ReplayDivergence(AssertionError):
    """Raised when a recorded match no longer matches what the engine wants."""


def roll_2d6(dice: Dice, kind: str, target: int, modifier: int = 0, note: str = "") -> Roll:
    """Two dice against a target — Armour and Injury.

    Deliberately NOT ``roll_target``. The natural-1-fails / natural-6-succeeds rule
    belongs to single-die Agility Tests; an Armour Roll is a straight 2D6 compared
    to the Armour Value, and applying the D6 rule to it would make armour break on
    a double six that should not have, and hold on a snake-eyes that should have.
    """
    a, b = dice.d6(), dice.d6()
    total = a + b + modifier
    r = Roll(
        kind=kind,
        dice=[a, b],
        total=total,
        target=target,
        modifier=modifier,
        passed=total >= target,
        note=note,
    )
    dice.rolls.append(r)
    return r


def roll_target(dice: Dice, kind: str, target: int, modifier: int = 0, note: str = "") -> Roll:
    """A single d6 against a target, the shape most Blood Bowl tests take.

    A natural 1 always fails and a natural 6 always succeeds, whatever the
    modifiers — the rule that keeps a heavily-modified test from becoming
    automatic in either direction.
    """
    raw = dice.d6()
    total = raw + modifier
    if raw == 1:
        passed = False
    elif raw == 6:
        passed = True
    else:
        passed = total >= target
    r = Roll(kind=kind, dice=[raw], total=total, target=target, modifier=modifier, passed=passed, note=note)
    dice.rolls.append(r)
    return r
