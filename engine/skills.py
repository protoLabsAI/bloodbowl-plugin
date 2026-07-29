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

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

CATALOGUE = Path(__file__).resolve().parent.parent / "data" / "skills.json"

# --- the registry ---------------------------------------------------------

# hook name -> [(skill name, fn)]. A list, not a dict, so two Skills can answer
# the same hook and both are applied in registration order.
_HOOKS: dict[str, list[tuple[str, Callable]]] = {}
_MODELLED: set[str] = set()
# Skills the engine applies only PARTLY, and what it leaves out. "Modelled" and
# "not modelled" is a binary that flatters: a Skill with two clauses of which one
# is applied would otherwise report as modelled and quietly do half its job, which
# is the same failure as saying nothing — worse, because it sounds settled.
_PARTIAL: dict[str, str] = {}


def skill_hook(skill: str, hook: str, partial: str = ""):
    """Register ``fn`` as ``skill``'s behaviour at ``hook``.

    ``partial`` names what the engine does NOT apply, for a Skill that is modelled
    in part. It is reported beside the Skill wherever the Skill is.
    """

    def deco(fn: Callable) -> Callable:
        _HOOKS.setdefault(hook, []).append((skill, fn))
        _MODELLED.add(skill.casefold())
        if partial:
            _PARTIAL[skill.casefold()] = partial
        return fn

    return deco


def partial_skills() -> dict[str, str]:
    """Skill (casefolded) -> what the engine leaves out."""
    return dict(_PARTIAL)


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


def can_use(player, skill: str) -> bool:
    """May this player use this Skill right now?

    S3, on Distracted: "Whilst a player is Distracted, they cannot use ACTIVE
    Skills or Traits, cannot attempt to Intercept a Pass Action, and cannot
    attempt to Catch the ball."

    Active versus Passive is in the shipped catalogue, so this is the one rule the
    engine can enforce for all 108 at once — including the 81 it does not model.
    Unknown Skills are allowed rather than blocked: a catalogue that failed to load
    must not silently switch every Skill off.
    """
    if not player.has_skill(skill):
        return False
    if not getattr(player, "distracted", False):
        return True
    entry = catalogue().get(skill.casefold())
    return not (entry and entry.get("when") == "Active")


def apply_value_hook(hook: str, ctx: SkillContext, player) -> int:
    """Run every registered hook the PLAYER actually has, folding ``ctx.value``."""
    for skill, fn in hooks_for(hook):
        if player.has_skill(skill):
            fn(ctx)
    return ctx.value


# --- the two hooks every roll goes through --------------------------------
#
# A Skill that changes a roll changes one of two things: the number, or whether
# you get a second go. Rather than a hook per test — dodge_modifier,
# catch_modifier, pickup_modifier — there is ONE of each, and the test's NAME is
# in the context. Skills that apply to several tests then read as they are
# written: Nerves of Steel says "when making an Agility Test to Catch the ball, or
# when making a Passing Ability Test to Pass", and that is one function with one
# `in ("catch", "pass")`.
#
# The tests, by the name the rules use: dodge · catch · pick_up · intercept · pass


def roll_modifier(match, player, test: str, base: int = 0, **flags) -> SkillContext:
    """Every Skill modifier that applies to ``player``'s own ``test`` roll.

    Returns the whole context: ``value`` is the modifier, ``notes`` go in the log
    (a coach reading "needed 3+, rolled 2 — passed" with no explanation has been
    handed a mystery rather than an adjudication), and ``flags`` carries anything
    a once-per-turn Skill needs recorded.
    """
    ctx = SkillContext(match=match, player=player, value=base, flags={"test": test, **flags})
    for skill, fn in hooks_for("roll_modifier"):
        if can_use(player, skill):
            fn(ctx)
    # …and the sky. Three of the five Weather conditions are modifiers on named
    # tests, which is what this hook already carries, so they ride the same rail
    # rather than being sprinkled through every roll site.
    from .weather import modifier as weather_modifier
    from .weather import name_of

    sky = weather_modifier(getattr(match, "weather", "perfect"), test)
    if sky:
        ctx.value += sky
        ctx.notes.append(f"{name_of(match.weather)}: {sky} to the {test.replace('_', ' ')}")
    return ctx


def may_reroll(match, player, test: str, **flags) -> tuple[bool, str]:
    """May ``player`` re-roll this failed ``test``, and under what name?

    Only Skills — Team Re-rolls are a separate thing the engine does not model.
    """
    ctx = SkillContext(match=match, player=player, flags={"test": test, **flags})
    for skill, fn in hooks_for("reroll"):
        if can_use(player, skill):
            fn(ctx)
            if ctx.flags.get("may_reroll"):
                return True, skill
    return False, ""


# --- activation gates ------------------------------------------------------
#
# Five Traits share one shape: "Whenever this player is activated, AFTER DECLARING
# THEIR ACTION they must roll a D6", and on a failure something goes wrong. They
# differ only in the target, the modifier and the consequence — so they are one
# mechanism with three numbers rather than five copies of a paragraph.
#
# The consequences, all defined in the rules rather than invented here:
#   distracted      no Tackle Zone, no Active Skills, no Catch, no Intercept, and
#                   "their activation immediately ends"
#   rooted          cannot Move, cannot Follow-up, cannot be Pushed Back
#   end_activation  nothing happens; the activation is simply over
#   lash_out        an adjacent Standing team-mate is Knocked Down


def activation_gates(match, player, action: str) -> list[dict]:
    """Every gate this player must pass before performing ``action``."""
    out = []
    for skill, fn in hooks_for("activation_gate"):
        if player.has_skill(skill):  # NOT can_use: a Trait you cannot avoid
            ctx = SkillContext(match=match, player=player, flags={"action": action})
            fn(ctx)
            out.append({"skill": skill, "notes": ctx.notes, **ctx.flags})
    return out


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
    out = []
    for skill, ids in sorted(holders.items()):
        row = {"skill": skill, "players": sorted(ids), "count": len(ids)}
        # What the rulebook calls it, so the summary itself cannot be glossed.
        # The full text is a `bb_get_skill` away rather than inline: 12 skills at
        # 400 characters each would bury the board this rides with.
        entry = catalogue().get(skill.casefold())
        if entry:
            row.update({"kind": entry["kind"], "category": entry["category"], "when": entry["when"]})
        out.append(row)
    return out


def partly_modelled_on_pitch(match) -> list[dict]:
    """Skills on the pitch that the engine applies only in part, and what it
    leaves out. The companion to ``unmodelled_on_pitch`` — a coach needs both, or
    a half-applied Skill reads as a fully applied one."""
    holders: dict[str, list[str]] = {}
    for p in match.on_pitch():
        for raw in p.player.skills or []:
            base = raw.split("(")[0].strip()
            if base.casefold() in _PARTIAL:
                holders.setdefault(base, []).append(p.id)
    return [
        {"skill": skill, "players": sorted(ids), "not_applied": _PARTIAL[skill.casefold()]}
        for skill, ids in sorted(holders.items())
    ]


def already_noted(match) -> set[str]:
    """Skills this match has already announced as unmodelled."""
    return {s for e in match.events if e.kind == NOTED for s in (e.detail.get("skills") or [])}


def first_mentions(match, skills) -> list[str]:
    """Of ``skills``, the ones this match has not mentioned yet."""
    return sorted(set(skills) - already_noted(match))


# --- the catalogue --------------------------------------------------------
#
# All 108 Skills and Traits with their real text, so a coach can QUOTE one.
#
# This is the cheap half of the fix for the failure in docs/HANDOFF.md §1. Asked
# to play a Foul, the agent drove the engine perfectly and quoted every roll — and
# then explained an unmodelled Skill unprompted, saying Break Tackle was "an
# ST-based alternative" to the dodge. It is a +1/+2/+3 MODIFIER to the Agility
# Test. The correct text existed and went unread.
#
# Modelling a Skill changes the game and has to be done one careful hook at a
# time. Shipping its TEXT changes what can honestly be said about it, costs one
# JSON file, and works for all 108 at once — including every one the engine will
# never model.


@lru_cache(maxsize=1)
def catalogue() -> dict:
    """Name (casefolded) -> entry. Empty if the catalogue is missing, because a
    plugin that cannot describe a Skill should still be able to play one."""
    try:
        raw = json.loads(CATALOGUE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k.casefold(): v for k, v in (raw.get("skills") or {}).items()}


def describe_skill(name: str) -> dict | None:
    """One Skill, as the rulebook has it, plus whether this engine applies it.

    The second half is the point. "Break Tackle: <text> — NOT applied by this
    engine" is a true sentence a coach can say; either half alone is a way to
    mislead them.
    """
    base = str(name or "").split("(")[0].strip()
    entry = catalogue().get(base.casefold())
    if entry is None:
        return None
    key = base.casefold()
    out = {**entry, "modelled": key in modelled()}
    if key in _PARTIAL:
        out["partial"] = _PARTIAL[key]
    return out


def find_skills(query: str = "", *, category: str = "", kind: str = "", only_unmodelled: bool = False) -> list[dict]:
    """Browse the catalogue. Every filter is optional; no filters lists all 108."""
    q, cat, knd = query.casefold(), category.casefold(), kind.casefold()
    out = []
    for key, entry in catalogue().items():
        if q and q not in key and q not in entry.get("text", "").casefold():
            continue
        if cat and entry.get("category", "").casefold() != cat:
            continue
        if knd and entry.get("kind", "").casefold() != knd:
            continue
        is_modelled = key in modelled()
        if only_unmodelled and is_modelled:
            continue
        row = {**entry, "modelled": is_modelled}
        if key in _PARTIAL:
            row["partial"] = _PARTIAL[key]
        out.append(row)
    return sorted(out, key=lambda e: e["name"])


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


# --- the Stunty Injury Table, and Thick Skull on top of it ----------------
#
# ORDER IS LOAD-BEARING HERE. Hooks run in registration order, so Stunty must
# REPLACE the table before Thick Skull ADJUSTS the result — reversed, a 7 on the
# Stunty table would come out Knocked-out when the rules say Stunned. The test
# `test_stunty_and_thick_skull_together` fails if these two are ever swapped.


@skill_hook("Stunty", "injury_outcome")
def _stunty_injury(ctx: SkillContext) -> None:
    """S3: "If an Injury Roll is made for a player with the Stunty Trait, then use
    the Stunty Injury Table below instead of the standard one."

        STUNTY INJURY TABLE — 2-6 Stunned · 7-8 Knocked-out · 9 Badly Hurt ·
        10-12 Casualty

    Badly Hurt is a Casualty for the purposes of a single match ("Remove them from
    the pitch and place them in the Casualty box"); the League consequence — an
    automatic Badly Hurt on the Casualty Table rather than a roll — belongs to the
    Casualty Roll, which this engine does not make either way.
    """
    total = ctx.value
    ctx.flags["outcome"] = "stunned" if total <= 6 else "knocked_out" if total <= 8 else "casualty"
    ctx.notes.append(f"Stunty: the Stunty Injury Table reads {total} as {ctx.flags['outcome'].replace('_', ' ')}")


@skill_hook("Thick Skull", "injury_outcome")
def _thick_skull(ctx: SkillContext) -> None:
    """S3: "When an Injury Roll is made for this player, they will only be
    Knocked-out on the roll of a 9; a roll of an 8 will be treated as a Stunned
    result. If this player also has the Stunty Trait, then they will only be
    Knocked-out on the roll of an 8; a roll of a 7 will be treated as a Stunned
    result."

    Written as "the lowest roll that still knocks them out", which is the same
    sentence for both tables and cannot drift apart the way two branches would.
    """
    ko_from = 8 if ctx.player.has_skill("Stunty") else 9
    if ctx.flags.get("outcome") == "knocked_out" and ctx.value < ko_from:
        ctx.flags["outcome"] = "stunned"
        ctx.notes.append(f"Thick Skull turns the {ctx.value} into a Stunned (Knocked-out only from {ko_from})")


# --- Skills that modify a roll --------------------------------------------


@skill_hook("Break Tackle", "roll_modifier")
def _break_tackle(ctx: SkillContext) -> None:
    """S3: "Once per Turn, when this player attempts to Dodge, they may apply a +1
    modifier to the Agility Test if they have a Strength characteristic of 3 or
    lower, a +2 modifier … if they have a Strength Characteristic of 4, or a +3
    modifier … if they have a Strength Characteristic of 5 or higher."

    A MODIFIER to the Agility Test — not, as is tempting to assume, a
    Strength-based alternative to rolling one. That exact mistake is the
    confabulation written up in docs/HANDOFF.md §1.
    """
    if ctx.flags.get("test") != "dodge" or ctx.flags.get("break_tackle_used"):
        return
    from .rules import strength_of

    st = strength_of(ctx.match, ctx.player)
    bonus = 1 if st <= 3 else 2 if st == 4 else 3
    ctx.value += bonus
    ctx.flags["break_tackle_spent"] = True
    ctx.notes.append(f"Break Tackle: +{bonus} for ST {st}")


@skill_hook("Stunty", "roll_modifier")
def _stunty_dodge(ctx: SkillContext) -> None:
    """S3: "When this player attempts to Dodge, they do not suffer any negative
    modifiers to their Agility Test for being Marked by opposition players.
    Additionally, this player applies a -1 modifier to the Agility Test when
    attempting to Intercept the ball."

    Only the MARKING penalty is cancelled, and only on a Dodge — a Stunty player
    still suffers everything else, which is why this adds back exactly the marking
    component rather than flooring the modifier at zero.
    """
    if ctx.flags.get("test") == "dodge":
        marking = int(ctx.flags.get("marking", 0))
        if marking:
            # `marking` arrives as the PENALTY, i.e. negative, and is already in
            # `value`. Cancelling it means subtracting it back out — adding it
            # doubles the very thing the Trait is meant to remove.
            ctx.value -= marking
            ctx.notes.append(f"Stunty: ignores {marking} for being Marked")
    elif ctx.flags.get("test") == "intercept":
        ctx.value -= 1
        ctx.notes.append("Stunty: -1 to Intercept")


@skill_hook("Titchy", "roll_modifier")
def _titchy(ctx: SkillContext) -> None:
    """S3: "A player with this Trait may apply a +1 modifier to the Agility Test
    when attempting to Dodge."

    (Its other half — that a Titchy player does NOT apply their own -1 for Marking
    an opponent dodging into their Tackle Zone — lives on the marker, not the
    dodger, and is applied in rules.dodge_modifier.)
    """
    if ctx.flags.get("test") == "dodge":
        ctx.value += 1
        ctx.notes.append("Titchy: +1 to the Dodge")


@skill_hook("Big Hand", "roll_modifier")
def _big_hand(ctx: SkillContext) -> None:
    """S3: "This player ignores all negative modifiers when attempting to pick up
    the ball." ALL of them, not just Marking — so this floors it at zero."""
    if ctx.flags.get("test") == "pick_up" and ctx.value < 0:
        ctx.notes.append(f"Big Hand: ignores {ctx.value} when picking up")
        ctx.value = 0


@skill_hook("Nerves of Steel", "roll_modifier")
def _nerves_of_steel(ctx: SkillContext) -> None:
    """S3: "This player may ignore any modifiers for being Marked when making an
    Agility Test to Catch the ball, or when making a Passing Ability Test to Pass
    the ball." Marked only — a Long Bomb is still a Long Bomb."""
    if ctx.flags.get("test") in ("catch", "pass"):
        marking = int(ctx.flags.get("marking", 0))
        if marking:
            ctx.value -= marking  # the penalty is already in `value`; take it back out
            ctx.notes.append(f"Nerves of Steel: ignores {marking} for being Marked")


@skill_hook("Accurate", "roll_modifier")
def _accurate(ctx: SkillContext) -> None:
    """S3: "When this player performs a Pass Action which is a Quick Pass or a
    Short Pass, this player may apply a +1 modifier to the Passing Ability Test."
    """
    if ctx.flags.get("test") == "pass" and ctx.flags.get("range") in ("Quick Pass", "Short Pass"):
        ctx.value += 1
        ctx.notes.append(f"Accurate: +1 on a {ctx.flags.get('range')}")


# --- Skills that grant a re-roll ------------------------------------------


@skill_hook("Sure Hands", "reroll")
def _sure_hands(ctx: SkillContext) -> None:
    """S3: "This player may re-roll the D6 when attempting to pick up the ball,
    though not when making a Secure the Ball Action."

    The exclusion is the whole point of Secure the Ball — it is already a flat 2+
    bought by giving up the rest of the activation.
    """
    if ctx.flags.get("test") == "pick_up" and not ctx.flags.get("securing"):
        ctx.flags["may_reroll"] = True


@skill_hook("Catch", "reroll")
def _catch(ctx: SkillContext) -> None:
    """S3: "This player may re-roll any failed Agility Test when attempting to
    Catch the ball." """
    if ctx.flags.get("test") == "catch":
        ctx.flags["may_reroll"] = True


@skill_hook("Pass", "reroll")
def _pass(ctx: SkillContext) -> None:
    """S3: "This player may re-roll any failed Passing Ability Test when performing
    a Pass Action." """
    if ctx.flags.get("test") == "pass":
        ctx.flags["may_reroll"] = True


# --- Skills that change where a push goes, or whether it happens ----------
#
# These four are applied in engine/actions/block.py rather than through a value
# hook, because a push is a POSITION rather than a number and the arc they widen
# is board geometry. They register here so the catalogue reports them as modelled
# and so this file stays the one place that lists what the engine applies.


@skill_hook(
    "Stand Firm",
    "push",
    partial="the engine only refuses a push that would go into the Crowd; refusing any "
    "other push is a coach's judgement it does not make",
)
def _stand_firm(ctx: SkillContext) -> None:
    """S3: "When this player would be Pushed Back during a Block Action, including
    during a Chain Push, they can choose to not be Pushed Back and instead remain
    in their current square."

    A CHOICE, and there is only one coach at the table — so the engine takes it in
    the one case where the alternative is unambiguously worse (the Crowd, which is
    an Injury Roll with no armour behind it) and says so in the log otherwise
    rather than guessing on the defence's behalf. See block._do_push.
    """


@skill_hook(
    "Sidestep",
    "push",
    partial="the engine picks the square furthest from the blocker; the rules let the "
    "coach pick any adjacent unoccupied one",
)
def _sidestep(ctx: SkillContext) -> None:
    """S3: "Whenever this player is Pushed Back for any reason, then instead of the
    opposing Coach choosing where this player is Pushed Back to, this player's
    Coach may choose any adjacent unoccupied square … If there are no adjacent
    unoccupied squares, then this Skill cannot be used."

    Played for the pushed player as "the square furthest from the blocker", which
    is the one thing a coach being shoved always wants. Suppressed by Grab.
    """


@skill_hook("Grab", "push")
def _grab(ctx: SkillContext) -> None:
    """S3: "When this player declares a Block Action, if the opposition player is
    Pushed Back, then this player's Coach may choose any unoccupied square adjacent
    to the target … Additionally, when this player performs a Block Action,
    opposition players cannot use the Sidestep Skill."

    Belongs to the acting coach, so it simply widens what ``push_to`` may name.
    """


@skill_hook("Fend", "push")
def _fend(ctx: SkillContext) -> None:
    """S3: "When a player with this Skill is Pushed Back as a result of a Block
    Action performed against them, then the opposition player may not Follow-up."

    Not optional and not overridable by the acting coach's ``follow_up`` — the
    only thing that beats it is a Juggernaut mid-Blitz.
    """


@skill_hook(
    "Juggernaut",
    "push",
    partial="both clauses are applied, but the Both Down conversion is taken only when "
    "the blocker would otherwise be Knocked Down — the rules leave it a free choice",
)
def _juggernaut(ctx: SkillContext) -> None:
    """S3: "when this player performs a Block Action as part of a Blitz Action,
    opposition players cannot use the Fend, Stand Firm or Wrestle Skills."

    Only the suppression half is modelled. Its other clause — "they may treat any
    result of Both Down as Pushed Back during any Block Actions they perform
    during the Blitz Action" — is a choice on a result the engine does not offer a
    choice on yet, and Wrestle is not modelled at all, so both are still reported.
    """


# --- the activation gates -------------------------------------------------


def _blitzy(ctx: SkillContext) -> bool:
    """Did they declare a Block or a Blitz? Three gates give +2 for it."""
    return ctx.flags.get("action") in ("block", "blitz")


@skill_hook("Bone Head", "activation_gate")
def _bone_head(ctx: SkillContext) -> None:
    """S3: "Whenever this player is activated, after declaring their Action they
    must roll a D6. On a 2+, the player may perform the declared Action as normal.
    On a 1, the player becomes Distracted." """
    ctx.flags.update(target=2, modifier=0, on_fail="distracted")


@skill_hook("Really Stupid", "activation_gate")
def _really_stupid(ctx: SkillContext) -> None:
    """S3: "…they must roll a D6. They may apply a +2 modifier to the roll if they
    have any Standing team-mates who are not Distracted, AND DO NOT HAVE THE
    REALLY STUPID TRAIT, adjacent to them. On a 4+, the player may perform the
    declared Action as normal. On a 1-3, this player becomes Distracted."

    Two Really Stupid players propping each other up is exactly what the rule
    excludes, and it is the clause a paraphrase drops.
    """
    from .rules import adjacent, has_tackle_zone

    p = ctx.player
    helpers = [
        q
        for q in ctx.match.on_pitch(p.side)
        if q.id != p.id and has_tackle_zone(q) and not q.has_skill("Really Stupid") and adjacent(q.x, q.y, p.x, p.y)
    ]
    ctx.flags.update(target=4, modifier=2 if helpers else 0, on_fail="distracted")
    if helpers:
        ctx.notes.append(f"+2 — {helpers[0].name()} is next to them")


@skill_hook("Take Root", "activation_gate")
def _take_root(ctx: SkillContext) -> None:
    """S3: "…IF THEY ARE STANDING they must roll a D6. On a 2+, the player may
    perform the declared Action as normal. On a 1, the player becomes Rooted." """
    if ctx.player.down != "standing":
        ctx.flags.update(skip=True)
        return
    ctx.flags.update(target=2, modifier=0, on_fail="rooted")


@skill_hook("Unchannelled Fury", "activation_gate")
def _unchannelled_fury(ctx: SkillContext) -> None:
    """S3: "…They may apply a +2 modifier to the roll if they have declared a Block
    Action or a Blitz Action. On a 4+, the player may perform the declared Action
    as normal. On a 1-3, this player rages incoherently but nothing really happens.
    Their activation immediately ends." """
    ctx.flags.update(target=4, modifier=2 if _blitzy(ctx) else 0, on_fail="end_activation")


@skill_hook("Animal Savagery", "activation_gate")
def _animal_savagery(ctx: SkillContext) -> None:
    """S3: "…+2 … if they have declared a Block Action or a Blitz Action. On a 4+,
    the player may perform the declared action as normal. On a 1-3, this player
    lashes out at one of their team-mates. Choose one Standing team-mate adjacent
    to this player; the chosen player is immediately Knocked Down. This will not
    cause a Turnover unless the player was holding the ball. … If this player rolls
    a 1-3 and there are no Standing team-mates adjacent to them, then they are
    Distracted."
    """
    ctx.flags.update(target=4, modifier=2 if _blitzy(ctx) else 0, on_fail="lash_out")


@skill_hook("Stab", "special_action")
def _stab(ctx: SkillContext) -> None:
    """S3: "select a Standing opposition player adjacent to this player and make an
    Armour Roll for the selected player. THIS ARMOUR ROLL CANNOT BE MODIFIED IN
    ANY WAY. If the player's Armour is broken, make an Injury Roll for them."

    Unmodifiable is why it passes no responsible player — Mighty Blow and Claws
    both hang off one, and letting either through would modify the unmodifiable.
    """


@skill_hook("Projectile Vomit", "special_action")
def _projectile_vomit(ctx: SkillContext) -> None:
    """S3: "roll a D6. On a 2+ … Make an Armour Roll for the targeted player … On a
    1, this player covers THEMSELVES in acidic bile; make an Armour Roll for THIS
    player." The roll is made for whoever it landed on, which can be the thrower."""


@skill_hook("Breathe Fire", "special_action")
def _breathe_fire(ctx: SkillContext) -> None:
    """S3: "roll a D6, applying a -1 modifier if the target has an ST of 5 or
    higher. On a 1, this player is immediately Knocked Down. On a 2-3, nothing
    happens. On a 4+, the opposition player is immediately Placed Prone. If the
    roll is a NATURAL 6, the opposition player is Knocked Down instead."

    Natural, so the -1 for a large target cannot take the knock-down away — and
    Placed Prone risks no harm while Knocked Down does, so the two outcomes on 4+
    and a natural 6 are genuinely different."""


@skill_hook("Chainsaw", "special_action")
def _chainsaw(ctx: SkillContext) -> None:
    """S3: "roll a D6. On a 2+, this player immediately makes an Armour Roll
    against one opposition player they are Marking, applying a +3 modifier … On a
    1, the Chainsaw will Kick-back and this player is Knocked Down instead. If
    this player is Knocked Down or Falls Over FOR ANY REASON … a +3 modifier is
    applied when the opposition Coach makes an Armour Roll for this player. THIS
    +3 MODIFIER MUST ALWAYS BE APPLIED. … this player may also use their chainsaw
    when performing a Foul Action, in which case they may apply a +3 modifier."

    Three clauses, and two of them are passive — carrying a chainsaw is dangerous
    to its owner, which is the trade the Trait exists to make."""


@skill_hook("Monstrous Mouth", "special_action")
def _monstrous_mouth(ctx: SkillContext) -> None:
    """S3: "roll a D6. On a 1-2 nothing happens. On a 3+, the opposition player is
    considered to be Chomped. Whilst Chomped, the opposition player cannot leave
    the square they are in whilst this player remains Marking them. THIS CONDITION
    ENDS IMMEDIATELY if this player is no longer Marking the opposition player for
    any reason."

    "For any reason" is why it is asked of the live board rather than remembered
    as a flag — being knocked down, pushed away or sent off would none of them
    think to clear one."""


@skill_hook("Ball & Chain", "special_action")
def _ball_and_chain(ctx: SkillContext) -> None:
    """S3: "the ONLY action they can declare is a Ball & Chain Special Action …
    position the Throw-in Template over this player so it faces one of the two End
    Zones or either Sideline. Then roll a D6 and move this player into the square
    as indicated … A player that moves in this manner does not have to make an
    Agility Test to Dodge away from another player's Tackle Zone; THEY WILL
    AUTOMATICALLY PASS … can move a number of squares up to their MA."

    Movement without agency: the coach picks the facing, the die picks the square.
    Free Dodges are what make the Trait survivable at all."""


@skill_hook("Bombardier", "special_action")
def _bombardier(ctx: SkillContext) -> None:
    """S3: "they throw a bomb IN THE SAME MANNER as when a player performs a Pass
    Action … Though this is NOT a Pass Action itself … may not perform a Move
    Action before throwing the bomb … When a bomb explodes, any player in the
    square it exploded in is hit. Additionally, roll a D6 for each player adjacent
    … On a 4+, they are hit. Any Standing player that is hit is immediately
    Knocked Down. Additionally, make an Armour Roll for any Prone or Stunned
    players hit."

    The catch-and-throw-it-back chain is reported rather than followed: it needs a
    coach at the other end, which is the one thing this engine cannot supply."""


@skill_hook("Kick Team-mate", "action")
def _kick_team_mate(ctx: SkillContext) -> None:
    """S3: "works exactly the same as a Throw Team-mate Action, with the following
    exceptions: Performing a Kick Team-mate Special Action DOES NOT COUNT as a
    team's Throw Team-mate Action for the Turn … However, if a Kick Team-mate
    Special Action results in a Fumbled Throw, immediately make an Injury Roll for
    the team-mate being kicked, TREATING ANY RESULT OF STUNNED AS KNOCKED OUT."

    Kicking somebody is worse for them than throwing them, which is the whole
    point of the distinction."""


@skill_hook("Throw Team-mate", "action")
def _throw_team_mate(ctx: SkillContext) -> None:
    """S3: "This player may declare the Throw Team-mate Action." The permission
    IS the Trait — see actions/throwteam.py."""


@skill_hook("Right Stuff", "action")
def _right_stuff(ctx: SkillContext) -> None:
    """S3: "This player can be thrown by a team-mate with the Throw Team-mate
    Trait, EVEN IF THIS PLAYER IS PRONE." Being on the floor is explicitly no
    obstacle to being thrown — though it does mean an automatic fail on landing."""


@skill_hook("Always Hungry", "action")
def _always_hungry(ctx: SkillContext) -> None:
    """S3: "Whenever this player performs a Throw Team-mate Action, BEFORE making
    the Passing Ability Test, they must roll a D6. On a 2+ … as normal. On a 1,
    the player will attempt to eat their team-mate … On a 2+, the team-mate will
    squirm free and the Throw Team-mate Action will automatically result in a
    Fumbled Throw. On a 1, the player will eat their team-mate — immediately
    remove them from your Team Draft List. No Apothecary can be used to save them,
    and no Regeneration rolls can be attempted." """


@skill_hook("Bullseye", "action")
def _bullseye(ctx: SkillContext) -> None:
    """S3: "if the result of the throw is a Superb Throw then the thrown player
    will not Scatter before landing and will instead land in the target square." """


@skill_hook("Loner", "team_reroll")
def _loner(ctx: SkillContext) -> None:
    """S3: "Whenever this player wishes to use a Team Re-roll, they must roll a D6.
    If they roll equal to or higher than the number shown in brackets, then they
    may use the Team Re-roll as normal. If they roll lower than the number shown
    in brackets, then they may not re-roll the dice and THE TEAM RE-ROLL IS LOST
    just as if it had been used."

    Losing it either way is the whole cost of the Trait, and the half that would
    be easy to skip — a Loner who fails has spent the re-roll and got nothing.
    Applied in engine/rerolls.py, where the number in the brackets is read off the
    player's own skill string (`Loner (4+)`) rather than assumed.
    """


@skill_hook("Drunkard", "roll_modifier")
def _drunkard(ctx: SkillContext) -> None:
    """S3: "This player applies a -1 modifier to test whenever they attempt to
    Rush." """
    if ctx.flags.get("test") == "rush":
        ctx.value -= 1
        ctx.notes.append("Drunkard: -1 to the Rush")


@skill_hook("Wrestle", "block_result")
def _wrestle(ctx: SkillContext) -> None:
    """S3: "When this player performs a Block Action, or is the target of a Block
    Action, if the Both Down result is applied, this player may choose to use this
    Skill. If they do, both players in the Block Action are Placed Prone,
    regardless of any other Skills they may possess."

    PLACED PRONE, not Knocked Down — "they aren't at risk of being caused harm",
    so neither player rolls armour. Applied by whichever participant would
    otherwise be Knocked Down; see block._both_down_choice.
    """


@skill_hook("Brawler", "block_result")
def _brawler(ctx: SkillContext) -> None:
    """S3: "When this player declares a Block Action, they may re-roll a single
    Both Down result."

    A single one, and it re-rolls the DICE rather than reinterpreting the result.
    "Declares", so a Blitz switches it off — see block.declared_a_block.
    """


@skill_hook("Dauntless", "block_result")
def _dauntless(ctx: SkillContext) -> None:
    """S3: "When a player with this Skill performs a Block Action against an
    opposition player with a higher Strength Characteristic (before any modifiers
    are applied to either player), this player may roll a D6 and add their own
    Strength Characteristic. If the result is higher than the opposition player's
    unmodified Strength Characteristic, then this player increases their unmodified
    Strength Characteristic to MATCH the opposition player for the duration of the
    Block Action. Modifiers are then applied as normal."

    Matches, never exceeds — and the assists are then re-counted against the new
    number. Because it is a ROLL, `validate` reports that it is coming rather than
    making it: validate is asked freely and must never touch the dice.
    """


@skill_hook("Horns", "block_result")
def _horns(ctx: SkillContext) -> None:
    """S3: "Whenever this player declares a Blitz Action, then they apply a +1
    modifier to their Strength Characteristic for any Block Actions performed
    during that Blitz Action." Only on a Blitz, and deterministic — so it is in
    `validate` too, and the odds a coach is shown are the odds they get.
    """


@skill_hook("Claws", "block_result")
def _claws(ctx: SkillContext) -> None:
    """S3: "Whenever an Armour Roll is made for an opposition player that has been
    Knocked Down by this player during a Block Action, even if this player is also
    Knocked Down, then any roll of a NATURAL 8+ on the Armour Roll will break the
    opposition player's armour regardless of their actual Armour Value."

    It lowers the bar to 8 rather than adding to the roll — which is everything
    against AV 10+ and nothing against AV 8+. Natural, so Mighty Blow's +1 cannot
    manufacture one.
    """


@skill_hook("Tackle", "deny_dodge_skill")
def _tackle(ctx: SkillContext) -> None:
    """S3: "When an opposition player attempts to Dodge away from a square in this
    player's Tackle Zone, they cannot use the Dodge Skill. Additionally, when this
    player performs a Block Action against an opposition player, the opposition
    player does not count as having the Dodge Skill if a Stumble result is
    selected."

    Both halves are the same sentence — "you do not have Dodge against me" — so
    both call this hook, and the caller says which case it is asking about. Note
    it is the square being LEFT that matters for the first half, not the one being
    entered.
    """
    ctx.flags["denied"] = True
    ctx.notes.append("Tackle: the Dodge Skill does not apply")


@skill_hook("Guard", "may_assist_while_marked")
def _guard(ctx: SkillContext) -> None:
    """S3: "This player can provide Offensive and Defensive Assists when a player
    performs a Block Action regardless of how many opposition players are Marking
    this player." """
    ctx.flags["assists_anyway"] = True
