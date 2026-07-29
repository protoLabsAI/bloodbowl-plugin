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

# S3 caps most Actions at one per TEAM per Turn, and says so action by action:
# "Only a single Pass Action can be declared each Turn", and the same sentence for
# Hand-off, Secure the Ball, Blitz, Foul and Throw Team-mate. The two exceptions
# are stated just as plainly — "There is no limit to the number of players that
# can declare a Move Action each Turn", and the same for Block.
ONCE_PER_TURN = ("blitz", "pass", "handoff", "secure", "foul")

# Actions that begin with a free Move: "A player that declares a Pass Action may
# also make a free Move Action before making the pass, but may not continue moving
# after the pass has been made." Hand-off, Secure the Ball, Foul and Throw
# Team-mate all carry the same clause.
FREE_MOVE_FIRST = ("pass", "handoff", "secure", "foul")


def refuse_if_spent(match, p, action: str) -> str:
    """Why ``p`` may not declare ``action`` right now — "" if they may.

    The subtlety is which flag to ask. ``acted`` means an activation has BEGUN,
    and a single step of movement sets it — so testing it here refused the free
    Move the rules explicitly grant, and move-then-pass was impossible for as long
    as passing has existed. ``done`` means the activation is OVER, which is the
    real question.

    ``p.action`` covers the one case where an activation is neither: a player
    part-way through a Blitz has declared their Action already, so they may not
    now decide it was a Pass.
    """
    if p.done:
        return f"{p.name()}'s activation is over"
    if p.action and p.action != action:
        return f"{p.name()} has already declared a {p.action.title()} Action this activation"
    used = match.turn_actions.get(action)
    if used and used != p.id:
        who = match.by_id(used)
        return f"{match.clock.active} have already used their one {action.title()} Action this turn" + (
            f" — {who.name()} did" if who is not None else ""
        )
    return ""


def ended(actor: str, action: str, text: str = ""):
    """The event that closes an activation, tagged with the Action it closes.

    The tag is what lets ``apply`` record a once-per-turn Action without knowing
    any rules: the decision is made here, beside the quoted text, and the event
    carries the answer.
    """
    from ..events import Event

    return Event(
        kind="activation_ended",
        actor=actor,
        detail={"action": action, "once_per_turn": action in ONCE_PER_TURN},
        text=text,
    )


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

    ``events`` are the facts that were APPLIED, in order — resolve owns applying
    them, and the caller must not apply them again. That is not an arbitrary
    choice: a Chain Push cannot work out where the next player goes until the
    previous one has actually moved, so any multi-step action has to mutate as it
    goes. Having one action return facts to apply and another apply them itself
    would be the same contract described two ways, which is how a double-applied
    event gets shipped.

    ``turnover`` ends the team's turn, and is separate from "the action failed"
    because in Blood Bowl most failures do both and a few do not.
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


class Recorder:
    """Applies each fact as it is made and keeps the ordered list.

    The one place the "resolve applies its own events" contract lives, so every
    action obeys it the same way.
    """

    def __init__(self, match):
        self.match = match
        self.events: list = []

    def emit(self, event):
        self.match.apply(event)
        self.events.append(event)
        return event

    def extend(self, events) -> None:
        """Apply and record each event. For facts not yet applied."""
        for e in events:
            self.emit(e)

    def absorb(self, events) -> None:
        """Record events a HELPER has already applied.

        The ball and injury helpers apply as they go, because each step depends on
        the last — a bounce has to land before anyone can try to catch it. Using
        ``extend`` on those would apply them a second time, moving the ball twice
        and scoring twice. Two verbs, so which one a call site wants is a decision
        rather than an accident.
        """
        self.events.extend(events)


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
    from . import blitz, block, forego, foul, handoff, move, secure, throw  # noqa: F401
