"""The action registry: one module per Action, discovered by name.

Every Action answers the same two questions, and nothing outside these modules
gets to decide either of them:

    validate(match, cmd) -> Legality      may this be done, and if not, why
    resolve(match, cmd, dice) -> Outcome  what happened, as recorded Events

Splitting them is what makes the engine usable by an agent without letting the
agent adjudicate. A coach can ask ``validate`` for free, as many times as it
likes, to find out what is legal — that is ``bb_game_legal``. Only ``resolve``
rolls dice and only ``resolve`` produces facts.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

_ACTIONS: dict[str, dict[str, Callable]] = {}


@dataclass
class Legality:
    ok: bool
    reason: str = ""
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "reason": self.reason, **({"detail": self.detail} if self.detail else {})}


@dataclass
class Outcome:
    """What resolving an action produced.

    ``events`` are the facts to apply. ``turnover`` ends the team's turn, and is
    separate from "the action failed" because in Blood Bowl most failures do both
    and a few do not.
    """

    ok: bool
    events: list = field(default_factory=list)
    turnover: bool = False
    text: str = ""
    unmodelled: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "turnover": self.turnover,
            "text": self.text,
            "events": [e.to_dict() for e in self.events],
            "unmodelled_skills": self.unmodelled,
        }


def register(name: str, validate: Callable, resolve: Callable) -> None:
    _ACTIONS[name] = {"validate": validate, "resolve": resolve}


def names() -> list[str]:
    return sorted(_ACTIONS)


def get(name: str) -> dict | None:
    return _ACTIONS.get(name)


def load_all() -> None:
    """Import the action modules so their registrations run.

    Explicit rather than a directory scan: a scan makes the set of legal actions
    depend on what happens to be on disk, and an action that silently fails to
    import would look like an action that does not exist.
    """
    from . import move  # noqa: F401
