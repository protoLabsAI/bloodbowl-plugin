"""The event log: what happened, in order, with the dice that decided it.

An event is a FACT, not a request. ``MovedTo`` means the player moved; it is not
re-checked when applied. All the judgement — was the dodge legal, did it succeed —
happens once, in an action's ``resolve``, and its outcome is frozen here.

That split is what makes replay exact. Re-watching a match is ``fold(events)``:
apply each recorded fact to a fresh match. No dice are rolled and no rule is
consulted, so a match saved before a rules change re-watches exactly as it played.

It is also what lets the agent narrate honestly. The log already says "Dodge
needed 3+, rolled 2, turnover"; the coach quotes it rather than inferring what
must have happened from a board that changed. Every event carries the rolls that
produced it for exactly this reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .dice import Roll

# Event kinds. Strings rather than an enum because they are serialized into the
# saved match and read by the view and the tools; a string survives both.
KINDS = (
    "match_started",
    "turn_started",
    "player_stood_up",
    "player_moved",
    "player_fell",
    "player_placed_prone",
    "player_pushed",
    "player_followed_up",
    "block_rolled",
    "armour_roll",
    "injury_roll",
    "player_condition",
    "player_left_pitch",
    "ball_moved",
    "ball_picked_up",
    "ball_dropped",
    "ball_out_of_bounds",
    "touchdown",
    "turnover",
    "turn_ended",
    "note",
)


@dataclass
class Event:
    """One recorded fact.

    ``rolls`` holds every die that produced it, so the log is self-describing:
    nothing that reads it later has to guess which roll caused which outcome.
    """

    kind: str
    actor: str = ""  # player id, when one acted
    detail: dict = field(default_factory=dict)
    rolls: list[Roll] = field(default_factory=list)
    text: str = ""  # plain-language line, written when the fact is known

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "actor": self.actor,
            "detail": dict(self.detail),
            "rolls": [r.to_dict() for r in self.rolls],
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Event:
        return cls(
            kind=str(d.get("kind") or "note"),
            actor=str(d.get("actor") or ""),
            detail=dict(d.get("detail") or {}),
            rolls=[Roll.from_dict(r) for r in (d.get("rolls") or [])],
            text=str(d.get("text") or ""),
        )


def describe(event: Event) -> str:
    """The line a coach reads. Prefers the text written when the fact was known,
    and falls back to the rolls rather than to a guess."""
    if event.text:
        return event.text
    if event.rolls:
        return f"{event.kind}: " + "; ".join(r.describe() for r in event.rolls)
    return event.kind
