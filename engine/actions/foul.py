"""The Foul Action — sticking the boot in, and what the referee makes of it.

Every rule below is quoted from the S3 source.

    "A player that declares a Foul Action may also make a free Move Action before
     making the foul, but may not continue moving after the foul has been made.
     Only a single Foul Action can be declared each Turn."

    "The player must finish their Move Action adjacent to a Prone or Stunned
     opposition player in order to perform the Foul Action. To perform the Foul
     Action, the player makes an Armour Roll for the target of the Foul Action.
     Offensive and Defensive Assists will also apply to a Foul Action in the same
     manner as a Block Action. When making the Armour Roll, the player making the
     Foul Action may apply a +1 modifier for each Offensive Assist, and apply a -1
     modifier for each Defensive Assist."

    BEING SENT-OFF: "Regardless of the outcome, if during a Foul Action a natural
     double is rolled for either the Armour Roll or Injury Roll, then the player
     performing the Foul Action is Sent-off after the Foul Action has been
     completed. The Sent-off player is immediately removed from the pitch and will
     play no further part in the game. When a player on the active team is
     Sent-off, a Turnover is caused."

    ARGUE THE CALL: "When a player is Sent-off for any reason, their Coach may
     attempt to Argue the Call - roll a D6":
       1    "YOU'RE OUTTA HERE!"  — still Sent-off, AND "their controlling Coach
            may not attempt to Argue the Call for the remainder of the game."
       2-5  "I DON'T CARE!"       — still Sent-off.
       6    "WELL, WHEN YOU PUT IT LIKE THAT..." — "The player is placed back in
            the square they were in and is not Sent-off, though a Turnover is
            still caused."

THE FOUL IS THE MIRROR OF THE BLOCK. A Block may only target a Standing player; a
Foul may only target one who is Prone or Stunned. Neither is a special case of the
other, and the two refusals name each other so a coach who picks the wrong one is
told which they wanted.

Note what is NOT here: Mighty Blow. It reads "whenever this player Knocks Down an
opposition player during a BLOCK ACTION", and a Foul is not one — so ``risk_injury``
is called with no ``by``, which is what suppresses it.
"""

from __future__ import annotations

from ..dice import Roll
from ..events import Event
from ..injury import risk_injury
from ..rules import adjacent, assist_count
from ..skills import unmodelled_skills
from ..state import Match
from . import Legality, Outcome, Recorder, ended, refuse_if_spent, register

ARGUE_REINSTATED = 6
ARGUE_BANS_COACH = 1


def _assists(match: Match, p, t) -> tuple[int, int]:
    """Offensive and Defensive Assists, "in the same manner as a Block Action" —
    so literally the same function, and the same exclusion of the two
    participants."""
    return assist_count(match, p.side, t, exclude={p.id}), assist_count(match, t.side, p, exclude={t.id})


def validate(match: Match, cmd: dict) -> Legality:
    if match.over:
        return Legality(False, "the match is over")
    p = match.by_id(str(cmd.get("player") or ""))
    if p is None:
        return Legality(False, f"no player with id {cmd.get('player')!r}")
    if p.side != match.clock.active:
        return Legality(False, f"it is {match.clock.active}'s turn, and that player is {p.side}")
    spent = refuse_if_spent(match, p, "foul")
    if spent:
        return Legality(False, spent)
    if p.place != "pitch" or p.down != "standing":
        return Legality(False, "only a Standing player on the pitch can put the boot in")

    t = match.by_id(str(cmd.get("target") or ""))
    if t is None:
        return Legality(False, f"no target with id {cmd.get('target')!r}")
    if t.place != "pitch":
        return Legality(False, "the target is not on the pitch")
    if t.side == p.side:
        return Legality(False, "you cannot Foul your own team-mate")
    if t.down == "standing":
        # The exact complement of the Block's own rule, so the refusal points at it.
        return Legality(
            False,
            f"{t.name()} is Standing — a Foul may only target a Prone or Stunned player. Block them instead.",
        )
    if not adjacent(p.x, p.y, t.x, t.y):
        return Legality(False, "a Foul is put in from an adjacent square")

    off, dfn = _assists(match, p, t)
    return Legality(
        True,
        "",
        {
            "target": t.id,
            "armour_target": t.player.AV,
            "offensive_assists": off,
            "defensive_assists": dfn,
            "armour_modifier": off - dfn,
            # Every Foul risks it, which is the whole tension of the Action.
            "sending_off_on": "a natural double on either the Armour or Injury Roll",
            "may_argue": p.side not in match.argue_banned,
        },
    )


def _natural_double(events) -> Event | None:
    """The Armour or Injury Roll that came up a natural double, if either did.

    NATURAL — the raw dice, before the assist modifier. A +2 from assists turning
    a 3 and a 5 into "10" is not a double, and a modified 4+4 still is.
    """
    for e in events:
        if e.kind not in ("armour_roll", "injury_roll"):
            continue
        for r in e.rolls:
            if len(r.dice) == 2 and r.dice[0] == r.dice[1]:
                return e
    return None


def _argue_the_call(match: Match, p, dice, rec: Recorder) -> bool:
    """Roll it. Returns True if the player stays on the pitch.

    Only the sending-off is optional to argue; the Turnover is not — "though a
    Turnover is still caused" is the sentence that makes a successful argument
    worth less than it looks.
    """
    if p.side in match.argue_banned:
        rec.emit(
            Event(
                kind="note",
                actor=p.id,
                text=f"The {p.side} Coach was ejected earlier and may not Argue the Call again this game.",
            )
        )
        return False

    d = dice.d6()
    roll = Roll(kind="Argue the Call", dice=[d], total=d, note="1 ejects the Coach · 6 reinstates the player")
    dice.rolls.append(roll)

    if d == ARGUE_REINSTATED:
        rec.emit(
            Event(
                kind="argue_the_call",
                actor=p.id,
                detail={"result": d, "outcome": "reinstated"},
                rolls=[roll],
                text='"WELL, WHEN YOU PUT IT LIKE THAT…" — the referee is swayed and '
                + f"{p.name()} stays on. {roll.describe()}",
            )
        )
        rec.emit(
            Event(
                kind="player_reinstated",
                actor=p.id,
                detail={"x": p.x, "y": p.y},
                text=f"{p.name()} is placed back in the square they were in.",
            )
        )
        return True

    banned = d == ARGUE_BANS_COACH
    rec.emit(
        Event(
            kind="argue_the_call",
            actor=p.id,
            detail={"result": d, "outcome": "ejected_coach" if banned else "refused", "side": p.side},
            rolls=[roll],
            text=(
                f'"YOU\'RE OUTTA HERE!" — the referee ejects the {p.side} Coach as well; '
                f"they may not Argue the Call again this game. {roll.describe()}"
                if banned
                else f'"I DON\'T CARE!" — the referee is unmoved. {roll.describe()}'
            ),
        )
    )
    return False


def resolve(match: Match, cmd: dict, dice) -> Outcome:
    legal = validate(match, cmd)
    if not legal.ok:
        return Outcome(ok=False, text=legal.reason)

    p = match.by_id(str(cmd["player"]))
    t = match.by_id(str(cmd["target"]))
    rec = Recorder(match)
    unmodelled = sorted(set(unmodelled_skills(p)) | set(unmodelled_skills(t)))
    d = legal.detail
    mod = d["armour_modifier"]

    rec.emit(
        Event(
            kind="foul_committed",
            actor=p.id,
            detail={"target": t.id, **{k: d[k] for k in ("offensive_assists", "defensive_assists", "armour_modifier")}},
            text=f"{p.name()} Fouls {t.name()} while they are down"
            + (f" ({mod:+d} from assists)." if mod else ".")
            + " The referee is watching…",
        )
    )

    # No `by`: Mighty Blow is a Block Action Skill and must not ride along here.
    hurt = risk_injury(match, t, dice, by=None, armour_modifier=mod)
    rec.absorb(hurt)

    caught = _natural_double(hurt)
    if caught is None:
        rec.emit(ended(p.id, "foul", f"{p.name()} gets away with it. Their activation ends."))
        return Outcome(
            ok=True,
            events=rec.events,
            text=f"{p.name()} Fouled {t.name()} and the referee saw nothing.",
            unmodelled=unmodelled,
        )

    rec.emit(
        Event(
            kind="player_sent_off",
            actor=p.id,
            detail={"reason": "foul", "spotted_on": caught.kind},
            text=f"The referee spots the double on the {caught.kind.replace('_', ' ')} — {p.name()} is SENT OFF.",
        )
    )
    stayed = _argue_the_call(match, p, dice, rec)
    rec.emit(ended(p.id, "foul"))

    # "When a player on the active team is Sent-off, a Turnover is caused", and on
    # a successful argument "a Turnover is still caused". Either way the turn ends.
    return Outcome(
        ok=False,
        events=rec.events,
        turnover=True,
        text=(
            f"{p.name()} was caught fouling but argued their way out of it — turnover."
            if stayed
            else f"{p.name()} was caught fouling and is Sent off — turnover."
        ),
        unmodelled=unmodelled,
    )


register("foul", validate, resolve)
