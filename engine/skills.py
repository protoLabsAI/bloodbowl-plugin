"""Skills as hooks, so adding one is a registration rather than an edit.

Blood Bowl has around a hundred Skills and Traits, and every one of them is an
exception to a rule somewhere. Written as branches inside the actions, the
movement code would be a wall of `if player.has_skill(...)` within a release. So
each Skill registers against a NAMED HOOK, the actions ask the registry, and a
new Skill touches exactly one file.

The other half of the design matters as much: **a Skill nobody has implemented is
reported, not ignored.** Every roster player carries their real Skill list, so the
engine always knows when a Skill it does not model is sitting on the pitch. It
says so, in the action's result and in the log, rather than quietly playing a
Troll as though Always Hungry did not exist. Silence there would be the same
failure this whole plugin exists to avoid — a confident answer with something
missing from underneath it.

Only Skills whose text has been read off the S3 source are registered. Guessing at
one from memory is exactly the confabulation the engine is meant to rule out.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

# --- the registry ---------------------------------------------------------

# hook name -> [(skill name, fn)]. A list, not a dict, so two Skills can answer
# the same hook and both are applied in registration order.
_HOOKS: dict[str, list[tuple[str, Callable]]] = {}
_MODELLED: set[str] = set()


def skill_hook(skill: str, hook: str):
    """Register ``fn`` as ``skill``'s behaviour at ``hook``."""

    def deco(fn: Callable) -> Callable:
        _HOOKS.setdefault(hook, []).append((skill, fn))
        _MODELLED.add(skill.casefold())
        return fn

    return deco


def modelled() -> set[str]:
    return set(_MODELLED)


def hooks_for(hook: str) -> list[tuple[str, Callable]]:
    return list(_HOOKS.get(hook, ()))


@dataclass
class SkillContext:
    """What a hook is allowed to look at. Deliberately narrow — a hook that can
    reach the whole match can also change it behind the action's back."""

    match: object
    player: object
    value: int = 0
    flags: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def apply_value_hook(hook: str, ctx: SkillContext, player) -> int:
    """Run every registered hook the PLAYER actually has, folding ``ctx.value``."""
    for skill, fn in hooks_for(hook):
        if player.has_skill(skill):
            fn(ctx)
    return ctx.value


def unmodelled_skills(player) -> list[str]:
    """The Skills this player has that the engine does not implement.

    Reported so a coach is never told a clean result that quietly ignored half a
    Troll's profile.
    """
    known = modelled()
    out = []
    for s in player.player.skills or []:
        base = s.split("(")[0].strip()
        if base.casefold() not in known:
            out.append(base)
    return sorted(set(out))


# --- reporting the gap, once ----------------------------------------------
#
# "Unmodelled is reported, never ignored" is a rule about HONESTY, not about
# volume, and the two pull apart at scale. Naming an Orc Blitzer's Break Tackle on
# every step of every activation, for twenty-two players over sixteen turns, is
# not more honest than saying it once — it is the same fact several hundred times,
# and a warning that always fires is one a coach stops reading.
#
# So the gap is reported TWICE, in two different registers:
#
#   * ``unmodelled_on_pitch`` is the standing summary, recomputed from the board
#     whenever anyone asks. It cannot go stale and needs no bookkeeping, so a
#     coach can always find out what this engine is not applying.
#   * ``first_mentions`` is the running one: the first time a Skill is actually
#     relevant to something that happened, the log says so, and then never again.
#
# The ledger for the second is the LOG — the same invariant as everything else
# here. Remembering "already said that" on the object would not survive the match
# being reloaded from disk between tool calls, and would re-announce every Skill
# on every call while looking like it worked.

NOTED = "unmodelled_noted"


def unmodelled_on_pitch(match) -> list[dict]:
    """Every unmodelled Skill currently on the pitch, and who is carrying it.

    The standing answer to "what is this engine not applying?" — derived from the
    board on demand rather than recorded, so it is right after a Casualty leaves
    and right again after a new drive brings the Knocked-out back.
    """
    holders: dict[str, list[str]] = {}
    for p in match.on_pitch():
        for s in unmodelled_skills(p):
            holders.setdefault(s, []).append(p.id)
    return [{"skill": skill, "players": sorted(ids), "count": len(ids)} for skill, ids in sorted(holders.items())]


def already_noted(match) -> set[str]:
    """Skills this match has already announced as unmodelled."""
    return {s for e in match.events if e.kind == NOTED for s in (e.detail.get("skills") or [])}


def first_mentions(match, skills) -> list[str]:
    """Of ``skills``, the ones this match has not mentioned yet."""
    return sorted(set(skills) - already_noted(match))


# --- the Skills that are actually modelled --------------------------------
#
# Each one quotes the S3 text it implements. If a Skill is not here, the engine
# does not apply it and says so.


@skill_hook("Jump Up", "stand_up_cost")
def _jump_up(ctx: SkillContext) -> None:
    """S3: "A Prone player with this Skill can stand up for free without having
    to spend 3 squares of movement to do so." """
    ctx.value = 0
    ctx.notes.append("Jump Up: stood up for free")


@skill_hook("Dodge", "dodge_reroll")
def _dodge(ctx: SkillContext) -> None:
    """S3: "Once per Turn, this player may re-roll a single Agility Test when
    attempting to Dodge." Once per TURN, not per activation — the engine tracks
    it on the player for the turn."""
    if not ctx.flags.get("dodge_reroll_used"):
        ctx.flags["may_reroll"] = True
        ctx.notes.append("Dodge: re-rolling the failed Agility Test")


@skill_hook("Prehensile Tail", "opponent_dodge_modifier")
def _prehensile_tail(ctx: SkillContext) -> None:
    """S3: "When an opposition player attempts to Dodge, Jump or Leap away from a
    square in this player's Tackle Zone, they apply an additional -1 modifier."

    Note this is the OPPONENT's Skill modifying our roll, which is why it hangs
    off a separate hook — the player rolling does not have it.
    """
    ctx.value -= 1
    ctx.notes.append("Prehensile Tail: -1 to the Dodge")


@skill_hook("Block", "both_down_optout")
def _block(ctx: SkillContext) -> None:
    """S3: "A player with this Skill may choose not to be Knocked Down when a Both
    Down result is applied during a Block Action that they are part of."

    Either party may have it — that is what makes Both Down a good result for a
    blocker who has Block against a target who does not.
    """
    ctx.flags["stays_up"] = True
    ctx.notes.append("Block: stays on their feet")


@skill_hook("Mighty Blow", "knockdown_bonus")
def _mighty_blow(ctx: SkillContext) -> None:
    """S3: "Whenever this player Knocks Down an opposition player during a Block
    Action, even if this player is also Knocked Down, they may apply a +1 modifier
    to either the Armour Roll or Injury Roll. This modifier may be applied after
    the roll has been made."
    """
    ctx.value += 1


@skill_hook("Thick Skull", "injury_outcome")
def _thick_skull(ctx: SkillContext) -> None:
    """S3: "When an Injury Roll is made for this player, they will only be
    Knocked-out on the roll of a 9; a roll of an 8 will be treated as a Stunned
    result."

    (The Stunty interaction — KO only on 8, a 7 becoming Stunned — is not modelled;
    no roster player here has both, and guessing at it is the failure this engine
    exists to avoid.)
    """
    if ctx.value == 8 and ctx.flags.get("outcome") == "knocked_out":
        ctx.flags["outcome"] = "stunned"
        ctx.notes.append("Thick Skull turns the 8 into a Stunned")


@skill_hook("Guard", "may_assist_while_marked")
def _guard(ctx: SkillContext) -> None:
    """S3: "This player can provide Offensive and Defensive Assists when a player
    performs a Block Action regardless of how many opposition players are Marking
    this player." """
    ctx.flags["assists_anyway"] = True
