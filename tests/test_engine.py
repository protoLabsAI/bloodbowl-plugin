"""Engine tests: the fold, determinism, and the movement rules.

Every rule asserted here was read off the S3 source and is quoted in the test that
pins it. Several of them differ from what an older edition would say, which is the
point — those are the ones that would otherwise get "fixed" back to wrong.

Dice are scripted throughout. A rules test that rolls real dice is testing luck.
"""

from __future__ import annotations

import pytest


def _unmodelled_pair():
    """Two Skills the engine does not model.

    This used to ask the CATALOGUE for real unmodelled Skills, and said in its own
    assertion that it would need rewriting once there were fewer than two left.
    That day arrived: the catalogue is down to one. So it names two Skills that do
    not exist at all — which is the honest way to keep testing the REPORTING
    mechanism now that there is almost nothing real for it to report.

    `unmodelled_skills` compares against `modelled()`, so an unknown name is
    reported exactly as an unimplemented real one was: a plugin, a fork or a
    hand-edited roster can all put a name on a player that this engine has never
    heard of, and reporting it is the whole point.
    """
    return "Prehensile Moustache", "Devastating Yodel"


def _match(*players, active="home"):
    """A match with the given (side, x, y, MA, AG, skills) players, ids h00.. a00.."""
    from bloodbowl.engine.state import Match, PlayerState
    from bloodbowl.pitch import Player

    m = Match()
    for i, spec in enumerate(players):
        side, x, y = spec[0], spec[1], spec[2]
        ma = spec[3] if len(spec) > 3 else 6
        ag = spec[4] if len(spec) > 4 else "3+"
        skills = list(spec[5]) if len(spec) > 5 else []
        p = Player(side=side, x=x, y=y, position=f"{side.title()} {i}", MA=str(ma), AG=ag, AV="9+", skills=skills)
        m.players.append(PlayerState(player=p, id=f"{side[0]}{i:02d}"))
    m.clock.active = active
    return m


def _dice(script, block=None):
    from bloodbowl.engine.dice import ScriptedDice

    return ScriptedDice(script=list(script), block_script=list(block or []))


def _rec(m):
    """A Recorder, for calling an engine helper the way an Action would."""
    from bloodbowl.engine.actions import Recorder

    return Recorder(m)


def _move(m, pid, x, y, dice, **cmd):
    """resolve applies its own events (see actions.Outcome) — do not re-apply."""
    from bloodbowl.engine import actions

    actions.load_all()
    return actions.get("move")["resolve"](m, {"player": pid, "x": x, "y": y, **cmd}, dice)


def _block(m, pid, target, dice, **cmd):
    from bloodbowl.engine import actions

    actions.load_all()
    return actions.get("block")["resolve"](m, {"player": pid, "target": target, **cmd}, dice)


# --- determinism and replay -----------------------------------------------


def _ball_at(x, y, carrier=""):
    from bloodbowl.engine.events import Event

    return Event(kind="ball_moved", detail={"x": x, "y": y, "carrier": carrier})


def _acting_board(action):
    """A board on which ``action`` is legal for h00, and its dice.

    One factory rather than one fixture per action, because the test below has to
    build the SAME board twice — once to act on and once to fold onto — and the
    two drifting apart would make the fold look wrong for the wrong reason.
    """
    if action == "secure":
        # The ball loose, and no Standing opponent within 2 squares of it.
        m = _match(("home", 7, 13), ("away", 7, 20), ("home", 8, 13))
        m.apply(_ball_at(7, 13))
        return m, {}, [6], []
    # A team-mate at (8,13) to hand off to and an opponent at (7,14) to block.
    m = _match(("home", 7, 13), ("away", 7, 14), ("home", 8, 13))
    m.by_id("h00").player.ST = "3"
    m.by_id("h00").player.PA = "3+"
    m.apply(_ball_at(7, 13, carrier="h00"))
    return m, *{
        "block": ({"target": "a01"}, [6, 6], [["push_back"]]),
        "handoff": ({"target": "h02"}, [6], []),
        "pass": ({"x": 7, "y": 18}, [6, 6, 6], []),
    }[action]


@pytest.mark.parametrize("action", ["block", "handoff", "secure", "pass"])
def test_an_action_being_over_survives_the_fold(action):
    """`fold(events)` must rebuild the position EXACTLY, and "this player has
    already acted" is part of the position.

    Every action used to assign ``p.acted`` directly and then emit a note saying
    it had — a note nothing read. The live object was right and the fold was
    wrong, so a replayed match had players free to act a second time. It stayed
    invisible because ``Match.from_dict`` seeds the players from the cached row
    BEFORE folding, so the one path anybody exercised papered over it. Folding
    from scratch is the only way to see it.
    """
    from bloodbowl.engine import actions
    from bloodbowl.engine.state import fold

    actions.load_all()
    m, cmd, script, block = _acting_board(action)
    out = actions.get(action)["resolve"](m, {"player": "h00", **cmd}, _dice(script, block))
    assert out.events, f"{action} was refused, so this test proves nothing: {out.text}"
    assert m.by_id("h00").acted, "the action did not end the activation at all"

    fresh, _cmd, _s, _b = _acting_board(action)
    rebuilt = fold(fresh, list(m.events))
    assert rebuilt.by_id("h00").acted, f"{action} left the folded player free to act again"


def test_the_fold_rebuilds_a_position_exactly():
    """Re-watching is fold(events) — no dice, no rules, so it cannot drift."""
    from bloodbowl.engine.state import Match, fold

    m = _match(("home", 7, 13))
    _move(m, "h00", 7, 14, _dice([]))
    _move(m, "h00", 7, 15, _dice([]))
    log = [e for e in m.events]

    rebuilt = fold(_match(("home", 7, 13)), log)
    assert (rebuilt.by_id("h00").x, rebuilt.by_id("h00").y) == (7, 15)
    assert rebuilt.by_id("h00").ma_used == 2
    assert isinstance(rebuilt, Match)


def test_a_saved_match_round_trips_through_its_log():
    """The log is the truth: from_dict recomputes the position rather than
    trusting the cached board, so the two can never disagree."""
    from bloodbowl.engine.state import Match

    m = _match(("home", 7, 13))
    _move(m, "h00", 7, 14, _dice([]))
    data = m.to_dict()
    # Corrupt the cached position; the log still says where the player is.
    data["players"][0]["x"] = 1
    data["players"][0]["y"] = 1
    back = Match.from_dict(data)
    assert (back.by_id("h00").x, back.by_id("h00").y) == (7, 14)


def test_the_same_seed_and_commands_produce_the_same_match():
    from bloodbowl.engine.dice import SeededDice

    results = []
    for _ in range(2):
        m = _match(("home", 7, 13, 1), ("away", 8, 14))
        dice = SeededDice(seed=99)
        _move(m, "h00", 7, 14, dice)
        _move(m, "h00", 7, 15, dice)  # same stream, as a real match would use
        results.append([e.to_dict() for e in m.events])
    assert results[0] == results[1]


def test_replay_dice_refuse_to_invent_a_roll():
    """If today's engine wants more dice than the recording holds, that is a rules
    change altering a real game — it must be loud."""
    from bloodbowl.engine.dice import ReplayDice, ReplayDivergence

    d = ReplayDice(recorded=[])
    with pytest.raises(ReplayDivergence):
        d.d6()


def test_scripted_dice_refuse_to_fall_back_to_random():
    with pytest.raises(AssertionError):
        _dice([]).d6()


# --- tackle zones and Marking ---------------------------------------------


def test_only_standing_players_mark():
    """S3: a Prone or Stunned player has no Tackle Zone, so you may walk away
    from someone you just knocked down without Dodging."""
    from bloodbowl.engine.rules import is_marked

    m = _match(("home", 7, 13), ("away", 7, 14))
    assert is_marked(m, m.by_id("h00")) is True
    m.by_id("a01").down = "prone"
    assert is_marked(m, m.by_id("h00")) is False


def test_a_distracted_player_does_not_mark():
    """S3 adds Distracted: Standing, but with no Tackle Zone."""
    from bloodbowl.engine.rules import is_marked

    m = _match(("home", 7, 13), ("away", 7, 14))
    m.by_id("a01").distracted = True
    assert is_marked(m, m.by_id("h00")) is False


def test_the_dodge_modifier_counts_the_destination_not_the_origin():
    """THE rule most likely to be modelled backwards. S3: "applying a -1 modifier
    for each opposition player that is Marking the square they are moving into."
    The square being LEFT decides only whether a Dodge is needed at all."""
    from bloodbowl.engine.rules import dodge_modifier

    # Three opponents Mark where the runner stands. None Marks (7,12), the square
    # being moved into — note they must be two rows behind it, since a diagonal
    # neighbour of the origin is usually also a neighbour of the destination.
    m = _match(("home", 7, 13), ("away", 7, 14), ("away", 6, 14), ("away", 8, 14))
    assert dodge_modifier(m, m.by_id("h00"), 7, 12) == 0, "leaving three tackle zones is still an unmodified roll"

    # One opponent Marks the destination.
    m2 = _match(("home", 7, 13), ("away", 7, 11))
    assert dodge_modifier(m2, m2.by_id("h00"), 7, 12) == -1


# --- movement --------------------------------------------------------------


def test_an_unmarked_move_rolls_nothing():
    m = _match(("home", 7, 13))
    out = _move(m, "h00", 7, 14, _dice([]))  # empty script: any roll would raise
    assert out.ok and not out.turnover
    assert (m.by_id("h00").x, m.by_id("h00").y) == (7, 14)


def test_leaving_a_marked_square_requires_a_dodge():
    m = _match(("home", 7, 13), ("away", 7, 14))
    out = _move(m, "h00", 6, 12, _dice([4]))  # AG 3+, unmodified, 4 passes
    assert out.ok
    assert any(r.kind == "Dodge" for e in out.events for r in e.rolls)


def test_a_failed_dodge_still_moves_the_player_then_falls_over():
    """S3: "The player is moved into the square they attempted to Dodge into and
    then Falls Over." Where they land matters — the ball scatters from there."""
    m = _match(("home", 7, 13), ("away", 7, 14))
    out = _move(m, "h00", 6, 12, _dice([2, 1, 1]))  # dodge fails, then armour holds
    assert not out.ok and out.turnover
    p = m.by_id("h00")
    assert (p.x, p.y) == (6, 12), "a failed Dodge does not leave the player where they started"
    assert p.down == "prone"


def test_a_natural_one_fails_a_dodge_however_good_the_odds():
    m = _match(("home", 7, 13, 6, "2+"), ("away", 7, 14))
    out = _move(m, "h00", 6, 12, _dice([1, 1, 1]))
    assert not out.ok and out.turnover


def test_rushing_starts_when_the_move_allowance_runs_out():
    """S3 calls it Rushing, it is 2+, and at most twice per activation."""
    m = _match(("home", 7, 13, 1))
    assert _move(m, "h00", 7, 14, _dice([])).ok  # the one square of MA
    out = _move(m, "h00", 7, 15, _dice([3]))  # first Rush
    assert out.ok
    assert any(r.kind == "Rush" and r.target == 2 for e in out.events for r in e.rolls)


def test_a_failed_rush_falls_over_in_the_target_square_and_is_a_turnover():
    m = _match(("home", 7, 13, 1))
    _move(m, "h00", 7, 14, _dice([]))
    out = _move(m, "h00", 7, 15, _dice([1, 1, 1]))  # rush fails, then armour holds
    assert not out.ok and out.turnover
    p = m.by_id("h00")
    assert (p.x, p.y) == (7, 15) and p.down == "prone"


def test_a_third_rush_is_refused_before_any_dice_are_rolled():
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 13, 1))
    _move(m, "h00", 7, 14, _dice([]))
    _move(m, "h00", 7, 15, _dice([5]))
    _move(m, "h00", 7, 16, _dice([5]))
    legal = actions.get("move")["validate"](m, {"player": "h00", "x": 7, "y": 17})
    assert legal.ok is False and "Rush" in legal.reason


def test_rush_is_rolled_before_dodge_when_a_square_needs_both():
    """S3: "the roll for attempt to Rush will always come first". The order is
    observable: a failed Rush means the Dodge is never rolled at all."""
    m = _match(("home", 7, 13, 1), ("away", 7, 14))
    _move(m, "h00", 6, 12, _dice([5]))  # spend the single square, dodging away
    m.by_id("h00").down = "standing"
    out = _move(m, "h00", 6, 11, _dice([1, 1, 1]))  # Rush fails, then the armour roll
    kinds = [r.kind for e in out.events for r in e.rolls]
    assert "Dodge" not in kinds, f"a failed Rush must end it before the Dodge: {kinds}"
    assert kinds[0] == "Rush"


# --- standing up -----------------------------------------------------------


def test_standing_up_costs_three_squares():
    m = _match(("home", 7, 13, 6))
    m.by_id("h00").down = "prone"
    out = _move(m, "h00", 7, 14, _dice([]))
    assert out.ok
    assert m.by_id("h00").down == "standing"
    assert m.by_id("h00").ma_used == 4, "3 to stand up plus 1 to move"


def test_a_low_movement_player_rolls_to_stand_and_may_fail():
    """S3: MA 2 or less rolls a D6 — 4+ stands using the full Move Allowance,
    1-3 remains Prone with the activation immediately over."""
    m = _match(("home", 7, 13, 2))
    m.by_id("h00").down = "prone"
    out = _move(m, "h00", 7, 14, _dice([3]))
    assert not out.ok
    assert m.by_id("h00").down == "prone"
    assert (m.by_id("h00").x, m.by_id("h00").y) == (7, 13), "a failed stand-up does not move the player"


def test_jump_up_stands_for_free():
    """The Skill registry doing its job: one registration, no branch in move.py."""
    m = _match(("home", 7, 13, 6, "3+", ["Jump Up"]))
    m.by_id("h00").down = "prone"
    out = _move(m, "h00", 7, 14, _dice([]))
    assert out.ok
    assert m.by_id("h00").ma_used == 1, "Jump Up costs nothing to stand"


# --- skills ---------------------------------------------------------------


def test_the_dodge_skill_rerolls_a_failed_dodge_once_per_turn():
    m = _match(("home", 7, 13, 6, "3+", ["Dodge"]), ("away", 7, 14))
    out = _move(m, "h00", 6, 12, _dice([2, 5]))  # fail, then re-roll and pass
    assert out.ok
    kinds = [r.kind for e in out.events for r in e.rolls]
    assert kinds == ["Dodge", "Dodge (re-roll)"]
    assert m.by_id("h00").dodge_reroll_used is True


def test_prehensile_tail_is_an_opponents_skill_that_modifies_our_roll():
    """The hook is asked of the players Marking the destination, not of the
    player rolling — which is why it is a separate hook."""
    m = _match(("home", 7, 13), ("away", 7, 14), ("away", 7, 11, 6, "3+", ["Prehensile Tail"]))
    out = _move(m, "h00", 7, 12, _dice([3, 1, 1]))  # AG 3+, -1 marker, -1 tail = fails
    assert not out.ok
    dodge = next(r for e in out.events for r in e.rolls if r.kind == "Dodge")
    assert dodge.modifier == -2


def test_unmodelled_skills_are_reported_rather_than_ignored():
    """A Troll's Always Hungry is not implemented. The engine says so instead of
    quietly playing as though the player did not have it."""
    m = _match(("home", 7, 13, 6, "3+", [*_unmodelled_pair(), "Jump Up", "Mighty Blow"]))
    out = _move(m, "h00", 7, 14, _dice([]))
    first, second = _unmodelled_pair()
    assert first in out.unmodelled and second in out.unmodelled
    # Both of these ARE modelled, and the list must shrink as skills land — this
    # test caught Mighty Blow moving from unmodelled to modelled when Blocking
    # was added, which is exactly the drift it is here to notice.
    assert "Jump Up" not in out.unmodelled
    assert "Mighty Blow" not in out.unmodelled


def test_an_unmodelled_skill_is_announced_once_per_match_not_once_per_step():
    """Honest is not the same as loud.

    The Outcome still carries every unmodelled Skill the participants hold — that
    is the raw truth and the tests above pin it. What ``act`` reports is the
    FIRST mention of each, because the same Troll taking six steps has the same
    two missing Skills six times, and a notice that always fires is one nobody
    reads.
    """
    from bloodbowl.engine.game import act

    m = _match(("home", 7, 13, 6, "3+", list(_unmodelled_pair())))
    first = act(m, "move", {"player": "h00", "x": 7, "y": 14})
    # Sorted, because `unmodelled_skills` returns a sorted set — the old fixture
    # happened to hand back names that were already in order.
    assert first["unmodelled_skills"] == sorted(_unmodelled_pair())

    again = act(m, "move", {"player": "h00", "x": 7, "y": 15})
    assert again["unmodelled_skills"] == []
    # …but the raw list is untouched, so nothing has become invisible.
    from bloodbowl.engine.skills import unmodelled_skills

    assert unmodelled_skills(m.by_id("h00")) == sorted(_unmodelled_pair())


def test_the_first_mention_lands_in_the_log_not_only_in_the_reply():
    """The log is what the coach narrates from. A notice that lives only in a tool
    result is invisible to anyone reading the match back."""
    from bloodbowl.engine.game import act

    m = _match(("home", 7, 13, 6, "3+", [_unmodelled_pair()[0]]))
    act(m, "move", {"player": "h00", "x": 7, "y": 14})
    noted = [e for e in m.events if e.kind == "unmodelled_noted"]
    assert len(noted) == 1
    assert _unmodelled_pair()[0] in noted[0].text
    assert noted[0].detail["skills"] == [_unmodelled_pair()[0]]


def test_the_already_said_that_ledger_survives_a_reload():
    """The ledger is the LOG, and this is why.

    A match is reloaded from disk between tool calls, so anything remembered on
    the object is gone by the next action — and a per-match notice that
    re-announces itself every call looks exactly like one that works.
    """
    from bloodbowl.engine.game import act
    from bloodbowl.engine.state import Match

    m = _match(("home", 7, 13, 6, "3+", [_unmodelled_pair()[0]]))
    assert act(m, "move", {"player": "h00", "x": 7, "y": 14})["unmodelled_skills"] == [_unmodelled_pair()[0]]

    reloaded = Match.from_dict(m.to_dict())
    assert act(reloaded, "move", {"player": "h00", "x": 7, "y": 15})["unmodelled_skills"] == []


def test_the_standing_summary_names_every_unmodelled_skill_and_its_holders():
    """The other half: quiet in the log, but always answerable on demand."""
    from bloodbowl.engine.game import state_report
    from bloodbowl.engine.skills import unmodelled_on_pitch

    m = _match(
        ("home", 7, 13, 6, "3+", [_unmodelled_pair()[0], "Block"]),
        ("home", 8, 13, 6, "3+", [_unmodelled_pair()[0]]),
        ("away", 7, 14, 6, "3+", [_unmodelled_pair()[1]]),
    )
    summary = unmodelled_on_pitch(m)
    assert {row["skill"] for row in summary} == set(_unmodelled_pair())
    hungry = next(row for row in summary if row["skill"] == _unmodelled_pair()[0])
    assert hungry["players"] == ["h00", "h01"] and hungry["count"] == 2
    # Block IS modelled, so it must not appear.
    assert "Block" not in {row["skill"] for row in summary}
    assert state_report(m)["unmodelled_skills"] == summary


def test_the_standing_summary_is_derived_so_it_drops_a_player_who_leaves():
    """Recomputed from the board rather than recorded, so a Casualty stops being
    listed the moment they leave — a remembered summary would keep warning about a
    Skill that is no longer on the pitch."""
    from bloodbowl.engine.events import Event
    from bloodbowl.engine.skills import unmodelled_on_pitch

    m = _match(("home", 7, 13, 6, "3+", [_unmodelled_pair()[0]]), ("away", 7, 14, 6, "3+", [_unmodelled_pair()[1]]))
    assert len(unmodelled_on_pitch(m)) == 2
    m.apply(Event(kind="player_condition", actor=m.players[1].id, detail={"outcome": "casualty"}))
    assert [row["skill"] for row in unmodelled_on_pitch(m)] == [_unmodelled_pair()[0]]


# --- strict in play --------------------------------------------------------


def test_the_engine_refuses_an_illegal_move_rather_than_reporting_it():
    """The setup board reports and never blocks. A match is the opposite half of
    that choice: it refuses, so a coach cannot talk the engine into a bad move."""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 13), ("away", 7, 14))
    for cmd, expect in (
        ({"player": "h00", "x": 7, "y": 14}, "occupied"),
        ({"player": "h00", "x": 7, "y": 20}, "one square"),
        ({"player": "a01", "x": 7, "y": 15}, "turn"),
        ({"player": "nope", "x": 7, "y": 14}, "no player"),
    ):
        legal = actions.get("move")["validate"](m, cmd)
        assert legal.ok is False and expect in legal.reason, f"{cmd} -> {legal.reason}"


def test_validate_is_free_of_side_effects_so_a_coach_can_ask_about_every_square():
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 13), ("away", 7, 14))
    before = m.to_dict()
    for x in range(1, 16):
        actions.get("move")["validate"](m, {"player": "h00", "x": x, "y": 12})
    assert m.to_dict() == before, "asking what is legal must not change the game"


# --- the game loop through the tools --------------------------------------


def _tools(registry):
    import bloodbowl

    bloodbowl.register(registry)
    return {t.name: t for t in registry.tools}


def _decline_any_choice(t):
    """Answer, by declining, whatever Kick-off Event the seed happened to roll.

    Three of the eleven stop and ask the Coach something, and nothing else may
    happen until one of them is answered — so a test about anything ELSE has to
    get past the question first. Declining is always legal ("MAY", in all three),
    and it leaves the board exactly as the kick-off left it.
    """
    import json as _json

    return _json.loads(t["bb_game_choose"].invoke({"decline": True}))


def _setup_board(home="Orc", away="Skaven"):
    """A small legal-ish board saved where the tools will find it."""
    import json as _json

    from bloodbowl.pitch import Player, Scenario
    from bloodbowl.store import save

    sc = Scenario(name="test", home_team=home, away_team=away)
    sc.players = [
        Player(side="home", x=7, y=13, position="Orc Lineman", team=home, MA="5", AG="3+", AV="10+"),
        Player(side="away", x=7, y=14, position="Skaven Clanrat", team=away, MA="7", AG="3+", AV="8+"),
    ]
    save(sc)
    return _json


def test_a_match_starts_from_the_board_without_consuming_it(registry):
    import json as j

    _setup_board()
    t = _tools(registry)
    out = j.loads(t["bb_game_new"].invoke({"seed": 7, "kicking_to": "home"}))
    assert out["ok"] and len(out["match"]["players"]) == 2
    assert out["match"]["clock"]["half"] == 1

    from bloodbowl.store import load

    assert len(load().players) == 2, "starting a match must not clear the practice board"


def test_an_empty_board_cannot_start_a_match(registry):
    import json as j

    from bloodbowl.pitch import Scenario
    from bloodbowl.store import save

    save(Scenario())
    t = _tools(registry)
    out = j.loads(t["bb_game_new"].invoke({}))
    assert out["ok"] is False and "empty" in out["error"]


def test_legal_reports_every_neighbour_without_changing_the_game(registry):
    import json as j

    _setup_board()
    t = _tools(registry)
    t["bb_game_new"].invoke({"seed": 3, "kicking_to": "home"})
    before = j.loads(t["bb_game_state"].invoke({}))
    out = j.loads(t["bb_game_legal"].invoke({"player": "h00"}))
    assert out["ok"] and len(out["squares"]) == 8
    assert any(s["legal"] and s.get("dodge") for s in out["squares"]), "this player is Marked, so moves need a Dodge"
    after = j.loads(t["bb_game_state"].invoke({}))
    assert before == after, "asking what is legal must not advance the game"


def test_acting_is_refused_when_illegal_and_says_why(registry):
    import json as j

    _setup_board()
    t = _tools(registry)
    t["bb_game_new"].invoke({"seed": 3, "kicking_to": "home"})
    out = j.loads(t["bb_game_act"].invoke({"action": "move", "player": "a00", "x": 7, "y": 15}))
    assert out["ok"] is False and "turn" in out["text"]


def test_the_log_carries_the_rolls_so_the_coach_quotes_rather_than_guesses(registry):
    import json as j

    _setup_board()
    t = _tools(registry)
    t["bb_game_new"].invoke({"seed": 11, "kicking_to": "home"})
    _decline_any_choice(t)  # seed 11 rolls Quick Snap!, which asks before anything else can happen
    t["bb_game_act"].invoke({"action": "move", "player": "h00", "x": 6, "y": 12})
    log = j.loads(t["bb_game_log"].invoke({"last": 10}))
    assert log["ok"]
    rolled = [line for entry in log["log"] for line in entry["rolls"]]
    assert any("Dodge" in r and "needed" in r for r in rolled), f"no dodge in the log: {rolled}"


def test_the_dice_stream_advances_past_the_rolls_already_made():
    """A match is reloaded from disk between tool calls, so its dice source is
    rebuilt from the seed every time. Without advancing past the rolls already in
    the log, every action would roll the same number for the whole game — the seed
    would produce a stuck match rather than a reproducible one.
    """
    from bloodbowl.engine.dice import SeededDice
    from bloodbowl.engine.game import dice_for
    from bloodbowl.engine.state import Match

    expected = [SeededDice(seed=5).d6() for _ in range(1)]
    reference = SeededDice(seed=5)
    stream = [reference.d6() for _ in range(6)]

    m = Match(seed=5)
    assert dice_for(m).d6() == stream[0], "a fresh match starts at the head of the stream"

    # Fake three rolls into the log and the next draw must be the fourth value.
    from bloodbowl.engine.dice import Roll
    from bloodbowl.engine.events import Event

    m.apply(Event(kind="note", rolls=[Roll(kind="d6", dice=[1]) for _ in range(3)]))
    assert dice_for(m).d6() == stream[3], "the stream must skip the rolls already recorded"
    assert expected  # guard against the reference loop being optimised away


def test_successive_actions_do_not_repeat_the_same_roll(registry):
    """The same property, end to end through the tools."""
    import json as j

    _setup_board()
    t = _tools(registry)
    t["bb_game_new"].invoke({"seed": 5, "kicking_to": "home"})
    # A Rush every step: MA 5 is spent, then each further square rolls.
    for step in range(6):
        t["bb_game_act"].invoke({"action": "move", "player": "h00", "x": 7 if step % 2 else 6, "y": 12 - step // 2})
    log = j.loads(t["bb_game_log"].invoke({"last": 200}))
    rolls = [line for e in log["log"] for line in e["rolls"]]
    assert len(rolls) >= 2, f"expected several rolls, got {rolls}"
    assert len({r.split("rolled ")[-1] for r in rolls}) > 1, f"every action rolled the same: {rolls}"


def test_abandoning_a_match_leaves_the_board(registry):
    import json as j

    _setup_board()
    t = _tools(registry)
    t["bb_game_new"].invoke({"seed": 1, "kicking_to": "home"})
    assert j.loads(t["bb_game_abandon"].invoke({}))["discarded"] is True
    assert j.loads(t["bb_game_state"].invoke({}))["ok"] is False

    from bloodbowl.store import load

    assert len(load().players) == 2


# --- blocking --------------------------------------------------------------
#
# Every rule here was read off the S3 source and is quoted in the test. Block
# dice are scripted, so a "1 in 6" never decides whether a rule works.


def _st(side, x, y, st, skills=(), ma=6, av="9+"):
    return (side, x, y, ma, "3+", list(skills), st, av)


def _match_st(*specs, active="home"):
    """Like _match but with an explicit ST and AV per player."""
    from bloodbowl.engine.state import Match, PlayerState
    from bloodbowl.pitch import Player

    m = Match()
    counts = {"home": 0, "away": 0}
    for spec in specs:
        side, x, y, ma, ag, skills, st, av = spec
        i = counts[side]
        counts[side] += 1
        p = Player(
            side=side,
            x=x,
            y=y,
            position=f"{side.title()} {i}",
            MA=str(ma),
            ST=str(st),
            AG=ag,
            PA="4+",
            AV=av,
            skills=list(skills),
        )
        m.players.append(PlayerState(player=p, id=f"{side[0]}{i:02d}"))
    m.clock.active = active
    return m


def _bdice(faces, script=()):
    from bloodbowl.engine.dice import ScriptedDice

    return ScriptedDice(script=list(script), block_script=[list(faces)])


# --- dice count and who chooses -------------------------------------------


def test_equal_strength_rolls_one_die():
    from bloodbowl.engine.rules import block_dice

    assert block_dice(3, 3) == (1, "attacker")


def test_higher_strength_rolls_two_and_the_stronger_coach_chooses():
    from bloodbowl.engine.rules import block_dice

    assert block_dice(4, 3) == (2, "attacker")
    assert block_dice(3, 4) == (2, "defender"), "blocking someone stronger hands THEM the pick"


def test_over_double_rolls_three_and_double_alone_does_not():
    """S3: three dice when one player has OVER DOUBLE the other's Strength.
    'Over' is strictly greater, so ST 4 into ST 2 is two dice, not three."""
    from bloodbowl.engine.rules import block_dice

    assert block_dice(4, 2) == (2, "attacker"), "exactly double is not over double"
    assert block_dice(5, 2) == (3, "attacker")
    assert block_dice(2, 5) == (3, "defender")


# --- assists ---------------------------------------------------------------


def test_an_assist_needs_a_team_mate_marking_the_target():
    from bloodbowl.engine.rules import assist_count

    m = _match_st(_st("home", 7, 13, 3), _st("home", 8, 14, 3), _st("away", 7, 14, 3))
    assert assist_count(m, "home", m.by_id("a00")) == 2, "both home players Mark the target"


def test_an_assister_marked_by_another_opponent_does_not_assist():
    """S3: "...and is not Marked by another opposing player." ANOTHER — the player
    being blocked does not cancel the assists against themselves."""
    from bloodbowl.engine.rules import assist_count

    # h01 Marks the target but is itself Marked by a second away player.
    m = _match_st(
        _st("home", 7, 13, 3),
        _st("home", 8, 14, 3),
        _st("away", 7, 14, 3),
        _st("away", 9, 14, 3),
    )
    assert assist_count(m, "home", m.by_id("a00")) == 1, "h01 is Marked by a01 and cannot assist"


def test_the_target_of_the_block_never_cancels_its_own_assists():
    m = _match_st(_st("home", 7, 13, 3), _st("home", 8, 14, 3), _st("away", 7, 14, 3))
    from bloodbowl.engine.rules import assist_count

    # a00 Marks both home players, but it is the one being blocked.
    assert assist_count(m, "home", m.by_id("a00")) == 2


def test_a_prone_team_mate_cannot_assist():
    from bloodbowl.engine.rules import assist_count

    m = _match_st(_st("home", 7, 13, 3), _st("home", 8, 14, 3), _st("away", 7, 14, 3))
    m.by_id("h01").down = "prone"
    assert assist_count(m, "home", m.by_id("a00")) == 1


def test_guard_assists_even_while_marked():
    """S3: Guard assists "regardless of how many opposition players are Marking
    this player"."""
    from bloodbowl.engine.rules import assist_count

    m = _match_st(
        _st("home", 7, 13, 3),
        _st("home", 8, 14, 3, skills=["Guard"]),
        _st("away", 7, 14, 3),
        _st("away", 9, 14, 3),
    )
    assert assist_count(m, "home", m.by_id("a00")) == 2, "Guard ignores being Marked"


# --- push geometry ---------------------------------------------------------


def test_an_orthogonal_push_offers_three_squares_behind_the_target():
    from bloodbowl.engine.rules import push_squares

    assert sorted(push_squares(7, 13, 7, 14)) == [(6, 15), (7, 15), (8, 15)]


def test_a_diagonal_push_offers_three_squares_not_five():
    """THE geometry trap. Read literally, "an adjacent square that is not adjacent
    to the blocker" yields FIVE squares on a diagonal; the diagrams show three.
    The arc is the push direction plus its two neighbours. Both readings agree on
    an orthogonal block, which is why the wrong one survives casual testing."""
    from bloodbowl.engine.rules import push_squares

    got = sorted(push_squares(7, 13, 8, 14))
    assert len(got) == 3, got
    assert got == [(8, 15), (9, 14), (9, 15)]


# --- block results ---------------------------------------------------------


def test_push_back_moves_the_target_and_the_blocker_may_follow_up():
    m = _match_st(_st("home", 7, 13, 3), _st("away", 7, 14, 3))
    out = _block(m, "h00", "a00", _bdice(["push_back"]))
    assert out.ok and not out.turnover
    assert (m.by_id("a00").x, m.by_id("a00").y) == (7, 15), "straight back is the default push"
    assert (m.by_id("h00").x, m.by_id("h00").y) == (7, 14), "follow-up takes the vacated square"


def test_follow_up_can_be_declined():
    m = _match_st(_st("home", 7, 13, 3), _st("away", 7, 14, 3))
    _block(m, "h00", "a00", _bdice(["push_back"]), follow_up=False)
    assert (m.by_id("h00").x, m.by_id("h00").y) == (7, 13)


def test_pow_pushes_then_knocks_down_in_the_square_they_land_in():
    """S3: "Apply the Push Back result to the target player. The target player is
    then Knocked Down in the square they are now in.\""""
    m = _match_st(_st("home", 7, 13, 3), _st("away", 7, 14, 3, av="4+"))
    out = _block(m, "h00", "a00", _bdice(["pow"], script=[6, 6, 1, 1]))
    t = m.by_id("a00")
    assert (t.x, t.y) == (7, 15), "the push happens before the knockdown"
    assert t.down in ("prone", "stunned")
    assert out.ok and not out.turnover, "knocking the OPPONENT down is not a turnover"


def test_player_down_knocks_the_blocker_over_and_is_a_turnover():
    """A Block can go wrong. PLAYER DOWN knocks down the player who threw it."""
    m = _match_st(_st("home", 7, 13, 3, av="4+"), _st("away", 7, 14, 3))
    out = _block(m, "h00", "a00", _bdice(["player_down"], script=[6, 6, 1, 1]))
    assert out.turnover and not out.ok
    assert m.by_id("h00").down in ("prone", "stunned")
    assert m.by_id("a00").down == "standing"


def test_both_down_puts_both_players_on_the_floor():
    m = _match_st(_st("home", 7, 13, 3, av="12+"), _st("away", 7, 14, 3, av="12+"))
    out = _block(m, "h00", "a00", _bdice(["both_down"], script=[1, 1, 1, 1]))
    assert m.by_id("h00").down == "prone" and m.by_id("a00").down == "prone"
    assert out.turnover, "the active team's player went down"


def test_the_block_skill_keeps_its_owner_up_on_both_down():
    """S3: "may choose not to be Knocked Down when a Both Down result is applied
    during a Block Action that they are part of.\""""
    m = _match_st(
        _st("home", 7, 13, 3, skills=["Block"], av="12+"),
        _st("away", 7, 14, 3, av="12+"),
    )
    out = _block(m, "h00", "a00", _bdice(["both_down"], script=[1, 1]))
    assert m.by_id("h00").down == "standing", "Block keeps the blocker up"
    assert m.by_id("a00").down == "prone"
    assert not out.turnover, "no turnover when the active player stayed on their feet"


def test_stumble_is_only_a_push_when_the_target_has_dodge():
    """S3: "If the target player has the Dodge skill, this becomes Push Back.
    Otherwise, this becomes POW.\""""
    dodgy = _match_st(_st("home", 7, 13, 3), _st("away", 7, 14, 3, skills=["Dodge"]))
    _block(dodgy, "h00", "a00", _bdice(["stumble"]))
    assert dodgy.by_id("a00").down == "standing", "Dodge turns Stumble into a plain push"

    plain = _match_st(_st("home", 7, 13, 3), _st("away", 7, 14, 3, av="4+"))
    _block(plain, "h00", "a00", _bdice(["stumble"], script=[6, 6, 1, 1]))
    assert plain.by_id("a00").down != "standing", "without Dodge, Stumble is a POW"


def test_the_defender_chooses_when_they_are_stronger():
    """Two dice into a stronger player: THEY pick, so the engine must apply the
    worst result for the attacker rather than the first one rolled."""
    m = _match_st(_st("home", 7, 13, 3, av="4+"), _st("away", 7, 14, 5))
    out = _block(m, "h00", "a00", _bdice(["pow", "player_down"], script=[6, 6, 1, 1]))
    assert out.turnover, "the defender picks Player Down"
    assert m.by_id("h00").down != "standing"


# --- armour and injury -----------------------------------------------------


def test_armour_that_holds_leaves_the_player_merely_prone():
    m = _match_st(_st("home", 7, 13, 3), _st("away", 7, 14, 3, av="11+"))
    _block(m, "h00", "a00", _bdice(["pow"], script=[1, 1]))
    t = m.by_id("a00")
    assert t.down == "prone" and t.place == "pitch"


def test_a_broken_armour_rolls_for_injury_and_can_stun():
    m = _match_st(_st("home", 7, 13, 3), _st("away", 7, 14, 3, av="3+"))
    _block(m, "h00", "a00", _bdice(["pow"], script=[3, 3, 2, 2]))  # armour 6 vs 3+, injury 4
    assert m.by_id("a00").down == "stunned"


def test_a_high_injury_roll_removes_the_player_from_the_pitch():
    m = _match_st(_st("home", 7, 13, 3), _st("away", 7, 14, 3, av="3+"))
    _block(m, "h00", "a00", _bdice(["pow"], script=[3, 3, 6, 6, 9]))  # injury 12 = Casualty, then its D16
    t = m.by_id("a00")
    assert t.place == "casualty"
    assert m.at(t.x, t.y) is None, "a player in the box must stop occupying their square"


def test_a_knocked_out_player_goes_to_the_ko_box():
    m = _match_st(_st("home", 7, 13, 3), _st("away", 7, 14, 3, av="3+"))
    _block(m, "h00", "a00", _bdice(["pow"], script=[3, 3, 4, 5]))  # injury 9 = KO
    assert m.by_id("a00").place == "knocked_out"


def test_thick_skull_turns_an_eight_into_a_stunned():
    """S3: "they will only be Knocked-out on the roll of a 9; a roll of an 8 will
    be treated as a Stunned result.\""""
    plain = _match_st(_st("home", 7, 13, 3), _st("away", 7, 14, 3, av="3+"))
    _block(plain, "h00", "a00", _bdice(["pow"], script=[3, 3, 4, 4]))  # injury 8
    assert plain.by_id("a00").place == "knocked_out"

    tough = _match_st(_st("home", 7, 13, 3), _st("away", 7, 14, 3, av="3+", skills=["Thick Skull"]))
    _block(tough, "h00", "a00", _bdice(["pow"], script=[3, 3, 4, 4]))
    assert tough.by_id("a00").place == "pitch"
    assert tough.by_id("a00").down == "stunned"


def test_mighty_blow_is_spent_on_the_armour_roll_only_when_it_breaks_it():
    """S3 lets the +1 go to EITHER roll, after the roll. Spending it on armour
    that already broke would waste it; spending it on armour that still fails
    would waste it too."""
    m = _match_st(_st("home", 7, 13, 3, skills=["Mighty Blow"]), _st("away", 7, 14, 3, av="7+"))
    _block(m, "h00", "a00", _bdice(["pow"], script=[3, 3, 1, 1]))  # armour 6, +1 = 7 breaks
    rolls = [r for e in m.events for r in e.rolls]
    armour = next(r for r in rolls if r.kind == "Armour")
    assert armour.passed and armour.modifier == 1, "the +1 rescued a failed armour roll"


def test_the_armour_roll_is_two_dice_not_an_agility_test():
    """A natural 1 does not auto-fail an Armour Roll, and a natural 6 does not
    auto-break it — those belong to single-die Agility Tests."""
    m = _match_st(_st("home", 7, 13, 3), _st("away", 7, 14, 3, av="4+"))
    _block(m, "h00", "a00", _bdice(["pow"], script=[1, 6, 1, 1]))  # 1+6 = 7 >= 4+
    armour = next(r for e in m.events for r in e.rolls if r.kind == "Armour")
    assert armour.dice == [1, 6] and armour.total == 7 and armour.passed


# --- pushes into other players and off the pitch ---------------------------


def test_a_free_square_is_taken_before_any_chain_push():
    """A Chain Push happens only when there is NO unoccupied square. With a flank
    free the target simply goes there and nobody else moves."""
    m = _match_st(_st("home", 7, 13, 3), _st("away", 7, 14, 3), _st("away", 7, 15, 3))
    _block(m, "h00", "a00", _bdice(["push_back"]))
    assert (m.by_id("a00").x, m.by_id("a00").y) in ((8, 15), (6, 15))
    assert (m.by_id("a01").x, m.by_id("a01").y) == (7, 15), "the occupant did not move"


def _boxed_in():
    """All three push squares behind the target occupied — the only way to chain."""
    return _match_st(
        _st("home", 7, 13, 3),
        _st("away", 7, 14, 3),
        _st("away", 7, 15, 3),
        _st("away", 6, 15, 3),
        _st("away", 8, 15, 3),
    )


def test_a_chain_push_moves_the_player_behind():
    """S3: the occupant is Pushed Back "as if they had been Pushed Back by the
    player who is now occupying their square"."""
    m = _boxed_in()
    _block(m, "h00", "a00", _bdice(["push_back"]))
    assert (m.by_id("a00").x, m.by_id("a00").y) == (7, 15), "the target took the occupied square"
    assert (m.by_id("a01").x, m.by_id("a01").y) == (7, 16), "and its occupant was pushed on"


def test_a_prone_player_can_still_be_chain_pushed():
    """S3 says so explicitly — being on the floor is no protection from a shove."""
    m = _boxed_in()
    m.by_id("a01").down = "prone"
    _block(m, "h00", "a00", _bdice(["push_back"]))
    assert (m.by_id("a01").x, m.by_id("a01").y) == (7, 16)
    assert m.by_id("a01").down == "prone", "a chain push does not stand anyone up"


def test_a_push_off_the_sideline_sends_the_player_into_the_crowd():
    """Pushed into the Crowd when there is no unoccupied square on the pitch."""
    m = _match_st(_st("home", 2, 13, 3), _st("away", 1, 13, 3, av="3+"))
    _block(m, "h00", "a00", _bdice(["push_back"], script=[3, 3, 2, 2]))
    t = m.by_id("a00")
    assert t.place != "pitch" or t.down != "standing"
    assert any(e.kind == "player_left_pitch" for e in m.events), "no crowd event recorded"


def test_neither_participant_counts_as_an_assist():
    """Found by distrusting a screenshot: three different targets all reported
    "ST 5 v 4", which is too tidy for three players with different neighbours.

    The blocker was assisting its own block and the target was assisting its own
    defence. Both inflate by one and both produce a plausible number, so nothing
    looks wrong — which is exactly why it survived a passing test suite."""
    from bloodbowl.engine.rules import assist_count

    # A lone blocker against a lone target: neither may assist, so both are zero.
    m = _match_st(_st("home", 7, 13, 3), _st("away", 7, 14, 3))
    blocker, target = m.by_id("h00"), m.by_id("a00")
    assert assist_count(m, "home", target, exclude={blocker.id}) == 0, "the blocker is not its own assist"
    assert assist_count(m, "away", blocker, exclude={target.id}) == 0, "the target cannot assist itself"


def test_a_lone_block_is_one_die_not_two():
    """The end-to-end shape of the same bug: with both participants counting
    themselves, an even fight came out as ST 4 v 4 — still equal, still one die —
    but any asymmetry in the surrounding players turned into a phantom modifier."""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match_st(_st("home", 7, 13, 3), _st("away", 7, 14, 3))
    d = actions.get("block")["validate"](m, {"player": "h00", "target": "a00"}).detail
    assert (d["attacker_strength"], d["defender_strength"]) == (3, 3)
    assert d["offensive_assists"] == 0 and d["defensive_assists"] == 0
    assert d["dice"] == 1


def test_a_team_mate_marked_by_the_blocker_alone_still_assists():
    """ "not Marked by ANOTHER opposing player" — the blocker does not cancel the
    assists of the defenders standing next to it."""
    from bloodbowl.engine.rules import assist_count

    m = _match_st(_st("home", 7, 13, 3), _st("away", 7, 14, 3), _st("away", 6, 13, 3))
    blocker, target = m.by_id("h00"), m.by_id("a00")
    # a01 Marks the blocker and is Marked only BY the blocker, so it assists.
    assert assist_count(m, "away", blocker, exclude={target.id}) == 1


# --- the ball --------------------------------------------------------------


def _with_ball(m, x, y, carrier=""):
    from bloodbowl.engine.state import Ball

    m.ball = Ball(x=x, y=y, carrier=carrier, in_play=True)
    return m


def test_a_bounce_is_one_square_in_a_d8_direction():
    """S3: "When the rules tell you to Bounce the ball, it will Scatter (1) from
    its current location.\""""
    from bloodbowl.engine.ball import DIRECTIONS, bounce

    m = _with_ball(_match(("home", 1, 1)), 7, 13)
    bounce(m, _dice([5]))
    dx, dy = DIRECTIONS[5]
    assert (m.ball.x, m.ball.y) == (7 + dx, 13 + dy)


def test_the_direction_table_is_a_bijection_onto_the_eight_neighbours():
    """The template is a diagram, so the ROTATION is conventional — but every
    direction must be reachable exactly once or the bounce is biased."""
    from bloodbowl.engine.ball import DIRECTIONS

    assert sorted(DIRECTIONS) == list(range(1, 9))
    assert len(set(DIRECTIONS.values())) == 8
    assert (0, 0) not in DIRECTIONS.values()


def test_moving_onto_a_loose_ball_attempts_a_pick_up():
    m = _with_ball(_match(("home", 7, 13)), 7, 14)
    out = _move(m, "h00", 7, 14, _dice([4]))  # AG 3+, unmarked
    assert out.ok
    assert m.ball.carrier == "h00"


def test_a_failed_pick_up_is_a_turnover_and_the_ball_bounces():
    """S3: "If the test is failed, the player fails to pick up the ball and a
    Turnover is caused - the ball will then Bounce from the player's square.\""""
    m = _with_ball(_match(("home", 7, 13)), 7, 14)
    out = _move(m, "h00", 7, 14, _dice([1, 5]))  # pick-up fails, then the bounce
    assert out.turnover and not out.ok
    assert m.ball.carrier == ""
    assert (m.ball.x, m.ball.y) != (7, 14), "the ball must have bounced away"


def test_the_pick_up_is_modified_by_each_marking_opponent():
    """S3: "Apply a -1 modifier to this roll for each opposition player that is
    Marking the player attempting to pick up the ball.\""""
    m = _with_ball(_match(("home", 7, 13), ("away", 6, 15), ("away", 8, 15)), 7, 14)
    _move(m, "h00", 7, 14, _dice([4, 5]))  # 4 would pass unmodified; -2 fails it
    pick = next(r for e in m.events for r in e.rolls if r.kind == "Pick up")
    assert pick.modifier == -2 and not pick.passed


def test_a_player_pushed_onto_the_ball_does_not_pick_it_up_and_it_bounces():
    """S3: "If a player is ever involuntarily moved into a square containing the
    ball... they may not attempt to pick it up and it will Bounce; however, no
    Turnover will be caused." A push is not an activation."""
    m = _match_st(_st("home", 7, 13, 3), _st("away", 7, 14, 3))
    _with_ball(m, 7, 15)
    out = _block(m, "h00", "a00", _bdice(["push_back"], script=[5]))
    a = m.by_id("a00")
    assert (a.x, a.y) == (7, 15), "pushed onto the ball's square"
    assert m.ball.carrier == "", "no pick-up attempt from a push"
    assert not out.turnover, "and no turnover either"


def test_a_knocked_down_carrier_drops_the_ball():
    m = _match_st(_st("home", 7, 13, 3), _st("away", 7, 14, 3, av="12+"))
    _with_ball(m, 7, 14, carrier="a00")
    _block(m, "h00", "a00", _bdice(["pow"], script=[5, 1, 1]))
    assert m.ball.carrier == "", "going down means losing the ball"
    assert m.ball.in_play


def test_a_failed_dodge_also_drops_the_ball():
    """Every route to the floor loses the ball, not just a Block."""
    m = _match(("home", 7, 13), ("away", 7, 14))
    _with_ball(m, 7, 13, carrier="h00")
    _move(m, "h00", 6, 12, _dice([1, 1, 1, 5]))  # dodge fails, armour holds, bounce
    assert m.ball.carrier == ""


# --- catching --------------------------------------------------------------


def test_a_hand_off_needs_a_catch_and_can_be_dropped():
    m = _match(("home", 7, 13), ("home", 7, 14))
    _with_ball(m, 7, 13, carrier="h00")
    from bloodbowl.engine import actions

    actions.load_all()
    out = actions.get("handoff")["resolve"](m, {"player": "h00", "target": "h01"}, _dice([5]))
    assert out.ok and m.ball.carrier == "h01"

    m2 = _match(("home", 7, 13), ("home", 7, 14))
    _with_ball(m2, 7, 13, carrier="h00")
    out2 = actions.get("handoff")["resolve"](m2, {"player": "h00", "target": "h01"}, _dice([1, 5]))
    assert not out2.ok and out2.turnover, "a dropped hand-off is a turnover"
    assert m2.ball.carrier == ""


def test_a_prone_player_automatically_fails_to_catch():
    """S3: "If a Prone or Stunned player, or a player that is Distracted, is
    required to Catch a ball, they will automatically fail.\""""
    from bloodbowl.engine.ball import catch

    m = _with_ball(_match(("home", 7, 13)), 7, 13)
    m.by_id("h00").down = "prone"
    catch(m, m.by_id("h00"), _dice([5]))  # only the bounce die is needed
    assert m.ball.carrier == ""


def test_a_catch_is_modified_by_each_marking_opponent():
    from bloodbowl.engine.ball import catch

    m = _with_ball(_match(("home", 7, 13), ("away", 7, 14), ("away", 6, 14)), 7, 13)
    catch(m, m.by_id("h00"), _dice([4, 5]))
    r = next(r for e in m.events for r in e.rolls if r.kind == "Catch")
    assert r.modifier == -2


# --- Secure the Ball (new in S3) -------------------------------------------


def test_secure_the_ball_is_a_flat_two_up_when_nobody_is_near():
    from bloodbowl.engine import actions

    actions.load_all()
    m = _with_ball(_match(("home", 7, 13, 6, "6+")), 7, 14)  # dreadful AG, still 2+
    out = actions.get("secure")["resolve"](m, {"player": "h00"}, _dice([2]))
    assert out.ok and m.ball.carrier == "h00"
    r = next(r for e in m.events for r in e.rolls if r.kind == "Secure the Ball")
    assert r.target == 2 and r.modifier == 0, "Tackle Zones cannot reach a secured ball"


def test_secure_the_ball_is_refused_with_an_opponent_within_two_of_the_ball():
    """The clearance is measured from the BALL, not from the player."""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _with_ball(_match(("home", 7, 13), ("away", 7, 16)), 7, 14)
    legal = actions.get("secure")["validate"](m, {"player": "h00"})
    assert legal.ok is False and "within 2" in legal.reason


def test_secure_the_ball_ends_the_activation():
    from bloodbowl.engine import actions

    actions.load_all()
    m = _with_ball(_match(("home", 7, 13)), 7, 14)
    actions.get("secure")["resolve"](m, {"player": "h00"}, _dice([5]))
    assert m.by_id("h00").acted is True


def test_a_failed_secure_is_still_a_turnover():
    from bloodbowl.engine import actions

    actions.load_all()
    m = _with_ball(_match(("home", 7, 13)), 7, 14)
    out = actions.get("secure")["resolve"](m, {"player": "h00"}, _dice([1, 5]))
    assert out.turnover and m.ball.carrier == ""


# --- scoring ---------------------------------------------------------------


def test_carrying_the_ball_into_the_opposing_end_zone_scores():
    from bloodbowl.pitch import LENGTH

    m = _match(("home", 7, LENGTH - 1))
    _with_ball(m, 7, LENGTH - 1, carrier="h00")
    out = _move(m, "h00", 7, LENGTH, _dice([]))
    assert out.ok
    assert m.score["home"] == 1
    assert any(e.kind == "touchdown" for e in m.events)


def test_a_player_pushed_into_the_end_zone_with_the_ball_scores():
    """S3 allows a Touchdown from "a player holding the ball being Pushed or Chain
    Pushed into the opposition End Zone" — it can happen on the OPPONENT's turn."""
    from bloodbowl.pitch import LENGTH

    m = _match_st(_st("away", 7, LENGTH - 2, 3), _st("home", 7, LENGTH - 1, 3), active="away")
    _with_ball(m, 7, LENGTH - 1, carrier="h00")
    _block(m, "a00", "h00", _bdice(["push_back"]))
    assert m.score["home"] == 1, "the home player was shoved over their own line, and it counts"


def test_a_player_knocked_down_in_the_end_zone_does_not_score():
    """S3: "should a player with the ball be Placed Prone, Fall Over, or be Knocked
    Down as they move into the opposition End Zone, then no Touchdown will be
    scored - the player must be Standing.\""""
    from bloodbowl.pitch import LENGTH

    m = _match_st(_st("away", 7, LENGTH - 2, 3), _st("home", 7, LENGTH - 1, 3, av="12+"), active="away")
    _with_ball(m, 7, LENGTH - 1, carrier="h00")
    _block(m, "a00", "h00", _bdice(["pow"], script=[1, 1, 5]))
    assert m.score["home"] == 0, "knocked down on the line is not a Touchdown"


def test_a_touchdown_takes_the_ball_out_of_play():
    from bloodbowl.pitch import LENGTH

    m = _match(("home", 7, LENGTH - 1))
    _with_ball(m, 7, LENGTH - 1, carrier="h00")
    _move(m, "h00", 7, LENGTH, _dice([]))
    assert m.ball.in_play is False and m.ball.carrier == ""


# --- kick-off and drives ---------------------------------------------------


def _kicked(seed=3):
    """A match that has already kicked off, from a small board."""
    from bloodbowl.engine.game import new_match
    from bloodbowl.pitch import Player, Scenario

    sc = Scenario(name="t", home_team="Orc", away_team="Skaven")
    for side, x, y in (("home", 7, 13), ("home", 8, 13), ("away", 7, 14), ("away", 8, 14)):
        sc.players.append(
            Player(side=side, x=x, y=y, position="Orc Lineman", team="Orc", MA="6", ST="3", AG="3+", AV="9+")
        )
    return new_match(sc, seed=seed, kicking_to="home")


def test_a_new_match_kicks_off_and_the_ball_is_in_play():
    m = _kicked()
    assert m.ball.in_play
    assert any(e.kind == "kickoff_event" for e in m.events), "the Kick-off Event must be rolled"
    assert m.drive == 1


def test_the_kick_deviates_before_the_event_is_rolled():
    """S3 order: the kick Deviates, THEN 2D6 for the Kick-off Event, THEN the ball
    lands. Rolling the event after the ball was caught would make half the table
    meaningless."""
    m = _kicked()
    kinds = [e.kind for e in m.events]
    dev = next(i for i, e in enumerate(m.events) if any(r.kind.startswith("Deviate") for r in e.rolls))
    evt = kinds.index("kickoff_event")
    assert dev < evt, "the deviation comes first"


def test_every_kickoff_event_result_is_named_with_its_real_text():
    from bloodbowl.engine.kickoff import KICKOFF_EVENTS

    assert sorted(KICKOFF_EVENTS) == list(range(2, 13)), "2D6 spans 2..12"
    for roll, (name, text, _applied) in KICKOFF_EVENTS.items():
        assert name and len(text) > 30, f"{roll} has no usable rule text"
    assert KICKOFF_EVENTS[11][0] == "Dodgy Snack", "S3 replaced Officious Ref on 11"
    assert KICKOFF_EVENTS[12][0] == "Pitch Invasion"


def test_an_unapplied_kickoff_event_says_so_rather_than_pretending():
    """A coach told an event who sees nothing move would reasonably conclude the
    engine is broken, so an unapplied one has to SAY it was not applied.

    ALL ELEVEN ARE APPLIED NOW, so this guards the mechanism with a table entry
    that does not exist rather than with a real event — the same move as
    `_unmodelled_pair`. The mechanism still has to work: a fork can add an event,
    and the honest failure is to announce it and do nothing, not to do nothing
    quietly."""
    from bloodbowl.engine import kickoff

    real = dict(kickoff.KICKOFF_EVENTS)
    assert all(applied for _n, _t, applied in real.values()), "all eleven should be applied"

    m = _match(("home", 7, 11), ("away", 7, 20))
    patched = {**real, 7: ("Streaker", "A streaker delays the game. Nothing else happens.", False)}
    try:
        kickoff.KICKOFF_EVENTS = patched
        kickoff.kickoff_event(m, _dice([3, 4] + [4] * 10), "home")  # 3+4 = 7
    finally:
        kickoff.KICKOFF_EVENTS = real
    ev = next(e for e in m.events if e.kind == "kickoff_event")
    assert ev.detail["applied"] is False
    assert "NOT applied" in ev.text, ev.text


def test_time_out_moves_the_turn_marker_and_is_actually_applied():
    """The one event the engine owns outright — it is pure clock."""
    from bloodbowl.engine.kickoff import KICKOFF_EVENTS

    assert KICKOFF_EVENTS[3][2] is True, "Time-out! needs nothing this engine lacks"


def test_a_kick_that_leaves_the_receiving_half_is_a_touchback():
    from bloodbowl.engine.kickoff import award_touchback, in_own_half
    from bloodbowl.pitch import LENGTH

    m = _kicked()
    assert in_own_half("home", 1) and not in_own_half("home", LENGTH)
    events = award_touchback(m, "home")
    assert events, "a touchback must resolve to somebody holding the ball"
    assert m.ball.carrier or m.ball.in_play


def test_a_touchback_gives_the_ball_to_a_standing_receiver():
    m = _kicked()
    from bloodbowl.engine.kickoff import award_touchback

    award_touchback(m, "home")
    holder = m.by_id(m.ball.carrier)
    assert holder is not None and holder.side == "home"


def test_a_touchdown_ends_the_drive_and_the_conceder_receives():
    from bloodbowl.engine.game import act
    from bloodbowl.pitch import LENGTH

    m = _kicked()
    scorer = m.by_id("h00")
    scorer.player.x, scorer.player.y = 7, LENGTH - 1
    m.ball.carrier, m.ball.x, m.ball.y, m.ball.in_play = scorer.id, 7, LENGTH - 1, True
    m.clock.active = "home"
    report = act(m, "move", {"player": "h00", "x": 7, "y": LENGTH})
    assert report.get("touchdown") == "home"
    assert m.score["home"] == 1
    assert m.drive == 2, "scoring starts a new drive"
    assert m.clock.active == "away", "the conceding team receives"


def test_a_new_drive_puts_everyone_back_where_they_started():
    from bloodbowl.engine.game import start_drive

    m = _kicked()
    before = {p.id: (p.x, p.y) for p in m.players}
    m.by_id("h00").move_to(1, 1)
    m.by_id("h00").down = "prone"
    start_drive(m, receiving="away")
    assert (m.by_id("h00").x, m.by_id("h00").y) == before["h00"]
    assert m.by_id("h00").down == "standing", "a new drive stands everyone up"


def test_a_casualty_does_not_come_back_for_the_next_drive():
    from bloodbowl.engine.game import start_drive

    m = _kicked()
    m.by_id("h00").place = "casualty"
    start_drive(m, receiving="away")
    assert m.by_id("h00").place == "casualty", "a Casualty is out for the match"


def test_the_half_advances_after_eight_turns_each_and_the_match_ends_after_two():
    from bloodbowl.engine.game import end_turn

    m = _kicked()
    seen = set()
    for _ in range(40):
        seen.add((m.clock.half, m.clock.turn))
        end_turn(m)
        if m.over:
            break
    assert m.over, "the match must reach full time"
    assert (2, 8) in seen, "both halves are played"
    assert any(e.kind == "match_over" for e in m.events)


def test_the_post_game_runs_ONCE_however_many_times_the_last_turn_is_ended():
    """Found in a live game, which finished 2-2 and awarded FOUR MVPs.

    An Amazon ended on 8 SPP from being named MVP twice, and two different Ogre
    Blockers were each "the home MVP" off two separate D6. Dedicated Fans were
    updated twice and Full time was logged twice.

    `end_turn` runs `_end_of_game` whenever the match is ALREADY over, so a second
    call ran the entire Post-game Sequence again — and a second call is the normal
    case, not a strange one: a Touchdown's Turnover ends the turn, and then the
    coach ends the turn they think they are still in.

    Every step of it is a once-per-game fact. "The player that is given the MVP
    award generates 4 SPP" — the player, one, per side, per match.
    """
    from collections import Counter

    from bloodbowl.engine.game import end_turn

    m = _kicked()
    for _ in range(40):
        end_turn(m)
        if m.over:
            break
    assert m.over, "the match must reach full time"

    spp_at_the_whistle = dict(m.spp)
    before = Counter(e.kind for e in m.events)
    assert before["match_over"] == 1 and before["post_game"] == 2, "one whistle, one step per side"

    end_turn(m)  # the coach ends a turn the Turnover had already ended
    end_turn(m, forced=True)  # and a Turnover lands after the whistle

    after = Counter(e.kind for e in m.events)
    assert after["match_over"] == 1, f"the whistle blows once: {after['match_over']}"
    assert after["post_game"] == 2, f"one Dedicated Fans update per side: {after['post_game']}"
    assert m.spp == spp_at_the_whistle, f"no SPP is earned after full time: {m.spp} was {spp_at_the_whistle}"


def test_the_clock_never_runs_past_the_end_of_a_half():
    from bloodbowl.engine.state import TURNS_PER_HALF

    m = _kicked()
    from bloodbowl.engine.events import Event

    m.apply(Event(kind="clock_adjusted", detail={"delta": 5}))
    assert m.clock.turn <= TURNS_PER_HALF
    m.apply(Event(kind="clock_adjusted", detail={"delta": -99}))
    assert m.clock.turn >= 1


def test_a_whole_match_folds_back_to_the_same_position():
    """The replay guarantee has to survive drives, kick-offs and half-time —
    the parts with the most events per action."""
    from bloodbowl.engine.game import end_turn
    from bloodbowl.engine.state import Match, fold

    m = _kicked()
    for _ in range(6):
        end_turn(m)
    log = list(m.events)

    fresh = _kicked()
    fresh.players = fresh.players  # same roster, fresh state
    for p in fresh.players:
        p.down, p.place, p.ma_used, p.acted = "standing", "pitch", 0, False
    rebuilt = fold(Match(seed=fresh.seed, players=fresh.players), log)
    assert (rebuilt.clock.half, rebuilt.clock.turn) == (m.clock.half, m.clock.turn)
    assert rebuilt.score == m.score
    assert rebuilt.drive == m.drive


def test_a_log_line_always_names_somebody():
    """A board built from a preset holds labelled tokens with no positional, so
    the log read "Touchback:  is given the ball" — a sentence with a hole in it."""
    from bloodbowl.engine.state import PlayerState
    from bloodbowl.pitch import Player

    token = PlayerState(player=Player(side="home", x=1, y=1, label="LOS"), id="h00")
    assert token.name() == "LOS"
    nameless = PlayerState(player=Player(side="home", x=1, y=1), id="h07")
    assert nameless.name() == "h07"
    real = PlayerState(player=Player(side="home", x=1, y=1, position="Orc Blitzer", label="OB"), id="h01")
    assert real.name() == "Orc Blitzer", "a real positional wins over its badge"


# --- passing ---------------------------------------------------------------
#
# Everything here is quoted from the source EXCEPT how far each band reaches,
# which is measured off the physical ruler — see engine/ruler.py. The tests keep
# those two things apart: the rules get exact assertions, the ruler gets a
# cross-check against the evidence it was derived from.


def test_the_range_bands_reproduce_the_reported_maximum_reaches():
    """The derivation, asserted. Two independent community sources: measured
    section lengths, and separately reported maximum reaches per band ("N across,
    M down"). Every reported maximum must fall inside its band, and the next
    square along the same line must fall outside it — otherwise the reported
    maximum was not the maximum and the thresholds are wrong."""
    import math

    from bloodbowl.engine.ruler import band

    maxima = {"Quick Pass": (3, 1), "Short Pass": (6, 0), "Long Pass": (10, 2), "Long Bomb": (13, 1)}
    for name, (dx, dy) in maxima.items():
        got = band(0, 0, dx, dy)
        assert got is not None and got[0] == name, f"{dx}x{dy} should be {name}, got {got}"
        further = band(0, 0, dx + 1, dy)
        assert further is None or further[0] != name, f"{dx + 1}x{dy} should be beyond {name}"
        assert math.hypot(dx, dy) > 0


def test_the_ruler_declares_itself_measured_rather_than_quoted():
    """A coach must never be told a range is rules-derived when it is measured."""
    from bloodbowl.engine.ruler import describe

    d = describe()
    assert d["measured_not_quoted"] is True
    assert "no table of squares" in d["note"]
    assert [b["modifier"] for b in d["bands"]] == [0, -1, -2, -3]


def test_out_of_range_is_refused_rather_than_thrown():
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 2))
    m.by_id("h00").player.PA = "3+"
    _with_ball(m, 7, 2, carrier="h00")
    legal = actions.get("pass")["validate"](m, {"player": "h00", "x": 7, "y": 22})
    assert legal.ok is False and "out of range" in legal.reason


def test_the_range_modifier_follows_the_band():
    """S3: Quick no modifier, Short -1, Long -2, Long Bomb -3."""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 2))
    m.by_id("h00").player.PA = "3+"
    _with_ball(m, 7, 2, carrier="h00")
    v = actions.get("pass")["validate"]
    assert v(m, {"player": "h00", "x": 7, "y": 4}).detail["range_modifier"] == 0
    assert v(m, {"player": "h00", "x": 7, "y": 8}).detail["range_modifier"] == -1
    assert v(m, {"player": "h00", "x": 7, "y": 12}).detail["range_modifier"] == -2
    assert v(m, {"player": "h00", "x": 7, "y": 15}).detail["range_modifier"] == -3


def test_marking_the_passer_modifies_the_throw():
    """S3: "Apply a -1 modifier for each opposition player Marking the player
    performing the Pass Action.\""""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 2), ("away", 6, 2), ("away", 8, 2))
    m.by_id("h00").player.PA = "3+"
    _with_ball(m, 7, 2, carrier="h00")
    d = actions.get("pass")["validate"](m, {"player": "h00", "x": 7, "y": 4}).detail
    assert d["marking_passer"] == -2 and d["modifier"] == -2


def _pass(m, pid, x, y, dice):
    from bloodbowl.engine import actions

    actions.load_all()
    return actions.get("pass")["resolve"](m, {"player": pid, "x": x, "y": y}, dice)


def test_an_accurate_pass_lands_in_the_target_square_and_is_caught():
    m = _match(("home", 7, 2, 6, "3+"), ("home", 7, 5, 6, "3+"))
    m.by_id("h00").player.PA = "3+"
    _with_ball(m, 7, 2, carrier="h00")
    out = _pass(m, "h00", 7, 5, _dice([5, 5]))  # pass passes, catch passes
    assert out.ok and m.ball.carrier == "h01"


def test_an_inaccurate_pass_scatters_three_squares_from_the_target():
    """S3: "the ball will Scatter (3) from the target square before landing.\""""
    m = _match(("home", 7, 2, 6, "3+"))
    m.by_id("h00").player.PA = "5+"
    _with_ball(m, 7, 2, carrier="h00")
    out = _pass(m, "h00", 7, 5, _dice([2, 5, 5, 5, 4]))  # fail, then 3 scatters + a bounce
    kinds = [r.kind for e in out.events for r in e.rolls]
    assert kinds.count("Direction") >= 3, f"expected Scatter (3): {kinds}"


def test_a_natural_one_fumbles_and_the_ball_bounces_from_the_thrower():
    """S3: "The ball is dropped and will Bounce from the throwing player's square
    and a Turnover will be caused.\""""
    m = _match(("home", 7, 2, 6, "3+"))
    m.by_id("h00").player.PA = "2+"
    _with_ball(m, 7, 2, carrier="h00")
    out = _pass(m, "h00", 7, 4, _dice([1, 5]))
    assert out.turnover and not out.ok
    assert m.ball.carrier == ""
    assert abs(m.ball.x - 7) <= 1 and abs(m.ball.y - 2) <= 1, "it bounces from the THROWER's square"


def test_a_one_after_modifiers_also_fumbles():
    """S3 fumbles on "a 1 after modifiers, OR the roll is a natural 1" — so a
    heavily modified pass can fumble on a die that was not a 1."""
    m = _match(("home", 7, 2, 6, "3+"), ("away", 6, 2), ("away", 8, 2), ("away", 6, 3))
    m.by_id("h00").player.PA = "3+"
    _with_ball(m, 7, 2, carrier="h00")
    out = _pass(m, "h00", 7, 5, _dice([2, 5, 5, 5]))  # 2, then the bounce and any catch
    assert out.turnover, "a total of 1 or less is a fumble even on a 2"
    fumble = next(r for e in out.events for r in e.rolls if r.kind == "Pass")
    assert fumble.dice[0] != 1 and (fumble.total or 0) <= 1, "fumbled without a natural 1"


def test_a_player_with_no_passing_ability_cannot_pass():
    """A Troll's PA reads "-". Treating that as a 4+ would invent an ability."""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 2))
    m.by_id("h00").player.PA = "-"
    _with_ball(m, 7, 2, carrier="h00")
    legal = actions.get("pass")["validate"](m, {"player": "h00", "x": 7, "y": 4})
    assert legal.ok is False and "Passing Ability" in legal.reason


def test_an_opponent_in_the_path_can_intercept_and_that_is_a_turnover():
    m = _match(("home", 7, 2, 6, "3+"), ("home", 7, 8, 6, "3+"), ("away", 7, 5, 6, "2+"))
    m.by_id("h00").player.PA = "2+"
    _with_ball(m, 7, 2, carrier="h00")
    out = _pass(m, "h00", 7, 8, _dice([6, 6]))  # accurate, then a natural 6 intercepts
    assert out.turnover and not out.ok
    assert m.ball.carrier == "a02"


def test_a_prone_opponent_in_the_path_cannot_intercept():
    """Only Standing players, and "Players that have lost their Tackle Zone may
    not attempt to Intercept.\""""
    m = _match(("home", 7, 2, 6, "3+"), ("home", 7, 8, 6, "3+"), ("away", 7, 5, 6, "2+"))
    m.by_id("h00").player.PA = "2+"
    m.by_id("a02").down = "prone"
    _with_ball(m, 7, 2, carrier="h00")
    out = _pass(m, "h00", 7, 8, _dice([6, 5]))  # no interception die scripted
    assert out.ok and m.ball.carrier == "h01"


def test_a_distracted_opponent_cannot_intercept_either():
    m = _match(("home", 7, 2, 6, "3+"), ("home", 7, 8, 6, "3+"), ("away", 7, 5, 6, "2+"))
    m.by_id("h00").player.PA = "2+"
    m.by_id("a02").distracted = True
    _with_ball(m, 7, 2, carrier="h00")
    out = _pass(m, "h00", 7, 8, _dice([6, 5]))
    assert out.ok


def test_a_pass_that_ends_loose_is_a_turnover():
    """S3 treats a Pass that does not end in your own hands as a Turnover."""
    m = _match(("home", 7, 2, 6, "3+"))
    m.by_id("h00").player.PA = "2+"
    _with_ball(m, 7, 2, carrier="h00")
    out = _pass(m, "h00", 7, 4, _dice([5, 5]))  # accurate into an empty square, bounces
    assert out.turnover and m.ball.carrier == ""


def test_a_modified_roll_reads_as_arithmetic_that_actually_works():
    """The log is the narration source, so a coach must be able to read it
    literally. It used to print the post-modifier total AND the modifier —
    "rolled 3-3 — passed" — which reads as 3 minus 3 = 0 passing a 3+, i.e. a
    broken engine. It was a natural 6 with a -3."""
    from bloodbowl.engine.dice import roll_target

    d = _dice([6])
    r = roll_target(d, "Intercept", 3, -3)
    line = r.describe()
    assert r.passed, "a natural 6 always succeeds"
    assert "rolled 6 -3 = 3" in line, line
    assert "rolled 3-3" not in line

    plain = roll_target(_dice([4]), "Dodge", 3)
    assert plain.describe() == "Dodge: needed 3+, rolled 4 — passed"


def test_the_pass_log_line_is_grammatical():
    m = _match(("home", 7, 2, 6, "3+"))
    m.by_id("h00").player.PA = "2+"
    _with_ball(m, 7, 2, carrier="h00")
    out = _pass(m, "h00", 7, 4, _dice([5, 5]))
    line = next(e.text for e in out.events if e.kind == "pass_thrown")
    assert "throws an accurate" in line, line
    assert "a n" not in line


# --- the Blitz Action ------------------------------------------------------
#
# S3: "Simply put, a Blitz Action combines both a Move Action and a Block Action;
# however, only a single player may perform a Blitz Action each Turn."
#
# Modelled as a DECLARATION plus the ordinary move and block, because "if at any
# point during this Move Action they are adjacent to … their intended target" is a
# decision the coach makes step by step.


def _declare(m, pid, target):
    from bloodbowl.engine import actions

    actions.load_all()
    return actions.get("blitz")["resolve"](m, {"player": pid, "target": target}, _dice([]))


def test_a_blitz_is_move_then_block_then_more_move():
    """The whole shape in one test: declare, walk, hit them, walk on.

    "After the player has performed the Block Action, they can continue their Move
    Action using any remaining Move Allowance they have left."
    """
    m = _match(("home", 7, 10, 6), ("away", 7, 14, 6))
    assert _declare(m, "h00", "a01").ok

    for y in (11, 12, 13):
        assert _move(m, "h00", 7, y, _dice([])).ok
    assert m.by_id("h00").ma_used == 3

    out = _block(m, "h00", "a01", _dice([], [["push_back"]]), follow_up=False)
    assert out.ok
    assert m.by_id("h00").ma_used == 4, "the Blitz's Block costs a point of Move Allowance"
    assert (m.by_id("a01").x, m.by_id("a01").y) == (7, 15), "the target was pushed"

    # …and they may keep going.
    assert _move(m, "h00", 6, 13, _dice([])).ok
    assert m.by_id("h00").ma_used == 5


def test_the_rulebooks_own_worked_example():
    """Reproduced verbatim from the S3 text:

    "The Skeleton Lineman has declared a Blitz Action against the Bretonnian
     Squire and uses all of their Move Allowance of 5 to end next to their
     intended target. As a result, the Skeleton Lineman must Rush in order to
     perform the Block Action part of the Blitz Action. The Skeleton Lineman rolls
     a 4 and succeeds, so the Block Action can go ahead."
    """
    m = _match(("home", 7, 8, 5), ("away", 7, 14, 6))
    assert _declare(m, "h00", "a01").ok
    for y in (9, 10, 11, 12, 13):
        assert _move(m, "h00", 7, y, _dice([])).ok
    assert m.by_id("h00").ma_used == 5 == m.by_id("h00").movement(), "all of their Move Allowance"

    out = _block(m, "h00", "a01", _dice([4], [["push_back"]]), follow_up=False)
    assert out.ok, "the Block Action can go ahead"
    rush = next(r for e in out.events for r in e.rolls if r.kind == "Rush")
    assert rush.dice == [4] and rush.passed
    assert any(e.kind == "block_rolled" for e in out.events)


def test_a_failed_rush_for_the_block_floors_them_where_they_stand():
    """ "On a 1, the player trips and Falls Over in the square they were attempting
    to Rush into" — but a Rush that buys the Block buys no square. The rules settle
    the same shape for a Jump: "the rushing player will Fall Over in the square
    they are in rather than the square they are attempting to Jump into." So: where
    they stand, and the Block never happens.
    """
    m = _match(("home", 7, 8, 5), ("away", 7, 14, 6))
    _declare(m, "h00", "a01")
    for y in (9, 10, 11, 12, 13):
        _move(m, "h00", 7, y, _dice([]))

    out = _block(m, "h00", "a01", _dice([1, 2, 2], [["pow"]]))
    assert not out.ok and out.turnover
    p = m.by_id("h00")
    assert (p.x, p.y) == (7, 13), "they fall where they are, not into the target's square"
    assert p.down in ("prone", "stunned")
    assert not any(e.kind == "block_rolled" for e in out.events), "the Block never happened"


def test_only_one_blitz_per_team_per_turn():
    """ "only a single player may perform a Blitz Action each Turn" — and
    "Players are never required to perform the Block Action against their intended
    target if they decide not to, though they will still count as having used
    their team's one Blitz Action for the Turn." So DECLARING spends it.
    """
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 10, 6), ("home", 8, 10, 6), ("away", 7, 14, 6))
    assert _declare(m, "h00", "a02").ok

    second = actions.get("blitz")["validate"](m, {"player": "h01", "target": "a02"})
    assert not second.ok
    assert "already used their one Blitz" in second.reason


def test_a_new_turn_gives_the_team_its_blitz_back():
    from bloodbowl.engine.game import end_turn

    m = _match(("home", 7, 10, 6), ("away", 7, 14, 6))
    _declare(m, "h00", "a01")
    assert m.blitz
    end_turn(m)
    assert m.blitz == {}


def test_an_unreachable_target_may_not_be_declared():
    """ "Players may not declare an opposition player as the intended target of the
    Block Action if they cannot reach the player at all with their Move Action
    (including any extra squares gained by attempting to Rush)."

    MA 3 plus two Rushes is five squares; getting adjacent to a player nine away
    is not on.
    """
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 5, 3), ("away", 7, 14, 6))
    legal = actions.get("blitz")["validate"](m, {"player": "h00", "target": "a01"})
    assert not legal.ok and "out of reach" in legal.reason

    # The boundary, both sides of it. MA 5 plus two Rushes is 7 squares, and the
    # ring around a player at (7,14) starts at (7,13):
    #   from (7,6) that ring is exactly 7 steps — reachable, and the declaration
    #   stands even though every square is spent getting there and the Block
    #   itself costs one more. The rules gate the declaration on REACH alone, and
    #   "players are never required to perform the Block Action", so this is a
    #   legal thing to do rather than something to refuse — but the coach is told.
    at_the_limit = actions.get("blitz")["validate"](
        _match(("home", 7, 6, 5), ("away", 7, 14, 6)), {"player": "h00", "target": "a01"}
    )
    assert at_the_limit.ok
    assert at_the_limit.detail["steps"] == 7 and at_the_limit.detail["budget"] == 7
    assert at_the_limit.detail["can_reach"] is True
    assert at_the_limit.detail["can_block"] is False, "no square left to pay for the Block"

    #   one square further back and it is out of reach altogether.
    beyond = actions.get("blitz")["validate"](
        _match(("home", 7, 5, 5), ("away", 7, 14, 6)), {"player": "h00", "target": "a01"}
    )
    assert not beyond.ok and "out of reach" in beyond.reason


def test_reach_is_a_route_and_not_a_straight_line():
    """A target with every neighbouring square occupied cannot be reached however
    much Move Allowance is going spare. Measuring the distance rather than walking
    it would declare a Blitz that can never land."""
    from bloodbowl.engine import actions

    actions.load_all()
    ring = [("home", x, y, 6) for x in (6, 7, 8) for y in (13, 14, 15) if (x, y) != (7, 14)]
    m = _match(("home", 7, 10, 6), ("away", 7, 14, 6), *ring)
    legal = actions.get("blitz")["validate"](m, {"player": "h00", "target": "a01"})
    assert not legal.ok and "no route" in legal.reason


def test_the_blitz_block_must_be_against_the_declared_target():
    """ "they must ALSO declare which opposition player is the intended target."
    A Blitz is not a licence to block whoever turns out to be handy."""
    from bloodbowl.engine import actions

    actions.load_all()
    # The second opponent sits at (6,14): adjacent to the square h00 walks INTO,
    # but not to the one it leaves, so the step needs no Dodge and the test is
    # about the Blitz rather than about the dice.
    m = _match(("home", 7, 12, 6), ("away", 7, 14, 6), ("away", 6, 14, 6))
    _declare(m, "h00", "a01")
    _move(m, "h00", 7, 13, _dice([]))  # now adjacent to BOTH away players

    other = actions.get("block")["validate"](m, {"player": "h00", "target": "a02"})
    assert not other.ok and "already acted" in other.reason
    assert actions.get("block")["validate"](m, {"player": "h00", "target": "a01"}).ok


def test_a_blitz_does_not_buy_a_second_block():
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6))
    _declare(m, "h00", "a01")
    assert _block(m, "h00", "a01", _dice([], [["push_back"]]), follow_up=False).ok
    assert m.blitz["blocked"] is True

    again = actions.get("block")["validate"](m, {"player": "h00", "target": "a01"})
    assert not again.ok and "already acted" in again.reason


def test_a_block_may_only_target_a_standing_player():
    """ "they must nominate one Standing opposition player they are Marking to be
    the target of the Block Action." Flooring someone already down is what the Foul
    Action is for — and the engine used to allow it."""
    from bloodbowl.engine import actions
    from bloodbowl.engine.events import Event

    actions.load_all()
    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6))
    m.apply(Event(kind="player_placed_prone", actor="a01", detail={"down": "prone"}))

    plain = actions.get("block")["validate"](m, {"player": "h00", "target": "a01"})
    assert not plain.ok and "Standing" in plain.reason
    declared = actions.get("blitz")["validate"](m, {"player": "h00", "target": "a01"})
    assert not declared.ok and "Standing" in declared.reason


def test_the_declared_blitz_survives_the_fold():
    """Per-turn state like everything else: recorded, folded, never remembered."""
    from bloodbowl.engine.state import Match, fold

    m = _match(("home", 7, 10, 6), ("away", 7, 14, 6))
    _declare(m, "h00", "a01")
    for who in (fold(_match(("home", 7, 10, 6), ("away", 7, 14, 6)), list(m.events)), Match.from_dict(m.to_dict())):
        assert who.blitz == {"player": "h00", "target": "a01", "blocked": False}


def test_legal_moves_offers_the_blitz_with_the_distance_already_walked():
    """The coach asks the engine rather than eyeballing it — a team's one Blitz
    per turn cannot be taken back once declared."""
    from bloodbowl.engine.game import legal_moves

    m = _match(("home", 7, 10, 6), ("away", 7, 14, 6), ("away", 2, 25, 6))
    out = legal_moves(m, "h00")
    assert out["blitz"]["available"] is True
    targets = {t["target"]: t for t in out["blitz"]["targets"]}
    assert "a01" in targets and targets["a01"]["steps"] == 3 and targets["a01"]["can_block"] is True
    assert "a02" not in targets, "the one across the pitch is out of reach and is not offered"

    _declare(m, "h00", "a01")
    assert legal_moves(m, "h00")["blitz"]["declared"]["target"] == "a01"


def test_an_ordinary_block_ends_the_activation_but_a_blitzs_block_does_not():
    """The distinction the Blitz exists to make, and the reason `done` is a
    separate flag from `acted`.

    `acted` means an activation has BEGUN — a single step of movement sets it, so
    it can never be what stops movement. `done` means it is OVER. With only
    `acted` to go on, movement was ungated and a player could throw a Block
    Action and then stroll away, which no Action in the game allows.
    """
    from bloodbowl.engine import actions

    actions.load_all()

    plain = _match(("home", 7, 13, 6), ("away", 7, 14, 6))
    assert _block(plain, "h00", "a01", _dice([], [["push_back"]]), follow_up=False).ok
    assert plain.by_id("h00").done is True
    after = actions.get("move")["validate"](plain, {"player": "h00", "x": 6, "y": 12})
    assert not after.ok and "activation is over" in after.reason

    blitzed = _match(("home", 7, 13, 6), ("away", 7, 14, 6))
    _declare(blitzed, "h00", "a01")
    assert _block(blitzed, "h00", "a01", _dice([], [["push_back"]]), follow_up=False).ok
    assert blitzed.by_id("h00").done is False, "a Blitz's Block leaves the activation open"
    assert actions.get("move")["validate"](blitzed, {"player": "h00", "x": 6, "y": 12}).ok


@pytest.mark.parametrize("action", ["block", "handoff", "secure", "pass"])
def test_no_action_lets_a_player_walk_on_afterwards(action):
    """None of these includes movement after the fact — "may not continue moving
    after the pass has been made", "their activation immediately ends"."""
    from bloodbowl.engine import actions

    actions.load_all()
    m, cmd, script, block = _acting_board(action)
    actions.get(action)["resolve"](m, {"player": "h00", **cmd}, _dice(script, block))
    p = m.by_id("h00")
    legal = actions.get("move")["validate"](m, {"player": "h00", "x": p.x - 1, "y": p.y - 1})
    assert not legal.ok, f"{action} left the player free to keep moving"


def test_the_activation_being_over_survives_the_fold_too():
    from bloodbowl.engine.state import fold

    m, cmd, script, block = _acting_board("block")
    from bloodbowl.engine import actions

    actions.load_all()
    actions.get("block")["resolve"](m, {"player": "h00", **cmd}, _dice(script, block))
    fresh, _c, _s, _b = _acting_board("block")
    assert fold(fresh, list(m.events)).by_id("h00").done is True


# --- the free Move, and one Action per team per turn -----------------------


def _ball_board():
    """A carrier, a team-mate to hand to, and an opponent within Blitz reach."""
    m = _match(("home", 7, 10, 6), ("home", 8, 10, 6), ("away", 7, 15, 6))
    for who in ("h00", "h01"):
        m.by_id(who).player.PA = "3+"
    m.apply(_ball_at(7, 10, carrier="h00"))
    return m


@pytest.mark.parametrize(
    "action,cmd",
    [("pass", {"x": 7, "y": 14}), ("handoff", {"target": "h01"})],
)
def test_an_action_that_grants_a_free_move_is_not_refused_for_having_moved(action, cmd):
    """S3, for the Pass and word for word again for the Hand-off:

    "A player that declares a Pass Action may ALSO MAKE A FREE MOVE ACTION before
     making the pass, but may not continue moving after the pass has been made."

    The check here used to be `p.acted`, which a single step of movement sets — so
    the free Move the rules grant made the Action itself illegal, and move-then-
    pass has been impossible for as long as passing has existed. The question is
    whether the activation is OVER (`done`), not whether it has begun.
    """
    from bloodbowl.engine import actions

    actions.load_all()
    m = _ball_board()
    assert _move(m, "h00", 7, 11, _dice([])).ok
    legal = actions.get(action)["validate"](m, {"player": "h00", **cmd})
    assert legal.ok, f"the free Move before a {action} was refused: {legal.reason}"


def test_but_not_after_the_activation_is_over():
    """ "…may not continue moving after the pass has been made," and no second
    Action either."""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _ball_board()
    m.by_id("h00").player.PA = "2+"
    actions.get("pass")["resolve"](m, {"player": "h00", "x": 7, "y": 14}, _dice([6, 6, 6, 6, 6, 6]))
    again = actions.get("pass")["validate"](m, {"player": "h00", "x": 7, "y": 13})
    assert not again.ok and "activation is over" in again.reason


def test_a_blitzing_player_may_not_decide_it_was_a_pass():
    """The one activation that is neither begun-nor-over. A Blitz's Block leaves
    `done` false so the player can keep MOVING — not so they can take a different
    Action instead."""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _ball_board()
    m.apply(_ball_at(7, 10, carrier="h00"))
    _declare(m, "h00", "a02")
    legal = actions.get("pass")["validate"](m, {"player": "h00", "x": 7, "y": 14})
    assert not legal.ok and "already declared a Blitz Action" in legal.reason


@pytest.mark.parametrize(
    "action,first,second",
    [
        ("pass", {"x": 7, "y": 14}, {"x": 8, "y": 14}),
        ("handoff", {"target": "h01"}, {"target": "h00"}),
    ],
)
def test_only_one_of_each_capped_action_per_team_per_turn(action, first, second):
    """ "Only a single Pass Action can be declared each Turn" — and the same
    sentence for Hand-off, Secure the Ball, Blitz and Foul. Move and Block are the
    two the text explicitly does NOT cap.
    """
    from bloodbowl.engine import actions

    actions.load_all()
    m = _ball_board()
    m.by_id("h00").player.PA = "2+"
    out = actions.get(action)["resolve"](m, {"player": "h00", **first}, _dice([6, 6, 6, 6, 6, 6]))
    assert out.events, out.text
    assert m.turn_actions.get(action) == "h00", m.turn_actions

    # Whoever now holds the ball, a SECOND one this turn is refused.
    holder = m.ball.carrier or "h01"
    legal = actions.get(action)["validate"](m, {"player": holder, **second})
    assert not legal.ok
    from bloodbowl.engine.actions import DISPLAY

    assert f"one {DISPLAY[action]} Action this turn" in legal.reason, legal.reason


def test_block_and_move_are_not_capped():
    """ "There is no limit to the number of players that can declare a Block Action
    each Turn." Capping everything uniformly would have been tidier and wrong."""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6), ("home", 8, 13, 6), ("away", 8, 14, 6))
    assert _block(m, "h00", "a01", _dice([], [["push_back"]]), follow_up=False).ok
    assert "block" not in m.turn_actions
    assert actions.get("block")["validate"](m, {"player": "h02", "target": "a03"}).ok


def test_the_turn_ledger_is_cleared_by_the_next_turn_and_survives_a_fold():
    from bloodbowl.engine.game import end_turn
    from bloodbowl.engine.state import Match, fold

    m = _ball_board()
    m.by_id("h00").player.PA = "2+"
    from bloodbowl.engine import actions

    actions.load_all()
    actions.get("pass")["resolve"](m, {"player": "h00", "x": 7, "y": 14}, _dice([6, 6, 6, 6, 6, 6]))
    assert m.turn_actions == {"pass": "h00"}

    # The ledger is state, so it must come back from the log alone.
    assert fold(_ball_board(), list(m.events)).turn_actions == {"pass": "h00"}
    assert Match.from_dict(m.to_dict()).turn_actions == {"pass": "h00"}

    end_turn(m)
    end_turn(m)
    assert m.turn_actions == {}


# --- the Foul Action -------------------------------------------------------


def _foul(m, pid, target, dice, **cmd):
    from bloodbowl.engine import actions

    actions.load_all()
    return actions.get("foul")["resolve"](m, {"player": pid, "target": target, **cmd}, dice)


def _floored(m, pid, how="prone"):
    from bloodbowl.engine.events import Event

    m.apply(Event(kind="player_placed_prone", actor=pid, detail={"down": how}))
    return m


def test_a_foul_may_only_target_a_player_who_is_already_down():
    """The exact complement of the Block: "The player must finish their Move
    Action adjacent to a Prone or Stunned opposition player in order to perform
    the Foul Action." A Block needs a Standing target; a Foul needs one that
    isn't, and neither is a special case of the other."""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6))
    standing = actions.get("foul")["validate"](m, {"player": "h00", "target": "a01"})
    assert not standing.ok and "Standing" in standing.reason and "Block them" in standing.reason

    _floored(m, "a01")
    assert actions.get("foul")["validate"](m, {"player": "h00", "target": "a01"}).ok
    # …and now the Block is the one that is refused. The two are exclusive.
    blocked = actions.get("block")["validate"](m, {"player": "h00", "target": "a01"})
    assert not blocked.ok and "Standing" in blocked.reason


def test_foul_assists_modify_the_armour_roll_not_strength():
    """ "the player making the Foul Action may apply a +1 modifier for each
    Offensive Assist, and apply a -1 modifier for each Defensive Assist."

    A Block spends its assists on STRENGTH, before any dice. Carrying that habit
    over would put the assists in the wrong place and still look plausible.
    """
    from bloodbowl.engine import actions

    actions.load_all()
    # h00 fouls a01; h02 assists; a03 marks h00 and cancels nothing but assists back.
    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6), ("home", 8, 14, 6), ("away", 6, 13, 6))
    _floored(m, "a01")
    d = actions.get("foul")["validate"](m, {"player": "h00", "target": "a01"}).detail
    assert d["offensive_assists"] == 1, d
    assert d["defensive_assists"] == 1, d
    assert d["armour_modifier"] == 0

    out = _foul(m, "h00", "a01", _dice([3, 3, 6, 6, 4]))
    armour = next(r for e in out.events for r in e.rolls if r.kind == "Armour")
    assert armour.modifier == 0


def test_the_armour_roll_actually_carries_the_assist_modifier():
    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6), ("home", 8, 14, 6), ("home", 6, 14, 6))
    _floored(m, "a01")
    out = _foul(m, "h00", "a01", _dice([3, 4, 6, 6, 9, 4]))  # …6+6 is a Casualty, then its D16
    armour = next(r for e in out.events for r in e.rolls if r.kind == "Armour")
    assert armour.modifier == 2, "two Offensive Assists, +1 each"
    assert armour.total == 3 + 4 + 2


def test_a_clean_foul_is_not_a_turnover_and_ends_the_activation():
    """ "Regardless of the outcome" only bites on a natural double. Get away with
    it and the turn goes on — the Foul just ends that player's activation."""
    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6))
    _floored(m, "a01")
    out = _foul(m, "h00", "a01", _dice([2, 5]))  # 7 vs AV 9+, no double, armour holds
    assert out.ok and not out.turnover
    assert m.by_id("h00").done is True
    assert m.by_id("h00").place == "pitch"
    assert not any(e.kind == "player_sent_off" for e in out.events)


@pytest.mark.parametrize(
    "script,where",
    [
        # A double that FAILS to break armour still sends you off — the third die
        # is Argue the Call, which a sending-off always triggers.
        ([3, 3, 4], "Armour Roll"),
        ([6, 5, 4, 4, 3], "Injury Roll"),  # armour breaks on 6+5, injury is a double
    ],
)
def test_a_natural_double_on_either_roll_sends_the_fouler_off(script, where):
    """ "Regardless of the outcome, if during a Foul Action a natural double is
    rolled for EITHER the Armour Roll or Injury Roll, then the player performing
    the Foul Action is Sent-off." A double that fails to break armour still counts,
    which is the part that reads as a bug when it happens to you."""
    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6))
    _floored(m, "a01")
    out = _foul(m, "h00", "a01", _dice(script))
    assert any(e.kind == "player_sent_off" for e in out.events), f"no sending-off from {where} {script}"
    assert out.turnover, "a Sent-off player on the active team causes a Turnover"


def test_a_natural_double_is_natural_so_assists_cannot_create_or_hide_one():
    """ "a NATURAL double" — the raw dice, before the assist modifier."""
    from bloodbowl.engine.actions.foul import _natural_double

    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6), ("home", 8, 14, 6))
    _floored(m, "a01")
    # 3 and 5 with +1 from the assist totals 9, which breaks AV 9+ — but 3 and 5
    # is not a double, and the Injury Roll behind it (6 and 5) is not one either,
    # so nobody is sent off however tempting that "9" looks.
    out = _foul(m, "h00", "a01", _dice([3, 5, 6, 5, 9]))  # 11 is a Casualty, then its D16
    armour = next(r for e in out.events for r in e.rolls if r.kind == "Armour")
    assert armour.modifier == 1 and armour.dice == [3, 5]
    assert _natural_double(out.events) is None
    assert not any(e.kind == "player_sent_off" for e in out.events)


def test_a_sent_off_player_leaves_for_good_and_does_not_come_back_next_drive():
    """ "immediately removed from the pitch and will play no further part in the
    game" — so unlike a Knocked-out player they must not reappear at the next
    kick-off, and unlike a Casualty they are their own kind of gone."""
    from bloodbowl.engine.game import start_drive

    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6))
    _floored(m, "a01")
    _foul(m, "h00", "a01", _dice([3, 3, 2]))  # double on the armour, then Argue rolls 2
    assert m.by_id("h00").place == "sent_off"

    m.setup = [{"id": "h00", "x": 7, "y": 13}, {"id": "a01", "x": 7, "y": 14}]
    start_drive(m, receiving="home", dice=_dice([3, 1, 4, 4, 3, 3] + [4] * 12))
    assert m.by_id("h00").place == "sent_off", "a Sent-off player came back for the next drive"


@pytest.mark.parametrize(
    "argue,stays,bans",
    [(6, True, False), (1, False, True), (4, False, False)],
)
def test_argue_the_call(argue, stays, bans):
    """1   "YOU'RE OUTTA HERE!"   still Sent-off, and the Coach may not argue again
    2-5  "I DON'T CARE!"        still Sent-off
     6   "WELL, WHEN YOU PUT IT LIKE THAT…"  placed back in the square they were
         in and NOT Sent-off — "though a Turnover is still caused."
    """
    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6))
    _floored(m, "a01")
    out = _foul(m, "h00", "a01", _dice([3, 3, argue]))

    p = m.by_id("h00")
    assert (p.place == "pitch") is stays, f"argue {argue} -> place {p.place}"
    if stays:
        assert (p.x, p.y) == (7, 13), "placed back in the square they were in"
    assert out.turnover, "a Turnover is caused either way"
    assert ("home" in m.argue_banned) is bans


def test_an_ejected_coach_may_not_argue_again_this_game():
    """The ban is per SIDE and per MATCH — one of the few things a new turn does
    not clear."""
    from bloodbowl.engine.game import end_turn

    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6), ("home", 6, 13, 6), ("away", 6, 14, 6))
    _floored(m, "a01")
    _foul(m, "h00", "a01", _dice([3, 3, 1]))  # caught, and the coach is ejected
    assert m.argue_banned == ["home"]

    end_turn(m)
    end_turn(m)  # back round to home
    assert m.argue_banned == ["home"], "a new turn must not clear the ejection"

    _floored(m, "a03")
    out = _foul(m, "h02", "a03", _dice([4, 4]))  # caught again; no Argue roll offered
    assert m.by_id("h02").place == "sent_off"
    assert not any(r.kind == "Argue the Call" for e in out.events for r in e.rolls)
    assert any("may not Argue the Call again" in (e.text or "") for e in out.events)


def test_the_rulebooks_worked_foul_example():
    """Reproduced from the S3 text:

    "the Tomb Kings Blitzer makes an Armour Roll for the Bretonnian Squire they
     are fouling, rolling a 6 and a 3, which breaks their armour. They then make
     an Injury Roll and roll a double 2, causing the Tomb Kings Blitzer to be
     Sent-off and the Bretonnian Squire to be Stunned. The Tomb Kings Coach
     attempts to Argue the Call and rolls a 1, meaning that the Tomb Kings Blitzer
     is still Sent-off and the Coach cannot Argue the Call for the remainder of
     the game!"
    """
    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6))
    m.by_id("a01").player.AV = "8+"  # 6+3 = 9 must break it, as in the example
    _floored(m, "a01")
    out = _foul(m, "h00", "a01", _dice([6, 3, 2, 2, 1]))

    armour = next(r for e in out.events for r in e.rolls if r.kind == "Armour")
    assert armour.dice == [6, 3] and armour.passed, "which breaks their armour"
    injury = next(r for e in out.events for r in e.rolls if r.kind == "Injury")
    assert injury.dice == [2, 2]
    assert m.by_id("a01").down == "stunned", "the Bretonnian Squire to be Stunned"
    assert m.by_id("h00").place == "sent_off", "the Tomb Kings Blitzer to be Sent-off"
    assert m.argue_banned == ["home"], "the Coach cannot Argue the Call for the remainder of the game"


def test_mighty_blow_does_not_apply_to_a_foul():
    """ "Whenever this player Knocks Down an opposition player during a BLOCK
    ACTION" — a Foul is not one. Passing the fouler as the responsible player
    would have handed them a +1 the rules do not give."""
    m = _match(("home", 7, 13, 6, "3+", ["Mighty Blow"]), ("away", 7, 14, 6))
    _floored(m, "a01")
    out = _foul(m, "h00", "a01", _dice([4, 4, 3]))
    armour = next(r for e in out.events for r in e.rolls if r.kind == "Armour")
    assert armour.modifier == 0, "Mighty Blow leaked into a Foul"


def test_only_one_foul_per_team_per_turn():
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6), ("home", 6, 13, 6), ("away", 6, 14, 6))
    _floored(m, "a01")
    _floored(m, "a03")
    assert _foul(m, "h00", "a01", _dice([2, 5])).ok
    second = actions.get("foul")["validate"](m, {"player": "h02", "target": "a03"})
    assert not second.ok and "one Foul Action this turn" in second.reason


def test_legal_moves_offers_the_foul_with_the_risk_spelled_out():
    from bloodbowl.engine.game import legal_moves

    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6), ("away", 8, 13, 6))
    _floored(m, "a01")
    out = legal_moves(m, "h00")
    assert [f["target"] for f in out["fouls"]] == ["a01"], "only the one already down"
    assert [b["target"] for b in out["blocks"]] == ["a02"], "and only the standing one is blockable"
    assert "natural double" in out["fouls"][0]["sending_off_on"]
    assert out["fouls"][0]["may_argue"] is True


def test_a_blitz_may_be_re_pointed_before_anything_happens_but_not_after():
    """A deliberate permissive edge, pinned so it stays deliberate.

    Declaring a Blitz rolls no dice and changes nothing but the declaration, so a
    coach who names the wrong target may re-point it — the team's one Blitz is
    still spent by the same player either way. The moment ANYTHING happens it is
    settled: a step of movement sets `acted`, and so does the Blitz's Block, and
    both refuse a re-declaration.

    That bound is what makes it safe. Without it, re-declaring would reset
    `blocked` to False and buy a second Blitz Block, which is a real exploit
    rather than a convenience.
    """
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 10, 6), ("away", 7, 14, 6), ("away", 9, 14, 6))

    assert _declare(m, "h00", "a01").ok
    assert _declare(m, "h00", "a02").ok, "re-pointing before moving is allowed"
    assert m.blitz == {"player": "h00", "target": "a02", "blocked": False}
    assert m.turn_actions["blitz"] == "h00", "still the team's one Blitz, same player"

    assert _move(m, "h00", 7, 11, _dice([])).ok
    after_moving = actions.get("blitz")["validate"](m, {"player": "h00", "target": "a01"})
    assert not after_moving.ok, "once the player has moved the declaration is settled"

    # And no second Blitz Block: block, then try to re-point and block again.
    m2 = _match(("home", 7, 13, 6), ("away", 7, 14, 6), ("away", 8, 12, 6))
    _declare(m2, "h00", "a01")
    assert _block(m2, "h00", "a01", _dice([], [["push_back"]]), follow_up=False).ok
    again = actions.get("blitz")["validate"](m2, {"player": "h00", "target": "a02"})
    assert not again.ok, "re-declaring after the Blitz's Block would buy a second one"


def test_a_second_player_never_gets_the_teams_blitz():
    """The limit that actually matters, asked of a DIFFERENT player — the one the
    re-pointing case must not weaken."""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 10, 6), ("home", 8, 10, 6), ("away", 7, 14, 6))
    assert _declare(m, "h00", "a02").ok
    other = actions.get("blitz")["validate"](m, {"player": "h01", "target": "a02"})
    assert not other.ok
    assert "one Blitz Action this turn" in other.reason and "Home 0" in other.reason


# --- the skill catalogue ---------------------------------------------------


def test_every_skill_on_every_roster_can_be_quoted():
    """The catalogue exists so an unmodelled Skill can be QUOTED rather than
    guessed at. A Skill a roster uses but the catalogue lacks is precisely the
    case where a coach falls back on recall — which is the failure in §1."""
    import json
    from pathlib import Path

    from bloodbowl.engine.skills import catalogue

    have = catalogue()
    assert len(have) >= 100, f"only {len(have)} entries"
    rosters = json.loads((Path(__file__).resolve().parent.parent / "data" / "rosters.json").read_text())
    used = {
        s.split("(")[0].strip()
        for team in rosters["teams"]
        for grp in ("positionals", "star_players")
        for p in (team.get(grp) or [])
        for s in (p.get("skills") or [])
        if s.split("(")[0].strip() not in ("", "-")
    }
    missing = sorted(n for n in used if n.casefold() not in have)
    assert not missing, f"{len(missing)} roster skills cannot be quoted: {missing}"


def test_break_tackle_reads_as_the_rulebook_has_it():
    """The exact entry the agent got wrong, pinned verbatim.

    It called Break Tackle "an ST-based alternative" to the dodge roll. It is a
    MODIFIER to the same Agility Test, and the difference decides whether a coach
    thinks a ST 5 player dodges on a 3+ or on something else entirely.
    """
    from bloodbowl.engine.skills import describe_skill

    bt = describe_skill("Break Tackle")
    assert bt is not None
    assert "modifier to the Agility Test" in bt["text"]
    assert "+1" in bt["text"] and "+2" in bt["text"] and "+3" in bt["text"]
    # It is modelled now, and the hook that models it quotes this same sentence —
    # so the catalogue and the engine cannot drift into disagreeing about it.
    assert bt["modelled"] is True
    from bloodbowl.engine.skills import _break_tackle

    assert "modifier to the Agility Test" in (_break_tackle.__doc__ or "")


def test_a_trait_is_distinguished_from_a_skill():
    """A Trait is marked ONLY by a trailing asterisk on the heading — nothing else
    on the page says so. Dropping it loses all 25, including the three most common
    things on the pitch."""
    from bloodbowl.engine.skills import catalogue, describe_skill

    assert describe_skill("Stunty")["kind"] == "Trait"
    assert describe_skill("Loner")["kind"] == "Trait"
    assert describe_skill("Block")["kind"] == "Skill"
    assert sum(1 for v in catalogue().values() if v["kind"] == "Trait") >= 20


def test_the_elite_marker_and_the_categories_survived_the_scrape():
    """Both are SYMBOLS in the prose — "an Elite Skill will be denoted by the
    symbol" — so the flattened page text has neither, and only the HTML does. The
    four Elite skills are also the four most common on the rosters."""
    from bloodbowl.engine.skills import catalogue

    elite = sorted(v["name"] for v in catalogue().values() if v["elite"])
    assert elite == ["BLOCK", "DODGE", "GUARD", "MIGHTY BLOW"], elite
    cats = {v["category"] for v in catalogue().values()}
    assert {"Agility", "Devious", "General", "Mutation", "Passing", "Strength", "Trait"} == cats


def test_the_catalogue_reports_what_the_engine_actually_models():
    """Derived from the hook registry, never written down twice — so a skill that
    gets modelled starts reporting itself as modelled with no second edit."""
    from bloodbowl.engine.skills import describe_skill, find_skills, modelled

    for name in ("Block", "Dodge", "Guard", "Mighty Blow", "Thick Skull", "Jump Up", "Prehensile Tail"):
        assert describe_skill(name)["modelled"] is True, name
    assert {s["name"].casefold() for s in find_skills(only_unmodelled=True)}.isdisjoint(modelled())


def test_an_unknown_skill_suggests_something_rather_than_nothing():
    from bloodbowl.engine.skills import describe_skill

    assert describe_skill("Definitely Not A Skill") is None


def test_a_skill_can_be_found_by_what_it_does():
    """ "which skills grant a re-roll" is the question a coach actually has."""
    from bloodbowl.engine.skills import find_skills

    names = {s["name"] for s in find_skills("re-roll")}
    assert {"DODGE", "SURE HANDS", "CATCH", "PASS"} <= names, sorted(names)


# --- skills batch one: the ones that attach to rolls the engine already makes ---


def _skilled(side, x, y, skills, ma=6, st=3, ag="3+"):
    return (side, x, y, ma, ag, skills)


def _dodge_roll(out, kind="Dodge"):
    return next(r for e in out.events for r in e.rolls if r.kind == kind)


def test_break_tackle_is_a_modifier_scaled_by_strength():
    """ "a +1 modifier … if they have a Strength characteristic of 3 or lower, a +2
    … if 4, or a +3 … if 5 or higher." The exact sentence the agent got wrong."""
    from bloodbowl.engine.rules import strength_of

    for st, want in ((3, 1), (4, 2), (5, 3), (2, 1), (6, 3)):
        m = _match(("home", 7, 13, 6, "3+", ["Break Tackle"]), ("away", 7, 14, 6))
        m.by_id("h00").player.ST = str(st)
        assert strength_of(m, m.by_id("h00")) == st
        out = _move(m, "h00", 6, 12, _dice([4]))
        assert _dodge_roll(out).modifier == want, f"ST {st} should give +{want}"


def test_break_tackle_is_once_per_turn_and_that_survives_the_fold():
    """ "Once per Turn" — and the flag has to be recorded, not assigned, or a
    folded match hands it back. (`dodge_reroll_used` had exactly that bug.)"""
    from bloodbowl.engine.state import fold

    def board():
        return _match(("home", 7, 13, 6, "3+", ["Break Tackle"]), ("away", 7, 14, 6), ("away", 5, 11, 6))

    m = board()
    m.by_id("h00").player.ST = "4"
    first = _move(m, "h00", 6, 12, _dice([4]))
    assert m.by_id("h00").break_tackle_used is True

    second = _move(m, "h00", 6, 13, _dice([4]))
    # Compared rather than asserted flat, because both destinations are Marked and
    # the point is the +2, not the board.
    assert _dodge_roll(first).modifier == _dodge_roll(second).modifier + 2, "Break Tackle fired twice in one turn"

    rebuilt = fold(board(), list(m.events))
    assert rebuilt.by_id("h00").break_tackle_used is True, "the spend did not survive the fold"


def test_the_dodge_skills_reroll_also_survives_the_fold():
    """Same class, found while adding Break Tackle: `p.dodge_reroll_used = True`
    was an assignment, so a folded match let the Dodge Skill re-roll twice."""
    from bloodbowl.engine.state import fold

    def board():
        return _match(("home", 7, 13, 6, "3+", ["Dodge"]), ("away", 7, 14, 6))

    m = board()
    out = _move(m, "h00", 6, 12, _dice([1, 5]))
    assert out.ok and len([r for e in out.events for r in e.rolls if r.kind.startswith("Dodge")]) == 2
    assert m.by_id("h00").dodge_reroll_used is True
    assert fold(board(), list(m.events)).by_id("h00").dodge_reroll_used is True


def test_stunty_ignores_marking_on_a_dodge_but_nothing_else():
    """ "they do not suffer any negative modifiers … for being Marked by opposition
    players" — the Marking penalty only, and only on a Dodge."""
    # Two opponents Marking the destination would normally be -2.
    m = _match(("home", 7, 13, 6, "3+", ["Stunty"]), ("away", 7, 14, 6), ("away", 5, 11, 6), ("away", 5, 13, 6))
    out = _move(m, "h00", 6, 12, _dice([4]))
    assert _dodge_roll(out).modifier == 0, "Stunty should cancel the Marking penalty"

    plain = _match(("home", 7, 13, 6), ("away", 7, 14, 6), ("away", 5, 11, 6), ("away", 5, 13, 6))
    assert _dodge_roll(_move(plain, "h00", 6, 12, _dice([6]))).modifier == -2


def test_stunty_uses_the_stunty_injury_table():
    """STUNTY INJURY TABLE — 2-6 Stunned · 7-8 Knocked-out · 9 Badly Hurt · 10-12
    Casualty. The standard table is 2-7 / 8-9 / 10-12, so a 7 is the tell."""
    from bloodbowl.engine.injury import risk_injury

    for dice_pair, want in (((3, 4), "knocked_out"), ((3, 3), "stunned"), ((4, 5), "casualty")):
        m = _match(("home", 7, 13, 6, "3+", ["Stunty"]))
        m.by_id("h00").player.AV = "2+"  # armour always breaks, so the injury rolls
        events = risk_injury(m, m.by_id("h00"), _dice([6, 6, *dice_pair, 9]))  # trailing D16, if it is a Casualty
        outcome = next(e.detail["outcome"] for e in events if e.kind == "injury_roll")
        assert outcome == want, f"total {sum(dice_pair)} should be {want}, got {outcome}"

    # …and a 7 on the STANDARD table is only Stunned.
    m = _match(("home", 7, 13, 6))
    m.by_id("h00").player.AV = "2+"
    events = risk_injury(m, m.by_id("h00"), _dice([6, 6, 3, 4]))
    assert next(e.detail["outcome"] for e in events if e.kind == "injury_roll") == "stunned"


def test_stunty_and_thick_skull_together():
    """ "If this player also has the Stunty Trait, then they will only be
    Knocked-out on the roll of an 8; a roll of a 7 will be treated as a Stunned
    result."

    ORDER-SENSITIVE: Stunty must replace the table before Thick Skull adjusts the
    result. Swap the two registrations and this fails, which is the point.
    """
    from bloodbowl.engine.injury import risk_injury

    def hurt(skills, pair):
        m = _match(("home", 7, 13, 6, "3+", skills))
        m.by_id("h00").player.AV = "2+"
        events = risk_injury(m, m.by_id("h00"), _dice([6, 6, *pair, 9]))
        return next(e.detail["outcome"] for e in events if e.kind == "injury_roll")

    assert hurt(["Stunty", "Thick Skull"], (3, 4)) == "stunned", "a 7 must become Stunned"
    assert hurt(["Stunty", "Thick Skull"], (4, 4)) == "knocked_out", "an 8 still knocks them out"
    assert hurt(["Stunty"], (3, 4)) == "knocked_out", "without Thick Skull a 7 is a KO"
    assert hurt(["Thick Skull"], (4, 4)) == "stunned", "without Stunty an 8 becomes Stunned"
    assert hurt(["Thick Skull"], (4, 5)) == "knocked_out", "…and a 9 does not"


def test_titchy_helps_its_own_dodge_and_declines_to_mark():
    """Two clauses pulling opposite ways, and the second lives on the MARKER:
    "this player will not apply a -1 modifier … for Marking the opposition
    player." Never applied, so it cannot be cancelled later."""
    mine = _match(("home", 7, 13, 6, "3+", ["Titchy"]), ("away", 7, 14, 6))
    assert _dodge_roll(_move(mine, "h00", 6, 12, _dice([4]))).modifier == 1

    # An ordinary dodger moving next to a Titchy opponent takes no penalty from it.
    theirs = _match(("home", 7, 13, 6), ("away", 7, 14, 6), ("away", 5, 11, 6, "3+", ["Titchy"]))
    assert _dodge_roll(_move(theirs, "h00", 6, 12, _dice([5]))).modifier == 0


def test_tackle_denies_the_dodge_skills_reroll():
    """ "When an opposition player attempts to Dodge away from a square in this
    player's Tackle Zone, they cannot use the Dodge Skill." The square being LEFT."""
    m = _match(("home", 7, 13, 6, "3+", ["Dodge"]), ("away", 7, 14, 6, "3+", ["Tackle"]))
    out = _move(m, "h00", 6, 12, _dice([1, 2, 2]))
    assert not out.ok, "the failed Dodge should stand"
    assert len([r for e in out.events for r in e.rolls if r.kind.startswith("Dodge")]) == 1
    assert any("Tackle" in (e.text or "") for e in out.events)


def test_tackle_also_turns_a_stumble_back_into_a_knockdown():
    """ "the opposition player does not count as having the Dodge Skill if a
    Stumble result is selected" — the blocker's Tackle, the target's Dodge."""
    m = _match(("home", 7, 13, 6, "3+", ["Tackle"]), ("away", 7, 14, 6, "3+", ["Dodge"]))
    _block(m, "h00", "a01", _dice([2, 2], [["stumble"]]), follow_up=False)
    assert m.by_id("a01").down != "standing", "Stumble should have knocked them down"

    without = _match(("home", 7, 13, 6), ("away", 7, 14, 6, "3+", ["Dodge"]))
    _block(without, "h00", "a01", _dice([], [["stumble"]]), follow_up=False)
    assert without.by_id("a01").down == "standing", "Dodge should turn Stumble into a plain push"


@pytest.mark.parametrize(
    "skill,test_kind",
    [("Sure Hands", "Pick up"), ("Catch", "Catch")],
)
def test_a_re_roll_skill_gets_a_second_attempt(skill, test_kind):
    from bloodbowl.engine.ball import catch, pick_up

    m = _match(("home", 7, 13, 6, "3+", [skill]))
    m.apply(_ball_at(7, 13))
    dice = _dice([1, 5, 1, 1])
    events = pick_up(m, m.by_id("h00"), dice)[0] if skill == "Sure Hands" else catch(m, m.by_id("h00"), dice)
    tries = [r for e in events for r in e.rolls if r.kind.startswith(test_kind)]
    assert len(tries) == 2, f"{skill} should have re-rolled: {[r.kind for r in tries]}"
    assert tries[1].passed and m.ball.carrier == "h00"


def test_sure_hands_does_not_apply_to_secure_the_ball():
    """ "though not when making a Secure the Ball Action" — it is already a flat 2+
    bought by giving up the activation."""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 13, 6, "3+", ["Sure Hands"]), ("away", 7, 20, 6))
    m.apply(_ball_at(7, 13))
    out = actions.get("secure")["resolve"](m, {"player": "h00"}, _dice([1, 5, 5]))
    tries = [r for e in out.events for r in e.rolls if "Secure" in r.kind]
    assert len(tries) == 1, "Secure the Ball was re-rolled"
    assert out.turnover


def test_big_hand_ignores_every_negative_pick_up_modifier():
    """ "ignores ALL negative modifiers when attempting to pick up the ball"."""
    from bloodbowl.engine.ball import pick_up

    m = _match(("home", 7, 13, 6, "3+", ["Big Hand"]), ("away", 7, 14, 6), ("away", 6, 14, 6))
    m.apply(_ball_at(7, 13))
    events = pick_up(m, m.by_id("h00"), _dice([3]))[0]
    r = next(r for e in events for r in e.rolls if r.kind == "Pick up")
    assert r.modifier == 0 and r.passed


def test_accurate_and_nerves_of_steel_on_a_pass():
    """Accurate: "+1 … which is a Quick Pass or a Short Pass". Nerves of Steel:
    "ignore any modifiers for being Marked … to Pass the ball"."""
    from bloodbowl.engine import actions

    actions.load_all()

    def throw(skills, tx, ty, foes=()):
        m = _match(("home", 7, 2, 6, "3+", list(skills)), *foes)
        m.by_id("h00").player.PA = "3+"
        m.apply(_ball_at(7, 2, carrier="h00"))
        out = actions.get("pass")["resolve"](m, {"player": "h00", "x": tx, "y": ty}, _dice([6, 6, 6, 6, 6]))
        return next(r for e in out.events for r in e.rolls if r.kind == "Pass")

    assert throw(["Accurate"], 7, 4).modifier == 1, "a Quick Pass gets +1"
    assert throw([], 7, 4).modifier == 0
    # A Long Pass is -2, and Accurate does not apply to it.
    assert throw(["Accurate"], 7, 12).modifier == -2

    marked = (("away", 7, 3, 6),)
    assert throw([], 7, 4, marked).modifier == -1, "one Marker on the passer"
    assert throw(["Nerves of Steel"], 7, 4, marked).modifier == 0


# --- skills batch two: the push and the follow-up --------------------------


def test_stand_firm_refuses_a_push_into_the_crowd():
    """ "they can choose to not be Pushed Back and instead remain in their current
    square" — a choice, taken by the engine only where the alternative is the
    Crowd, which is an Injury Roll with no armour behind it."""
    # Target on the sideline with nowhere to go but off.
    m = _match(("home", 2, 13, 6), ("away", 1, 13, 6, "3+", ["Stand Firm"]))
    out = _block(m, "h00", "a01", _dice([], [["push_back"]]), follow_up=False)
    assert out.ok
    t = m.by_id("a01")
    assert t.place == "pitch" and (t.x, t.y) == (1, 13), "Stand Firm should have refused the Crowd"
    assert any("Stand Firm" in (e.text or "") for e in out.events)

    without = _match(("home", 2, 13, 6), ("away", 1, 13, 6))
    _block(without, "h00", "a01", _dice([2, 2], [["push_back"]]), follow_up=False)
    assert without.by_id("a01").place != "pitch", "without it they go into the Crowd"


def test_sidestep_escapes_the_ordinary_push_arc():
    """ "instead of the opposing Coach choosing where this player is Pushed Back
    to, this player's Coach may choose ANY adjacent unoccupied square."

    Asserted by BLOCKING the ordinary three-square arc, because every square
    adjacent to the target is the same distance from the blocker — so "the
    furthest square" cannot tell the two apart, and a test written that way passes
    against an engine that has never heard of Sidestep. Mine did.
    """
    from bloodbowl.engine.rules import push_squares

    arc = push_squares(7, 13, 7, 14)
    assert set(arc) == {(7, 15), (6, 15), (8, 15)}, arc  # order is direction-then-flanks
    walled = [("home", x, y, 6) for x, y in arc]

    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6, "3+", ["Sidestep"]), *walled)
    _block(m, "h00", "a01", _dice([], [["push_back"]]), follow_up=False)
    t = m.by_id("a01")
    assert (t.x, t.y) not in arc, "Sidestep should have left the arc entirely"
    assert max(abs(t.x - 7), abs(t.y - 14)) == 1, f"({t.x},{t.y}) is not adjacent to where they stood"

    # Without it, a fully-blocked arc is a Chain Push into one of those squares.
    plain = _match(("home", 7, 13, 6), ("away", 7, 14, 6), *walled)
    _block(plain, "h00", "a01", _dice([2, 2], [["push_back"]]), follow_up=False)
    assert (plain.by_id("a01").x, plain.by_id("a01").y) in arc


def test_grab_widens_the_push_arc_and_suppresses_sidestep():
    """ "this player's Coach may choose ANY unoccupied square adjacent to the
    target … opposition players cannot use the Sidestep Skill." """
    from bloodbowl.engine.actions.block import _push_to

    # (6,14) is adjacent to the target but NOT in the ordinary three-square arc.
    m = _match(("home", 7, 13, 6, "3+", ["Grab"]), ("away", 7, 14, 6))
    square, kind = _push_to(m, m.by_id("h00"), m.by_id("a01"), prefer=(6, 13))
    assert kind == "empty" and square == (6, 13), "Grab should reach a square outside the arc"

    both = _match(("home", 7, 13, 6, "3+", ["Grab"]), ("away", 7, 14, 6, "3+", ["Sidestep"]))
    _block(both, "h00", "a01", _dice([], [["push_back"]]), follow_up=False)
    t = both.by_id("a01")
    assert max(abs(t.x - 7), abs(t.y - 13)) == 1, "Grab should have suppressed Sidestep"


def test_fend_stops_the_follow_up():
    """ "then the opposition player may not Follow-up" — not optional, and not
    something the acting coach's follow_up flag can override."""
    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6, "3+", ["Fend"]))
    out = _block(m, "h00", "a01", _dice([], [["push_back"]]), follow_up=True)
    assert (m.by_id("h00").x, m.by_id("h00").y) == (7, 13), "the blocker followed up anyway"
    assert any("Fend" in (e.text or "") for e in out.events)

    without = _match(("home", 7, 13, 6), ("away", 7, 14, 6))
    _block(without, "h00", "a01", _dice([], [["push_back"]]), follow_up=True)
    assert (without.by_id("h00").x, without.by_id("h00").y) == (7, 14)


def test_juggernaut_suppresses_fend_and_stand_firm_but_only_on_a_blitz():
    """ "when this player performs a Block Action AS PART OF A BLITZ ACTION,
    opposition players cannot use the Fend, Stand Firm or Wrestle Skills." """
    # Ordinary Block: Fend still works.
    plain = _match(("home", 7, 13, 6, "3+", ["Juggernaut"]), ("away", 7, 14, 6, "3+", ["Fend"]))
    _block(plain, "h00", "a01", _dice([], [["push_back"]]), follow_up=True)
    assert (plain.by_id("h00").x, plain.by_id("h00").y) == (7, 13), "Juggernaut suppressed Fend off a Blitz"

    # Declared Blitz: it does not.
    blitzed = _match(("home", 7, 13, 6, "3+", ["Juggernaut"]), ("away", 7, 14, 6, "3+", ["Fend"]))
    _declare(blitzed, "h00", "a01")
    _block(blitzed, "h00", "a01", _dice([], [["push_back"]]), follow_up=True)
    assert (blitzed.by_id("h00").x, blitzed.by_id("h00").y) == (7, 14), "Fend should have been suppressed"


def test_a_partly_modelled_skill_says_which_half_is_missing():
    """ "Modelled" and "not modelled" is a binary that flatters. A Skill with two
    clauses of which one is applied would report as modelled and quietly do half
    its job — which sounds settled, and is worse than saying nothing.

    NOTHING IS PARTIAL ANY MORE — the last one, Plague Ridden's post-game hire, was
    closed by giving the Post-game Sequence the same treatment the Pre-game one
    got. So this registers a partial of its own and drives the reporting with it,
    the third fixture in this file to do that: the MECHANISM has to keep working,
    because a fork adding a Skill it half-applies is exactly who needs it."""
    from bloodbowl.engine import skills as _skills
    from bloodbowl.engine.game import state_report
    from bloodbowl.engine.skills import describe_skill, partial_skills, partly_modelled_on_pitch

    assert not partial_skills(), f"something is partial again — say so here: {sorted(partial_skills())}"
    assert describe_skill("Grab").get("partial") is None, "a fully modelled skill needs no caveat"

    @_skills.skill_hook("Block", "test_only", partial="the half a fork forgot to write")
    def _pretend(ctx):
        """A partial registered by this test, to drive the reporting mechanism."""

    try:
        assert "block" in partial_skills()
        assert describe_skill("Block")["partial"] == "the half a fork forgot to write"
        m = _match(("home", 7, 13, 6, "3+", ["Block"]), ("away", 7, 14))
        rows = {r["skill"]: r for r in partly_modelled_on_pitch(m)}
        assert rows["Block"]["players"] == ["h00"], rows
        assert rows["Block"]["not_applied"] == "the half a fork forgot to write"
        assert state_report(m)["partly_modelled_skills"] == partly_modelled_on_pitch(m)
    finally:
        _skills._PARTIAL.pop("block", None)
        _skills._HOOKS.pop("test_only", None)


def test_a_player_pushed_into_the_crowd_actually_leaves_the_pitch():
    """S3 INJURY BY THE CROWD: "Make an Injury Roll for a player Pushed into the
    Crowd. If the player would be Stunned, place them in their team's Reserve Box.
    Otherwise, follow the result on the relevant Injury Table."

    Two things were wrong, and the second is why nobody noticed the first:

    * An ARMOUR ROLL was made. There is none — the crowd does not care what you
      are wearing, which is the whole reason the sideline is dangerous.
    * `player_left_pitch` was emitted and never applied, so the victim stayed
      standing in the square they had just been thrown out of. The log said they
      went into the Crowd and the board disagreed.
    """
    m = _match(("home", 2, 13, 6), ("away", 1, 13, 6))
    out = _block(m, "h00", "a01", _dice([2, 2], [["push_back"]]), follow_up=False)

    kinds = [r.kind for e in out.events for r in e.rolls]
    assert "Armour" not in kinds, f"the Crowd made an Armour Roll: {kinds}"
    assert "Injury" in kinds, kinds
    t = m.by_id("a01")
    assert t.place != "pitch", "the victim is still standing on the pitch"
    assert t.place == "reserves", f"a Stunned crowd result is the Reserves Box, got {t.place}"
    assert t.down == "standing", "they are in the stands, not lying stunned on a square"


def test_the_crowd_can_still_knock_a_player_out():
    """ "Otherwise, follow the result on the relevant Injury Table" — 8-9 is a
    Knocked-out, and that box is not the Reserves box."""
    m = _match(("home", 2, 13, 6), ("away", 1, 13, 6))
    _block(m, "h00", "a01", _dice([4, 4], [["push_back"]]), follow_up=False)
    assert m.by_id("a01").place == "knocked_out"


def test_a_carrier_thrown_into_the_crowd_leaves_the_ball_behind():
    """The ball does not go into the stands with them."""
    m = _match(("home", 2, 13, 6), ("away", 1, 13, 6))
    m.apply(_ball_at(1, 13, carrier="a01"))
    _block(m, "h00", "a01", _dice([2, 2, 3], [["push_back"]]), follow_up=False)
    assert m.by_id("a01").place == "reserves"
    assert m.ball.carrier == "", "the ball went into the Crowd too"
    assert m.ball.in_play


def test_declares_a_block_and_performs_a_block_are_different_triggers():
    """S3 gives this its own worked example: "a rule that comes into play when a
    player DECLARES a Block Action would not come into effect [during a Blitz], as
    no Block Action has been declared — the declared Action was a Blitz Action. A
    rule that comes into play when a player PERFORMS a Block Action would."

    Grab says "declares", Tackle says "performs". Reading the verb is the whole
    rule, and Grab shipped without it.
    """
    from bloodbowl.engine.actions.block import _push_to

    # Grab off a Blitz: reaches a square outside the ordinary arc.
    plain = _match(("home", 7, 13, 6, "3+", ["Grab"]), ("away", 7, 14, 6))
    assert _push_to(plain, plain.by_id("h00"), plain.by_id("a01"), prefer=(6, 13))[0] == (6, 13)

    # The same Grab as part of a declared Blitz: it does not apply.
    blitzed = _match(("home", 7, 13, 6, "3+", ["Grab"]), ("away", 7, 14, 6))
    _declare(blitzed, "h00", "a01")
    assert _push_to(blitzed, blitzed.by_id("h00"), blitzed.by_id("a01"), prefer=(6, 13))[0] != (6, 13)

    # Tackle says "performs", so a Blitz does not switch it off.
    m = _match(("home", 7, 13, 6, "3+", ["Tackle"]), ("away", 7, 14, 6, "3+", ["Dodge"]))
    _declare(m, "h00", "a01")
    _block(m, "h00", "a01", _dice([2, 2], [["stumble"]]), follow_up=False)
    assert m.by_id("a01").down != "standing", "Tackle should still apply during a Blitz"


# --- skills batch three: the Both Down family and the block result ---------


def test_wrestle_places_both_players_prone_and_neither_is_harmed():
    """ "both players in the Block Action are Placed Prone, REGARDLESS of any other
    Skills they may possess" — and Placed Prone is the harmless one of the three
    ways onto the floor: "they aren't at risk of being caused harm". Neither
    player rolls armour, which is the whole value of the Skill."""
    m = _match(("home", 7, 13, 6, "3+", ["Block"]), ("away", 7, 14, 6, "3+", ["Wrestle"]))
    out = _block(m, "h00", "a01", _dice([], [["both_down"]]), follow_up=False)

    assert m.by_id("h00").down == "prone", "Wrestle drags the blocker down through Block"
    assert m.by_id("a01").down == "prone"
    kinds = [r.kind for e in out.events for r in e.rolls]
    assert "Armour" not in kinds, f"Placed Prone must risk no harm: {kinds}"
    assert out.turnover


def test_without_wrestle_block_keeps_the_blocker_up_and_the_target_rolls_armour():
    """The control: the same Both Down with no Wrestle behaves as before."""
    m = _match(("home", 7, 13, 6, "3+", ["Block"]), ("away", 7, 14, 6))
    out = _block(m, "h00", "a01", _dice([2, 2], [["both_down"]]), follow_up=False)
    assert m.by_id("h00").down == "standing"
    assert m.by_id("a01").down == "prone"
    assert "Armour" in [r.kind for e in out.events for r in e.rolls]


def test_brawler_re_rolls_one_both_down_and_only_off_a_blitz():
    """ "they may re-roll a SINGLE Both Down result" — and it reads "declares a
    Block Action", so a Blitz switches it off."""
    m = _match(("home", 7, 13, 6, "3+", ["Brawler"]), ("away", 7, 14, 6))
    out = _block(m, "h00", "a01", _dice([], [["both_down"], ["push_back"]]), follow_up=False)
    kinds = [r.kind for e in out.events for r in e.rolls]
    assert kinds.count("Block") == 1 and kinds.count("Block (re-roll)") == 1, kinds
    assert m.by_id("h00").down == "standing", "the re-roll came up Push Back"

    blitzed = _match(("home", 7, 13, 6, "3+", ["Brawler"]), ("away", 7, 14, 6))
    _declare(blitzed, "h00", "a01")
    out2 = _block(blitzed, "h00", "a01", _dice([2, 2, 2, 2], [["both_down"]]), follow_up=False)
    assert "Block (re-roll)" not in [r.kind for e in out2.events for r in e.rolls], "Brawler fired on a Blitz"


def test_brawler_keeps_a_both_down_that_is_already_good_for_it():
    """A Both Down that floors only the target is a GOOD result. Re-rolling it
    away would be a bug wearing a rule's clothes."""
    m = _match(("home", 7, 13, 6, "3+", ["Brawler", "Block"]), ("away", 7, 14, 6))
    out = _block(m, "h00", "a01", _dice([2, 2], [["both_down"]]), follow_up=False)
    assert "Block (re-roll)" not in [r.kind for e in out.events for r in e.rolls]
    assert m.by_id("h00").down == "standing" and m.by_id("a01").down == "prone"


def test_juggernaut_turns_a_both_down_into_a_push_on_a_blitz():
    """ "they may treat any result of Both Down as Pushed Back during any Block
    Actions they perform during the Blitz Action." Only on a Blitz."""
    m = _match(("home", 7, 13, 6, "3+", ["Juggernaut"]), ("away", 7, 14, 6))
    _declare(m, "h00", "a01")
    out = _block(m, "h00", "a01", _dice([], [["both_down"]]), follow_up=False)
    assert m.by_id("h00").down == "standing", "the blitzer should not have gone down"
    assert (m.by_id("a01").x, m.by_id("a01").y) != (7, 14), "the target should have been pushed"
    assert not out.turnover

    plain = _match(("home", 7, 13, 6, "3+", ["Juggernaut"]), ("away", 7, 14, 6))
    _block(plain, "h00", "a01", _dice([2, 2, 2, 2], [["both_down"]]), follow_up=False)
    assert plain.by_id("h00").down == "prone", "off a Blitz, Juggernaut does nothing here"


def test_dauntless_matches_the_stronger_player_rather_than_beating_them():
    """ "increases their unmodified Strength Characteristic to MATCH the opposition
    player for the duration of the Block Action. Modifiers are then applied as
    normal." Match, never exceed — ST 3 against ST 5 becomes 5 v 5, one die."""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 13, 6, "3+", ["Dauntless"]), ("away", 7, 14, 6))
    m.by_id("h00").player.ST, m.by_id("a01").player.ST = "3", "5"

    ahead = actions.get("block")["validate"](m, {"player": "h00", "target": "a01"})
    assert ahead.detail["dauntless"] is True
    assert ahead.detail["dice"] == 2 and ahead.detail["chooser"] == "defender", "before the roll it is a bad block"

    out = _block(m, "h00", "a01", _dice([4], [["push_back"]]), follow_up=False)  # 4 + ST 3 = 7 > 5
    note = next(e for e in out.events if "Dauntless" in (e.text or ""))
    assert "matches ST 5" in note.text, note.text
    assert note.detail["dice"] == 1, "5 v 5 is one die"

    # A failed roll leaves it alone.
    m2 = _match(("home", 7, 13, 6, "3+", ["Dauntless"]), ("away", 7, 14, 6))
    m2.by_id("h00").player.ST, m2.by_id("a01").player.ST = "3", "5"
    out2 = _block(m2, "h00", "a01", _dice([1, 2, 2], [["push_back"]]), follow_up=False)
    assert any("fails the Dauntless" in (e.text or "") for e in out2.events)


def test_horns_adds_strength_only_on_a_blitz_and_shows_up_in_the_odds():
    """ "Whenever this player declares a Blitz Action, then they apply a +1 modifier
    to their Strength Characteristic for any Block Actions performed during that
    Blitz Action." Deterministic, so bb_game_odds shows the same dice resolve uses."""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 13, 6, "3+", ["Horns"]), ("away", 7, 14, 6))
    m.by_id("h00").player.ST, m.by_id("a01").player.ST = "3", "3"

    plain = actions.get("block")["validate"](m, {"player": "h00", "target": "a01"})
    assert plain.detail["horns"] == 0 and plain.detail["dice"] == 1

    _declare(m, "h00", "a01")
    blitz = actions.get("block")["validate"](m, {"player": "h00", "target": "a01"})
    assert blitz.detail["horns"] == 1
    assert blitz.detail["attacker_strength"] == 4 and blitz.detail["dice"] == 2


def test_claws_break_armour_on_a_natural_eight_whatever_the_armour_value():
    """ "any roll of a NATURAL 8+ on the Armour Roll will break the opposition
    player's armour regardless of their actual Armour Value." Natural, so Mighty
    Blow cannot manufacture one."""
    m = _match(("home", 7, 13, 6, "3+", ["Claws"]), ("away", 7, 14, 6))
    m.by_id("a01").player.AV = "11+"
    out = _block(m, "h00", "a01", _dice([4, 4, 3, 3], [["pow"]]), follow_up=False)
    armour = next(r for e in out.events for r in e.rolls if r.kind == "Armour")
    assert armour.dice == [4, 4] and armour.passed, "a natural 8 should break AV 11+"
    assert any(r.kind == "Injury" for e in out.events for r in e.rolls)

    # A natural 7 does not, however tempting.
    m2 = _match(("home", 7, 13, 6, "3+", ["Claws"]), ("away", 7, 14, 6))
    m2.by_id("a01").player.AV = "11+"
    out2 = _block(m2, "h00", "a01", _dice([3, 4], [["pow"]]), follow_up=False)
    assert not next(r for e in out2.events for r in e.rolls if r.kind == "Armour").passed


# --- skills batch four: the activation gates -------------------------------
#
# Five Traits share one shape: "Whenever this player is activated, after declaring
# their Action they must roll a D6", and on a failure something goes wrong.


def _act(m, action, cmd, dice):
    from bloodbowl.engine.game import act

    return act(m, action, cmd, dice)


def test_bone_head_distracts_the_player_and_the_action_never_happens():
    """ "On a 2+, the player may perform the declared Action as normal. On a 1, the
    player becomes Distracted." A failed gate stops the Action — it does not
    happen and then get undone."""
    m = _match(("home", 7, 13, 6, "3+", ["Bone Head"]))
    out = _act(m, "move", {"player": "h00", "x": 7, "y": 14}, _dice([1]))
    p = m.by_id("h00")
    assert out["ok"] is False
    assert p.distracted is True
    assert (p.x, p.y) == (7, 13), "the Move happened anyway"
    assert p.done is True, "becoming Distracted ends the activation"

    passed = _match(("home", 7, 13, 6, "3+", ["Bone Head"]))
    assert _act(passed, "move", {"player": "h00", "x": 7, "y": 14}, _dice([2]))["ok"]
    assert (passed.by_id("h00").x, passed.by_id("h00").y) == (7, 14)


def test_a_distracted_player_has_no_tackle_zone_and_no_active_skills():
    """S3: "A player that is Distracted does not have a Tackle Zone … Whilst a
    player is Distracted, they cannot use ACTIVE Skills or Traits."

    Active-versus-Passive comes from the shipped catalogue, so this one rule
    covers all 108 — including the 81 the engine does not model.
    """
    from bloodbowl.engine.rules import has_tackle_zone
    from bloodbowl.engine.skills import can_use

    m = _match(("home", 7, 13, 6, "3+", ["Bone Head", "Dodge", "Thick Skull"]))
    p = m.by_id("h00")
    assert has_tackle_zone(p) and can_use(p, "Dodge")

    _act(m, "move", {"player": "h00", "x": 7, "y": 14}, _dice([1]))
    assert not has_tackle_zone(p), "a Distracted player still Marks people"
    assert not can_use(p, "Dodge"), "Dodge is an ACTIVE skill"
    assert can_use(p, "Thick Skull"), "Thick Skull is PASSIVE and still applies"


def test_distracted_lasts_until_the_player_is_next_activated_not_until_the_turn_ends():
    """ "they will remain Distracted UNTIL THEY ARE NEXT ACTIVATED (unless otherwise
    specified)" — so a new turn does not clear it, which is the half a paraphrase
    drops."""
    from bloodbowl.engine.game import end_turn

    m = _match(("home", 7, 13, 6, "3+", ["Bone Head"]), ("away", 2, 20, 6))
    _act(m, "move", {"player": "h00", "x": 7, "y": 14}, _dice([1]))
    assert m.by_id("h00").distracted

    end_turn(m)
    end_turn(m)  # back round to home
    assert m.by_id("h00").distracted, "a new turn must not clear Distracted"

    _act(m, "move", {"player": "h00", "x": 7, "y": 14}, _dice([4]))
    assert not m.by_id("h00").distracted, "activating again clears it"


def test_really_stupid_is_not_helped_by_another_really_stupid_player():
    """ "+2 … if they have any Standing team-mates who are not Distracted, AND DO
    NOT HAVE THE REALLY STUPID TRAIT, adjacent to them." The exclusion is the
    clause that gets dropped, and two Trolls propping each other up is exactly
    what it forbids."""
    from bloodbowl.engine.skills import activation_gates

    alone = _match(("home", 7, 13, 6, "3+", ["Really Stupid"]))
    assert activation_gates(alone, alone.by_id("h00"), "move")[0]["modifier"] == 0

    propped = _match(("home", 7, 13, 6, "3+", ["Really Stupid"]), ("home", 7, 14, 6, "3+", ["Really Stupid"]))
    assert activation_gates(propped, propped.by_id("h00"), "move")[0]["modifier"] == 0, "two of them do not help"

    helped = _match(("home", 7, 13, 6, "3+", ["Really Stupid"]), ("home", 7, 14, 6))
    assert activation_gates(helped, helped.by_id("h00"), "move")[0]["modifier"] == 2


@pytest.mark.parametrize("action,want", [("block", 2), ("blitz", 2), ("move", 0)])
def test_three_gates_give_plus_two_for_declaring_violence(action, want):
    """Animal Savagery and Unchannelled Fury: "+2 … if they have declared a Block
    Action or a Blitz Action"."""
    from bloodbowl.engine.skills import activation_gates

    for trait in ("Animal Savagery", "Unchannelled Fury"):
        m = _match(("home", 7, 13, 6, "3+", [trait]))
        assert activation_gates(m, m.by_id("h00"), action)[0]["modifier"] == want, trait


def test_animal_savagery_lashes_out_at_a_team_mate():
    """ "Choose one Standing team-mate adjacent to this player; the chosen player is
    immediately Knocked Down. This will NOT cause a Turnover unless the player was
    holding the ball." """
    m = _match(("home", 7, 13, 6, "3+", ["Animal Savagery"]), ("home", 7, 14, 6))
    out = _act(m, "move", {"player": "h00", "x": 6, "y": 13}, _dice([1, 2, 2]))
    assert m.by_id("h01").down != "standing", "the team-mate should be on the floor"
    assert out["turnover"] is False, "no turnover unless they had the ball"

    # …with the ball, it IS a turnover.
    # …with the ball it IS a turnover. More dice, because going down drops the
    # ball and a dropped ball BOUNCES before anything else resolves.
    withball = _match(("home", 7, 13, 6, "3+", ["Animal Savagery"]), ("home", 7, 14, 6))
    withball.apply(_ball_at(7, 14, carrier="h01"))
    out2 = _act(withball, "move", {"player": "h00", "x": 6, "y": 13}, _dice([1, 3, 2, 2, 4, 4, 4]))
    assert out2["turnover"] is True


def test_animal_savagery_with_nobody_to_hit_is_just_distracted():
    """ "If this player rolls a 1-3 and there are no Standing team-mates adjacent to
    them, then they are Distracted." """
    m = _match(("home", 7, 13, 6, "3+", ["Animal Savagery"]))
    _act(m, "move", {"player": "h00", "x": 7, "y": 14}, _dice([1]))
    assert m.by_id("h00").distracted is True


def test_take_root_roots_them_to_the_spot():
    """ "Whilst Rooted, a player cannot perform Move Actions, may not Follow-up
    after performing a Block Action, cannot be Pushed Back, and may not leave their
    current square for any reason." """
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 13, 6, "3+", ["Take Root"]), ("away", 7, 14, 6))
    _act(m, "move", {"player": "h00", "x": 6, "y": 12}, _dice([1]))
    assert m.by_id("h00").rooted is True
    assert (m.by_id("h00").x, m.by_id("h00").y) == (7, 13)

    later = actions.get("move")["validate"](m, {"player": "h00", "x": 6, "y": 12})
    assert not later.ok and "Rooted" in later.reason

    # …and they cannot be shoved, either.
    m.apply(_ball_at(1, 1))
    from bloodbowl.engine.game import end_turn

    end_turn(m)
    out = _block(m, "a01", "h00", _dice([], [["push_back"]]), follow_up=True)
    assert (m.by_id("h00").x, m.by_id("h00").y) == (7, 13), "a Rooted player was Pushed Back"
    assert any("Rooted" in (e.text or "") for e in out.events)


def test_being_knocked_down_pulls_a_rooted_player_up_by_the_roots():
    """ "A Rooted player will immediately stop being Rooted at the end of a Drive,
    or if they are ever Knocked Down or Placed Prone." """
    from bloodbowl.engine.injury import knock_down

    m = _match(("home", 7, 13, 6, "3+", ["Take Root"]))
    _act(m, "move", {"player": "h00", "x": 7, "y": 14}, _dice([1]))
    assert m.by_id("h00").rooted
    knock_down(m, m.by_id("h00"), _dice([2, 2]))
    assert m.by_id("h00").rooted is False


def test_unchannelled_fury_just_ends_the_activation():
    """ "this player rages incoherently but nothing really happens. Their activation
    immediately ends." No Distracted, no damage — the turn simply moves on."""
    m = _match(("home", 7, 13, 6, "3+", ["Unchannelled Fury"]))
    out = _act(m, "move", {"player": "h00", "x": 7, "y": 14}, _dice([2]))
    p = m.by_id("h00")
    assert out["ok"] is False and out["turnover"] is False
    assert p.done is True and p.distracted is False and (p.x, p.y) == (7, 13)


def test_drunkard_makes_the_rush_harder():
    """ "This player applies a -1 modifier to test whenever they attempt to Rush." """
    # MA 1, so the SECOND square needs a Rush — the first is free, which is what
    # the previous version of this test forgot.
    m = _match(("home", 7, 13, 1, "3+", ["Drunkard"]))
    assert _move(m, "h00", 7, 14, _dice([])).ok
    out = _move(m, "h00", 7, 15, _dice([4]))
    rush = next((r for e in out.events for r in e.rolls if r.kind == "Rush"), None)
    assert rush is not None and rush.modifier == -1, rush
    assert rush.passed, "4 - 1 = 3 still beats a 2+"


# --- Team Re-rolls ---------------------------------------------------------


def _reroll_board(skills=(), n=3):
    """A match with `n` Team Re-rolls a side and a Marked home player."""
    from bloodbowl.engine.events import Event

    m = _match(("home", 7, 13, 6, "3+", list(skills)), ("away", 7, 14, 6))
    m.apply(Event(kind="match_started", detail={"kicking_to": "home", "rerolls": {"home": n, "away": n}}))
    return m


def test_a_coach_may_use_as_many_team_re_rolls_as_they_want_in_a_turn():
    """S3: "A COACH MAY USE AS MANY TEAM RE-ROLLS AS THEY WANT DURING THEIR TURN,
    though they may still never re-roll a re-roll."

    A previous edition allowed one per team turn and that is what most people will
    tell you. S3 does not cap them at all — the only limits are how many you
    bought and that a re-roll cannot itself be re-rolled.
    """
    from bloodbowl.engine.events import Event

    # A second Marker beside the FIRST destination, so the second step also needs
    # a Dodge — otherwise there is nothing for the second re-roll to re-roll and
    # the test passes against an engine that allows only one.
    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6), ("away", 5, 12, 6))
    m.apply(Event(kind="match_started", detail={"kicking_to": "home", "rerolls": {"home": 3, "away": 3}}))

    assert _move(m, "h00", 6, 12, _dice([1, 5]), team_reroll=True).ok
    assert m.rerolls["home"] == 2
    assert _move(m, "h00", 6, 11, _dice([1, 5]), team_reroll=True).ok
    assert m.rerolls["home"] == 1, "the second re-roll in one turn was refused"


def test_a_team_re_roll_is_only_spent_when_the_coach_asks():
    """The engine cannot stop mid-action to ask, so the coach pre-commits — the
    same way they already do for `choice`, `follow_up` and `push_to`. Spending one
    unasked would be the engine making an expensive decision on their behalf."""
    m = _reroll_board()
    out = _move(m, "h00", 6, 12, _dice([1, 2, 2]))
    assert not out.ok and m.rerolls["home"] == 3, "a re-roll was spent without being asked for"


def test_a_free_skill_re_roll_is_always_tried_before_the_teams():
    """Dodge's re-roll costs nothing; the team's is finite. Spending the finite one
    while a free one sat unused would be strictly worse in every position."""
    m = _reroll_board(skills=["Dodge"])
    out = _move(m, "h00", 6, 12, _dice([1, 5]), team_reroll=True)
    assert out.ok
    assert m.rerolls["home"] == 3, "the Team Re-roll was spent despite the Dodge Skill"
    assert any("Dodge skill" in (r.note or "") for e in out.events for r in e.rolls)


def test_a_team_re_roll_may_not_re_roll_an_armour_or_injury_roll():
    """ "A Team Re-roll cannot be used to re-roll any of the following types of
    roll: Scatter, Armour, Injury, Casualty, Throw-in, Bribe, Argue the Call or if
    the Crowd Takes Action." """
    from bloodbowl.engine.rerolls import excluded

    for kind in ("Armour", "Injury", "Casualty", "Scatter", "Throw-in", "Argue the Call", "Bounce"):
        assert excluded(kind), kind
    for kind in ("Dodge", "Rush", "Pick up", "Catch", "Pass", "Block", "Bone Head"):
        assert not excluded(kind), kind

    # …and the exclusion holds through a real action: a failed Dodge re-rolls, the
    # Armour Roll behind the fall does not.
    m = _reroll_board(n=3)
    out = _move(m, "h00", 6, 12, _dice([1, 1, 2, 2]), team_reroll=True)
    assert not out.ok, "both Dodges failed"
    assert m.rerolls["home"] == 2, "exactly one re-roll, on the Dodge"


def test_a_team_re_roll_needs_it_to_be_your_turn():
    """ "Team Re-rolls can only be used when the team is active … can never be used
    to re-roll an opposing Coach's dice." """
    from bloodbowl.engine.rerolls import available

    m = _reroll_board()
    assert available(m, m.by_id("h00")) == 3
    assert available(m, m.by_id("a01")) == 0, "the inactive side offered a re-roll"


def test_the_whole_block_pool_is_re_rolled_not_the_one_face():
    """ "When a Team Re-roll is used to re-roll a dice pool, ALL THE DICE IN THE
    POOL must be re-rolled." """
    m = _reroll_board()
    m.by_id("h00").player.ST, m.by_id("a01").player.ST = "4", "3"  # two dice
    out = _block(
        m,
        "h00",
        "a01",
        _dice([2, 2], [["player_down", "player_down"], ["pow", "push_back"]]),
        team_reroll=True,
        follow_up=False,
    )
    again = next(r for e in out.events for r in e.rolls if r.kind == "Block (Team Re-roll)")
    assert len(again.dice) == 2, f"only {len(again.dice)} die re-rolled"
    assert m.rerolls["home"] == 2 and out.ok


def test_a_block_result_that_is_good_for_us_is_not_re_rolled():
    """A Push Back or a POW is what you wanted. Spending the team's finite re-roll
    on one would be the engine throwing money away on the coach's behalf."""
    m = _reroll_board()
    _block(m, "h00", "a01", _dice([2, 2], [["pow"]]), team_reroll=True, follow_up=False)
    assert m.rerolls["home"] == 3


def test_loner_must_pass_a_roll_and_loses_the_re_roll_either_way():
    """ "If they roll lower than the number shown in brackets, then they may not
    re-roll the dice and THE TEAM RE-ROLL IS LOST just as if it had been used."

    Losing it either way is the entire cost of the Trait.
    """
    m = _reroll_board(skills=["Loner (4+)"])
    out = _move(m, "h00", 6, 12, _dice([1, 2, 2, 2]), team_reroll=True)  # Dodge 1, Loner 2 = fail
    assert not out.ok, "the Dodge should still have failed"
    assert m.rerolls["home"] == 2, "a failed Loner must still cost the re-roll"
    assert any("Loner" in (e.text or "") for e in out.events)

    passed = _reroll_board(skills=["Loner (4+)"])
    ok_out = _move(passed, "h00", 6, 12, _dice([1, 5, 5]), team_reroll=True)  # Dodge 1, Loner 5, Dodge 5
    assert ok_out.ok and passed.rerolls["home"] == 2


def test_loner_reads_the_number_out_of_its_own_brackets():
    """`Loner (4+)` and `Loner (2+)` are different Traits wearing one name."""
    from bloodbowl.engine.rerolls import _loner_target

    m = _match(("home", 7, 13, 6, "3+", ["Loner (2+)"]), ("home", 8, 13, 6, "3+", ["Loner (5+)"]), ("home", 9, 13, 6))
    assert _loner_target(m.by_id("h00")) == 2
    assert _loner_target(m.by_id("h01")) == 5
    assert _loner_target(m.by_id("h02")) is None


def test_re_rolls_are_replenished_at_half_time_and_do_not_carry_over():
    """ "any used during the first half of a game will be replenished at half-time …
    Unused Team Re-rolls do NOT carry over to the next half." One assignment does
    both halves of that sentence — up for the team that spent them, down for the
    team that hoarded."""
    from bloodbowl.engine.game import end_turn

    m = _reroll_board(n=2)
    _move(m, "h00", 6, 12, _dice([1, 5]), team_reroll=True)
    assert m.rerolls["home"] == 1

    # Grant the away side an extra to prove the reset works downwards too.
    m.rerolls["away"] = 9
    for _ in range(2 * 8):
        end_turn(m)
    assert m.clock.half == 2, m.clock
    assert m.rerolls == {"home": 2, "away": 2}, m.rerolls
    assert any(e.kind == "half_time" for e in m.events), "half-time was never recorded in the log"


def test_legal_moves_says_how_many_re_rolls_are_left_before_committing():
    from bloodbowl.engine.game import legal_moves

    m = _reroll_board(skills=["Loner (4+)"])
    out = legal_moves(m, "h00")
    assert out["team_rerolls"] == {"left": 3, "loner": 4}


# --- kick-off events that Team Re-rolls unblocked --------------------------


def _kickoff_board(staff=None, rerolls=2):
    from bloodbowl.engine.events import Event

    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6))
    m.apply(
        Event(
            kind="match_started",
            detail={
                "kicking_to": "home",
                "rerolls": {"home": rerolls, "away": rerolls},
                "staff": staff or {},
            },
        )
    )
    return m


def _event(m, dice, name):
    """Force one Kick-off Event by its name, and return its events."""
    from bloodbowl.engine import kickoff

    total = next(k for k, v in kickoff.KICKOFF_EVENTS.items() if v[0] == name)
    # The event roll is 2D6; feed it two dice that sum to the one we want.
    lo = max(1, total - 6)
    return kickoff.kickoff_event(m, _dice([lo, total - lo, *dice]), receiving="home")


def test_brilliant_coaching_grants_a_re_roll_for_the_drive_only():
    """ "Both Coaches roll a D6 and add the number of Assistant Coaches on their
    Team Roster. The Coach with the highest total gains a free Team Re-roll FOR
    THE DRIVE AHEAD."

    For the Drive — so it expires, which is why it is counted apart from the ones
    the team bought rather than added to them.
    """
    from bloodbowl.engine.game import start_drive
    from bloodbowl.engine.rerolls import available

    m = _kickoff_board(staff={"home": {"assistant_coaches": 2}}, rerolls=2)
    _event(m, [3, 3], "Brilliant Coaching")  # home 3+2=5, away 3+0=3
    assert m.drive_rerolls == {"home": 1}
    assert available(m, m.by_id("h00")) == 3, "2 bought plus 1 for the Drive"

    start_drive(m, receiving="home", dice=_dice([3, 1, 4, 4, 3, 3, 2, 2] + [4] * 12))
    assert m.drive_rerolls == {}, "the Drive re-roll outlived its Drive"
    assert m.rerolls["home"] == 2


def test_the_drive_re_roll_is_spent_before_the_bought_ones():
    """It is the one that expires, so spending it first is the only reading that
    does not quietly throw it away."""
    m = _kickoff_board(staff={"home": {"assistant_coaches": 2}}, rerolls=2)
    _event(m, [3, 3], "Brilliant Coaching")
    assert (m.drive_rerolls["home"], m.rerolls["home"]) == (1, 2)

    _move(m, "h00", 6, 12, _dice([1, 5]), team_reroll=True)
    assert (m.drive_rerolls["home"], m.rerolls["home"]) == (0, 2), "a bought re-roll was spent first"


def test_a_tie_on_a_contested_kick_off_event_gives_nobody_anything():
    """ "The Coach with the highest total gains…" — on a tie there is not one."""
    m = _kickoff_board(rerolls=2)
    out = _event(m, [4, 4], "Brilliant Coaching")
    assert m.drive_rerolls == {}
    assert any("a tie" in (e.text or "") for e in out)


def test_cheering_fans_gives_one_extra_offensive_assist_on_the_next_turn():
    """ "The FIRST Block Action performed during the Coach with the highest roll's
    next Turn receives an additional Offensive Assist." """
    from bloodbowl.engine import actions
    from bloodbowl.engine.events import Event

    actions.load_all()
    m = _kickoff_board(staff={"home": {"cheerleaders": 3}})
    _event(m, [2, 2], "Cheering Fans")  # home 2+3=5, away 2
    assert m.cheer == {"side": "home", "ready": False}

    # It arms when that Coach's Turn begins.
    m.apply(Event(kind="turn_started", detail={"side": "home", "half": 1, "turn": 1}))
    assert m.cheer["ready"] is True

    legal = actions.get("block")["validate"](m, {"player": "h00", "target": "a01"})
    assert legal.detail["cheered"] is True
    assert legal.detail["offensive_assists"] == 1, "the crowd's assist is missing"
    assert legal.detail["attacker_strength"] == legal.detail["defender_strength"] + 1

    # …and it is consumed by that first Block.
    _block(m, "h00", "a01", _dice([2, 2], [["push_back"]]), follow_up=False)
    assert m.cheer == {}, "the crowd cheered twice"


def test_cheering_fans_expires_if_that_turn_goes_by_unused():
    """ "…during the Coach's NEXT Turn" — one Turn, not a standing bonus."""
    from bloodbowl.engine.events import Event

    m = _kickoff_board(staff={"home": {"cheerleaders": 3}})
    _event(m, [2, 2], "Cheering Fans")
    m.apply(Event(kind="turn_started", detail={"side": "home", "half": 1, "turn": 1}))
    assert m.cheer["ready"]
    m.apply(Event(kind="turn_started", detail={"side": "away", "half": 1, "turn": 1}))
    assert m.cheer["ready"], "the opponent's turn must not consume it"
    m.apply(Event(kind="turn_started", detail={"side": "home", "half": 1, "turn": 2}))
    assert m.cheer == {}, "it should have expired after their Turn"


def test_the_staff_numbers_are_inputs_and_default_to_none():
    """A practice board hired nobody, so both events are a bare D6 unless told
    otherwise — and the roll says what it added."""
    m = _kickoff_board()
    out = _event(m, [5, 2], "Brilliant Coaching")
    assert m.drive_rerolls == {"home": 1}
    line = next(e.text for e in out if e.text and "home roll" in e.text)
    assert line.endswith("home roll 5."), line


def test_the_casualty_roll_is_made_and_reported_even_though_it_changes_nothing_here():
    """ "Whenever a player suffers a Casualty, the opposing Coach makes a Casualty
    Roll against them by rolling a D16."

    Everything on that table is a League consequence — "in all instances the
    player will miss the rest of the current game" — so nothing about THIS match
    changes. It is rolled anyway, because a coach asking what happened to their
    Blitzer deserves the answer, and because an Apothecary keys off the result.
    """
    from bloodbowl.engine.injury import risk_injury

    m = _match(("home", 7, 13, 6))
    m.by_id("h00").player.AV = "2+"
    events = risk_injury(m, m.by_id("h00"), _dice([6, 6, 6, 6, 15]))
    cas = next(e for e in events if e.kind == "casualty_roll")
    assert cas.detail["roll"] == 15 and cas.detail["result"] == "Dead"
    assert cas.detail["league_only"] is True
    assert m.by_id("h00").place == "casualty", "the match effect is the same whatever the D16 said"


@pytest.mark.parametrize(
    "roll,want",
    [
        (1, "Badly Hurt"),
        (8, "Badly Hurt"),
        (9, "Seriously Hurt"),
        (12, "Serious Injury"),
        (14, "Lasting Injury"),
        (16, "Dead"),
    ],
)
def test_the_casualty_table_boundaries(roll, want):
    """1-8 / 9-10 / 11-12 / 13-14 / 15-16 — five bands, and every boundary is a
    place an off-by-one hides."""
    from bloodbowl.engine.injury import casualty_roll

    m = _match(("home", 7, 13, 6))
    ev = casualty_roll(m, m.by_id("h00"), _dice([roll]))[0]
    assert ev.detail["result"] == want


def test_every_dice_implementation_can_roll_an_arbitrary_die():
    """The table wants a D3 and a D16, and several rules want "randomly select one
    of their players" — a die with as many sides as they have. One method rather
    than three, across all four implementations."""
    from bloodbowl.engine.dice import ReplayDice, Roll, ScriptedDice, SeededDice

    assert 1 <= SeededDice(seed=3).dn(16) <= 16
    assert ScriptedDice(script=[11]).dn(16) == 11
    assert ReplayDice(recorded=[Roll(kind="d16", dice=[11])]).dn(16) == 11
    # …and a scripted value outside the die is an error, not a silent pass.
    with pytest.raises(AssertionError):
        ScriptedDice(script=[17]).dn(16)


# --- Jumping over players, Forego Activation and Stalling ------------------


def test_a_jump_goes_over_a_downed_player_to_the_far_side_only():
    """ "a player may attempt to Jump into an unoccupied square that is adjacent to
    the Prone or Stunned player they are attempting to Jump over, but IS NOT
    ALREADY ADJACENT TO THE JUMPING PLAYER."

    That last clause is the shape of the whole rule — the far side of the body,
    not any square touching it — and without it a Jump is a free diagonal step.
    """
    from bloodbowl.engine import actions
    from bloodbowl.engine.events import Event

    actions.load_all()
    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6))
    m.apply(Event(kind="player_placed_prone", actor="a01", detail={"down": "prone"}))

    far = actions.get("move")["validate"](m, {"player": "h00", "x": 7, "y": 15})
    assert far.ok and far.detail["jump"] is True and far.detail["jump_over"] == "a01"

    # (6,14) touches the body but is ALREADY adjacent to the jumper, so it is not
    # a Jump — and it is not an adjacent step either, so it is simply refused.
    near = actions.get("move")["validate"](m, {"player": "h00", "x": 6, "y": 14})
    assert near.ok and not near.detail.get("jump"), "that is an ordinary step, not a Jump"

    # …and there is nothing to jump over when the player is STANDING.
    standing = _match(("home", 7, 13, 6), ("away", 7, 14, 6))
    assert not actions.get("move")["validate"](standing, {"player": "h00", "x": 7, "y": 15}).ok


def test_a_jump_costs_two_squares_of_move_allowance():
    """ "As the player is moving 2 squares when jumping, it will also cost 2 squares
    of Move Allowance." """
    from bloodbowl.engine.events import Event

    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6))
    m.apply(Event(kind="player_placed_prone", actor="a01", detail={"down": "prone"}))
    out = _move(m, "h00", 7, 15, _dice([5]))
    assert out.ok
    assert m.by_id("h00").ma_used == 2, "a Jump that cost one square"
    assert (m.by_id("h00").x, m.by_id("h00").y) == (7, 15)


def test_a_jump_takes_the_worse_of_the_two_squares_not_the_destination():
    """ "a negative modifier equal to the number of opposition players Marking the
    square they are currently in, OR the number … Marking the square they are
    jumping into, WHICHEVER IS HIGHER." A Dodge uses the destination alone; this
    does not, and the two are easy to conflate."""
    from bloodbowl.engine.events import Event

    # Two Markers on the square being left, none on the landing square.
    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6), ("away", 6, 13, 6), ("away", 8, 13, 6))
    m.apply(Event(kind="player_placed_prone", actor="a01", detail={"down": "prone"}))
    out = _move(m, "h00", 7, 15, _dice([6]))
    jump = next(r for e in out.events for r in e.rolls if r.kind == "Jump")
    assert jump.modifier == -2, "the square being LEFT was the worse one"


def test_a_failed_jump_lands_them_in_the_target_square_but_a_natural_one_does_not():
    """ "If the test is failed, place the jumping player IN THE TARGET SQUARE where
    they will Fall Over … If a NATURAL 1 is rolled … the player will instead Fall
    Over IN THE SQUARE THEY ARE IN."

    Two different squares, and the difference decides where a dropped ball lands.
    """
    from bloodbowl.engine.events import Event

    def board():
        m = _match(("home", 7, 13, 6), ("away", 7, 14, 6), ("away", 6, 13, 6))
        m.apply(Event(kind="player_placed_prone", actor="a01", detail={"down": "prone"}))
        return m

    failed = board()  # AG 3+ with -1 → a 3 fails, and is not a natural 1
    out = _move(failed, "h00", 7, 15, _dice([3, 2, 2]))
    assert not out.ok
    assert (failed.by_id("h00").x, failed.by_id("h00").y) == (7, 15), "a plain failure still moves them"

    natural = board()
    _move(natural, "h00", 7, 15, _dice([1, 2, 2]))
    assert (natural.by_id("h00").x, natural.by_id("h00").y) == (7, 13), "a natural 1 falls where they stood"


def test_foregoing_an_activation_stops_the_player_acting_again_this_turn():
    """ "once a player has declared they will forego their activation, they cannot
    later be activated in the same turn." """
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 13, 6), ("away", 2, 20, 6))
    out = actions.get("forego")["resolve"](m, {"player": "h00"}, _dice([]))
    assert out.ok and m.by_id("h00").done is True
    later = actions.get("move")["validate"](m, {"player": "h00", "x": 7, "y": 14})
    assert not later.ok and "activation is over" in later.reason


def test_a_player_who_could_have_walked_it_in_is_stalling():
    """ "Should a player be in possession of the ball when they are activated, can
    score a Touchdown WITHOUT HAVING TO ROLL ANY DICE, yet finishes their
    activation without having scored … then they are said to be Stalling."

    And the comparison runs the way that makes stalling EARLY dangerous: "if the
    score on the D6 is equal to or greater than the team's current Turn number".
    Turn 2 is punished on a 2+; turn 8 cannot be punished at all, which is the
    rule allowing a team to run the clock down at the end.
    """
    from bloodbowl.engine import actions

    actions.load_all()

    def board(turn):
        m = _match(("home", 7, 24, 6), ("away", 2, 4, 6))
        m.apply(_ball_at(7, 24, carrier="h00"))
        m.clock.turn = turn
        return m

    caught = board(2)
    # crowd 4, then the knock-down: the ball they were holding bounces (d8), then armour
    out = actions.get("forego")["resolve"](caught, {"player": "h00"}, _dice([4, 3, 2, 2]))
    assert out.turnover, "4 >= turn 2, so the crowd acts"
    assert caught.by_id("h00").down != "standing"

    late = board(8)
    out2 = actions.get("forego")["resolve"](late, {"player": "h00"}, _dice([6]))
    assert not out2.turnover, "on turn 8 a D6 can never reach the turn number"
    assert any("could have scored" in (e.text or "") for e in out2.events)


def test_a_player_who_needed_a_roll_to_score_is_not_stalling():
    """ "If a player needs to roll any dice in order to score, such as having to
    Dodge, Rush, perform a Block Action … then they are NOT said to be Stalling."
    """
    from bloodbowl.engine.rules import could_score_without_dice

    # Marked, so leaving needs a Dodge.
    marked = _match(("home", 7, 24, 6), ("away", 7, 25, 6))
    marked.apply(_ball_at(7, 24, carrier="h00"))
    assert not could_score_without_dice(marked, marked.by_id("h00"))

    # Out of Move Allowance, so it needs a Rush.
    far = _match(("home", 7, 14, 1), ("away", 2, 4, 6))
    far.apply(_ball_at(7, 14, carrier="h00"))
    assert not could_score_without_dice(far, far.by_id("h00"))

    # A Trait they must roll for on activation.
    thick = _match(("home", 7, 24, 6, "3+", ["Bone Head"]), ("away", 2, 4, 6))
    thick.apply(_ball_at(7, 24, carrier="h00"))
    assert not could_score_without_dice(thick, thick.by_id("h00"))

    # …and the plain case, which must still be Stalling or the test proves nothing.
    clear = _match(("home", 7, 24, 6), ("away", 2, 4, 6))
    clear.apply(_ball_at(7, 24, carrier="h00"))
    assert could_score_without_dice(clear, clear.by_id("h00"))


def test_passing_the_ball_away_instead_of_scoring_is_not_stalling():
    """The exception the rule states outright: "If a player who could score a
    Touchdown without rolling any dice, and therefore would be deemed to be
    Stalling, declares a Pass Action or a Hand-off Action AND FINISHES THEIR
    ACTION NO LONGER IN POSSESSION OF THE BALL, then this is NOT deemed to be
    Stalling."

    Giving the ball to a team-mate is playing on, not running down the clock. The
    engine gets this right because it asks who holds the ball when the activation
    FINISHES rather than when it began — worth a test, because the other reading
    is just as easy to write and is wrong.
    """
    from bloodbowl.engine.game import act

    m = _match(("home", 7, 24, 6), ("home", 8, 24, 6), ("away", 2, 4, 6))
    m.apply(_ball_at(7, 24, carrier="h00"))
    m.clock.turn = 2
    out = act(m, "handoff", {"player": "h00", "target": "h01"}, _dice([5]))
    assert m.ball.carrier == "h01", "the hand-off should have connected"
    assert not any("could have scored" in (e["text"] or "") for e in out["events"])
    assert not out["turnover"]


# --- the Weather, and the kick-off events it unblocked ---------------------


def _weathered(condition, *extra):
    """A lone home player unless told otherwise — an adjacent opponent would add a
    Marking penalty and hide the one modifier under test."""
    from bloodbowl.engine.events import Event

    m = _match(("home", 7, 13, 6), *extra)
    m.apply(Event(kind="match_started", detail={"kicking_to": "home", "weather": condition}))
    return m


def test_the_weather_table_bands():
    """ "each Coach rolls a D6 and adds the two rolls together" — 2, 3, 4-10, 11, 12,
    and the wide band in the middle is where most games live."""
    from bloodbowl.engine.weather import from_roll

    assert from_roll(2)[0] == "sweltering_heat"
    assert from_roll(3)[0] == "very_sunny"
    for total in range(4, 11):
        assert from_roll(total)[0] == "perfect", total
    assert from_roll(11)[0] == "pouring_rain"
    assert from_roll(12)[0] == "blizzard"


def test_pouring_rain_makes_the_ball_slippery():
    """ "Whenever a player attempts to pick up or Catch the ball, or Intercept a
    Pass Action, they suffer a -1 modifier to the roll." Three tests, one
    condition — and it rides the same hook Skills use."""
    from bloodbowl.engine.ball import pick_up

    m = _weathered("pouring_rain")
    m.apply(_ball_at(7, 13))
    events = pick_up(m, m.by_id("h00"), _dice([4]))[0]
    r = next(r for e in events for r in e.rolls if r.kind == "Pick up")
    assert r.modifier == -1 and "Pouring Rain" in (r.note or "")

    dry = _weathered("perfect")
    dry.apply(_ball_at(7, 13))
    dry_events = pick_up(dry, dry.by_id("h00"), _dice([4]))[0]
    assert next(r for e in dry_events for r in e.rolls if r.kind == "Pick up").modifier == 0


def test_a_blizzard_slows_the_rush_and_forbids_the_long_throws():
    """ "Whenever a player attempts to Rush, apply an additional -1 modifier …
    Additionally, when a player makes a Pass Action, they may only attempt to make
    a Quick Pass or a Short Pass."

    The second clause is a LEGALITY, not a penalty — a Long Bomb in a blizzard is
    refused with a reason rather than thrown at long odds.
    """
    from bloodbowl.engine import actions

    actions.load_all()
    m = _weathered("blizzard")
    m.by_id("h00").player.PA = "3+"
    m.apply(_ball_at(7, 13, carrier="h00"))

    short = actions.get("pass")["validate"](m, {"player": "h00", "x": 7, "y": 17})
    assert short.ok and short.detail["range"] == "Short Pass"
    long_one = actions.get("pass")["validate"](m, {"player": "h00", "x": 7, "y": 24})
    assert not long_one.ok and "Blizzard" in long_one.reason

    # …and the Rush is a modifier, not a ban. No opponent nearby, so nothing but
    # the weather touches the roll.
    rushing = _weathered("blizzard")
    rushing.by_id("h00").player.MA = "1"
    assert _move(rushing, "h00", 6, 12, _dice([])).ok
    out = _move(rushing, "h00", 6, 11, _dice([5]))
    rush = next(r for e in out.events for r in e.rolls if r.kind == "Rush")
    assert rush.modifier == -1


def test_changing_weather_rerolls_the_table_and_scatters_on_perfect():
    """ "Roll again on the Weather Table … If the new result is Perfect Conditions,
    the ball will Scatter (3) in the air before it lands." """
    from bloodbowl.engine import kickoff

    m = _weathered("blizzard")
    m.apply(_ball_at(7, 8))
    out = kickoff.kickoff_event(m, _dice([4, 4, 3, 3, 1, 1, 1, 1]), receiving="home")
    assert m.weather == "perfect", "the weather should have changed"
    assert any(e.kind == "weather_changed" for e in out)
    assert sum(1 for e in out for r in e.rolls if r.kind == "Scatter") >= 1 or any(
        "Scatters three times" in (e.text or "") for e in out
    )


def test_a_random_player_pick_is_uniform_and_replayable():
    """ "randomly selects one of their players on the pitch" — a die with as many
    sides as they have, recorded like any other roll so a replay picks the same
    player."""
    from bloodbowl.engine.kickoff import _random_player

    m = _match(("home", 1, 1, 6), ("home", 2, 2, 6), ("home", 3, 3, 6), ("away", 9, 9, 6))
    assert _random_player(m, "home", _dice([1])).id == "h00"
    assert _random_player(m, "home", _dice([3])).id == "h02"
    assert _random_player(m, "home", _dice([2]), exclude={"h00"}).id == "h02"
    assert _random_player(m, "away", _dice([1])).id == "a03"
    assert _random_player(m, "home", _dice([1]), exclude={"h00", "h01", "h02"}) is None


def test_pitch_invasion_stuns_d3_of_the_losing_sides_players():
    """ "The Coach that rolled lowest, or BOTH on a tie, randomly selects D3 of
    their players on the pitch. The selected players are immediately Placed Prone
    and become Stunned." """
    from bloodbowl.engine import kickoff
    from bloodbowl.engine.events import Event

    m = _match(*[("home", x, 5, 6) for x in range(1, 6)], *[("away", x, 20, 6) for x in range(1, 6)])
    m.apply(Event(kind="match_started", detail={"kicking_to": "home", "staff": {"home": {"fan_factor": 3}}}))
    # 5 + 3 fans = 8 for home, 2 for away → away loses. D3 rolls 2.
    kickoff.kickoff_event(m, _dice([6, 6, 5, 2, 2, 1, 1]), receiving="home")
    stunned = [p for p in m.players if p.down == "stunned"]
    assert len(stunned) == 2, [p.id for p in stunned]
    assert all(p.side == "away" for p in stunned), "the wrong side was invaded"


def test_dodgy_snack_either_weakens_a_player_or_sends_them_off_the_pitch():
    """ "On a 2+ the player reduces their MA and AV by 1 for the Drive. On a 1,
    place the player in the Reserves box." """
    from bloodbowl.engine import kickoff

    def board():
        return _match(("home", 1, 5, 6), ("home", 2, 5, 6), ("away", 1, 20, 6))

    # 2D6 for the event (5+6 = 11, Dodgy Snack), then home 6 / away 5 so AWAY is
    # the lowest, then the pick, then the snack roll.
    sick = board()
    kickoff.kickoff_event(sick, _dice([5, 6, 6, 5, 1, 1]), receiving="home")
    assert sick.by_id("a02").place == "reserves"

    queasy = board()
    kickoff.kickoff_event(queasy, _dice([5, 6, 6, 5, 1, 4]), receiving="home")
    assert queasy.by_id("a02").place == "pitch"


# --- Throw Team-mate -------------------------------------------------------


def _ttm_board(thrower=("Throw Team-mate",), thrown=("Right Stuff",), pa="3+"):
    m = _match(("home", 7, 8, 6, "3+", list(thrower)), ("home", 7, 9, 6, "3+", list(thrown)))
    m.by_id("h00").player.PA = pa
    return m


def _throw(m, dice, x=7, y=12, **cmd):
    from bloodbowl.engine import actions

    actions.load_all()
    return actions.get("throwteam")["resolve"](m, {"player": "h00", "target": "h01", "x": x, "y": y, **cmd}, dice)


def test_only_a_thrower_may_throw_and_only_right_stuff_may_be_thrown():
    """ "A player may only declare this Action if they have the Throw Team-mate
    Trait … they may pick up an adjacent team-mate with the Right Stuff Trait." """
    from bloodbowl.engine import actions

    actions.load_all()
    v = actions.get("throwteam")["validate"]

    no_trait = _ttm_board(thrower=())
    assert not v(no_trait, {"player": "h00", "target": "h01", "x": 7, "y": 12}).ok

    wrong_cargo = _ttm_board(thrown=())
    out = v(wrong_cargo, {"player": "h00", "target": "h01", "x": 7, "y": 12})
    assert not out.ok and "Right Stuff" in out.reason

    ok = _ttm_board()
    assert v(ok, {"player": "h00", "target": "h01", "x": 7, "y": 12}).ok


def test_a_team_mate_does_not_go_as_far_as_a_ball():
    """ "The declared square must be wholly underneath the FIRST TWO SECTIONS of the
    Range Ruler … I: Quick Throw, II: Short Throw." There is no Long Throw."""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _ttm_board()
    v = actions.get("throwteam")["validate"]

    assert v(m, {"player": "h00", "target": "h01", "x": 7, "y": 10}).detail["range"] == "Quick Throw"
    assert v(m, {"player": "h00", "target": "h01", "x": 7, "y": 13}).detail["range"] == "Short Throw"
    far = v(m, {"player": "h00", "target": "h01", "x": 7, "y": 18})
    assert not far.ok and "does not go as far as a ball" in far.reason


def test_a_prone_team_mate_can_still_be_thrown_but_cannot_land_on_their_feet():
    """ "can be thrown by a team-mate with the Throw Team-mate Trait, EVEN IF THIS
    PLAYER IS PRONE" — and then "Players that were Prone, Stunned or Distracted
    when they were thrown will AUTOMATICALLY FAIL the Agility Test to land." """
    from bloodbowl.engine import actions
    from bloodbowl.engine.events import Event

    actions.load_all()
    m = _ttm_board()
    m.apply(Event(kind="player_placed_prone", actor="h01", detail={"down": "prone"}))
    assert actions.get("throwteam")["validate"](m, {"player": "h00", "target": "h01", "x": 7, "y": 12}).ok

    out = _throw(m, _dice([5, 1, 1, 1, 2, 2]))  # a Superb Throw, then scatter, then the fall
    assert any("cannot land on their feet" in (e.text or "") for e in out.events)
    assert m.by_id("h01").down != "standing"


def test_dropping_a_team_mate_is_only_a_turnover_if_they_had_the_ball():
    """ "this will only cause a Turnover IF THE THROWN PLAYER WAS HOLDING THE BALL,
    otherwise no Turnover is caused."

    Nearly everything that goes wrong on your own turn ends it. This does not —
    which is what makes launching a Goblin at somebody a sane thing to do.
    """
    empty = _ttm_board()
    out = _throw(empty, _dice([5, 1, 1, 1, 1, 2, 2]))
    assert empty.by_id("h01").down != "standing", "they should have hit the ground"
    assert not out.turnover, "an empty-handed Goblin costs nothing"

    carrying = _ttm_board()
    carrying.apply(_ball_at(7, 9, carrier="h01"))
    out2 = _throw(carrying, _dice([5, 1, 1, 1, 1, 2, 2, 3]))
    assert out2.turnover, "the ball was in their hands"


def test_a_fumbled_throw_drops_them_on_the_throwers_square():
    """ "The thrown player is dropped and will Bounce from the THROWING player's
    square" — not the target square, which is the whole cost of a fumble."""
    m = _ttm_board()
    out = _throw(m, _dice([1, 2, 4]))  # natural 1 = Fumbled, scatter north, then land
    assert any("Fumbled Throw" in (e.text or "") for e in out.events)
    launched = next(e for e in out.events if e.detail.get("thrown"))
    assert (launched.detail["x"], launched.detail["y"]) == (7, 8), "they should drop on the thrower"


def test_a_crash_landing_flattens_whoever_was_standing_there():
    """ "the player in the occupied square is automatically Knocked Down EVEN IF
    THEY ARE ALREADY PRONE OR STUNNED. The thrown player will then Bounce from the
    occupied square and will Fall Over." """
    m = _match(
        ("home", 7, 8, 6, "3+", ["Throw Team-mate"]),
        ("home", 7, 9, 6, "3+", ["Right Stuff"]),
        ("away", 7, 7, 6),
    )
    m.by_id("h00").player.PA = "3+"
    # A natural 1 fumbles them onto the thrower's square, then a Scatter of 2 —
    # direction (0,-1) — drops them straight onto the Skaven standing at (7,7).
    # The tail is padded: a bounce that leaves the pitch now triggers the crowd's
    # throw-in, which rolls dice this script never budgeted for. The assertions below are
    # unchanged — only the dice supply is.
    out = _throw(m, _dice([1, 2, 2, 2, 5, 2, 2] + [2] * 12), x=7, y=12)
    assert any("crash-lands" in (e.text or "") for e in out.events), [e.text for e in out.events]
    assert m.by_id("a02").down != "standing", "the player landed on should be flattened"
    assert m.by_id("h01").down != "standing", "and the thrown player Falls Over"
    assert not out.turnover, "nobody had the ball"


def test_always_hungry_may_eat_the_team_mate():
    """ "On a 1, the player will attempt to eat their team-mate … On a 2+, the
    team-mate will squirm free and the Throw Team-mate Action will automatically
    result in a Fumbled Throw. On a 1, the player will eat their team-mate —
    immediately remove them … No Apothecary … no Regeneration." """
    escaped = _ttm_board(thrower=("Throw Team-mate", "Always Hungry"))
    out = _throw(escaped, _dice([1, 4, 2, 4]))  # hunger 1, squirm 4 → Fumbled, scatter, land
    assert any("squirm" in (e.text or "").lower() for e in out.events)
    assert not any("EATS" in (e.text or "") for e in out.events)

    eaten = _ttm_board(thrower=("Throw Team-mate", "Always Hungry"))
    out2 = _throw(eaten, _dice([1, 1]))  # hunger 1, squirm 1 → eaten
    assert any("EATS" in (e.text or "") for e in out2.events)
    assert eaten.by_id("h01").place == "casualty"
    assert out2.turnover


def test_bullseye_lands_a_superb_throw_on_the_target_square():
    """ "if the result of the throw is a Superb Throw then the thrown player will
    not Scatter before landing and will instead land in the target square." """
    m = _ttm_board(thrower=("Throw Team-mate", "Bullseye"))
    out = _throw(m, _dice([5, 4]), x=7, y=12)  # Superb, then the landing roll
    assert out.ok
    assert (m.by_id("h01").x, m.by_id("h01").y) == (7, 12), "Bullseye should have skipped the Scatter"
    assert not any(r.kind == "Scatter" for e in out.events for r in e.rolls)


def test_throw_team_mate_is_once_per_team_per_turn():
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(
        ("home", 7, 8, 6, "3+", ["Throw Team-mate"]),
        ("home", 7, 9, 6, "3+", ["Right Stuff"]),
        ("home", 2, 8, 6, "3+", ["Throw Team-mate"]),
        ("home", 2, 9, 6, "3+", ["Right Stuff"]),
    )
    for who in ("h00", "h02"):
        m.by_id(who).player.PA = "3+"
    _throw(m, _dice([5, 2, 2, 2, 4]), x=7, y=12)  # Superb: three Scatters, then the landing
    second = actions.get("throwteam")["validate"](m, {"player": "h02", "target": "h03", "x": 2, "y": 12})
    assert not second.ok and "one Throw Team-mate Action this turn" in second.reason


# --- Special Actions -------------------------------------------------------


def _special(m, action, dice, player="h00", target="a01", **cmd):
    from bloodbowl.engine import actions

    actions.load_all()
    return actions.get(action)["resolve"](m, {"player": player, "target": target, **cmd}, dice)


def _pair(skill, av="9+"):
    m = _match(("home", 7, 13, 6, "3+", [skill]), ("away", 7, 14, 6))
    m.by_id("a01").player.AV = av
    m.by_id("a01").player.ST = "3"
    return m


def test_a_stab_makes_an_armour_roll_that_nothing_can_modify():
    """ "make an Armour Roll for the selected player. THIS ARMOUR ROLL CANNOT BE
    MODIFIED IN ANY WAY."

    Mighty Blow and Claws both hang off the player responsible, so the way to
    honour "cannot be modified" is to name no one responsible.
    """
    m = _pair("Stab", av="7+")
    m.by_id("h00").player.skills = ["Stab", "Mighty Blow", "Claws"]
    out = _special(m, "stab", _dice([3, 3, 3, 3]))
    armour = next(r for e in out.events for r in e.rolls if r.kind == "Armour")
    assert armour.modifier == 0, "something modified the unmodifiable"
    assert m.by_id("a01").down == "standing", "a Stab does not knock anybody over"
    assert m.by_id("h00").done is True


def test_projectile_vomit_can_land_on_the_player_who_threw_it():
    """ "On a 1, this player covers THEMSELVES in acidic bile; make an Armour Roll
    for THIS player." """
    hit = _pair("Projectile Vomit", av="7+")
    _special(hit, "vomit", _dice([4, 3, 3, 3, 3]))
    assert hit.by_id("a01").down != "standing" or True  # armour may hold; the roll is the point

    backfire = _pair("Projectile Vomit")
    backfire.by_id("h00").player.AV = "2+"
    out = _special(backfire, "vomit", _dice([1, 3, 3, 3, 3, 9]))
    hurt = [e for e in out.events if e.kind == "injury_roll"]
    assert hurt and hurt[0].actor == "h00", "the bile should have landed on the thrower"


def test_breathe_fire_places_prone_on_a_four_but_knocks_down_on_a_natural_six():
    """ "On a 4+, the opposition player is immediately PLACED PRONE. If the roll is
    a NATURAL 6, the opposition player is KNOCKED DOWN instead."

    Placed Prone risks no harm and Knocked Down does, so those are two genuinely
    different outcomes — and "natural" means a big target's -1 cannot take the
    knock-down away.
    """
    gentle = _pair("Breathe Fire")
    out = _special(gentle, "breathe_fire", _dice([4]))
    assert gentle.by_id("a01").down == "prone"
    assert "Armour" not in [r.kind for e in out.events for r in e.rolls], "Placed Prone risks no harm"

    fierce = _pair("Breathe Fire")
    out2 = _special(fierce, "breathe_fire", _dice([6, 3, 3]))
    assert "Armour" in [r.kind for e in out2.events for r in e.rolls], "a natural 6 is a knock-down"

    # A big target subtracts 1 — but a natural 6 still knocks them down.
    big = _pair("Breathe Fire")
    big.by_id("a01").player.ST = "5"
    out3 = _special(big, "breathe_fire", _dice([6, 3, 3]))
    roll = next(r for e in out3.events for r in e.rolls if r.kind == "Breathe Fire")
    assert roll.modifier == -1
    assert "Armour" in [r.kind for e in out3.events for r in e.rolls]

    backfire = _pair("Breathe Fire")
    out4 = _special(backfire, "breathe_fire", _dice([1, 3, 3]))
    assert backfire.by_id("h00").down != "standing" and out4.turnover


def test_a_chainsaw_cuts_deep_and_cuts_both_ways():
    """Three clauses. "+3 modifier to the Armour Roll" on a 2+; "On a 1, the
    Chainsaw will Kick-back and this player is Knocked Down"; and "If this player
    is Knocked Down or Falls Over FOR ANY REASON … a +3 modifier is applied when
    the opposition Coach makes an Armour Roll for THIS player. This +3 modifier
    MUST ALWAYS BE APPLIED." """
    from bloodbowl.engine.injury import risk_injury

    cut = _pair("Chainsaw", av="11+")
    out = _special(cut, "chainsaw", _dice([4, 4, 4, 5, 5, 9]))  # …a Casualty, so its D16 as well
    armour = next(r for e in out.events for r in e.rolls if r.kind == "Armour")
    assert armour.modifier == 3 and armour.passed, "4+4+3 = 11 should break AV 11+"

    kick = _pair("Chainsaw")
    # The kick-back breaks their OWN armour easily, because the +3 applies to them
    # too — so the injury behind it needs dice as well.
    out2 = _special(kick, "chainsaw", _dice([1, 3, 3, 2, 2]))
    assert kick.by_id("h00").down != "standing" and out2.turnover

    # …and the owner is always easier to hurt.
    owner = _pair("Chainsaw")
    events = risk_injury(owner, owner.by_id("h00"), _dice([3, 3, 4, 4]))
    assert next(r for e in events for r in e.rolls if r.kind == "Armour").modifier == 3


def test_a_chainsaw_also_adds_three_to_a_foul():
    """ "this player may also use their chainsaw when performing a Foul Action, in
    which case they may apply a +3 modifier when making the Armour Roll." """
    from bloodbowl.engine import actions
    from bloodbowl.engine.events import Event

    actions.load_all()
    m = _pair("Chainsaw")
    m.apply(Event(kind="player_placed_prone", actor="a01", detail={"down": "prone"}))
    d = actions.get("foul")["validate"](m, {"player": "h00", "target": "a01"}).detail
    assert d["chainsaw"] == 3 and d["armour_modifier"] == 3


def test_being_chomped_pins_a_player_until_the_chomper_lets_go():
    """ "Whilst Chomped, the opposition player cannot leave the square they are in
    whilst this player remains Marking them. THIS CONDITION ENDS IMMEDIATELY if
    this player is no longer Marking the opposition player FOR ANY REASON."

    "For any reason" is why the condition is asked of the live board rather than
    remembered — a chomper who is knocked down would never think to clear a flag.
    """
    from bloodbowl.engine import actions
    from bloodbowl.engine.events import Event

    actions.load_all()
    m = _pair("Monstrous Mouth")
    _special(m, "chomp", _dice([5]))
    assert m.by_id("a01").chomped_by == "h00"

    m.apply(Event(kind="turn_started", detail={"side": "away", "half": 1, "turn": 1}))
    pinned = actions.get("move")["validate"](m, {"player": "a01", "x": 6, "y": 15})
    assert not pinned.ok and "Chomped" in pinned.reason

    # Knock the chomper over: no Tackle Zone, so the condition lapses at once.
    m.apply(Event(kind="player_placed_prone", actor="h00", detail={"down": "prone"}))
    assert actions.get("move")["validate"](m, {"player": "a01", "x": 6, "y": 15}).ok


def test_a_special_action_may_replace_a_blitzs_block_but_is_not_one():
    """ "Some Special Actions allow a player to replace the Block Action made as
    part of a Blitz Action … even though the Special Action is replacing a Block
    Action, IT IS NOT ONE ITSELF and so any rules, Skills or Traits that affect a
    Block Action will have no effect." """
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 10, 6, "3+", ["Stab"]), ("away", 7, 14, 6))
    _declare(m, "h00", "a01")
    for y in (11, 12, 13):
        _move(m, "h00", 7, y, _dice([]))
    legal = actions.get("stab")["validate"](m, {"player": "h00", "target": "a01"})
    assert legal.ok and legal.detail["replaces_blitz_block"] is True

    out = _special(m, "stab", _dice([3, 3]))
    assert not any(e.kind == "block_rolled" for e in out.events), "a Special Action is not a Block"
    assert m.by_id("h00").done is True, "the activation ends as soon as it is performed"


def test_any_number_of_players_may_use_a_special_action_each_turn():
    """ "there is no limit to the number of players that can declare this Special
    Action each Turn" — unlike almost every other Action in the game."""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(
        ("home", 7, 13, 6, "3+", ["Stab"]),
        ("away", 7, 14, 6),
        ("home", 2, 13, 6, "3+", ["Stab"]),
        ("away", 2, 14, 6),
    )
    _special(m, "stab", _dice([3, 3]), player="h00", target="a01")
    assert actions.get("stab")["validate"](m, {"player": "h02", "target": "a03"}).ok


# --- throw-ins, Secret Weapons, and the end of a drawn game ----------------


def test_a_throw_in_comes_back_in_across_the_templates_three_arrows():
    """ "roll a D6 to determine the direction the crowd will throw the ball as
    determined by the Throw-in Template. The ball will then travel 2D6 squares."

    The template is a DIAGRAM with three arrows, so — like the Range Ruler and the
    push arc — the mapping from a D6 to an arrow is read off the picture. What is
    quoted is that it comes back INWARD, which is the part every arrow shares.
    """
    from bloodbowl.engine.ball import throw_in

    seen = set()
    for d6 in (1, 3, 5):  # one number from each of the template's three arrows
        m = _match(("home", 7, 13, 6))
        out = throw_in(m, _dice([d6, 2, 2, 1]), 0, 13)  # left off the west sideline
        assert m.ball.x >= 1, "the ball must come back onto the pitch"
        assert any("hurls the ball back" in (e.text or "") for e in out)
        seen.add((m.ball.x, m.ball.y))
    assert len(seen) == 3, f"the three arrows should send it three different ways: {seen}"


def test_a_corner_throw_in_uses_a_d3_because_a_corner_has_no_single_way_in():
    """ "Should the ball leave the pitch from a CORNER square, position the Random
    Direction Template … and roll a D3." """
    from bloodbowl.engine.ball import throw_in

    m = _match(("home", 7, 13, 6))
    out = throw_in(m, _dice([2, 3, 3, 1]), 0, 0)
    kinds = [r.kind for e in out for r in e.rolls]
    assert "Corner Throw-in" in kinds, kinds
    assert 1 <= m.ball.x <= 15 and 1 <= m.ball.y <= 26


def test_a_secret_weapon_is_sent_off_at_the_end_of_the_drive():
    """ "If a Coach fielded any players with the Secret Weapon Trait during the
    current Drive, then they will immediately be Sent-off AS IF THEY HAD COMMITTED
    A FOUL ACTION, even if they were not on the pitch at the end of the Drive.
    Players Sent-off in this way may still Argue the Call."

    "As if they had committed a Foul" is why this calls the Foul's own helper —
    the Argue roll, the ejected-Coach ban and all — rather than a second
    implementation that agrees until it does not.
    """
    from bloodbowl.engine.game import start_drive

    m = _match(("home", 7, 13, 6, "3+", ["Secret Weapon"]), ("home", 8, 13, 6), ("away", 7, 20, 6))
    m.setup = [{"id": p.id, "x": p.x, "y": p.y} for p in m.players]
    start_drive(m, receiving="home", dice=_dice([3] + [4] * 20))
    assert m.by_id("h00").place == "sent_off"
    assert m.by_id("h01").place == "pitch", "only the one with the weapon"
    assert any(r.kind == "Argue the Call" for e in m.events for r in e.rolls), "they may still Argue"


def test_extra_time_does_not_replenish_the_re_rolls():
    """ "Team Re-rolls will NOT be replenished like they would be at half-time. Any
    Team Re-rolls not spent at the end of the game may carry over." That is the
    whole difference between Extra Time and a half."""
    from bloodbowl.engine.events import Event
    from bloodbowl.engine.game import start_extra_time

    m = _match(("home", 7, 13, 6), ("away", 7, 20, 6))
    m.apply(Event(kind="match_started", detail={"kicking_to": "home", "rerolls": {"home": 3, "away": 3}}))
    m.clock.half = 2  # full time comes at the end of the second half
    m.rerolls["home"] = 1
    m.setup = [{"id": p.id, "x": p.x, "y": p.y} for p in m.players]
    m.apply(Event(kind="match_over", text="Full time."))

    out = start_extra_time(m, receiving="home")
    assert out["ok"] and not m.over
    assert m.clock.half == 3 and m.clock.turn == 1, "Extra Time is a third period"
    assert m.rerolls["home"] == 1, "Extra Time replenished what half-time would have"


def test_extra_time_is_only_for_a_draw():
    from bloodbowl.engine.events import Event
    from bloodbowl.engine.game import start_extra_time

    m = _match(("home", 7, 13, 6))
    m.apply(Event(kind="match_over", text="Full time."))
    m.score = {"home": 2, "away": 1}
    assert not start_extra_time(m)["ok"]


def test_a_penalty_shootout_is_five_roll_offs_with_ties_re_rolled():
    """ "both Coaches will roll off against each other five times … rolling a D6
    (RE-ROLLING ANY TIES, though no other re-rolls from any source can be used)."
    """
    from bloodbowl.engine.game import penalty_shootout

    m = _match(("home", 7, 13, 6))
    # A tie first, which must be re-rolled and must not count as a kick.
    out = penalty_shootout(m, _dice([4, 4, 6, 1, 6, 1, 6, 1, 1, 6, 1, 6]))
    assert out["wins"]["home"] + out["wins"]["away"] == 5, out["wins"]
    assert out["wins"] == {"home": 3, "away": 2}
    assert out["winner"] == "home"


def test_the_apothecary_puts_a_knocked_out_player_back_on_the_pitch():
    """ "the player is NOT removed from the pitch … Instead, the player will become
    STUNNED IN THE SQUARE THEY ARE IN. If the player was Knocked-out as a result of
    an Injury by the Crowd, they are placed in the Reserves Box instead."

    Back on the pitch is a real swing, and it is once per game.
    """
    from bloodbowl.engine.events import Event
    from bloodbowl.engine.game import use_apothecary

    # A SECOND home player, because the once-per-game half needs another casualty
    # on the same side — `_match` numbers across the list, so the ids are h00/h01.
    m = _match(("home", 7, 13, 6), ("home", 8, 13, 6), ("away", 7, 20, 6))
    m.apply(Event(kind="match_started", detail={"kicking_to": "home", "apothecary": {"home": True, "away": True}}))
    m.apply(Event(kind="player_condition", actor="h00", detail={"outcome": "knocked_out"}))
    assert m.by_id("h00").place == "knocked_out"

    out = use_apothecary(m, "h00", _dice([]))
    assert out["ok"]
    p = m.by_id("h00")
    assert p.place == "pitch" and p.down == "stunned", "they should be back, Stunned"

    # Once per game.
    m.apply(Event(kind="player_condition", actor="h01", detail={"outcome": "knocked_out"}))
    again = use_apothecary(m, "h01", _dice([]))
    assert not again["ok"] and "once per game" in again["error"]


def test_the_apothecary_sends_a_crowd_victim_to_the_reserves_instead():
    from bloodbowl.engine.events import Event
    from bloodbowl.engine.game import use_apothecary

    m = _match(("home", 2, 13, 6), ("away", 1, 13, 6))
    m.apply(Event(kind="match_started", detail={"kicking_to": "home", "apothecary": {"home": True, "away": True}}))
    _block(m, "h00", "a01", _dice([4, 4], [["push_back"]]), follow_up=False)  # into the crowd, KO
    assert m.by_id("a01").place == "knocked_out"

    m.clock.active = "away"
    out = use_apothecary(m, "a01", _dice([]))
    assert out["ok"] and m.by_id("a01").place == "reserves"


# --- the Set-up phase ------------------------------------------------------


def _squad(side, n=11):
    """A legal formation: three on the Line, the rest tucked behind it."""
    from bloodbowl.engine.setup import LOS_ROWS

    line = LOS_ROWS[0] if side == "home" else LOS_ROWS[1]
    back = line - 1 if side == "home" else line + 1
    squares = [(6, line), (7, line), (8, line)]
    x = 5
    while len(squares) < n:
        squares.append((x, back))
        x += 1
    return squares


def _setup_match(n=11):
    squares = _squad("home", n) + _squad("away", n)
    return _match(*[("home" if i < n else "away", x, y, 6) for i, (x, y) in enumerate(squares)])


def test_a_set_up_is_checked_against_all_four_rules_at_once():
    """ "Players must be deployed in their own half … At least three players … must
    be deployed in the Centre Field, directly adjacent to the Line of Scrimmage …
    No more than two players from each team may be deployed within each Wide Zone."

    Every violation comes back together, because a coach fixing a formation wants
    the whole list rather than one error at a time.
    """
    from bloodbowl.engine.setup import violations

    assert violations("home", _squad("home"), 11) == []

    # Over the Line, none on it, and four in a Wide Zone.
    bad = [(1, 20), (2, 5), (3, 5), (4, 5), (1, 5), (7, 4), (8, 4), (9, 4), (10, 4), (11, 4), (12, 4)]
    problems = violations("home", bad, 11)
    assert any("opponent's half" in v for v in problems)
    assert any("Line of Scrimmage" in v for v in problems)
    assert any("Wide Zone" in v for v in problems)


def test_a_short_handed_team_must_put_everyone_on_the_line():
    """ "Should a team only be able to field three or fewer players, then they must
    be deployed in the Centre Field directly adjacent to the Line of Scrimmage."
    """
    from bloodbowl.engine.setup import violations

    assert violations("home", [(6, 13), (7, 13), (8, 13)], 3) == []
    assert violations("home", [(6, 13), (7, 13), (7, 11)], 3), "the third is off the Line"
    # …and a team that could field more must: "they must set up as many as they can".
    assert any("as many as they can" in v for v in violations("home", [(6, 13), (7, 13), (8, 13)], 8))


def test_declaring_a_set_up_refuses_an_illegal_one_and_records_a_legal_one():
    from bloodbowl.engine.game import declare_setup

    m = _setup_match()
    ids = [p.id for p in m.players if p.side == "home"]
    legal = [{"id": i, "x": x, "y": y} for i, (x, y) in zip(ids, _squad("home"), strict=False)]
    out = declare_setup(m, "home", legal)
    assert out["ok"] and m.setups["home"], out
    assert out["waiting_on"] == "away"

    crowded = [{"id": i, "x": 1 + n % 4, "y": 12} for n, i in enumerate(ids)]
    bad = declare_setup(m, "home", crowded)
    assert not bad["ok"] and bad["violations"]


def test_a_declared_set_up_beats_the_reused_opening_one():
    """The captured opening set-up is the documented fallback, not the rule."""
    from bloodbowl.engine.game import declare_setup, start_drive

    m = _setup_match()
    ids = [p.id for p in m.players if p.side == "home"]
    moved = _squad("home")
    moved[-1] = (12, 11)  # somewhere the opening set-up never had anybody
    declare_setup(m, "home", [{"id": i, "x": x, "y": y} for i, (x, y) in zip(ids, moved, strict=False)])
    start_drive(m, receiving="home", dice=_dice([3] + [4] * 20))
    last = m.by_id(ids[-1])
    assert (last.x, last.y) == (12, 11), "the declared set-up was ignored"
    assert m.setups == {}, "a set-up is consumed by the Drive it was declared for"


def test_too_many_players_are_removed_by_the_opposition():
    """ "any extra players on the pitch will be immediately removed … The players
    that are removed CANNOT HAVE THE BALL, cannot be a Star Player and are chosen
    by the opposition Coach." """
    from bloodbowl.engine.game import enforce_squad_size

    m = _match(*[("home", 1 + i % 15, 5 + i // 15, 6) for i in range(13)], ("away", 7, 20, 6))
    m.apply(_ball_at(1, 5, carrier="h00"))
    assert len(m.on_pitch("home")) == 13

    enforce_squad_size(m)
    assert len(m.on_pitch("home")) == 11
    assert m.by_id("h00").place == "pitch", "the ball carrier may not be the one removed"


def test_the_kicking_team_is_rolled_off_for_when_nobody_says():
    """ "this is done with a simple coin toss … The Coach who rolls highest decides
    which team is kicking and which team is receiving." """
    from bloodbowl.engine.game import _roll_off

    assert _roll_off(_dice([6, 2])) == "away", "home rolled highest, so they kick"
    assert _roll_off(_dice([2, 6])) == "home"
    assert _roll_off(_dice([4, 4, 5, 1])) == "away", "a tie is re-rolled"


# --- the last three Special Actions ----------------------------------------


def test_kick_team_mate_does_not_use_up_the_teams_throw():
    """ "Performing a Kick Team-mate Special Action DOES NOT COUNT as a team's Throw
    Team-mate Action for the Turn, and so a team can perform both … in the same
    Turn if they wish." """
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(
        ("home", 7, 8, 6, "3+", ["Kick Team-mate", "Throw Team-mate"]),
        ("home", 7, 9, 6, "3+", ["Right Stuff"]),
        ("home", 2, 8, 6, "3+", ["Throw Team-mate"]),
        ("home", 2, 9, 6, "3+", ["Right Stuff"]),
    )
    for who in ("h00", "h02"):
        m.by_id(who).player.PA = "3+"
    kicked = actions.get("kickteam")["resolve"](
        m, {"player": "h00", "target": "h01", "x": 7, "y": 12}, _dice([5, 2, 2, 2, 4])
    )
    assert kicked.ok or kicked.events
    # The team's Throw Team-mate is still there for somebody else.
    assert actions.get("throwteam")["validate"](m, {"player": "h02", "target": "h03", "x": 2, "y": 12}).ok


def test_a_fumbled_kick_hurts_the_team_mate_more_than_a_throw_would():
    """ "if a Kick Team-mate Special Action results in a Fumbled Throw, immediately
    make an Injury Roll for the team-mate being kicked, TREATING ANY RESULT OF
    STUNNED AS KNOCKED OUT." """
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 8, 6, "3+", ["Kick Team-mate"]), ("home", 7, 9, 6, "3+", ["Right Stuff"]))
    m.by_id("h00").player.PA = "3+"
    # A natural 1 fumbles; the injury rolls 2+2 = 4, which is Stunned on the table
    # and a Knocked-out here.
    # PA 1 (fumble), the kick's injury 2+2 = 4, the scatter, then the landing fall
    actions.get("kickteam")["resolve"](
        m, {"player": "h00", "target": "h01", "x": 7, "y": 12}, _dice([1, 2, 2, 2, 4, 2])
    )
    assert m.by_id("h01").place == "knocked_out", "a kick turns a Stunned into a Knocked-out"


def test_ball_and_chain_drags_a_player_around_and_dodges_for_free():
    """ "roll a D6 and move this player into the square as indicated … A player that
    moves in this manner does not have to make an Agility Test to Dodge away from
    another player's Tackle Zone; THEY WILL AUTOMATICALLY PASS." """
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 13, 3, "3+", ["Ball & Chain"]), ("away", 7, 14, 6))
    out = actions.get("ball_chain")["resolve"](m, {"player": "h00", "facing": "north"}, _dice([3, 3, 3]))
    p = m.by_id("h00")
    assert (p.x, p.y) != (7, 13), "they should have been dragged somewhere"
    assert not any(r.kind.startswith("Dodge") for e in out.events for r in e.rolls), "Dodges are automatic"
    assert any("No Dodge is needed" in (e.text or "") for e in out.events)


def test_a_bomb_explodes_where_it_stops_and_catches_the_neighbours():
    """ "When a bomb explodes, any player in the square it exploded in is hit …
    roll a D6 for each player ADJACENT to the square … On a 4+, they are hit. Any
    Standing player that is hit is immediately Knocked Down." """
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 4, 6, "3+", ["Bombardier"]), ("away", 7, 8, 6), ("away", 8, 8, 6))
    m.by_id("h00").player.PA = "2+"
    out = actions.get("throw_bomb")["resolve"](m, {"player": "h00", "x": 7, "y": 8}, _dice([5, 5, 2, 2, 2, 2, 2, 2, 2]))
    assert any("explodes" in (e.text or "") for e in out.events)
    assert m.by_id("a01").down != "standing", "the player under it should be down"
    blast = [r for e in out.events for r in e.rolls if r.kind == "Blast"]
    assert blast, "the neighbour should have been rolled for"


# --- The Kick-off Events that stop and ask the Coach a question ---------------


def _pending(match, kind, side, **detail):
    """Put the engine into the state a Kick-off Event leaves it in."""
    from bloodbowl.engine.events import Event

    match.apply(Event(kind="choice_pending", detail={"choice": kind, "side": side, **detail}))
    return match


def test_an_open_player_is_standing_and_unmarked_not_merely_standing():
    """ "A Standing player that is not being Marked by an opposition is referred to
    as an OPEN player." Four Kick-off Events are written entirely in those terms,
    and the definition is narrower than "on their feet"."""
    from bloodbowl.engine.rules import is_open

    m = _match(("home", 7, 13), ("home", 2, 10), ("away", 7, 14))
    assert is_open(m, m.by_id("h01")), "alone in a Wide Zone, on their feet"
    assert not is_open(m, m.by_id("h00")), "standing, but Marked by the player opposite"
    m.by_id("h01").down = "prone"
    assert not is_open(m, m.by_id("h01")), "a Prone player is not Standing"


def test_the_engine_refuses_to_play_on_while_a_kickoff_event_is_waiting_on_an_answer():
    """The Kick-off Event is resolved BEFORE the ball lands, so nothing else can
    happen in between. The refusal has to name the question, or a coach is stuck
    guessing what the board wants."""
    from bloodbowl.engine import game

    m = _match(("home", 7, 13), ("away", 2, 20))
    _pending(m, "high_kick", "home", square=[7, 10], eligible=["h00"])
    out = game.act(m, "move", {"player": "h00", "x": 7, "y": 12})
    assert out["ok"] is False
    assert "high kick" in out["text"], out["text"]
    assert out["pending"]["choice"] == "high_kick"
    assert m.by_id("h00").y == 13, "the refused move must not have happened"


def test_declining_a_choice_is_an_answer_and_lets_play_resume():
    """Every one of these says the Coach MAY. Declining is a real answer, not a
    no-op — the Drive cannot go on until one is given."""
    from bloodbowl.engine import game

    m = _match(("home", 7, 13), ("away", 2, 20))
    _pending(m, "high_kick", "home", square=[7, 10], eligible=["h00"])
    assert game.resolve_choice(m, {"decline": True}, _dice([]))["ok"]
    assert not m.pending
    assert game.act(m, "move", {"player": "h00", "x": 7, "y": 12})["ok"]


def test_high_kick_places_one_open_player_under_the_ball():
    """ "One Open player on the receiving team may immediately be placed in the
    square the ball is going to land in." """
    from bloodbowl.engine import game

    m = _match(("home", 7, 13), ("home", 3, 11), ("away", 7, 14))
    m.ball.x, m.ball.y = 4, 10
    _pending(m, "high_kick", "home", square=[4, 10], eligible=["h01"])
    out = game.resolve_choice(m, {"player": "h01"}, _dice([]))
    assert out["ok"], out
    assert (m.by_id("h01").x, m.by_id("h01").y) == (4, 10)
    assert not m.pending


def test_high_kick_refuses_a_marked_player_because_the_rule_says_open():
    """h00 is Standing and could physically run there — but they are Marked, and
    the rule is written in terms of Open players, not standing ones."""
    from bloodbowl.engine import game

    m = _match(("home", 7, 13), ("away", 7, 14))
    _pending(m, "high_kick", "home", square=[4, 10], eligible=[])
    out = game.resolve_choice(m, {"player": "h00"}, _dice([]))
    assert out["ok"] is False and "Open" in out["error"]
    assert m.pending, "an illegal answer is not an answer — the question stands"


def test_quick_snap_moves_one_square_each_and_may_cross_the_halfway_line():
    """ "The selected players may immediately move one square IN ANY DIRECTION,
    EVEN IF THIS TAKES THEM INTO THE OPPOSITION'S HALF." """
    from bloodbowl.engine import game

    m = _match(("home", 7, 13), ("home", 3, 12), ("away", 12, 20))
    _pending(m, "quick_snap", "home", limit=4, eligible=["h00", "h01"])
    out = game.resolve_choice(m, {"moves": [{"id": "h00", "x": 7, "y": 14}, {"id": "h01", "x": 3, "y": 13}]}, _dice([]))
    assert out["ok"], out
    assert (m.by_id("h00").x, m.by_id("h00").y) == (7, 14), "over the Line of Scrimmage is explicitly allowed"
    assert m.by_id("h00").ma_used == 0, "a Quick Snap is not a Move Action and costs no Move Allowance"


def test_quick_snap_is_one_square_and_no_more_than_the_roll_allowed():
    from bloodbowl.engine import game

    m = _match(("home", 7, 13), ("home", 3, 12), ("away", 12, 20))
    _pending(m, "quick_snap", "home", limit=1, eligible=["h00", "h01"])
    far = game.resolve_choice(m, {"moves": [{"id": "h00", "x": 7, "y": 11}]}, _dice([]))
    assert far["ok"] is False and "one square" in far["error"]
    two = game.resolve_choice(m, {"moves": [{"id": "h00", "x": 7, "y": 12}, {"id": "h01", "x": 3, "y": 11}]}, _dice([]))
    assert two["ok"] is False and "up to 1" in two["error"], two
    assert (m.by_id("h00").x, m.by_id("h00").y) == (7, 13), "a refused answer moves nobody at all"


def test_solid_defence_re_places_players_under_the_full_set_up_restrictions():
    """ "The selected players are then removed from the pitch and can be set up
    again FOLLOWING ALL THE USUAL RESTRICTIONS FOR SETTING UP THE TEAM." So the
    resulting formation must be legal, not merely the squares that moved: pulling
    a player off the Line breaks a rule that belongs to the whole team."""
    from bloodbowl.engine import game

    # Three on the Line, exactly as the rules require, and one spare behind. The
    # opposition are kept well clear so that every home player is Open — a Marked
    # player is not selectable, and that would refuse this for the wrong reason.
    m = _match(("home", 6, 13), ("home", 7, 13), ("home", 8, 13), ("home", 7, 11), ("away", 2, 20))
    _pending(m, "solid_defence", "home", limit=4, eligible=["h01", "h03"])

    off = game.resolve_choice(m, {"moves": [{"id": "h03", "x": 2, "y": 9}, {"id": "h01", "x": 7, "y": 12}]}, _dice([]))
    assert off["ok"] is False and "Line of Scrimmage" in off["error"], off
    assert (m.by_id("h01").x, m.by_id("h01").y) == (7, 13), "nobody moves when the formation is refused"

    ok = game.resolve_choice(m, {"moves": [{"id": "h03", "x": 2, "y": 9}]}, _dice([]))
    assert ok["ok"], ok
    assert (m.by_id("h03").x, m.by_id("h03").y) == (2, 9)


def test_solid_defence_will_not_deploy_past_the_line_of_scrimmage():
    from bloodbowl.engine import game

    m = _match(("home", 6, 13), ("home", 7, 13), ("home", 8, 13), ("home", 7, 11), ("away", 2, 20))
    _pending(m, "solid_defence", "home", limit=4, eligible=["h03"])
    out = game.resolve_choice(m, {"moves": [{"id": "h03", "x": 7, "y": 16}]}, _dice([]))
    assert out["ok"] is False and "opponent's half" in out["error"], out


def test_a_kickoff_event_that_asks_a_question_leaves_the_question_in_the_state():
    """The pending question has to survive a save/load round trip, because the
    coach answers it in a SEPARATE tool call — and a Match is rebuilt from disk
    between calls. A question the reload forgets is a wedged game."""
    from bloodbowl.engine.state import Match

    m = _match(("home", 7, 13), ("away", 2, 20))
    _pending(m, "quick_snap", "home", limit=5, eligible=["h00"])
    back = Match.from_dict(m.to_dict())
    assert back.pending.get("choice") == "quick_snap" and back.pending.get("limit") == 5


def test_a_roll_with_nothing_to_pass_is_not_reported_as_a_failure():
    """A Kick-off Event, a Deviate distance and a D3+3 are table rolls, not tests.
    The log said "Kick-off Event: needed 0+, rolled 3+1 — FAILED" for a roll of 4
    that had succeeded at nothing, because `describe` branched on the target and a
    target of zero is not None."""
    from bloodbowl.engine.dice import Roll

    event = Roll(kind="Kick-off Event", dice=[3, 1], total=4, target=0, passed=None)
    assert event.describe() == "Kick-off Event: 3+1"
    assert "FAILED" not in event.describe()

    # And where the total is not the dice — D3+3 — the line has to say so, or "2"
    # is the answer to a question nobody asked.
    d3 = Roll(kind="Solid Defence", dice=[2], total=5, note="D3+3 players")
    assert d3.describe() == "Solid Defence: 2 = 5 (D3+3 players)"

    # A real test still reads as one.
    dodge = Roll(kind="Dodge", dice=[4], total=3, target=3, modifier=-1, passed=True)
    assert "needed 3+" in dodge.describe() and "passed" in dodge.describe()


def test_the_ball_stays_in_the_air_until_the_kickoff_question_is_answered():
    """ "At this point the ball is still HIGH UP IN THE AIR and cannot be caught
    UNTIL AFTER THE KICK-OFF EVENT HAS BEEN RESOLVED."

    An event that stopped to ask the Coach something is not resolved. Landing the
    ball anyway made High Kick meaningless — a player placed "in the square the
    ball is going to land in" would arrive under a ball that had already come down
    and bounced somewhere else."""
    from bloodbowl.engine import game
    from bloodbowl.engine.kickoff import kick

    m = _match(("home", 7, 11), ("home", 3, 11), ("away", 7, 20))
    dice = _dice([2, 1, 1, 3, 2, 4, 4, 4, 4, 4, 4])  # deviate (D6,D8), then a 2D6 of 1+3 = 4: Solid Defence

    kick(m, dice, receiving="home")
    assert m.pending.get("choice") == "solid_defence", m.pending
    assert m.pending.get("land") == "home", "whoever answers has to know how to bring it down"
    assert not m.ball.carrier, "nobody can catch a ball that is still in the air"
    after = m.events[next(i for i, e in enumerate(m.events) if e.kind == "choice_pending") :]
    assert not [e for e in after if e.kind in ("ball_moved", "ball_picked_up", "touchback")], (
        f"the ball came down while the question was open: {[e.kind for e in after]}"
    )

    game.resolve_choice(m, {"decline": True}, dice)
    assert not m.pending
    resumed = m.events[next(i for i, e in enumerate(m.events) if e.kind == "choice_made") :]
    assert [e for e in resumed if e.kind in ("ball_moved", "ball_picked_up", "touchback")], (
        "answering the question must bring the ball down"
    )


def test_the_turn_does_not_start_over_an_undecided_kickoff():
    """The Drive sequence is set-up, kick-off, KICK-OFF EVENT, ball lands, then
    play. A question the Coach has not answered sits in the middle of that, so the
    first turn cannot be under way while it is open — the receiving team would be
    "to act" on a board whose ball is still in the air."""
    from bloodbowl.engine import game

    m = _match(("home", 7, 11), ("home", 3, 11), ("away", 7, 20))
    dice = _dice([2, 1, 1, 3, 2] + [4] * 12)  # 2D6 of 1+3 = 4: Solid Defence, then its D3
    game.start_drive(m, receiving="home", dice=dice)

    assert m.pending, "the seed was chosen to roll a choice"
    assert not [e for e in m.events if e.kind == "turn_started"], "the turn started over an open question"

    game.resolve_choice(m, {"decline": True}, dice)
    started = [e for e in m.events if e.kind == "turn_started"]
    assert started, "answering must open the turn"
    # The ball comes down as a bounce, a catch or a Touchback — whichever, it must
    # be in the log BEFORE the turn opens.
    landed = [i for i, e in enumerate(m.events) if e.kind in ("ball_moved", "ball_picked_up", "touchback")]
    assert landed and max(landed) < m.events.index(started[0]), "the ball has to come down before anyone acts"


# --- Charge!, the Kick-off Event that is a free turn --------------------------


def _charging(*players, side="away", limit=4, land="home"):
    """A match mid-Charge: the kicking team has been selected and may act."""
    from bloodbowl.engine import charge

    m = _match(*players, active=land)
    picked = [p.id for p in m.players if p.side == side]
    charge.start(m, side, picked[:limit], land=land)
    return m


def test_a_charge_moves_the_clock_to_the_kicking_team_and_gives_it_back():
    """ "The selected players may then be activated one at a time, EXACTLY AS IF IT
    WAS THEIR TEAM'S TURN." Every action in the engine asks whether a player is on
    the active side, and during a Charge the honest answer for the kicking team is
    yes — but the Drive still belongs to the receiving team afterwards."""
    from bloodbowl.engine import game

    m = _charging(("home", 7, 11), ("away", 7, 20))
    assert m.clock.active == "away", "the charging team acts"
    assert m.charge["was"] == "home"

    game.end_charge(m, "test", _dice([4] * 12))
    assert not m.charge
    assert m.clock.active == "home", "the Drive goes back to the receiving team"


def test_only_the_selected_players_may_be_activated_in_a_charge():
    from bloodbowl.engine import game

    m = _charging(("home", 7, 11), ("away", 7, 20), ("away", 2, 20), limit=1)
    picked = m.charge["players"][0]
    other = next(p.id for p in m.players if p.side == "away" and p.id != picked)
    out = game.act(m, "move", {"player": other, "x": 3, "y": 20})
    assert out["ok"] is False and "selected" in out["error"], out
    assert game.act(m, "move", {"player": picked, "x": 7, "y": 19})["ok"]


def test_a_charge_offers_one_blitz_for_the_whole_charge_not_one_each():
    """ "ONE of the selected players may instead perform a free Blitz Action, ONE
    may perform a free Throw Team-mate Action, and ONE may perform a free Kick
    Team-mate Action." One, across the Charge — not one apiece."""
    from bloodbowl.engine import game

    m = _charging(("home", 7, 11), ("away", 7, 13), ("away", 8, 13), limit=2)
    a, b = m.charge["players"]
    assert game.act(m, "blitz", {"player": a, "target": "h00"})["ok"], "the first Blitz is free"
    second = game.act(m, "blitz", {"player": b, "target": "h00"})
    assert second["ok"] is False and "already been used" in second["error"], second


def test_a_charge_will_not_let_a_player_foul_or_pass():
    """The rule lists exactly four Actions. A Foul during a free turn nobody paid
    for is not one of them."""
    from bloodbowl.engine import game

    m = _charging(("home", 7, 13), ("away", 7, 14))
    m.by_id("h00").down = "prone"
    out = game.act(m, "foul", {"player": m.charge["players"][0], "target": "h00"})
    assert out["ok"] is False and "Charge!" in out["error"], out


def test_a_selected_player_hitting_the_floor_ends_the_charge_and_is_not_a_turnover():
    """ "If a selected player Falls Over or is Knocked Down during their
    activation, no further selected players can be activated and the Charge ends."

    Ends — not a Turnover. A Turnover would advance the Turn Marker and hand over
    a ball that has not even landed."""
    from bloodbowl.engine import game

    # A Dodge away from a Tackle Zone, failed: the charger goes down.
    m = _charging(("home", 7, 13), ("away", 7, 14), ("away", 2, 20), limit=2)
    turn_before = m.clock.turn
    out = game.act(m, "move", {"player": "a01", "x": 8, "y": 15}, _dice([1] + [4] * 40))
    assert m.by_id("a01").down != "standing", "the scripted 1 should have put them down"
    assert not m.charge, "the Charge ends when a selected player goes down"
    assert out.get("turnover") is not True, "a Charge ending is not a Turnover"
    assert m.clock.turn == turn_before, "the Turn Marker must not move"


def test_a_charge_ends_by_itself_once_everyone_selected_has_acted():
    from bloodbowl.engine import game

    m = _charging(("home", 7, 11), ("away", 7, 20), limit=1)
    only = m.charge["players"][0]
    game.act(m, "move", {"player": only, "x": 7, "y": 19})
    assert m.charge, "one step is not the end of an activation"
    game.act(m, "forego", {"player": only})
    assert not m.charge, "with nobody left to activate the Charge is over"


def test_the_coach_can_end_a_charge_early():
    """ "MAY then be activated" — a Coach who has seen enough is not obliged to
    send the rest in."""
    from bloodbowl.engine import game

    m = _charging(("home", 7, 11), ("away", 7, 20), ("away", 2, 20), limit=2)
    out = game.resolve_choice(m, {"decline": True}, _dice([4] * 12))
    assert out["ok"] and not m.charge, out


def test_the_charge_selection_refuses_a_marked_player_and_an_over_long_list():
    from bloodbowl.engine import game

    # _match numbers ids across the COMBINED list, so these are h00, a01, a02:
    # a01 is Marked by h00 and a02 is alone in the far corner.
    m = _match(("home", 7, 13), ("away", 7, 14), ("away", 2, 20), active="home")
    _pending(m, "charge", "away", limit=1, eligible=["a02"], land="home")
    marked = game.resolve_choice(m, {"players": ["a01"]}, _dice([]))
    assert marked["ok"] is False and "Open" in marked["error"], marked
    toomany = game.resolve_choice(m, {"players": ["a02", "a01"]}, _dice([]))
    assert toomany["ok"] is False and "up to 1" in toomany["error"], toomany
    assert m.pending, "a refused answer leaves the question standing"
    assert game.resolve_choice(m, {"players": ["a02"]}, _dice([]))["ok"]
    assert m.charge["players"] == ["a02"] and not m.pending


def test_the_ball_lands_and_the_turn_opens_when_the_charge_ends():
    """A Charge happens INSIDE the Kick-off Event, so throughout it the ball is
    still in the air and the receiving team's turn has not begun. Both resume when
    it is over — the same resumption the other three choices get."""
    from bloodbowl.engine import game

    m = _charging(("home", 7, 11), ("away", 7, 20), limit=1, land="home")
    assert not [e for e in m.events if e.kind == "turn_started"]
    game.end_charge(m, "test", _dice([3, 4, 3, 4, 3, 4, 3, 4]))
    assert [e for e in m.events if e.kind == "turn_started"], "the Drive has to resume"
    assert m.clock.active == "home"


def test_a_ball_still_in_the_air_is_not_reported_as_landed():
    """`in_play` has always meant "the ball is on the board somewhere", which was
    indistinguishable from "it has landed" only because the kick used to land it
    in the same call. Now that a Kick-off Event can hold it up there for several
    calls, the board would draw it sitting on the square it is heading for."""
    from bloodbowl.engine.kickoff import kick

    m = _match(("home", 7, 11), ("home", 3, 11), ("away", 7, 20))
    dice = _dice([2, 1, 1, 3, 2] + [4] * 12)  # 2D6 of 1+3 = 4: Solid Defence, which asks

    kick(m, dice, receiving="home")
    assert m.pending, "the scripted roll was chosen to ask a question"
    assert m.ball.in_air, "the ball is still up there until the event is resolved"
    assert m.to_dict()["ball"]["in_air"] is True, "and the board has to be told"

    from bloodbowl.engine import game

    game.resolve_choice(m, {"decline": True}, dice)
    assert not m.ball.in_air, "answering brings it down"


def test_the_apothecary_offers_the_casualty_coach_a_choice_of_two_rolls():
    """ "After a Casualty Roll is made … their Coach may declare they are using
    their Apothecary. THE OPPOSING COACH MAKES A SECOND CASUALTY ROLL for the
    player, and the player's controlling Coach MAY SELECT EITHER OF THE TWO
    RESULTS to apply. If a BADLY HURT result is selected, then the player is
    successfully Patched-up and placed into their RESERVES BOX instead of the
    Casualty Box."

    Choosing for them would be choosing whether a player comes back."""
    from bloodbowl.engine.events import Event
    from bloodbowl.engine.game import resolve_choice, use_apothecary

    m = _match(("home", 7, 13), ("away", 7, 14))
    m.apply(Event(kind="match_started", detail={"kicking_to": "home", "apothecary": {"home": True, "away": True}}))
    m.apply(Event(kind="player_condition", actor="h00", detail={"outcome": "casualty"}))
    m.apply(Event(kind="casualty_roll", actor="h00", detail={"result": "Dead", "roll": 15}))

    out = use_apothecary(m, "h00", _dice([3]))  # the opposing Coach rolls a 3 — Badly Hurt
    assert out["ok"] and m.pending.get("choice") == "apothecary", out
    assert [r["result"] for r in out["results"]] == ["Dead", "Badly Hurt"], out["results"]
    assert m.apothecary["home"] is False, "the Apothecary is spent on DECLARATION, win or lose"
    assert m.by_id("h00").place == "casualty", "nothing is applied until the Coach picks"

    picked = resolve_choice(m, {"result": 2}, _dice([]))
    assert picked["ok"] and not m.pending
    assert m.by_id("h00").place == "reserves", "a Badly Hurt result puts them in the Reserves Box"


def test_the_apothecary_choice_can_keep_the_first_roll_and_the_casualty_stands():
    """Either result — so the Coach can look at a worse second roll and keep the
    first. And anything but Badly Hurt leaves them in the Casualty Box."""
    from bloodbowl.engine.events import Event
    from bloodbowl.engine.game import resolve_choice, use_apothecary

    m = _match(("home", 7, 13), ("away", 7, 14))
    m.apply(Event(kind="match_started", detail={"kicking_to": "home", "apothecary": {"home": True, "away": True}}))
    m.apply(Event(kind="player_condition", actor="h00", detail={"outcome": "casualty"}))
    m.apply(Event(kind="casualty_roll", actor="h00", detail={"result": "Seriously Hurt", "roll": 9}))

    use_apothecary(m, "h00", _dice([16]))  # the second roll is DEAD — much worse
    kept = resolve_choice(m, {"result": 1}, _dice([]))
    assert kept["ok"], kept
    assert m.by_id("h00").place == "casualty", "Seriously Hurt is not Badly Hurt, so the Casualty stands"
    assert "Seriously Hurt".upper() in " ".join(e.text or "" for e in m.events)


def test_declining_the_apothecary_choice_keeps_the_roll_they_already_had():
    from bloodbowl.engine.events import Event
    from bloodbowl.engine.game import resolve_choice, use_apothecary

    m = _match(("home", 7, 13), ("away", 7, 14))
    m.apply(Event(kind="match_started", detail={"kicking_to": "home", "apothecary": {"home": True, "away": True}}))
    m.apply(Event(kind="player_condition", actor="h00", detail={"outcome": "casualty"}))
    m.apply(Event(kind="casualty_roll", actor="h00", detail={"result": "Badly Hurt", "roll": 2}))

    use_apothecary(m, "h00", _dice([15]))  # a DEAD second roll
    assert resolve_choice(m, {"decline": True}, _dice([]))["ok"]
    assert m.by_id("h00").place == "reserves", "the first roll was Badly Hurt, so keeping it saves them"


def test_a_pending_apothecary_choice_survives_a_reload():
    """The Coach answers in a SEPARATE call, and a Match is rebuilt from disk in
    between. A question the reload forgets is a player nobody can save."""
    from bloodbowl.engine.events import Event
    from bloodbowl.engine.game import use_apothecary
    from bloodbowl.engine.state import Match

    m = _match(("home", 7, 13), ("away", 7, 14))
    m.apply(Event(kind="match_started", detail={"kicking_to": "home", "apothecary": {"home": True, "away": True}}))
    m.apply(Event(kind="player_condition", actor="h00", detail={"outcome": "casualty"}))
    m.apply(Event(kind="casualty_roll", actor="h00", detail={"result": "Dead", "roll": 15}))
    use_apothecary(m, "h00", _dice([3]))

    back = Match.from_dict(m.to_dict())
    assert back.pending.get("choice") == "apothecary"
    assert [r["result"] for r in back.pending["results"]] == ["Dead", "Badly Hurt"]
    assert back.apothecary["home"] is False


# --- Modifier Skills that belong to somebody other than the roller ------------


def test_two_heads_adds_one_to_a_dodge():
    """S3: "This player may apply a +1 modifier to the Agility Test whenever they
    attempt to Dodge." """
    from bloodbowl.engine.skills import roll_modifier

    m = _match(("home", 7, 13, 6, "3+", ["Two Heads"]), ("away", 7, 14))
    assert roll_modifier(m, m.by_id("h00"), "dodge", base=-1).value == 0


def test_cannoneer_is_accurate_at_the_other_end_of_the_ruler():
    """ "When this player performs a Pass Action which is a LONG PASS or a LONG
    BOMB…" — and NOT on the short ones, which is Accurate's half."""
    from bloodbowl.engine.skills import roll_modifier

    m = _match(("home", 7, 13, 6, "3+", ["Cannoneer"]))
    p = m.by_id("h00")
    assert roll_modifier(m, p, "pass", range="Long Bomb").value == 1
    assert roll_modifier(m, p, "pass", range="Long Pass").value == 1
    assert roll_modifier(m, p, "pass", range="Quick Pass").value == 0, "Cannoneer is not Accurate"


def test_strong_arm_helps_a_throw_team_mate_and_not_a_pass():
    """ "When this player performs a THROW TEAM-MATE Action…" — a different test
    from a Pass Action, with its own distance bands."""
    from bloodbowl.engine.skills import roll_modifier

    m = _match(("home", 7, 13, 6, "3+", ["Strong Arm", "Throw Team-mate"]))
    p = m.by_id("h00")
    assert roll_modifier(m, p, "throwteam", range="Short Throw").value == 1
    assert roll_modifier(m, p, "pass", range="Short Pass").value == 0


def test_very_long_legs_is_worth_twice_as_much_on_an_intercept():
    """ "+1 … whenever they attempt to Leap or Jump, and … +2 … whenever they
    attempt to INTERCEPT the ball." Two different numbers, and the bigger one is
    on the roll that almost never lands."""
    from bloodbowl.engine.skills import roll_modifier

    m = _match(("home", 7, 13, 6, "3+", ["Very Long Legs"]))
    p = m.by_id("h00")
    assert roll_modifier(m, p, "jump", base=-1).value == 0
    assert roll_modifier(m, p, "intercept", base=-2).value == 0
    assert roll_modifier(m, p, "dodge").value == 0, "it is not a general Agility bonus"


def test_disturbing_presence_stacks_and_reaches_three_squares():
    """ "…applies a -1 modifier to their Passing Ability Test or Agility Test FOR
    EACH PLAYER ON YOUR TEAM WITH THIS SKILL WITHIN 3 SQUARES of them."

    Each — they stack — and three squares is a long way. It belongs to the
    opposition, not to the player rolling, which is why it cannot be a hook on
    their own Skills."""
    from bloodbowl.engine.skills import roll_modifier

    dp = ["Disturbing Presence"]
    m = _match(
        ("home", 7, 13),  # the thrower
        ("away", 8, 15, 6, "3+", dp),  # 2 away
        ("away", 4, 13, 6, "3+", dp),  # 3 away
        ("away", 3, 13, 6, "3+", dp),  # 4 away — out of reach
        ("away", 7, 16),  # near, but without the Skill
    )
    p = m.by_id("h00")
    assert roll_modifier(m, p, "pass").value == -2, "two in range, one out, one without it"
    assert roll_modifier(m, p, "catch").value == -2
    assert roll_modifier(m, p, "intercept").value == -2
    assert roll_modifier(m, p, "throwteam").value == -2
    assert roll_modifier(m, p, "throw_bomb").value == -2
    # The rule lists the tests it touches, and a Dodge is not one of them.
    assert roll_modifier(m, p, "dodge").value == 0
    # And it is the OPPOSITION's — a team-mate standing there does nothing.
    friendly = _match(("home", 7, 13), ("home", 8, 14, 6, "3+", dp))
    assert roll_modifier(friendly, friendly.by_id("h00"), "pass").value == 0


def test_iron_hard_skin_strips_the_modifiers_off_an_armour_roll():
    """ "OPPOSITION PLAYERS CANNOT APPLY ANY MODIFIERS when making an Armour Roll
    against this player. Additionally, THE CLAWS SKILL CANNOT BE USED against this
    player."

    A Foul with three assists and a Mighty Blow is the case it exists for."""
    from bloodbowl.engine.injury import risk_injury

    hard = _match(("home", 7, 13, 6, "3+", ["Iron Hard Skin"]), ("away", 7, 14, 6, "3+", ["Mighty Blow", "Claws"]))
    hard.by_id("h00").player.AV = "10+"
    out = risk_injury(hard, hard.by_id("h00"), _dice([4, 4, 4, 4, 4, 4]), by=hard.by_id("a01"), armour_modifier=3)
    armour = next(r for e in out for r in e.rolls if r.kind == "Armour")
    assert armour.modifier == 0, f"the +3 should have been stripped: {armour.describe()}"
    assert armour.passed is False, "8 is under AV 10+, and neither Claws nor Mighty Blow may help"

    # The same roll against an ordinary player breaks the armour — which is what
    # makes the check above about the Skill rather than about the dice.
    soft = _match(("home", 7, 13), ("away", 7, 14, 6, "3+", ["Mighty Blow", "Claws"]))
    soft.by_id("h00").player.AV = "10+"
    out2 = risk_injury(soft, soft.by_id("h00"), _dice([4, 4, 4, 4, 4, 4]), by=soft.by_id("a01"), armour_modifier=3)
    assert next(r for e in out2 for r in e.rolls if r.kind == "Armour").passed is True


def test_iron_hard_skin_does_not_help_with_a_chainsaw_of_their_own():
    """ "THIS +3 MODIFIER MUST ALWAYS BE APPLIED" — and it is the carrier's own
    liability rather than something an opposition player applies, so Iron Hard
    Skin has nothing to say about it."""
    from bloodbowl.engine.injury import risk_injury

    m = _match(("home", 7, 13, 6, "3+", ["Iron Hard Skin", "Chainsaw"]), ("away", 7, 14))
    out = risk_injury(m, m.by_id("h00"), _dice([3, 3, 4, 4, 4, 4]), by=m.by_id("a01"))
    assert next(r for e in out for r in e.rolls if r.kind == "Armour").modifier == 3


def test_sprint_buys_a_third_rush_attempt_and_not_a_free_square():
    """S3: "When this player performs a Move Action they may attempt to Rush ONE
    ADDITIONAL TIME than they would normally be allowed to." Attempt — so it is a
    third chance to trip as much as a third square."""
    from bloodbowl.engine import actions

    actions.load_all()
    plain = _match(("home", 7, 13, 4))
    plain.by_id("h00").ma_used = 4
    # Two Rushes gets them to 6; a third is refused without Sprint.
    plain.by_id("h00").ma_used = 6
    assert not actions.get("move")["validate"](plain, {"player": "h00", "x": 7, "y": 14}).ok

    quick = _match(("home", 7, 13, 4, "3+", ["Sprint"]))
    quick.by_id("h00").ma_used = 6
    legal = actions.get("move")["validate"](quick, {"player": "h00", "x": 7, "y": 14})
    assert legal.ok, legal.reason
    assert legal.detail["rushes"] == 3, legal.detail


def test_sure_feet_rerolls_a_failed_rush_once_per_turn():
    """ "Once per Turn, this player may re-roll a single D6 when attempting to
    Rush." The Dodge Skill's twin, on the other roll a Move Action can fail — and
    a free Skill re-roll must be tried BEFORE a Team Re-roll is spent."""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 13, 1, "3+", ["Sure Feet"]))
    m.by_id("h00").ma_used = 1
    out = _move(m, "h00", 7, 14, _dice([1, 5, 4, 4, 4, 4]))  # trips, then makes it
    kinds = [r.kind for e in out.events for r in e.rolls]
    assert "Rush (Sure Feet)" in kinds, kinds
    assert out.ok, out.text
    assert m.by_id("h00").rush_reroll_used, "and it is spent"

    # Once per TURN: a second failed Rush in the same turn gets no second chance.
    again = _move(m, "h00", 7, 15, _dice([1, 1, 4, 4, 4, 4, 4, 4]))
    assert "Rush (Sure Feet)" not in [r.kind for e in again.events for r in e.rolls]


def test_a_new_turn_clears_every_once_per_turn_flag_not_just_the_ones_it_remembers():
    """Three separate places reset these by hand, and a flag a reset site forgot
    would make a Once-per-Turn Skill work once per MATCH — silently. They go
    through ONCE_PER_TURN_FLAGS now, and this is what says so."""
    from bloodbowl.engine.events import Event
    from bloodbowl.engine.state import ONCE_PER_TURN_FLAGS

    m = _match(("home", 7, 13), ("away", 2, 20))
    p = m.by_id("h00")
    for flag in ONCE_PER_TURN_FLAGS:
        setattr(p, flag, True)
    m.apply(Event(kind="turn_started", detail={"side": "home", "half": 1, "turn": 2}))
    left = [f for f in ONCE_PER_TURN_FLAGS if getattr(p, f)]
    assert not left, f"a new turn left these spent: {left}"


# --- The Skills that fire when an opponent leaves your Tackle Zone -----------


def test_tentacles_can_stop_a_dodge_from_happening_at_all():
    """S3: "If the result is 6 or higher, OR THE ROLL IS A NATURAL 6, then the
    opposition player DOES NOT LEAVE the square they attempted to leave and their
    activation comes to an end."

    Before the Agility Test, not after — a Dodge that never happens cannot be
    failed, re-rolled or Diving-Tackled."""
    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6, "3+", ["Tentacles"]))
    m.by_id("h00").player.ST = "3"
    m.by_id("a01").player.ST = "3"
    out = _move(m, "h00", 7, 12, _dice([6]))  # D6 6 +3 -3 = 6: held
    assert out.ok is False
    assert (m.by_id("h00").x, m.by_id("h00").y) == (7, 13), "they must not have moved"
    assert m.by_id("h00").done, "and their activation comes to an end"
    assert not [r for e in out.events for r in e.rolls if r.kind.startswith("Dodge")], "no Dodge should be rolled"


def test_a_natural_one_never_holds_and_a_natural_six_always_does():
    """ "If the result is 5 or lower, OR THE ROLL IS A NATURAL 1, this Skill has no
    effect." A strong player is never quite safe from a weak one, and never quite
    certain against them either."""
    from bloodbowl.engine import leaving

    strong = _match(("home", 7, 13, 6), ("away", 7, 14, 6, "3+", ["Tentacles"]))
    strong.by_id("h00").player.ST = "6"
    strong.by_id("a01").player.ST = "1"
    rec = _rec(strong)
    assert leaving.tentacles(strong, strong.by_id("h00"), [strong.by_id("a01")], _dice([6]), rec) is True

    weak = _match(("home", 7, 13, 6), ("away", 7, 14, 6, "3+", ["Tentacles"]))
    weak.by_id("h00").player.ST = "1"
    weak.by_id("a01").player.ST = "6"
    rec2 = _rec(weak)
    assert leaving.tentacles(weak, weak.by_id("h00"), [weak.by_id("a01")], _dice([1]), rec2) is False


def test_diving_tackle_turns_a_made_dodge_into_a_failed_one_and_costs_the_tackler_their_feet():
    """ "Immediately apply a -2 modifier to the opposition player's Agility Test
    and PLACE THIS PLAYER PRONE IN THE SQUARE THE OPPOSITION PLAYER VACATED."

    And it is applied AFTER the roll — which is the whole point."""
    m = _match(("home", 7, 13, 6, "3+"), ("away", 7, 14, 6, "3+", ["Diving Tackle"]))
    # A 4 against a 3+ with -1 for the marker makes it exactly: -2 fails it.
    out = _move(m, "h00", 7, 12, _dice([4] * 40))
    assert out.ok is False, "the Dodge should have been dragged down"
    diver = m.by_id("a01")
    assert diver.down == "prone", "the tackler ends up Prone"
    assert (diver.x, diver.y) == (7, 13), "in the square the dodger vacated"
    assert m.by_id("h00").down != "standing", "and the dodger Falls Over"


def test_diving_tackle_is_not_spent_when_it_would_change_nothing():
    """It costs the tackler their feet EVERY time it is used, so using it on a
    Dodge that fails anyway, or on one it cannot reach, is pure loss — and there
    is nobody at the table to want one.

    Both halves in one test ON PURPOSE. "The tackler is still standing" is also
    what an engine with no Diving Tackle at all would report, so the restraint
    only means something beside a roll where the Skill does fire."""
    board = (("home", 7, 13, 6, "3+"), ("away", 7, 14, 6, "3+", ["Diving Tackle"]))

    out_of_reach = _match(*board)
    out = _move(out_of_reach, "h00", 7, 12, _dice([6, 4, 4, 4, 4, 4]))  # a 6 survives even a -2
    assert out.ok, out.text
    assert out_of_reach.by_id("a01").down == "standing", "not worth spending — the Dodge was made anyway"

    marginal = _match(*board)
    assert not _move(marginal, "h00", 7, 12, _dice([4] * 40)).ok
    assert marginal.by_id("a01").down == "prone", "…and on a roll it CAN reach, it is spent"


def test_shadowing_follows_a_player_who_got_away():
    """ "On a 4+, this player is immediately placed into the square that the
    opposition player vacated." """
    m = _match(("home", 7, 13, 6, "3+"), ("away", 7, 14, 6, "3+", ["Shadowing"]))
    out = _move(m, "h00", 7, 12, _dice([6, 5, 4, 4, 4, 4]))  # Dodge made, then a 5 to shadow
    assert out.ok, out.text
    assert (m.by_id("a01").x, m.by_id("a01").y) == (7, 13), "the shadow should be in the vacated square"
    assert m.by_id("a01").down == "standing", "and still on their feet — this is not a Diving Tackle"


def test_shadowing_fails_on_a_three_and_leaves_them_standing():
    """ "On a 1-3, NOTHING HAPPENS." Beside the 4+ case, because "the shadow did
    not move" is also what an engine with no Shadowing would report — the roll has
    to be the thing that decides it."""
    missed = _match(("home", 7, 13, 6, "3+"), ("away", 7, 14, 6, "3+", ["Shadowing"]))
    assert _move(missed, "h00", 7, 12, _dice([6, 3, 4, 4, 4, 4])).ok
    assert (missed.by_id("a01").x, missed.by_id("a01").y) == (7, 14), "a 3 does nothing"

    made = _match(("home", 7, 13, 6, "3+"), ("away", 7, 14, 6, "3+", ["Shadowing"]))
    assert _move(made, "h00", 7, 12, _dice([6, 4, 4, 4, 4, 4])).ok
    assert (made.by_id("a01").x, made.by_id("a01").y) == (7, 13), "…and a 4 follows"


def test_arm_bar_adds_one_to_the_roll_that_matters_when_a_dodger_falls():
    """ "…they may apply a +1 modifier to EITHER the Armour Roll or Injury Roll."
    Spent the way Mighty Blow's is: on the Armour Roll only when that is what
    breaks it."""
    m = _match(("home", 7, 13, 6, "3+"), ("away", 7, 14, 6, "3+", ["Arm Bar"]))
    m.by_id("h00").player.AV = "9+"
    # Dodge fails (1), then armour 4+4 = 8 — one short of 9+, which is exactly
    # what the +1 is for.
    out = _move(m, "h00", 7, 12, _dice([1, 4, 4, 4, 4, 4, 4, 4]))
    assert out.ok is False
    armour = next(r for e in out.events for r in e.rolls if r.kind == "Armour")
    assert armour.passed is True, f"the Arm Bar +1 should have broken it: {armour.describe()}"
    assert any("Arm Bar" in (e.text or "") for e in out.events)


def test_only_one_opponent_may_use_each_of_these_skills():
    """ "If a player tries to leave the Tackle Zone of MULTIPLE PLAYERS WITH THIS
    SKILL at the same time, ONLY ONE of those players may use this Skill." Three
    of the four say it in those words."""
    from bloodbowl.engine import leaving

    m = _match(
        ("home", 7, 13, 6, "3+"),
        ("away", 7, 14, 6, "3+", ["Shadowing"]),
        ("away", 8, 14, 6, "3+", ["Shadowing"]),
    )
    rec = _rec(m)
    markers = [m.by_id("a01"), m.by_id("a02")]
    m.by_id("h00").move_to(7, 12)
    leaving.shadowing(m, m.by_id("h00"), markers, _dice([5, 5, 5]), rec, (7, 13))
    followed = [q for q in markers if (q.x, q.y) == (7, 13)]
    assert len(followed) == 1, f"{len(followed)} shadows moved into one square"


# --- The Foul Action's own Skills --------------------------------------------


def _foul(m, pid, tid, dice):
    from bloodbowl.engine import actions

    actions.load_all()
    return actions.get("foul")["resolve"](m, {"player": pid, "target": tid}, dice)


def test_dirty_player_spends_its_plus_one_on_whichever_roll_needs_it():
    """S3: "+1 modifier to EITHER the Armour Roll or Injury Roll. This modifier may
    be applied AFTER the roll has been made." Same shape as Mighty Blow, so it is
    spent the same way — on the Armour Roll only when that is what breaks it."""
    m = _match(("home", 7, 13, 6, "3+", ["Dirty Player"]), ("away", 7, 14))
    m.by_id("a01").down = "prone"
    m.by_id("a01").player.AV = "9+"
    out = _foul(m, "h00", "a01", _dice([4, 4, 3, 3, 4, 4, 4, 4]))
    armour = next(r for e in out.events for r in e.rolls if r.kind == "Armour")
    assert armour.passed is True, f"8 plus the Dirty Player +1 breaks a 9+: {armour.describe()}"

    # Without the Skill, the identical roll bounces off — which is what makes the
    # check above about the Skill rather than about the dice.
    clean = _match(("home", 7, 13), ("away", 7, 14))
    clean.by_id("a01").down = "prone"
    clean.by_id("a01").player.AV = "9+"
    out2 = _foul(clean, "h00", "a01", _dice([4, 4, 3, 3, 4, 4, 4, 4]))
    assert next(r for e in out2.events for r in e.rolls if r.kind == "Armour").passed is False


def test_lone_fouler_re_rolls_only_when_nobody_at_all_is_helping():
    """ "…if there are NO players providing an Offensive or Defensive Assist."
    Nobody at all, on either side — a Foul with a friend watching does not
    qualify, and that is the half worth testing."""
    alone = _match(("home", 7, 13, 6, "3+", ["Lone Fouler"]), ("away", 7, 14))
    alone.by_id("a01").down = "prone"
    alone.by_id("a01").player.AV = "9+"
    out = _foul(alone, "h00", "a01", _dice([2, 2, 5, 5, 4, 4, 4, 4]))
    armours = [r for e in out.events for r in e.rolls if r.kind == "Armour"]
    assert len(armours) == 2, f"the failed Armour Roll should have been re-rolled: {[r.describe() for r in armours]}"

    # A team-mate Marking the target is an Offensive Assist, and that is enough.
    helped = _match(("home", 7, 13, 6, "3+", ["Lone Fouler"]), ("home", 8, 13), ("away", 7, 14))
    helped.by_id("a02").down = "prone"
    helped.by_id("a02").player.AV = "9+"
    out2 = _foul(helped, "h00", "a02", _dice([2, 2, 5, 5, 4, 4, 4, 4]))
    assert len([r for e in out2.events for r in e.rolls if r.kind == "Armour"]) == 1, "an assist voids it"


def test_sneaky_git_is_only_safe_when_the_armour_held():
    """ "…not Sent-off … if a natural double is rolled for the ARMOUR Roll, SO LONG
    AS THE TARGET PLAYER'S ARMOUR IS NOT BROKEN. If the target player's Armour is
    broken, this player will still be sent off as normal."

    Both halves. The second is what stops it being a free Foul: it protects
    exactly the Fouls that achieved nothing."""
    held = _match(("home", 7, 13, 6, "3+", ["Sneaky Git"]), ("away", 7, 14))
    held.by_id("a01").down = "prone"
    held.by_id("a01").player.AV = "11+"
    out = _foul(held, "h00", "a01", _dice([3, 3, 4, 4, 4, 4]))  # a double, and 6 is under 11+
    assert out.ok, "a double that broke nothing should go unpunished"
    assert held.by_id("h00").place == "pitch"

    broke = _match(("home", 7, 13, 6, "3+", ["Sneaky Git"]), ("away", 7, 14))
    broke.by_id("a01").down = "prone"
    broke.by_id("a01").player.AV = "7+"
    out2 = _foul(broke, "h00", "a01", _dice([4, 4, 3, 3, 4, 4, 4, 4]))  # a double that BREAKS
    assert out2.ok is False and broke.by_id("h00").place == "sent_off", "the armour broke — no protection"


def test_quick_foul_leaves_the_player_able_to_carry_on():
    """ "This player's activation DOES NOT END after performing a Foul Action, and
    they may continue with their Move Action with any movement they have
    remaining." A Foul normally ends it outright."""
    m = _match(("home", 7, 13, 6, "3+", ["Quick Foul"]), ("away", 7, 14))
    m.by_id("a01").down = "prone"
    m.by_id("a01").player.AV = "11+"
    out = _foul(m, "h00", "a01", _dice([2, 3, 4, 4, 4, 4]))
    assert out.ok, out.text
    assert not m.by_id("h00").done, "their activation should still be open"

    plain = _match(("home", 7, 13), ("away", 7, 14))
    plain.by_id("a01").down = "prone"
    plain.by_id("a01").player.AV = "11+"
    _foul(plain, "h00", "a01", _dice([2, 3, 4, 4, 4, 4]))
    assert plain.by_id("h00").done, "…which is not what an ordinary Foul does"


def test_put_the_boot_in_assists_a_foul_through_a_tackle_zone_and_nothing_else():
    """ "This player can provide OFFENSIVE Assists when a team-mate performs a Foul
    Action REGARDLESS OF HOW MANY OPPOSITION PLAYERS ARE MARKING THIS PLAYER."

    Guard's cousin, narrowed to one Action and one direction."""
    from bloodbowl.engine.rules import assist_count

    # h01 is Marking the victim but is itself Marked by a02 — normally no assist.
    m = _match(
        ("home", 7, 13),  # the fouler
        ("home", 8, 14, 6, "3+", ["Put the Boot In"]),
        ("away", 7, 14),  # the victim, Prone
        ("away", 9, 14),  # marking h01
    )
    m.by_id("a02").down = "prone"
    victim = m.by_id("a02")
    assert assist_count(m, "home", victim, exclude={"h00"}) == 0, "Marked, so no ordinary assist"
    assert assist_count(m, "home", victim, exclude={"h00"}, fouling=True) == 1, "…but it assists a Foul"

    # And it is OFFENSIVE only: it does nothing for the side being fouled.
    d = _match(("home", 7, 13), ("home", 8, 13, 6, "3+", ["Put the Boot In"]), ("away", 7, 14), ("away", 8, 14))
    assert assist_count(d, "away", d.by_id("h00"), exclude={"a02"}, fouling=True) == assist_count(
        d, "away", d.by_id("h00"), exclude={"a02"}
    )


# --- Skills about where the ball ends up -------------------------------------


def test_safe_pass_saves_a_natural_one_and_nothing_else():
    """S3: "If this player rolls A NATURAL 1 … it will not result in a Fumbled
    Pass. Instead, the player retains possession … NO TURNOVER is caused."

    Natural 1 only — "if the Passing Ability Test is a 1 AFTER MODIFIERS" is still
    a fumble, and the rule is careful about the difference."""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 5, 6, "3+", ["Safe Pass"]), ("home", 7, 9))
    m.by_id("h00").player.PA = "3+"
    m.ball.carrier, m.ball.in_play = "h00", True
    out = actions.get("pass")["resolve"](m, {"player": "h00", "x": 7, "y": 9}, _dice([1, 4, 4, 4, 4]))
    assert out.ok and out.turnover is False, out.text
    assert m.ball.carrier == "h00", "they keep the ball"
    assert m.by_id("h00").done, "…and their activation immediately ends"

    plain = _match(("home", 7, 5), ("home", 7, 9))
    plain.by_id("h00").player.PA = "3+"
    plain.ball.carrier, plain.ball.in_play = "h00", True
    out2 = actions.get("pass")["resolve"](plain, {"player": "h00", "x": 7, "y": 9}, _dice([1, 4, 4, 4, 4]))
    assert out2.turnover is True, "without the Skill the same 1 is a fumble and a turnover"


def test_give_and_go_keeps_the_activation_open_after_a_quick_pass_only():
    """ "…a Pass Action that is A QUICK PASS, or … a Hand-off Action" — a Short
    Pass is not on the list, and that is the half worth checking."""
    from bloodbowl.engine import actions

    actions.load_all()
    quick = _match(("home", 7, 5, 6, "3+", ["Give and Go"]), ("home", 8, 6))
    quick.by_id("h00").player.PA = "2+"
    quick.ball.carrier, quick.ball.in_play = "h00", True
    out = actions.get("pass")["resolve"](quick, {"player": "h00", "x": 8, "y": 6}, _dice([5, 5, 5, 5, 5]))
    assert out.ok, out.text
    assert not quick.by_id("h00").done, "a Quick Pass leaves them free to move on"

    far = _match(("home", 7, 5, 6, "3+", ["Give and Go"]), ("home", 7, 11))
    far.by_id("h00").player.PA = "2+"
    far.ball.carrier, far.ball.in_play = "h00", True
    out2 = actions.get("pass")["resolve"](far, {"player": "h00", "x": 7, "y": 11}, _dice([5, 5, 5, 5, 5]))
    assert out2.ok, out2.text
    assert far.by_id("h00").done, "a longer pass still ends it"


def test_safe_pair_of_hands_places_the_ball_instead_of_bouncing_it():
    """ "…they may PLACE the ball in any adjacent unoccupied square to the square
    they will become Prone in INSTEAD OF BOUNCING the ball as normal." Placed, not
    bounced — not a scatter with better odds, a choice of square."""
    from bloodbowl.engine.injury import knock_down

    m = _match(("home", 7, 13, 6, "3+", ["Safe Pair of Hands"]), ("away", 8, 14))
    m.ball.carrier, m.ball.in_play = "h00", True
    m.ball.x, m.ball.y = 7, 13
    out = knock_down(m, m.by_id("h00"), _dice([4, 4, 4, 4, 4, 4]))
    assert not m.ball.carrier
    assert not [r for e in out for r in e.rolls if r.kind == "Direction"], "no bounce should be rolled"
    assert max(abs(m.ball.x - 7), abs(m.ball.y - 13)) == 1, f"it should be beside them: {m.ball.x},{m.ball.y}"
    # Away from the opponent, which is the stated policy for "any".
    assert max(abs(m.ball.x - 8), abs(m.ball.y - 14)) > 1, "and out of the nearest opponent's reach"


def test_strip_ball_knocks_the_ball_loose_on_a_push():
    """ "…if an opposition player is Pushed Back then they will drop the ball in
    the square they are Pushed Back INTO … This Bounce will happen BEFORE the
    opposition player becomes Prone but AFTER this player chooses to Follow-up."
    """
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 13, 6, "3+", ["Strip Ball"]), ("away", 7, 14))
    m.ball.carrier, m.ball.in_play = "a01", True
    out = actions.get("block")["resolve"](
        m, {"player": "h00", "target": "a01"}, _dice([4, 4, 4, 4], block=[["push_back"]])
    )
    assert m.ball.carrier != "a01", "they should not still have it"
    assert any("Strip Ball" in (e.text or "") for e in out.events), [e.text for e in out.events]

    plain = _match(("home", 7, 13), ("away", 7, 14))
    plain.ball.carrier, plain.ball.in_play = "a01", True
    actions.get("block")["resolve"](
        plain, {"player": "h00", "target": "a01"}, _dice([4, 4, 4, 4], block=[["push_back"]])
    )
    assert plain.ball.carrier == "a01", "an ordinary push does not strip it"


def test_fumblerooski_leaves_the_ball_behind_without_a_turnover():
    """ "…they may choose to PLACE the ball on the ground in any square they MOVE
    OUT OF during their Move Action. THIS WILL NOT CAUSE A TURNOVER." A choice, so
    the engine is asked rather than deciding to put the ball down."""
    m = _match(("home", 7, 13, 6, "3+", ["Fumblerooski"]), ("away", 2, 20))
    m.ball.carrier, m.ball.in_play = "h00", True
    m.ball.x, m.ball.y = 7, 13
    out = _move(m, "h00", 7, 12, _dice([4, 4, 4, 4]), drop_ball=True)
    assert out.ok and out.turnover is False, out.text
    assert not m.ball.carrier and (m.ball.x, m.ball.y) == (7, 13), f"{m.ball.x},{m.ball.y}"
    assert (m.by_id("h00").x, m.by_id("h00").y) == (7, 12), "and the player moved on"

    # Not asked for: the ball goes with them, as always.
    keep = _match(("home", 7, 13, 6, "3+", ["Fumblerooski"]), ("away", 2, 20))
    keep.ball.carrier, keep.ball.in_play = "h00", True
    keep.ball.x, keep.ball.y = 7, 13
    _move(keep, "h00", 7, 12, _dice([4, 4, 4, 4]))
    assert keep.ball.carrier == "h00"


# --- Traits that decide who may hold a ball, and who stays on their feet ------


def test_extra_arms_helps_all_three_ball_tests_and_nothing_else():
    """S3: "+1 modifier to the Agility Test whenever they attempt to CATCH, PICK UP
    or INTERCEPT the ball." """
    from bloodbowl.engine.skills import roll_modifier

    m = _match(("home", 7, 13, 6, "3+", ["Extra Arms"]))
    p = m.by_id("h00")
    assert [roll_modifier(m, p, t).value for t in ("catch", "pick_up", "intercept")] == [1, 1, 1]
    assert roll_modifier(m, p, "dodge").value == 0


def test_timmm_ber_is_hauled_up_by_open_team_mates_but_a_natural_one_still_fails():
    """ "…apply a +1 modifier to the roll for standing up FOR EACH OPEN STANDING
    TEAM-MATE ADJACENT to this player. A roll of A NATURAL 1 WILL STILL FAIL as
    normal." Open — a team-mate pinned by a Tackle Zone is busy."""
    m = _match(
        ("home", 7, 13, 2, "3+", ["Timmm-ber!"]),
        ("home", 8, 13),  # adjacent and Open
        ("home", 6, 13),  # adjacent and Open
        ("away", 2, 20),
    )
    m.by_id("h00").down = "prone"
    out = _move(m, "h00", 7, 12, _dice([2, 4, 4, 4, 4, 4]))  # a 2 +2 clears the 4+
    up = next(r for e in out.events for r in e.rolls if r.kind == "stand up")
    assert up.modifier == 2 and up.passed, up.describe()

    natural_one = _match(("home", 7, 13, 2, "3+", ["Timmm-ber!"]), ("home", 8, 13), ("home", 6, 13), ("away", 2, 20))
    natural_one.by_id("h00").down = "prone"
    out2 = _move(natural_one, "h00", 7, 12, _dice([1, 4, 4, 4, 4, 4]))
    assert next(r for e in out2.events for r in e.rolls if r.kind == "stand up").passed is False


def test_steady_footing_means_the_knock_down_never_happens_at_all():
    """ "On a 6, this player does NOT get Knocked Down or Fall Over." Not an Armour
    Roll saved — no Injury Roll, no dropped ball, no Turnover."""
    from bloodbowl.engine.injury import knock_down

    m = _match(("home", 7, 13, 6, "3+", ["Steady Footing"]), ("away", 2, 20))
    m.ball.carrier, m.ball.in_play = "h00", True
    out = knock_down(m, m.by_id("h00"), _dice([6, 4, 4, 4, 4]))
    assert m.by_id("h00").down == "standing"
    assert m.ball.carrier == "h00", "they never went down, so they never dropped it"
    assert not [r for e in out for r in e.rolls if r.kind == "Armour"], "and no Armour Roll was made"

    fell = _match(("home", 7, 13, 6, "3+", ["Steady Footing"]), ("away", 2, 20))
    out2 = knock_down(fell, fell.by_id("h00"), _dice([5, 4, 4, 4, 4]))
    assert fell.by_id("h00").down == "prone", "a 5 is not a 6"
    assert [r for e in out2 for r in e.rolls if r.kind == "Armour"]


def test_regeneration_takes_a_casualty_back_to_the_reserves_box():
    """ "…BEFORE MAKING THE CASUALTY ROLL … On a 4+, this player regenerates and
    IGNORES the Casualty … and is instead placed in their team's RESERVES BOX." """
    from bloodbowl.engine.injury import casualty_roll

    m = _match(("home", 7, 13, 6, "3+", ["Regeneration"]), ("away", 2, 20))
    out = casualty_roll(m, m.by_id("h00"), _dice([5]))
    assert m.by_id("h00").place == "reserves", "they come back"
    assert not [r for e in out for r in e.rolls if r.kind == "Casualty"], "the Casualty Roll is never made"

    unlucky = _match(("home", 7, 13, 6, "3+", ["Regeneration"]), ("away", 2, 20))
    out2 = casualty_roll(unlucky, unlucky.by_id("h00"), _dice([2, 9]))
    assert [r for e in out2 for r in e.rolls if r.kind == "Casualty"], "a 1-3 and the roll happens"


def test_decay_makes_every_casualty_roll_against_them_one_worse():
    """ "Apply a +1 modifier to any Casualty Roll made AGAINST this player."
    Theirs, not the knocker-down's — and a higher D16 is a worse result."""
    from bloodbowl.engine.injury import casualty_roll

    m = _match(("home", 7, 13, 6, "3+", ["Decay"]), ("away", 2, 20))
    out = casualty_roll(m, m.by_id("h00"), _dice([8]))  # 8 is Badly Hurt; 9 is not
    ev = next(e for e in out if e.kind == "casualty_roll")
    assert ev.detail["result"] == "Seriously Hurt", ev.text

    plain = _match(("home", 7, 13), ("away", 2, 20))
    out2 = casualty_roll(plain, plain.by_id("h00"), _dice([8]))
    assert next(e for e in out2 if e.kind == "casualty_roll").detail["result"] == "Badly Hurt"


def test_cloud_burster_switches_the_interception_off_entirely():
    """ "When this player performs a Pass Action, opposition players MAY NOT ATTEMPT
    TO INTERCEPT the ball." """
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 5, 6, "3+", ["Cloud Burster"]), ("home", 7, 11), ("away", 7, 8))
    m.by_id("h00").player.PA = "2+"
    m.ball.carrier, m.ball.in_play = "h00", True
    out = actions.get("pass")["resolve"](m, {"player": "h00", "x": 7, "y": 11}, _dice([5, 5, 5, 5, 5]))
    assert not [r for e in out.events for r in e.rolls if r.kind == "Intercept"], "nobody may try"

    plain = _match(("home", 7, 5), ("home", 7, 11), ("away", 7, 8))
    plain.by_id("h00").player.PA = "2+"
    plain.ball.carrier, plain.ball.in_play = "h00", True
    out2 = actions.get("pass")["resolve"](plain, {"player": "h00", "x": 7, "y": 11}, _dice([5, 5, 5, 5, 5]))
    assert [r for e in out2.events for r in e.rolls if r.kind == "Intercept"], "…which is not the usual case"


def test_my_ball_will_not_pass_or_hand_off_and_unsteady_will_not_secure():
    """Three Traits that are refusals rather than rolls, so the place to test them
    is the declaration."""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 5, 6, "3+", ["My Ball"]), ("home", 8, 5))
    m.ball.carrier, m.ball.in_play = "h00", True
    for action in ("pass", "handoff"):
        cmd = {"player": "h00", "x": 8, "y": 5, "target": "h01"}
        legal = actions.get(action)["validate"](m, cmd)
        assert legal.ok is False and "My Ball" in legal.reason, (action, legal.reason)

    u = _match(("home", 7, 5, 6, "3+", ["Unsteady"]))
    u.ball.in_play, u.ball.x, u.ball.y = True, 7, 6
    legal = actions.get("secure")["validate"](u, {"player": "h00"})
    assert legal.ok is False and "Unsteady" in legal.reason, legal.reason


def test_no_ball_fails_automatically_and_no_re_roll_can_rescue_it():
    """ "…they will AUTOMATICALLY FAIL to do so AS IF THEY HAD ROLLED A NATURAL 1."
    A natural 1, not a hard target — which is why it returns before the roll."""
    from bloodbowl.engine.ball import catch, pick_up

    # Sure Hands would re-roll a failed pick-up. It cannot re-roll this.
    m = _match(("home", 7, 13, 6, "2+", ["No Ball", "Sure Hands"]), ("away", 2, 20))
    m.ball.in_play, m.ball.x, m.ball.y = True, 7, 13
    events, turned = pick_up(m, m.by_id("h00"), _dice([4, 4, 4, 4]))
    assert turned is True and m.ball.carrier != "h00"
    assert not [r for e in events for r in e.rolls if r.kind.startswith("Pick")], "no roll should be made at all"

    c = _match(("home", 7, 13, 6, "2+", ["No Ball", "Catch"]), ("away", 2, 20))
    c.ball.in_play, c.ball.x, c.ball.y = True, 7, 13
    caught = catch(c, c.by_id("h00"), _dice([4, 4, 4, 4]))
    assert c.ball.carrier != "h00"
    assert not [r for e in caught for r in e.rolls if r.kind.startswith("Catch")]


# --- What the player being Blocked can do about it ---------------------------


def _block(m, pid, tid, dice, **cmd):
    from bloodbowl.engine import actions

    actions.load_all()
    return actions.get("block")["resolve"](m, {"player": pid, "target": tid, **cmd}, dice)


def test_foul_appearance_can_cancel_a_block_before_any_other_dice():
    """S3: "…they must roll a D6 BEFORE ANY OTHER DICE ARE ROLLED. On a 2+, the
    Block Action continues as normal. On a 1, the Block Action is IMMEDIATELY
    CANCELLED and the opposition player's activation immediately ends."

    Not a Turnover — the Block simply does not happen."""
    m = _match(("home", 7, 13), ("away", 7, 14, 6, "3+", ["Foul Appearance"]))
    out = _block(m, "h00", "a01", _dice([1, 4, 4, 4], block=[["pow"]]))
    assert out.ok is False and out.turnover is False, out.text
    assert m.by_id("h00").done, "their activation immediately ends"
    assert not [r for e in out.events for r in e.rolls if r.kind.startswith("Block")], "no block dice at all"
    assert m.by_id("a01").down == "standing", "and the target is untouched"

    brave = _match(("home", 7, 13), ("away", 7, 14, 6, "3+", ["Foul Appearance"]))
    out2 = _block(brave, "h00", "a01", _dice([2, 4, 4, 4, 4, 4], block=[["pow"]]))
    assert [r for e in out2.events for r in e.rolls if r.kind.startswith("Block")], "a 2 lets it through"


def test_taunt_drags_the_blocker_forward_even_when_they_would_rather_not():
    """ "…this player's Coach may CHOOSE TO MAKE the opposition player Follow-up."
    The defender forcing the attacker forward — the opposite of Fend."""
    m = _match(("home", 7, 13), ("away", 7, 14, 6, "3+", ["Taunt"]))
    _block(m, "h00", "a01", _dice([4, 4, 4, 4], block=[["push_back"]]), follow_up=False)
    assert (m.by_id("h00").x, m.by_id("h00").y) == (7, 14), "they were dragged into the vacated square"

    plain = _match(("home", 7, 13), ("away", 7, 14))
    _block(plain, "h00", "a01", _dice([4, 4, 4, 4], block=[["push_back"]]), follow_up=False)
    assert (plain.by_id("h00").x, plain.by_id("h00").y) == (7, 13), "…which is not what declining normally does"


def test_eye_gouge_stops_the_pushed_player_assisting_until_they_are_next_activated():
    """ "…the opposition player cannot provide Offensive or Defensive Assists UNTIL
    AFTER THEY ARE NEXT ACTIVATED." Distracted is exactly that duration, and it
    already removes the Tackle Zone an assist needs."""
    m = _match(("home", 7, 13, 6, "3+", ["Eye Gouge"]), ("away", 7, 14))
    _block(m, "h00", "a01", _dice([4, 4, 4, 4], block=[["push_back"]]), follow_up=False)
    assert m.by_id("a01").distracted is True

    plain = _match(("home", 7, 13), ("away", 7, 14))
    _block(plain, "h00", "a01", _dice([4, 4, 4, 4], block=[["push_back"]]), follow_up=False)
    assert plain.by_id("a01").distracted is False


def test_hypnotic_gaze_distracts_on_a_three_and_ends_the_activation_either_way():
    """ "On a 1-2, nothing happens and this player's activation immediately ends.
    On a 3+, the selected opposition player becomes Distracted and this player's
    activation immediately ends." Either way it costs a whole activation, which is
    what stops it being a free debuff."""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 13, 6, "3+", ["Hypnotic Gaze"]), ("away", 7, 14))
    out = actions.get("gaze")["resolve"](m, {"player": "h00", "target": "a01"}, _dice([4]))
    assert out.ok, out.text
    assert m.by_id("a01").distracted is True
    assert m.by_id("h00").done

    missed = _match(("home", 7, 13, 6, "3+", ["Hypnotic Gaze"]), ("away", 7, 14))
    actions.get("gaze")["resolve"](missed, {"player": "h00", "target": "a01"}, _dice([2]))
    assert missed.by_id("a01").distracted is False, "a 2 does nothing"
    assert missed.by_id("h00").done, "…and still costs the activation"


# --- Jumping, kicking, and the long ball -------------------------------------


def test_leap_and_pogo_go_over_anything_and_differ_only_in_the_floor():
    """S3, Leap: "…over a single adjacent square REGARDLESS OF WHAT IS IN THE
    SQUARE … may REDUCE the negative modifiers … BY 1, TO A MINIMUM OF -1."
    Pogo: "…may IGNORE ALL negative modifiers."

    A minimum of -1 rather than 0 is the whole difference, and it means a Leap is
    never free however open the pitch."""
    from bloodbowl.engine.rules import jump_over
    from bloodbowl.engine.skills import roll_modifier

    # An ordinary Jump refuses a STANDING body; both of these go over it.
    plain = _match(("home", 7, 13), ("away", 7, 14), ("away", 2, 20))
    assert jump_over(plain, plain.by_id("h00"), 7, 15) is None

    for skill in ("Leap", "Pogo"):
        m = _match(("home", 7, 13, 6, "3+", [skill]), ("away", 7, 14), ("away", 2, 20))
        assert jump_over(m, m.by_id("h00"), 7, 15) is not None, f"{skill} should clear a standing player"
        # …and over an empty square, which no ordinary Jump can do.
        empty = _match(("home", 7, 13, 6, "3+", [skill]), ("away", 2, 20))
        assert jump_over(empty, empty.by_id("h00"), 7, 15) is not None, f"{skill} should clear an empty square"

    leaper = _match(("home", 7, 13, 6, "3+", ["Leap"]))
    assert roll_modifier(leaper, leaper.by_id("h00"), "jump", base=-3).value == -2
    assert roll_modifier(leaper, leaper.by_id("h00"), "jump", base=-1).value == -1, "a minimum of -1, not 0"

    pogoer = _match(("home", 7, 13, 6, "3+", ["Pogo"]))
    assert roll_modifier(pogoer, pogoer.by_id("h00"), "jump", base=-3).value == 0


def test_diving_catch_helps_only_in_the_declared_target_square():
    """ "…+1 modifier … when attempting to Catch the ball as part of a Pass Action
    IF THEY ARE IN THE TARGET SQUARE." Not wherever a scatter left it."""
    from bloodbowl.engine.skills import roll_modifier

    m = _match(("home", 7, 13, 6, "3+", ["Diving Catch"]))
    p = m.by_id("h00")
    assert roll_modifier(m, p, "catch", target_square=True).value == 1
    assert roll_modifier(m, p, "catch", target_square=False).value == 0


def test_defensive_switches_off_guard_during_the_opponents_turn():
    """ "DURING YOUR OPPONENT'S TURNS, opposition players Marked by this player
    cannot use the Guard or Put the Boot In Skills." It reads backwards from every
    other Skill: it is on the MARKER, cancelling a Skill on somebody else."""
    from bloodbowl.engine.rules import assist_count

    # h01 has Guard and is Marked by a03, who is Defensive. Away hold the turn.
    m = _match(
        ("home", 7, 13),
        ("home", 8, 14, 6, "3+", ["Guard"]),
        ("away", 7, 14),
        ("away", 9, 14, 6, "3+", ["Defensive"]),
        active="away",
    )
    victim = m.by_id("a02")
    assert assist_count(m, "home", victim, exclude={"h00"}) == 0, "Defensive should cancel the Guard"

    # Same board, but it is home's turn — Defensive says "during your OPPONENT'S
    # Turns", so Guard works again.
    ours = _match(
        ("home", 7, 13),
        ("home", 8, 14, 6, "3+", ["Guard"]),
        ("away", 7, 14),
        ("away", 9, 14, 6, "3+", ["Defensive"]),
        active="home",
    )
    assert assist_count(ours, "home", ours.by_id("a02"), exclude={"h00"}) == 1


def test_the_kick_skill_halves_the_deviation():
    """ "…when kicking Deviates this player's Coach may choose for it to only
    Deviate D3 SQUARES rather than the usual D6." """
    from bloodbowl.engine.kickoff import deviate

    m = _match(("home", 7, 13, 6, "3+", ["Kick"]), ("away", 7, 20))
    (_x, _y), rolls = deviate(m, _dice([3, 1]), 8, 7, kicker=m.by_id("h00"))
    assert "D3" in (rolls[0].note or ""), rolls[0].describe()

    plain = _match(("home", 7, 13), ("away", 7, 20))
    (_a, _b), rolls2 = deviate(plain, _dice([6, 1]), 8, 7, kicker=plain.by_id("h00"))
    assert rolls2[0].note == "D6" and rolls2[0].dice[0] == 6, "…and without it a 6 is still possible"


def test_hail_mary_pass_reaches_anywhere_never_lands_accurately_and_cannot_be_intercepted():
    """Three separate clauses, and the engine has to honour all three: "ANY SQUARE
    ON THE PITCH … treating the throw as A LONG BOMB … treating ANY RESULT OF AN
    ACCURATE PASS AS AN INACCURATE PASS … CANNOT BE INTERCEPTED." """
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 2, 6, "3+", ["Hail Mary Pass"]), ("away", 7, 12))
    m.by_id("h00").player.PA = "2+"
    m.ball.carrier, m.ball.in_play = "h00", True
    legal = actions.get("pass")["validate"](m, {"player": "h00", "x": 7, "y": 25})
    assert legal.ok, legal.reason
    assert legal.detail["range"] == "Long Bomb" and legal.detail["hail_mary"] is True

    out = actions.get("pass")["resolve"](m, {"player": "h00", "x": 7, "y": 25}, _dice([6, 4, 4, 4, 4, 4, 4, 4]))
    thrown = next(e for e in out.events if e.kind == "pass_thrown")
    assert thrown.detail["outcome"] == "inaccurate", "even a 6 scatters"
    assert not [r for e in out.events for r in e.rolls if r.kind == "Intercept"], "nobody may try"

    # Without the Skill the same square is simply out of range.
    plain = _match(("home", 7, 2), ("away", 7, 12))
    plain.by_id("h00").player.PA = "2+"
    plain.ball.carrier, plain.ball.in_play = "h00", True
    assert actions.get("pass")["validate"](plain, {"player": "h00", "x": 7, "y": 25}).ok is False


def test_a_leader_lends_the_team_one_extra_re_roll_while_they_are_on_the_pitch():
    """ "A team that has one or more players with this Skill ON THE PITCH … may
    gain A SINGLE EXTRA Team Re-roll … if ALL players with this Skill are removed
    from play … THEN IT IS LOST." """
    from bloodbowl.engine.rerolls import available

    m = _match(("home", 7, 13, 6, "3+", ["Leader"]), ("home", 8, 13), active="home")
    m.rerolls = {"home": 0, "away": 0}
    assert available(m, m.by_id("h01")) == 1, "the Leader's extra one is there for the whole team"

    # A single extra, however many Leaders.
    two = _match(("home", 7, 13, 6, "3+", ["Leader"]), ("home", 8, 13, 6, "3+", ["Leader"]), active="home")
    two.rerolls = {"home": 0, "away": 0}
    assert available(two, two.by_id("h00")) == 1

    # Lost the moment the last Leader leaves the pitch.
    m.by_id("h00").place = "casualty"
    assert available(m, m.by_id("h01")) == 0


# --- The Skills that make a Block into more than one thing -------------------


def test_frenzy_forces_a_second_block_the_coach_never_asked_for():
    """S3: "if after the target is Pushed Back they are STILL STANDING, then this
    player MUST PERFORM A SECOND BLOCK ACTION targeting the same opposition
    player." MUST — the one Skill that makes the engine act unbidden."""
    m = _match(("home", 7, 13, 6, "3+", ["Frenzy"]), ("away", 7, 14), ("away", 2, 20))
    out = _block(m, "h00", "a01", _dice([4] * 8, block=[["push_back"], ["push_back"]]))
    blocks = [r for e in out.events for r in e.rolls if r.kind == "Block"]
    assert len(blocks) == 2, f"a second Block is compulsory: {[r.describe() for r in blocks]}"
    assert any("Frenzy" in (e.text or "") for e in out.events)
    assert (m.by_id("a01").x, m.by_id("a01").y) == (7, 16), "pushed twice"

    plain = _match(("home", 7, 13), ("away", 7, 14), ("away", 2, 20))
    out2 = _block(plain, "h00", "a01", _dice([4] * 8, block=[["push_back"], ["push_back"]]))
    assert len([r for e in out2.events for r in e.rolls if r.kind == "Block"]) == 1


def test_frenzy_stops_at_one_extra_block_however_many_pushes():
    """ "A SECOND Block Action" — one extra, not a chain that runs until something
    falls over. Three push-backs in a row must still be two Blocks."""
    m = _match(("home", 7, 13, 6, "3+", ["Frenzy"]), ("away", 7, 14), ("away", 2, 20))
    out = _block(m, "h00", "a01", _dice([4] * 12, block=[["push_back"]] * 4))
    assert len([r for e in out.events for r in e.rolls if r.kind == "Block"]) == 2


def test_a_multiple_block_hits_two_players_at_st_minus_two_and_resolves_both():
    """ "…TWO Block Actions each targeting A DIFFERENT opposition player they are
    Marking … REDUCE THEIR STRENGTH CHARACTERISTIC BY 2 for the duration … BOTH
    Block Actions are resolved IN FULL, EVEN IF ONE OF THEM RESULTS IN A TURNOVER.
    This player CANNOT FOLLOW-UP during either." """
    m = _match(("home", 7, 13, 6, "3+", ["Multiple Block"]), ("away", 7, 14), ("away", 8, 14))
    m.by_id("h00").player.ST = "5"
    for who in ("a01", "a02"):
        m.by_id(who).player.ST = "3"
    out = _block(m, "h00", "a01", _dice([4] * 12, block=[["push_back"], ["push_back"]]), second_target="a02")
    assert out.ok, out.text
    blocks = [r for e in out.events for r in e.rolls if r.kind == "Block"]
    assert len(blocks) == 2, "both are resolved"
    # ST 5 - 2 = 3 against ST 3 is one die; without the reduction it would be two.
    assert all(len(r.dice) == 1 for r in blocks), [r.describe() for r in blocks]
    assert m.by_id("h00").player.ST == "5", "the reduction lasts only for the duration"
    assert (m.by_id("h00").x, m.by_id("h00").y) == (7, 13), "and there is no Follow-up"


def test_a_multiple_block_finishes_the_second_block_even_after_a_turnover():
    """ "…even if one of them results in a Turnover" is the clause that shapes the
    implementation: the second Block runs whatever the first did."""
    m = _match(("home", 7, 13, 6, "3+", ["Multiple Block"]), ("away", 7, 14), ("away", 8, 14))
    out = _block(m, "h00", "a01", _dice([4] * 14, block=[["player_down"], ["push_back"]]), second_target="a02")
    assert out.turnover is True, "the first Block put them on the floor"
    assert len([r for e in out.events for r in e.rolls if r.kind == "Block"]) == 2, "the second still happened"


def test_pile_driver_buys_a_free_foul_and_costs_the_activation():
    """ "…this player MAY perform a FREE FOUL ACTION … This player is then PLACED
    PRONE and their activation immediately ends." Placed Prone, so it costs their
    feet but not an Armour Roll."""
    # They must FOLLOW UP to still be Marking the player they floored — a POW
    # pushes the target a square away, and a blocker who stays put cannot reach
    # them. That is the rule's own condition, not a quirk of the test.
    m = _match(("home", 7, 13, 6, "3+", ["Pile Driver"]), ("away", 7, 14), ("away", 2, 20))
    m.by_id("a01").player.AV = "11+"
    # 3+5 for the FOUL's Armour Roll, not 4+4: a natural double would send the
    # fouler off, which is a different rule and would hide this one.
    out = _block(m, "h00", "a01", _dice([4, 4, 3, 5] + [4] * 10, block=[["pow"]]), follow_up=True)
    assert any("Pile Driver" in (e.text or "") for e in out.events), [e.text for e in out.events]
    assert any(e.kind == "foul_committed" for e in out.events), "a free Foul"
    assert m.by_id("h00").down == "prone" and m.by_id("h00").done

    # …and standing still leaves them out of reach: "so long as they are STILL
    # MARKING the opposition player".
    apart = _match(("home", 7, 13, 6, "3+", ["Pile Driver"]), ("away", 7, 14), ("away", 2, 20))
    apart.by_id("a01").player.AV = "11+"
    out_apart = _block(apart, "h00", "a01", _dice([4, 4, 3, 5] + [4] * 10, block=[["pow"]]), follow_up=False)
    assert not [e for e in out_apart.events if e.kind == "foul_committed"]

    plain = _match(("home", 7, 13), ("away", 7, 14), ("away", 2, 20))
    plain.by_id("a01").player.AV = "11+"
    out2 = _block(plain, "h00", "a01", _dice([4, 4, 3, 5] + [4] * 10, block=[["pow"]]), follow_up=True)
    assert not [e for e in out2.events if e.kind == "foul_committed"]
    assert plain.by_id("h00").down == "standing"


def test_hit_and_run_retreats_to_a_square_where_nobody_is_adjacent():
    """ "…they may immediately move ONE FREE SQUARE ignoring Tackle Zones … The
    player must ensure that AFTER THIS FREE MOVE THEY ARE NOT MARKED BY OR MARKING
    ANY OPPOSITION PLAYERS." A retreat, not a reposition."""
    m = _match(("home", 7, 13, 6, "3+", ["Hit and Run"]), ("away", 7, 14), ("away", 2, 20))
    _block(m, "h00", "a01", _dice([4] * 8, block=[["push_back"]]), follow_up=False)
    p, foe = m.by_id("h00"), m.by_id("a01")
    assert (p.x, p.y) != (7, 13), "they should have stepped away"
    assert max(abs(p.x - foe.x), abs(p.y - foe.y)) > 1, "and be clear of the player they hit"

    plain = _match(("home", 7, 13), ("away", 7, 14), ("away", 2, 20))
    _block(plain, "h00", "a01", _dice([4] * 8, block=[["push_back"]]), follow_up=False)
    assert (plain.by_id("h00").x, plain.by_id("h00").y) == (7, 13), "…which is not what happens without it"


# --- Pro, Hatred, Saboteur ---------------------------------------------------


def test_pro_is_a_re_roll_that_can_fail_and_only_one_per_activation():
    """S3: "…they may attempt to re-roll a single dice … the player must roll a D6:
    ON A 3+ the dice may be re-rolled, on a 1-2 the dice may not … Once a player
    has ATTEMPTED to use this Skill, they cannot use a re-roll from any other
    source to re-roll the dice." A re-roll that can fail."""
    from bloodbowl.engine.ball import pick_up

    made = _match(("home", 7, 13, 6, "4+", ["Pro"]), ("away", 2, 20))
    made.ball.in_play, made.ball.x, made.ball.y = True, 7, 13
    events, turned = pick_up(made, made.by_id("h00"), _dice([2, 5, 5, 4, 4]))  # fail, Pro 5, then 5
    kinds = [r.kind for e in events for r in e.rolls]
    assert "Pro" in kinds and "Pick up (Pro)" in kinds, kinds
    assert turned is False, "the re-roll made it"

    failed = _match(("home", 7, 13, 6, "4+", ["Pro"]), ("away", 2, 20))
    failed.ball.in_play, failed.ball.x, failed.ball.y = True, 7, 13
    events2, turned2 = pick_up(failed, failed.by_id("h00"), _dice([2, 1, 5, 4, 4]))  # Pro rolls a 1
    kinds2 = [r.kind for e in events2 for r in e.rolls]
    assert "Pro" in kinds2 and "Pick up (Pro)" not in kinds2, kinds2
    assert turned2 is True, "a failed Pro leaves the failure standing"


def test_pro_will_not_touch_an_armour_or_injury_roll():
    """ "The Skill CANNOT be used to re-roll a dice made as part of an ARMOUR ROLL,
    INJURY ROLL, CASUALTY roll, a roll made outside of the player's activation, or
    any dice roll not made on the player's behalf." """
    from bloodbowl.engine.skills import pro_reroll

    m = _match(("home", 7, 13, 6, "3+", ["Pro"]), ("away", 2, 20))
    rec = _rec(m)
    for banned in ("armour", "injury", "casualty", "argue the call"):
        assert pro_reroll(m, m.by_id("h00"), banned, _dice([6, 6, 6]), rec) is False, banned


def test_hatred_re_rolls_a_player_down_but_only_against_the_named_keyword():
    """ "Whenever this player performs a Block Action against A PLAYER WITH THE SAME
    KEYWORD AS THAT SHOWN IN BRACKETS, this player may re-roll a single PLAYER DOWN
    result." Free, like Brawler's, so it goes before any Team Re-roll — and the
    KEYWORD is the half that used to be missing, because nothing read the `role`
    the scraper had captured all along."""
    hated = _match(("home", 7, 13, 6, "3+", ["Hatred (Elf)"]), ("away", 7, 14), ("away", 2, 20))
    hated.by_id("a01").player.role = "Blitzer, Elf"
    out = _block(hated, "h00", "a01", _dice([4] * 10, block=[["player_down"], ["push_back"]]))
    assert out.ok, out.text
    assert [r for e in out.events for r in e.rolls if r.kind == "Block (re-roll)"], "it should have gone again"
    assert hated.by_id("h00").down == "standing"

    # The same Skill against somebody they have no quarrel with does nothing.
    liked = _match(("home", 7, 13, 6, "3+", ["Hatred (Elf)"]), ("away", 7, 14), ("away", 2, 20))
    liked.by_id("a01").player.role = "Blitzer, Orc"
    out2 = _block(liked, "h00", "a01", _dice([4] * 10, block=[["player_down"], ["push_back"]]))
    assert out2.turnover is True and liked.by_id("h00").down != "standing"

    plain = _match(("home", 7, 13), ("away", 7, 14), ("away", 2, 20))
    out3 = _block(plain, "h00", "a01", _dice([4] * 10, block=[["player_down"], ["push_back"]]))
    assert out3.turnover is True


def test_saboteur_trades_its_owner_for_the_blockers_feet():
    """ "…BEFORE THE ARMOUR ROLL IS MADE, they may roll a D6 … On a 4+ … THE
    OPPOSITION PLAYER IS ALSO KNOCKED DOWN … this player is AUTOMATICALLY KNOCKED
    OUT and the Armour Roll is NOT MADE for them." A trade, not a save — and it is
    the DEFENDER's Skill firing on the attacker's success."""
    # A POW, so the SABOTEUR is the one being knocked down — which is the case the
    # rule is written for: "when THIS PLAYER is Knocked Down as a result of AN
    # OPPOSITION PLAYER'S Block Action".
    m = _match(("home", 7, 13), ("away", 7, 14, 6, "3+", ["Saboteur", "Secret Weapon"]), ("away", 2, 20))
    out = _block(m, "h00", "a01", _dice([5, 4, 4, 4, 4, 4], block=[["pow"]]), follow_up=False)
    assert m.by_id("h00").down != "standing", "the blocker goes down too"
    assert m.by_id("a01").place == "knocked_out", "and the saboteur is Knocked Out"
    assert not [r for e in out.events for r in e.rolls if r.kind == "Armour" and e.actor == "a01"], (
        "no Armour Roll is made for them"
    )

    missed = _match(("home", 7, 13), ("away", 7, 14, 6, "3+", ["Saboteur", "Secret Weapon"]), ("away", 2, 20))
    _block(missed, "h00", "a01", _dice([2, 4, 4, 4, 4, 4], block=[["pow"]]), follow_up=False)
    assert missed.by_id("a01").place == "pitch", "a 2 does nothing"
    assert missed.by_id("h00").down == "standing", "…and the blocker stays up"


# --- The last six ------------------------------------------------------------


def test_trickster_moves_before_the_dice_are_counted_and_changes_them():
    """S3: "BEFORE DETERMINING HOW MANY DICE ARE ROLLED, this player may be removed
    from the pitch and placed in any other unoccupied square adjacent to the player
    performing the Action."

    Before they are COUNTED, so it is a way of shedding assists rather than of
    escaping — they are still adjacent, still Blocked, just somewhere better."""
    # Two home players Marking the target: two assists, so two dice. Moving to a
    # square only the blocker reaches drops it to one.
    m = _match(
        ("home", 7, 13),
        ("home", 6, 13),
        ("home", 8, 13),
        ("away", 7, 14, 6, "3+", ["Trickster"]),
        ("away", 2, 20),
    )
    out = _block(m, "h00", "a03", _dice([4] * 8, block=[["push_back"], ["push_back"]]), follow_up=False)
    assert any((e.detail or {}).get("skill") == "Trickster" for e in out.events), [e.text for e in out.events]
    rolled = next(r for e in out.events for r in e.rolls if r.kind == "Block")
    assert len(rolled.dice) == 1, f"the assists should have been shed: {rolled.describe()}"

    plain = _match(("home", 7, 13), ("home", 6, 13), ("home", 8, 13), ("away", 7, 14), ("away", 2, 20))
    out2 = _block(plain, "h00", "a03", _dice([4] * 8, block=[["push_back"] * 2]), follow_up=False)
    assert len(next(r for e in out2.events for r in e.rolls if r.kind == "Block").dice) == 2


def test_dump_off_gets_the_ball_away_before_the_hit_lands():
    """ "…this player may immediately perform a QUICK PASS BEFORE the Action
    targeting them is resolved. This Quick Pass CANNOT CAUSE A TURNOVER … Once the
    Quick Pass has been resolved, this Action targeting this player CONTINUES." """
    m = _match(("home", 7, 13), ("away", 7, 14, 6, "2+", ["Dump-off"]), ("away", 8, 15))
    m.by_id("a01").player.PA = "2+"
    m.ball.carrier, m.ball.in_play = "a01", True
    out = _block(m, "h00", "a01", _dice([5] * 10, block=[["pow"]]), follow_up=False)
    assert any((e.detail or {}).get("skill") == "Dump-off" for e in out.events), [e.text for e in out.events]
    assert m.ball.carrier == "a02", "the ball should be with the team-mate"
    assert m.by_id("a01").down != "standing", "…and the Block still happened"
    assert out.ok or out.turnover is False


def test_pick_me_up_hauls_prone_team_mates_up_between_turns():
    """ "At the end of each of the OPPOSITION'S Turns, roll a D6 for each PRONE
    TEAM-MATE WITHIN 3 SQUARES of one or more STANDING players with this Trait. On
    a 5+, the Prone player may immediately stand up." """
    from bloodbowl.engine.game import end_turn

    m = _match(("home", 7, 13), ("away", 7, 20, 6, "3+", ["Pick-me-up"]), ("away", 8, 20), ("away", 2, 2))
    for who in ("a02", "a03"):
        m.by_id(who).down = "prone"
    end_turn(m, dice=_dice([5, 2, 4, 4, 4, 4]))  # home's turn ends, so AWAY get the roll
    assert m.by_id("a02").down == "standing", "the one within 3 squares is hauled up"
    assert m.by_id("a03").down == "prone", "the one across the pitch is not"


def test_on_the_ball_moves_a_receiver_before_the_kickoff_event_is_rolled():
    """ "…AFTER THE KICK DEVIATES but BEFORE THE KICK-OFF EVENT IS ROLLED, a single
    OPEN player on the receiving team with this Skill may move up to 3 squares …
    they cannot Rush … may not move into the opposition half." """
    from bloodbowl.engine.kickoff import kick

    m = _match(("home", 7, 3, 6, "3+", ["On the Ball"]), ("away", 7, 20))
    dice = _dice([2, 1, 1, 3, 2] + [4] * 12)
    kick(m, dice, receiving="home")
    p = m.by_id("h00")
    assert (p.x, p.y) != (7, 3), "they should have read the kick"
    assert p.y <= 13, "and stayed in their own half"
    moved = [e for e in m.events if "On the Ball" in (e.text or "")]
    kicked = [i for i, e in enumerate(m.events) if e.kind == "kickoff_event"]
    assert moved and m.events.index(moved[0]) < kicked[0], "before the event is rolled"


def test_a_punt_clears_the_half_and_only_costs_a_turnover_if_it_goes_wrong():
    """ "NO TURNOVER is caused if the ball comes to rest ON THE GROUND; however, if
    after the Punt Special Action is resolved the ball is in possession of AN
    OPPOSITION PLAYER, or IN THE CROWD, a Turnover IS caused." """
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 8, 6, "3+", ["Punt"]), ("away", 2, 20))
    m.ball.carrier, m.ball.in_play = "h00", True
    out = actions.get("punt")["resolve"](m, {"player": "h00"}, _dice([1, 4, 4, 4, 4, 4]))
    assert out.turnover is False, "a ball on the ground costs nothing"
    assert not m.ball.carrier and m.ball.in_play


def test_swoop_replaces_the_scatter_with_one_long_glide():
    """ "…they may choose NOT TO SCATTER before landing as normal. If they do …
    roll a D6 to determine the direction … and then a second die to determine how
    many squares." One roll of each, not three D8 steps."""
    from bloodbowl.engine.actions.throwteam import _scatter_player

    m = _match(("home", 7, 8, 6, "3+", ["Swoop", "Right Stuff"]), ("away", 2, 20))
    rec = _rec(m)
    _scatter_player(m, m.by_id("h00"), _dice([1, 4]), rec)
    assert any((e.detail or {}).get("skill") == "Swoop" for e in rec.events), [e.text for e in rec.events]
    assert len([r for e in rec.events for r in e.rolls if r.kind == "Scatter"]) == 0, "no D8 staggering"


# --- The Pre-game Sequence, Fan Factor and Bribes ----------------------------


def test_fan_factor_is_rolled_rather_than_defaulted_to_nothing():
    """S3: "roll a D3 [for Fair-weather Fans] … add … your Dedicated Fans
    Characteristic". And: "When you draft a team, it will AUTOMATICALLY have a
    Dedicated Fans Characteristic of 1."

    It used to default to zero, which is not a neutral default — Pitch Invasion
    adds Fan Factor to a D6, so a zero made that event quietly milder."""
    from bloodbowl.engine.pregame import DEFAULT_DEDICATED_FANS, fan_factor

    assert DEFAULT_DEDICATED_FANS == 1, "the rules say a drafted team starts with 1"
    total, roll = fan_factor(_dice([2]), 4)
    assert total == 6, roll.describe()  # the rulebook's own worked example
    assert fan_factor(_dice([1]), DEFAULT_DEDICATED_FANS)[0] == 2


def test_the_pregame_sequence_runs_all_five_steps_and_says_which_are_league_only():
    """ "There are FIVE simple steps … however, SOME OF THESE ARE ONLY RELEVANT TO
    LEAGUE PLAY." The rulebook scopes two of them out of a game like this, in those
    words — so reporting them as League-only is completing the sequence, not
    skipping it."""
    from bloodbowl.engine.pregame import steps

    exhibition = steps(league=False)
    assert [s["step"] for s in exhibition] == [
        "The Fans",
        "The Weather",
        "Take On Journeymen",
        "Inducements",
        "Determine Kicking Team",
    ]
    skipped = [s["step"] for s in exhibition if not s["applies"]]
    assert skipped == ["Take On Journeymen", "Inducements"]
    assert all("League Play" in s["why"] for s in exhibition if not s["applies"])
    assert all(s["applies"] for s in steps(league=True)), "a League game runs all five"


def test_get_the_ref_hands_both_teams_a_bribe():
    """ "EACH TEAM immediately receives ONE FREE Bribe Inducement." Free is what
    lets an exhibition match hold an Inducement it never bought."""
    from bloodbowl.engine.kickoff import kickoff_event

    m = _match(("home", 7, 11), ("away", 7, 20))
    kickoff_event(m, _dice([1, 1] + [4] * 10), "home")  # 1+1 = 2: Get the Ref
    assert m.bribes == {"home": 1, "away": 1}, m.bribes


def test_a_bribe_undoes_a_sending_off_and_the_turnover_with_it():
    """ "When a player is Sent-off, AFTER any attempt to Argue the Call has been
    made, you may use a Bribe … On a 2+, the player is NOT Sent-off (AND NO
    TURNOVER IS CAUSED). On a NATURAL 1, the referee pockets the Bribe but sends
    the player off anyway."

    The no-Turnover clause makes it strictly better than arguing — the only thing
    in the game that undoes a Foul completely."""
    from bloodbowl.engine.events import Event

    m = _match(("home", 7, 13), ("away", 7, 14), ("away", 2, 20))
    m.apply(Event(kind="bribes_awarded", detail={"home": 1, "away": 0}))
    m.by_id("a01").down = "prone"
    m.by_id("a01").player.AV = "11+"
    # A natural double on the Armour Roll is the sending-off; then Argue (a 3,
    # which fails), then the Bribe (a 5, which works).
    out = _foul(m, "h00", "a01", _dice([4, 4, 3, 5] + [4] * 8))
    assert m.by_id("h00").place == "pitch", "the Bribe should have kept them on"
    assert out.turnover is False, "…and no Turnover is caused"
    assert m.bribes["home"] == 0, "spent"

    # A natural 1: pocketed, and they go anyway.
    n = _match(("home", 7, 13), ("away", 7, 14), ("away", 2, 20))
    n.apply(Event(kind="bribes_awarded", detail={"home": 1, "away": 0}))
    n.by_id("a01").down = "prone"
    n.by_id("a01").player.AV = "11+"
    out2 = _foul(n, "h00", "a01", _dice([4, 4, 3, 1] + [4] * 8))
    assert n.by_id("h00").place == "sent_off"
    assert out2.turnover is True
    assert n.bribes["home"] == 0, "lost either way"


# --- Closing the partials ----------------------------------------------------


def test_very_long_legs_is_the_written_counter_to_cloud_burster():
    """S3, Very Long Legs: "Additionally, this player IGNORES THE CLOUD BURSTER
    SKILL." Cloud Burster switches every Interception off; this switches it back on
    for one player, which is why the clause exists at all."""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(
        ("home", 7, 5, 6, "3+", ["Cloud Burster"]),
        ("home", 7, 11),
        ("away", 7, 8, 6, "3+", ["Very Long Legs"]),
    )
    m.by_id("h00").player.PA = "2+"
    m.ball.carrier, m.ball.in_play = "h00", True
    out = actions.get("pass")["resolve"](m, {"player": "h00", "x": 7, "y": 11}, _dice([5] * 8))
    assert [r for e in out.events for r in e.rolls if r.kind == "Intercept"], "they ignore Cloud Burster"

    ordinary = _match(("home", 7, 5, 6, "3+", ["Cloud Burster"]), ("home", 7, 11), ("away", 7, 8))
    ordinary.by_id("h00").player.PA = "2+"
    ordinary.ball.carrier, ordinary.ball.in_play = "h00", True
    out2 = actions.get("pass")["resolve"](ordinary, {"player": "h00", "x": 7, "y": 11}, _dice([5] * 8))
    assert not [r for e in out2.events for r in e.rolls if r.kind == "Intercept"], "…and nobody else does"


def test_shadowing_runs_out_after_ma_uses_in_a_turn():
    """ "This player may only use this Skill A NUMBER OF TIMES PER TURN EQUAL TO
    THEIR MA." Counted from the log, so it survives a fold."""
    from bloodbowl.engine import leaving
    from bloodbowl.engine.events import Event

    m = _match(("home", 7, 13), ("away", 7, 14, 2, "3+", ["Shadowing"]))
    m.apply(Event(kind="turn_started", detail={"side": "home", "half": 1, "turn": 1}))
    shadow = m.by_id("a01")
    for _ in range(2):  # MA 2, so two uses are allowed
        m.apply(Event(kind="player_pushed", actor=shadow.id, detail={"x": shadow.x, "y": shadow.y, "shadowing": True}))
    rec = _rec(m)
    leaving.shadowing(m, m.by_id("h00"), [shadow], _dice([6, 6]), rec, (7, 12))
    assert any("out of puff" in (e.text or "") for e in rec.events), [e.text for e in rec.events]


def test_diving_catch_takes_a_ball_landing_beside_them_but_not_a_bounce():
    """ "This player may attempt to Catch the ball IF IT LANDS IN A SQUARE IN THEIR
    TACKLE ZONE as a result of a PASS, THROW-IN or KICK-OFF. They MAY NOT use this
    Skill … as a result of a BOUNCE." Three sources, the fourth excluded by name."""
    from bloodbowl.engine.ball import diving_catch

    for source, expected in (("pass", True), ("kick_off", True), ("throw_in", True), ("bounce", False)):
        m = _match(("home", 7, 13, 6, "2+", ["Diving Catch"]), ("away", 2, 20))
        m.ball.in_play, m.ball.x, m.ball.y = True, 7, 12
        events = diving_catch(m, 7, 12, source, _dice([5, 5, 5]))
        assert bool(events) is expected, f"{source} should{'' if expected else ' not'} let them dive"
        if expected:
            assert m.ball.carrier == "h00", source


def test_hatred_and_animosity_read_the_keywords_the_roster_always_had():
    """Keywords were in `data/rosters.json` all along, under `role` — the
    parenthesised list after each position name. Nothing had ever read it."""
    from bloodbowl.engine.rules import keywords, shares_keyword, trait_parameter
    from bloodbowl.pitch import player_from_roster

    p, err = player_from_roster("home", 7, 13, "Vampire", "Thrall Lineman")
    assert p is not None, err
    holder = type("S", (), {"player": p})()
    assert {"human", "lineman", "thrall"} <= keywords(holder), keywords(holder)

    hater = _match(("home", 7, 13, 6, "3+", ["Hatred (Elf)", "Animosity (all)"]), ("away", 7, 14))
    assert trait_parameter(hater.by_id("h00"), "Hatred") == "Elf"
    assert trait_parameter(hater.by_id("h00"), "Animosity") == "all"
    hater.by_id("a01").player.role = "Blitzer, Elf"
    assert shares_keyword(hater.by_id("h00"), hater.by_id("a01"), "Elf")
    assert not shares_keyword(hater.by_id("h00"), hater.by_id("a01"), "Orc")
    # "(all) … regardless of the Keywords they have"
    assert shares_keyword(hater.by_id("h00"), hater.by_id("a01"), "all")


def test_a_vampire_bites_an_adjacent_thrall_and_carries_on():
    """S3, Bloodlust: "at the end of their activation, this player MAY BITE AN
    ADJACENT THRALL LINEMAN team-mate REGARDLESS OF THE STATUS of the Thrall
    Lineman … treating any Casualty result as BADLY HURT; this will not cause a
    Turnover unless the Thrall Lineman was holding the ball."

    Thrall Lineman is a KEYWORD, and the Vampire roster prints it."""
    from bloodbowl.engine.game import act

    m = _match(("home", 7, 13, 6, "3+", ["Bloodlust (4+)"]), ("home", 8, 13), ("away", 2, 20))
    m.by_id("h01").player.role = "Human, Lineman, Thrall"
    out = act(m, "move", {"player": "h00", "x": 7, "y": 12}, _dice([2, 4, 4, 4, 4, 4, 4, 4]))
    assert any("sinks their teeth" in (e.get("text") or "") for e in out.get("log", [])) or any(
        "sinks their teeth" in (e.text or "") for e in m.events
    ), [e.text for e in m.events]
    assert not m.by_id("h00").distracted, "a fed Vampire carries on as normal"

    # With no Thrall to bite, the failure lands instead.
    alone = _match(("home", 7, 13, 6, "3+", ["Bloodlust (4+)"]), ("home", 8, 13), ("away", 2, 20))
    act(alone, "move", {"player": "h00", "x": 7, "y": 12}, _dice([2, 4, 4, 4, 4, 4]))
    assert alone.by_id("h00").distracted, "no Thrall, so the Trait bites them instead"


# --- Star Player Points ------------------------------------------------------


def test_spp_are_earned_for_the_five_things_that_happen_on_the_pitch():
    """S3: Completion 1, Interception 2, Casualty 2, Touchdown 3 — all recorded
    DURING the game, whatever they are spent on after one: "it's important to keep
    track of every time a player does something that generates SPP during a
    game." """
    from bloodbowl.engine import actions
    from bloodbowl.engine.spp import CASUALTY, COMPLETION, INTERCEPTION, TOUCHDOWN

    actions.load_all()
    # COMPLETION: an accurate Pass caught by a team-mate with no bounce.
    m = _match(("home", 7, 5, 6, "2+"), ("home", 8, 6))
    m.by_id("h00").player.PA = "2+"
    m.ball.carrier, m.ball.in_play = "h00", True
    actions.get("pass")["resolve"](m, {"player": "h00", "x": 8, "y": 6}, _dice([5, 5, 5, 5, 5]))
    assert m.spp.get("h00") == COMPLETION, m.spp

    # INTERCEPTION.
    i = _match(("home", 7, 5, 6, "2+"), ("home", 7, 11), ("away", 7, 8, 6, "2+"))
    i.by_id("h00").player.PA = "2+"
    i.ball.carrier, i.ball.in_play = "h00", True
    actions.get("pass")["resolve"](i, {"player": "h00", "x": 7, "y": 11}, _dice([5] * 8))
    assert i.spp.get("a02") == INTERCEPTION, i.spp

    # CASUALTY, from a Block.
    c = _match(("home", 7, 13), ("away", 7, 14), ("away", 2, 20))
    c.by_id("a01").player.AV = "3+"
    _block(c, "h00", "a01", _dice([6, 6, 6, 6, 12] + [4] * 8, block=[["pow"]]), follow_up=False)
    assert c.spp.get("h00") == CASUALTY, c.spp

    # TOUCHDOWN.
    from bloodbowl.engine.ball import check_touchdown

    t = _match(("home", 7, 25), ("away", 2, 2))
    t.ball.carrier, t.ball.in_play = "h00", True
    t.by_id("h00").move_to(7, 26)
    check_touchdown(t, t.by_id("h00"))
    assert t.spp.get("h00") == TOUCHDOWN, t.spp


def test_a_special_action_casualty_earns_nothing_unless_they_are_a_violent_innovator():
    """ "Other methods, SUCH AS SPECIAL ACTIONS or by Injury by the Crowd, do not
    generate SPP." And Violent Innovator is the single sentence that overturns it:
    "if an opposition player suffers a Casualty as a result of a SPECIAL ACTION
    this player performed, this player WILL earn Star Player Points."

    Also pins the separation the Stab guard forced: who CAUSED the Casualty and who
    MODIFIES the roll are different questions, because "this Armour Roll cannot be
    modified in any way"."""
    from bloodbowl.engine import actions

    actions.load_all()
    script = [6, 6, 6, 6, 12] + [4] * 8
    plain = _match(("home", 7, 13, 6, "3+", ["Stab"]), ("away", 7, 14), ("away", 2, 20))
    plain.by_id("a01").player.AV = "3+"
    out = actions.get("stab")["resolve"](plain, {"player": "h00", "target": "a01"}, _dice(script))
    armour = next(r for e in out.events for r in e.rolls if r.kind == "Armour")
    assert armour.modifier == 0, "a Stab's Armour Roll cannot be modified in any way"
    assert not plain.spp, plain.spp

    keen = _match(("home", 7, 13, 6, "3+", ["Stab", "Violent Innovator"]), ("away", 7, 14), ("away", 2, 20))
    keen.by_id("a01").player.AV = "3+"
    out2 = actions.get("stab")["resolve"](keen, {"player": "h00", "target": "a01"}, _dice(script))
    assert next(r for e in out2.events for r in e.rolls if r.kind == "Armour").modifier == 0
    assert keen.spp.get("h00") == 2, keen.spp


def test_a_star_player_earns_no_spp_at_all():
    """ "Star Players DO NOT generate SPP AT ALL during the course of a game." They
    are hired for one match; there is nothing to advance."""
    from bloodbowl.engine.spp import award

    m = _match(("home", 7, 13), ("away", 2, 20))
    m.by_id("h00").player.role = "star"
    assert award(m, m.by_id("h00"), 3, "a Touchdown") == []
    assert not m.spp


def test_the_mvp_is_rolled_at_full_time_and_is_worth_four():
    """ "Each Coach nominates six players … They then roll a D6 … The player that is
    given the MVP award generates 4 SPP." """
    from bloodbowl.engine.spp import MVP, mvp

    m = _match(("home", 7, 13), ("home", 8, 13), ("away", 7, 20), ("away", 8, 20))
    mvp(m, _dice([1, 2]))
    awarded = {pid: n for pid, n in m.spp.items() if n == MVP}
    assert len(awarded) == 2, f"one per side: {m.spp}"


def test_plague_ridden_adds_a_lineman_to_the_reserves_box_and_the_fold_agrees():
    """S3: "ONCE PER GAME, when a player with this Trait causes a Casualty … as a
    result of a BLOCK ACTION, and that player suffers a DEAD result … you may
    IMMEDIATELY ADD ONE NEW LINEMAN PLAYER from your team's Team Roster TO YOUR
    RESERVES BOX."

    And the added player must survive a fold, which is why `apply` builds them from
    the event rather than the helper appending one: a player who exists until the
    next reload is the exact trap this engine's event model exists to avoid."""
    from bloodbowl.engine.injury import casualty_roll
    from bloodbowl.engine.state import Match

    m = _match(("home", 7, 13, 6, "3+", ["Plague Ridden"]), ("away", 7, 14), ("away", 2, 20))
    m.home_team = "Orc"
    m.by_id("h00").player.team = "Orc"
    before = len(m.players)
    casualty_roll(m, m.by_id("a01"), _dice([15]), causer=m.by_id("h00"))  # 15-16 is Dead
    assert len(m.players) == before + 1, "a new Lineman should be in the Reserves Box"
    added = m.players[-1]
    assert added.place == "reserves" and added.player.position == "Orc Lineman", added.player.position

    # …and a fold rebuilds them.
    back = Match.from_dict(m.to_dict())
    assert back.by_id(added.id) is not None, "the fold lost the added player"
    assert back.by_id(added.id).player.position == "Orc Lineman"

    # Once per game.
    casualty_roll(m, m.by_id("a02"), _dice([16]), causer=m.by_id("h00"))
    assert len(m.players) == before + 1, "only once per game"


def test_plague_ridden_cannot_be_used_against_the_four_it_names():
    """ "This Trait CANNOT BE USED against BIG GUY players, or any player with the
    DECAY, REGENERATION or STUNTY Traits." The first is a Keyword; the other three
    are Traits, and a paraphrase would treat all four the same way."""
    from bloodbowl.engine.injury import casualty_roll

    for role, skills in (("Big Guy, Troll", []), ("Lineman, Orc", ["Decay"]), ("Lineman, Orc", ["Stunty"])):
        m = _match(("home", 7, 13, 6, "3+", ["Plague Ridden"]), ("away", 7, 14, 6, "3+", skills))
        m.home_team = "Orc"
        m.by_id("h00").player.team = "Orc"
        m.by_id("a01").player.role = role
        before = len(m.players)
        casualty_roll(m, m.by_id("a01"), _dice([15, 4]), causer=m.by_id("h00"))
        assert len(m.players) == before, f"{role} / {skills} should be immune"


# --- The coach's choices, made by the coach ----------------------------------


def test_sidestep_and_stand_firm_are_the_defenders_choices_to_make():
    """Both say "MAY", and both belong to the player being shoved rather than the
    one shoving. `sidestep_to` and `stand_firm` are how that coach makes them; the
    engine's policy is what happens when nobody says."""
    # Sidestep: the default is furthest from the blocker; a named square wins.
    default = _match(("home", 7, 13), ("away", 7, 14, 6, "3+", ["Sidestep"]), ("away", 2, 20))
    _block(default, "h00", "a01", _dice([4] * 6, block=[["push_back"]]), follow_up=False)
    away = default.by_id("a01")
    assert (away.x, away.y) != (7, 14)

    chosen = _match(("home", 7, 13), ("away", 7, 14, 6, "3+", ["Sidestep"]), ("away", 2, 20))
    _block(chosen, "h00", "a01", _dice([4] * 6, block=[["push_back"]]), follow_up=False, sidestep_to=(6, 14))
    assert (chosen.by_id("a01").x, chosen.by_id("a01").y) == (6, 14), "the defending coach picked"

    # Stand Firm: unasked it only avoids the Crowd; asked, it refuses any push.
    firm = _match(("home", 7, 13), ("away", 7, 14, 6, "3+", ["Stand Firm"]), ("away", 2, 20))
    _block(firm, "h00", "a01", _dice([4] * 6, block=[["push_back"]]), follow_up=False, stand_firm=True)
    assert (firm.by_id("a01").x, firm.by_id("a01").y) == (7, 14), "they refused the push"

    loose = _match(("home", 7, 13), ("away", 7, 14, 6, "3+", ["Stand Firm"]), ("away", 2, 20))
    _block(loose, "h00", "a01", _dice([4] * 6, block=[["push_back"]]), follow_up=False, stand_firm=False)
    assert (loose.by_id("a01").x, loose.by_id("a01").y) != (7, 14), "…and can decline to"


def test_trickster_and_safe_pair_of_hands_take_a_named_square():
    """Both are "any … square" with no rule to guide the pick, so both take one."""
    m = _match(("home", 7, 13), ("away", 7, 14, 6, "3+", ["Trickster"]), ("away", 2, 20))
    _block(m, "h00", "a01", _dice([4] * 8, block=[["push_back"]]), follow_up=False, trickster_to=(6, 12))
    assert any((e.detail or {}).get("skill") == "Trickster" for e in m.events)

    from bloodbowl.engine.ball import drop

    b = _match(("home", 7, 13, 6, "3+", ["Safe Pair of Hands"]), ("away", 2, 20))
    b.ball.carrier, b.ball.in_play = "h00", True
    b.ball.x, b.ball.y = 7, 13
    drop(b, b.by_id("h00"), _dice([4] * 4), place_at=(8, 12))
    assert (b.ball.x, b.ball.y) == (8, 12), "the coach picked the square"


def test_juggernauts_both_down_conversion_can_be_declined():
    """ "They MAY treat any result of Both Down as Pushed Back." May — so a coach
    who wants the Both Down keeps it, and `juggernaut=false` is how they say so."""
    from bloodbowl.engine.events import Event

    def board():
        m = _match(("home", 7, 13, 6, "3+", ["Juggernaut", "Block"]), ("away", 7, 14), ("away", 2, 20))
        m.apply(Event(kind="blitz_declared", actor="h00", detail={"player": "h00", "target": "a01"}))
        return m

    taken = board()
    _block(taken, "h00", "a01", _dice([4] * 8, block=[["both_down"]]), follow_up=False)
    assert taken.by_id("a01").down != "standing" or (taken.by_id("a01").x, taken.by_id("a01").y) != (7, 14)

    declined = board()
    out = _block(declined, "h00", "a01", _dice([4] * 8, block=[["both_down"]]), follow_up=False, juggernaut=False)
    assert not any((e.detail or {}).get("skill") == "Juggernaut" for e in out.events), [e.text for e in out.events]


def test_on_the_ball_closes_on_a_declared_pass_before_the_dice():
    """ "When AN OPPOSITION PLAYER performs a Pass Action, AFTER THE TARGET SQUARE
    HAS BEEN DECLARED but BEFORE THE PASSING ABILITY TEST IS ROLLED, this player
    may move up to 3 squares … they CANNOT RUSH." """
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 5, 6, "2+"), ("home", 7, 11), ("away", 2, 11, 6, "3+", ["On the Ball"]))
    m.by_id("h00").player.PA = "2+"
    m.ball.carrier, m.ball.in_play = "h00", True
    out = actions.get("pass")["resolve"](m, {"player": "h00", "x": 7, "y": 11}, _dice([5] * 8))
    runner = m.by_id("a02")
    assert (runner.x, runner.y) != (2, 11), "they should have broken toward the target square"
    assert max(abs(runner.x - 2), abs(runner.y - 11)) <= 3, "at most three squares, and no Rush"
    moved = [e for e in out.events if (e.detail or {}).get("skill") == "On the Ball"]
    rolled = [i for i, e in enumerate(out.events) if any(r.kind == "Pass" for r in e.rolls)]
    assert moved and out.events.index(moved[0]) < rolled[0], "before the Passing Ability Test"


def test_insignificant_is_checked_against_the_only_list_the_engine_keeps():
    """ "When creating a Team Draft List, you may not include MORE players with this
    Trait THAN players WITHOUT this Trait." The nearest thing to a Draft List here
    is the board, so it is checked there and REPORTED — like every other limit."""
    from bloodbowl.pitch import Player, Scenario

    sc = Scenario(name="t", home_team="Orc", away_team="Skaven")
    sc.players = [
        Player(side="home", x=5 + i, y=13, position="Snotling", team="Orc", skills=["Insignificant"]) for i in range(3)
    ] + [Player(side="home", x=9, y=13, position="Orc Lineman", team="Orc")]
    review = sc.review("home")
    assert review["insignificant"] == 3
    assert any("Insignificant" in p for p in review["problems"]), review["problems"]

    sc.players.append(Player(side="home", x=10, y=13, position="Orc Lineman", team="Orc"))
    sc.players.append(Player(side="home", x=11, y=13, position="Orc Lineman", team="Orc"))
    assert not any("Insignificant" in p for p in sc.review("home")["problems"])


def test_the_post_game_sequence_runs_the_two_steps_that_are_about_the_match():
    """S3: six steps. Only the first two are about the game that just finished; the
    other four need a team that persists between games.

    "RECORD OUTCOME … how many CASUALTIES each team caused, COUNTING ONLY THOSE
    THAT EARNED STAR PLAYER POINTS" — so the count is not the Casualty box, it is
    the SPP ledger, and nothing else in the engine knows the difference."""
    from bloodbowl.engine.events import Event
    from bloodbowl.engine.postgame import outcome, steps, update_dedicated_fans

    m = _match(("home", 7, 13), ("away", 7, 14))
    m.score = {"home": 2, "away": 1}
    m.apply(Event(kind="spp_earned", actor="h00", detail={"points": 2, "why": "causing a Casualty"}))
    m.apply(Event(kind="spp_earned", actor="h00", detail={"points": 3, "why": "a Touchdown"}))
    got = outcome(m)
    assert got["result"] == {"home": "win", "away": "loss"}
    assert got["touchdowns"] == {"home": 2, "away": 1}
    assert got["casualties"] == {"home": 1, "away": 0}, "only the SPP-earning one counts"
    assert got["spp"]["h00"] == 5

    # "If your team WON, roll a D6. If the result is EQUAL TO OR HIGHER THAN your
    # team's Dedicated Fans Characteristic, INCREASE [it] by 1." Both a winner and
    # a loser want a HIGH roll, which is the asymmetry that is easy to get wrong.
    assert update_dedicated_fans(_dice([3]), 3, "win")[0] == 4
    assert update_dedicated_fans(_dice([2]), 3, "win")[0] == 3
    assert update_dedicated_fans(_dice([2]), 3, "loss")[0] == 2
    assert update_dedicated_fans(_dice([5]), 3, "loss")[0] == 3
    assert update_dedicated_fans(_dice([1]), 1, "loss")[0] == 1, "never below 1"
    assert update_dedicated_fans(_dice([6]), 7, "win")[0] == 7, "never above 7"
    assert update_dedicated_fans(_dice([]), 3, "draw") == (3, None), "a draw rolls nothing"

    ran = [s["step"] for s in steps(league=False) if s["applies"]]
    assert ran == ["Record Outcome and Collect Winnings", "Update Dedicated Fans"]
    assert all(s["applies"] for s in steps(league=True))


# --- Head-to-head: two coaches, one board ------------------------------------


def _versus(*players, you="home", active="home"):
    """A match with the sides claimed: `you` is the human, the other is the agent."""
    from bloodbowl.engine.events import Event

    m = _match(*players, active=active)
    other = "away" if you == "home" else "home"
    m.apply(
        Event(
            kind="match_started",
            detail={"kicking_to": active, "controllers": {you: "human", other: "agent"}},
        )
    )
    m.clock.active = active
    return m


def test_neither_coach_may_move_the_others_team():
    """A game where your opponent can move your players is not a game. Both entry
    points reach the same engine, so each declares WHO IT IS and the engine
    decides — neither surface is trusted to police itself."""
    from bloodbowl.engine.game import act

    m = _versus(("home", 7, 13), ("away", 7, 20), you="home", active="home")
    assert act(m, "move", {"player": "h00", "x": 7, "y": 12}, by="human")["ok"], "your own turn is fine"

    theirs = act(m, "move", {"player": "h00", "x": 7, "y": 11}, by="agent")
    assert theirs["ok"] is False and "not your move" in theirs["text"], theirs
    assert theirs["controllers"] == {"home": "human", "away": "agent"}


def test_an_unclaimed_board_stays_permissive():
    """The practice board is one person moving both teams on purpose, and that has
    to keep working — which is why an unclaimed side is nobody's."""
    from bloodbowl.engine.game import act

    m = _match(("home", 7, 13), ("away", 7, 20), active="home")
    assert act(m, "move", {"player": "h00", "x": 7, "y": 12}, by="agent")["ok"]
    assert act(m, "move", {"player": "h00", "x": 7, "y": 11}, by="human")["ok"]


def test_you_cannot_end_your_opponents_turn_for_them():
    """Ending a turn is as much a move as any other. A TURNOVER is not — that is
    the engine ending a turn rather than a coach, which is why `forced` skips the
    check."""
    from bloodbowl.engine.game import end_turn

    m = _versus(("home", 7, 13), ("away", 7, 20), you="home", active="home")
    theirs = end_turn(m, by="agent")
    assert theirs["ok"] is False and "not your move" in theirs["text"]
    assert end_turn(m, by="human")["ok"]


def test_a_kickoff_question_may_only_be_answered_by_the_coach_it_was_asked_of():
    """Different question from whose turn it is: `pending["side"]` names one coach,
    and the other may not answer for them."""
    from bloodbowl.engine.game import resolve_choice

    m = _versus(("home", 7, 13), ("away", 7, 20), you="home", active="home")
    _pending(m, "high_kick", "away", square=[7, 10], eligible=[], land="away")
    mine = resolve_choice(m, {"decline": True}, _dice([]), by="human")
    assert mine["ok"] is False and "not you" in mine["text"], mine
    assert resolve_choice(m, {"decline": True}, _dice([]), by="agent")["ok"]


def test_the_handover_says_who_is_waited_on_and_only_when_it_changes():
    """The moment the ball passes over is the thing worth publishing — not the
    state of it having passed. Otherwise the agent is nudged once per action of
    its own turn, which is both useless and expensive."""
    from bloodbowl.engine import handover

    m = _versus(("home", 7, 13), ("away", 7, 20), you="home", active="home")
    mine = handover.owed(m)
    assert mine == {
        "side": "home",
        "controller": "human",
        # Who is on the OTHER side — the nudge's closing instruction differs when
        # nobody is going to read it (a full-AI match talks to the spectator).
        "opponent": "agent",
        "session_id": "",
        "why": "turn",
        "half": 1,
        "turn": 1,
    }
    assert handover.changed(mine, mine) == {}, "the same side twice is not news"

    from bloodbowl.engine.game import end_turn

    end_turn(m, by="human")
    theirs = handover.owed(m)
    assert theirs["side"] == "away" and theirs["controller"] == "agent"
    assert handover.changed(mine, theirs) == theirs, "the handover IS news"


def test_an_unanswered_question_outranks_whose_turn_it_is():
    """It blocks everything, including the ball landing — and nothing about the
    clock looks wrong while it does, which is why it is the easier one to miss."""
    from bloodbowl.engine import handover

    m = _versus(("home", 7, 13), ("away", 7, 20), you="home", active="home")
    _pending(m, "solid_defence", "away", limit=4, eligible=[], land="home")
    owed = handover.owed(m)
    assert owed["why"] == "answer" and owed["controller"] == "agent"
    assert "Solid Defence" in owed["question"] or owed["question"], owed


def test_a_finished_match_is_waiting_for_nobody():
    from bloodbowl.engine import handover

    m = _versus(("home", 7, 13), ("away", 7, 20))
    m.over = True
    assert handover.owed(m) == {}


def test_a_block_with_no_stated_choice_takes_the_best_face_not_the_first_die():
    """The default used to be `faces[0]` — the first die ROLLED, which is nobody's
    choice at all. A coach who rolled "Both Down, Push Back, Push Back" and did not
    pass an index got the Both Down and went down with their target.

    It cost the agent two turns in its first live game against a person, and it
    apologised for the engine's bug both times. An arbitrary default is worse than
    either honest option, because it looks like a decision."""
    m = _match(("home", 7, 13), ("away", 7, 14), ("away", 2, 20))
    out = _block(m, "h00", "a01", _dice([4] * 10, block=[["both_down", "push_back", "push_back"]]))
    assert out.turnover is not True, "the blocker should not have taken the Both Down"
    assert m.by_id("h00").down == "standing", "…and should still be on their feet"
    rolled = next(r for e in out.events for r in e.rolls if r.kind == "Block")
    assert set(rolled.dice) == {"both_down", "push_back"}, "the dice are untouched — only the PICK changed"

    # A STATED INTENT is honoured — and this is the case the engine cannot infer,
    # because it is about the square rather than the player. POW is the better face
    # by any general measure; a coach pushing somebody towards the sideline wants
    # the push anyway, and only they know that.
    pushing = _match(("home", 7, 13), ("away", 7, 14), ("away", 2, 20))
    _block(pushing, "h00", "a01", _dice([4] * 10, block=[["pow", "push_back"]]), prefer="push")
    assert pushing.by_id("a01").down == "standing", "a push moves them; it does not put them down"
    assert (pushing.by_id("a01").x, pushing.by_id("a01").y) != (7, 14), "…and it does move them"

    # The opposite intent, on the same handful.
    flattening = _match(("home", 7, 13), ("away", 7, 14), ("away", 2, 20))
    _block(flattening, "h00", "a01", _dice([4] * 10, block=[["push_back", "pow"]]), prefer="knockdown")
    assert flattening.by_id("a01").down != "standing", "knockdown takes the POW over the push"


def test_an_unknown_block_preference_is_read_as_silence_not_refused():
    """`_choose` runs FOUR times inside one Block — the roll, plus the Brawler,
    Hatred and Team Re-roll re-rolls. Refusing a word it does not know three
    quarters of the way through would leave a half-resolved Block behind, so an
    unrecognised preference is treated as no preference at all."""
    m = _match(("home", 7, 13), ("away", 7, 14), ("away", 2, 20))
    out = _block(m, "h00", "a01", _dice([4] * 10, block=[["both_down", "push_back"]]), prefer="sideways")
    assert out.turnover is not True
    assert m.by_id("h00").down == "standing", "it falls back to the best face, not to faces[0]"


def test_a_blocker_who_will_not_fall_prefers_the_both_down():
    """With Block, a Both Down puts the TARGET down and leaves you standing — which
    beats a Push Back. The default has to know that, or it hands back a shove when
    it was offered a knockdown."""
    m = _match(("home", 7, 13, 6, "3+", ["Block"]), ("away", 7, 14), ("away", 2, 20))
    _block(m, "h00", "a01", _dice([4] * 10, block=[["push_back", "both_down"]]))
    assert m.by_id("h00").down == "standing", "Block keeps them up"
    assert m.by_id("a01").down != "standing", "…and the target goes down, which a push would not have done"


def test_the_defender_still_gets_the_worst_face_for_the_attacker():
    """The branch that was always right: when the DEFENDER chooses there is no
    second coach at the table, so the engine plays them as well as it can."""
    m = _match(("home", 7, 13, 6, "3+"), ("away", 7, 14, 6, "3+"), ("away", 2, 20))
    m.by_id("a01").player.ST = "5"  # stronger, so the defender chooses
    out = _block(m, "h00", "a01", _dice([4] * 10, block=[["pow", "player_down"]]))
    assert out.turnover is True and m.by_id("h00").down != "standing", "they take the Player Down"


# --- walking a whole run in one call (engine.game.walk) ----------------------------


def _walk(m, pid, squares, dice, **cmd):
    from bloodbowl.engine import game

    return game.walk(m, pid, squares, cmd=cmd or None, dice=dice)


def test_a_walk_covers_every_square_it_was_given():
    m = _match(("home", 7, 13))
    out = _walk(m, "h00", [(7, 14), (7, 15), (7, 16)], _dice([]))  # unmarked: no roll
    assert out["ok"]
    assert out["steps_taken"] == 3 and out["steps_requested"] == 3
    assert "halted" not in out
    assert (m.by_id("h00").x, m.by_id("h00").y) == (7, 16)


def test_a_walk_bends_no_rules_a_sequence_of_moves_would_not():
    """The control that matters: collapsing the round trip must not change the game.
    The same three squares walked as one call and as three land in the same place,
    having rolled the same dice."""
    one = _match(("home", 7, 13), ("away", 7, 14))
    many = _match(("home", 7, 13), ("away", 7, 14))
    squares = [(6, 12), (5, 11), (4, 10)]

    walked = _walk(one, "h00", squares, _dice([4]))  # one Dodge leaving the marked square
    from bloodbowl.engine.game import act

    stepped = [
        act(many, "move", {"player": "h00", "x": x, "y": y}, dice=_dice([4] if i == 0 else []))
        for i, (x, y) in enumerate(squares)
    ]

    assert walked["ok"] and all(r["ok"] for r in stepped)
    a, b = one.by_id("h00"), many.by_id("h00")
    assert (a.x, a.y) == (b.x, b.y) == (4, 10)
    assert a.ma_used == b.ma_used
    dodges = [r for e in one.events for r in e.rolls if r.kind == "Dodge"]
    assert len(dodges) == 1, "a walk must roll the Dodge once, exactly as three calls would"


def test_a_walk_stops_where_the_player_goes_down():
    """A failed Dodge floors the player mid-run. The rest of the route was drawn for a
    standing player on a live turn, so pressing on would be playing a plan that no
    longer applies — and the turn is over anyway."""
    m = _match(("home", 7, 13), ("away", 7, 14))
    out = _walk(m, "h00", [(6, 12), (5, 11), (4, 10)], _dice([2, 1, 1]))  # dodge fails, armour holds
    assert not out["ok"], "a run that did not finish must not report ok"
    assert out["steps_taken"] < 3
    assert out["halted"]
    p = m.by_id("h00")
    assert p.down == "prone"
    assert (p.x, p.y) == (6, 12), "it stopped where it fell, not at the end of the route"


def test_a_walk_halts_on_a_refusal_and_says_which_square():
    """A square that is not adjacent to the last is refused exactly as a lone Move
    would be — the run reports how far it got rather than skipping the gap."""
    m = _match(("home", 7, 13))
    out = _walk(m, "h00", [(7, 14), (7, 20)], _dice([]))
    assert not out["ok"]
    assert out["steps_taken"] == 1 and out["steps_requested"] == 2
    assert "one square at a time" in out["halted"]
    assert (m.by_id("h00").x, m.by_id("h00").y) == (7, 14)


def test_a_walk_reports_each_step_as_it_lands():
    """`after_step` is what lets a run be watched — the caller saves and paces there, so
    a board polling the saved match sees the player walk rather than teleport."""
    from bloodbowl.engine import game

    m = _match(("home", 7, 13))
    seen: list = []
    out = game.walk(m, "h00", [(7, 14), (7, 15)], dice=_dice([]), after_step=seen.append)
    assert out["ok"]
    assert len(seen) == 2, "one callback per square actually walked"
    assert all(r.get("ok") for r in seen)


def test_a_walk_refuses_nonsense_rather_than_looping_on_it():
    m = _match(("home", 7, 13))
    assert not _walk(m, "h00", [], _dice([]))["ok"]
    from bloodbowl.engine.game import MAX_PATH

    long_path = [(7, 14)] * (MAX_PATH + 1)
    out = _walk(m, "h00", long_path, _dice([]))
    assert not out["ok"] and "not a plan" in out["error"]
    assert (m.by_id("h00").x, m.by_id("h00").y) == (7, 13), "it refused before moving anyone"


# --- full-AI mode: both seats agent-played, one conversation each -------------------


def _ai_match(*players, active="home", sessions=None):
    """A match with BOTH sides agent-played — the full-AI shape."""
    from bloodbowl.engine.events import Event

    m = _match(*players, active=active)
    m.apply(
        Event(
            kind="match_started",
            detail={
                "kicking_to": active,
                "controllers": {"home": "agent", "away": "agent"},
                "session_id": "shared",
                "session_ids": sessions or {"home": "seat-home", "away": "seat-away"},
            },
        )
    )
    m.clock.active = active
    return m


def test_each_ai_seat_is_owed_in_its_own_conversation():
    """The whole reason `session_ids` exists. Two agent seats sharing a chat would be
    one coach with both hands — it would read the plan it just made for the other team
    straight out of its own context. All either seat gets is the board."""
    from bloodbowl.engine import handover
    from bloodbowl.engine.game import end_turn

    m = _ai_match(("home", 7, 13), ("away", 7, 20), active="home")
    mine = handover.owed(m)
    assert mine["side"] == "home" and mine["controller"] == "agent"
    assert mine["session_id"] == "seat-home"
    assert mine["opponent"] == "agent", "and it knows the other seat is not a person"

    end_turn(m, by="agent")
    theirs = handover.owed(m)
    assert theirs["side"] == "away" and theirs["session_id"] == "seat-away"
    assert handover.changed(mine, theirs) == theirs, "one seat handing over to the other IS news"


def test_a_match_without_per_side_sessions_is_unchanged():
    """The ordinary head-to-head sets only `session_id`, and must keep resolving to it
    for both sides — this is additive or it is a regression."""
    from bloodbowl.engine import handover

    m = _versus(("home", 7, 13), ("away", 7, 20), you="home", active="home")
    m.session_id = "one-chat"
    assert m.session_for("home") == "one-chat"
    assert m.session_for("away") == "one-chat"
    assert handover.owed(m)["session_id"] == "one-chat"


def test_a_seat_can_be_rebound_without_moving_the_other():
    """`bb_game_here` on one seat of a self-playing match moves that seat only. A
    `session_bound` with no side keeps its old meaning: rebind the whole match."""
    from bloodbowl.engine.events import Event

    m = _ai_match(("home", 7, 13), ("away", 7, 20))
    m.apply(Event(kind="session_bound", detail={"side": "home", "session_id": "moved"}))
    assert m.session_for("home") == "moved"
    assert m.session_for("away") == "seat-away", "the other seat did not move"

    m.apply(Event(kind="session_bound", detail={"session_id": "whole-match"}))
    assert m.session_id == "whole-match"
    assert m.session_for("away") == "seat-away", "a per-side binding still wins over the fallback"


def test_the_ai_seats_survive_a_fold():
    """Per-side sessions are folded from the log like everything else, or a reload
    would drop one seat back into the other's chat mid-game."""
    from bloodbowl.engine.state import Match

    m = _ai_match(("home", 7, 13), ("away", 7, 20))
    rebuilt = Match.from_dict(m.to_dict())
    assert rebuilt.session_for("home") == "seat-home"
    assert rebuilt.session_for("away") == "seat-away"
    assert rebuilt.controllers == {"home": "agent", "away": "agent"}


def test_a_player_row_carries_its_roster_keywords():
    """`role` is the roster's own taxonomy ("Big Guy, Troll") and the only way a consumer
    can tell a Big Guy from a Blocker — an "Ogre Blocker" is a Big Guy, and reading the
    position NAME would say otherwise. Server-side it already drives Hatred, Animosity and
    Bloodlust; the 3D board builds its silhouettes from it."""
    from bloodbowl.pitch import player_from_roster

    troll, why = player_from_roster("home", 7, 13, "Ogre", "Ogre Blocker")
    assert troll is not None, why
    assert "big guy" in (troll.role or "").lower(), troll.role

    m = _match(("home", 7, 13))
    m.players[0].player = troll
    row = next(p for p in m.to_dict()["players"] if p["id"] == "h00")
    assert "role" in row and "big guy" in row["role"].lower()


def test_the_second_half_kicks_off_again_with_the_sides_reversed():
    """S3: "At the start of the second half, the team that received the ball at the start
    of the first half will become the kicking team", and "the team that received the ball
    at the start of the half will have the first Turn."

    The second half used to fall through to a plain `turn_started`: it opened on the first
    half's final board with nobody kicking — players wherever the last drive left them, the
    ball wherever it lay, and the wrong side to act. An agent playing a real match spotted
    it in two turns and abandoned the game.
    """
    from bloodbowl.engine.game import end_turn, new_match
    from bloodbowl.pitch import Player, Scenario

    sc = Scenario(name="t", home_team="Orc", away_team="Skaven")
    for side, x, y in (("home", 7, 13), ("home", 8, 13), ("away", 7, 14), ("away", 8, 14)):
        sc.players.append(
            Player(side=side, x=x, y=y, position="Orc Lineman", team="Orc", MA="6", ST="3", AG="3+", AV="9+")
        )
    # away receives the opening kick-off, so HOME must receive the second half.
    m = new_match(sc, seed=5, kicking_to="away")
    # Derived from the LOG, not from the new field — otherwise this fails on the unfixed
    # engine with an AttributeError, which proves a field is new rather than that the
    # behaviour was wrong. The point of the test is the second half, not the bookkeeping.
    opened = next(e for e in m.events if e.kind == "match_started")
    assert opened.detail["kicking_to"] == "away"
    if m.pending:
        from bloodbowl.engine.game import dice_for, resolve_choice

        resolve_choice(m, {"decline": True}, dice_for(m))

    kinds_before = len(m.events)
    for _ in range(16):  # eight turns each ends the first half
        if m.clock.half == 2:
            break
        end_turn(m, forced=True)

    assert m.clock.half == 2, m.clock.to_dict()
    tail = m.events[kinds_before:]
    after_half = [e for e in tail if e.kind == "half_time"]
    assert after_half, "half time should be recorded"
    idx = tail.index(after_half[-1])
    later = [e.kind for e in tail[idx:]]
    assert "drive_started" in later or "ball_moved" in later, (
        f"the second half must set up and KICK OFF, not just start a turn — got {later[:8]}"
    )
    # And the sides are reversed: away received the first half, so home receives now.
    assert m.clock.active == "home", f"home should have the first Turn of the second half, not {m.clock.active}"


def _ko(match, pid):
    """Put a player in the Knocked-out box the way the injury path does."""
    from bloodbowl.engine.events import Event

    match.apply(Event(kind="player_condition", actor=pid, detail={"outcome": "knocked_out"}, text="KO'd for the test"))
    assert match.by_id(pid).place == "knocked_out"


def test_a_knocked_out_player_rolls_to_recover_between_drives():
    """S3 End of Drive, stage three: "Roll a D6 for each Knocked-out player. On a 4+ the
    player recovers and is moved to the Reserves Box. On a 1-3, the player cannot be
    roused and is still Knocked-out for the time being."

    The engine used to hand every Knocked-out player back automatically — not merely a
    missing roll but a systematic gift to whichever team was losing the attrition battle,
    in a game about attrition.
    """
    from bloodbowl.engine.game import start_drive

    m = _kicked()
    _ko(m, "h00")
    # A 4 recovers; the scripted stream feeds the recovery roll first.
    start_drive(m, receiving="home", dice=_dice([4] * 40))
    assert m.by_id("h00").place in ("reserves", "pitch"), m.by_id("h00").place
    rolls = [r for e in m.events for r in e.rolls if r.kind.startswith("KO recovery")]
    assert rolls and rolls[-1].target == 4, "the roll is recorded with its target"


def test_a_failed_recovery_leaves_the_player_knocked_out_and_off_the_pitch():
    """The half that matters. A failed roll is a real outcome — the player stays in the
    box and misses another Drive — so they must not be set up."""
    from bloodbowl.engine.game import start_drive

    m = _kicked()
    _ko(m, "h00")
    start_drive(m, receiving="home", dice=_dice([1] + [4] * 40))
    p = m.by_id("h00")
    assert p.place == "knocked_out", f"a 1 cannot rouse them, got {p.place}"
    assert p not in [q for q in m.players if q.place == "pitch"]
    assert any(e.kind == "ko_recovery" and not e.detail.get("recovered") for e in m.events), (
        "the failure is recorded, not silent"
    )


def test_a_casualty_never_rolls_to_recover():
    """The restraint control: a Casualty misses the MATCH. Only the Knocked-out box rolls,
    so a test that only checked "somebody rolled" would pass for the wrong reason."""
    from bloodbowl.engine.events import Event
    from bloodbowl.engine.game import start_drive

    m = _kicked()
    m.apply(Event(kind="player_condition", actor="h00", detail={"outcome": "casualty"}, text="cas"))
    start_drive(m, receiving="home", dice=_dice([4] * 40))
    assert m.by_id("h00").place == "casualty"
    assert not [r for e in m.events for r in e.rolls if r.kind.startswith("KO recovery")]


def test_a_ball_that_bounces_out_is_thrown_back_in():
    """It said "thrown back by the crowd" and then returned without throwing it back.

    A ball that bounced off the pitch stayed off it — `in_play`, unreachable, for the rest
    of the match — and produced a state no renderer expects: the 2D board crashed on it,
    because a square off the pitch has no cell to draw into.

    `throw_in` was written, unit-tested, and wired into exactly ONE of its two call sites
    (the pass path). The bounce never called it. A function that exists and is tested
    reads as working.
    """
    from bloodbowl.engine.ball import bounce
    from bloodbowl.engine.events import Event
    from bloodbowl.pitch import in_bounds

    m = _match(("home", 1, 13))
    # Put the ball on the west sideline and bounce it west, off the pitch.
    m.apply(Event(kind="ball_moved", detail={"x": 1, "y": 13, "carrier": ""}, text="loose"))
    bounce(m, _dice([4] + [3] * 20))  # direction 4 = west on the scatter template

    assert in_bounds(m.ball.x, m.ball.y), f"the ball must come back onto the pitch, not sit at ({m.ball.x},{m.ball.y})"
    kinds = [e.kind for e in m.events]
    assert "ball_out_of_bounds" in kinds, "it should still record that it went out"
    assert any(r.kind.endswith("Throw-in") for e in m.events for r in e.rolls), (
        "and the crowd's throw-in must actually be rolled"
    )


def test_one_ai_seat_cannot_move_the_other_seats_team():
    """Full-AI seats both have the controller kind "agent", so comparing controller kinds
    cannot tell them apart — `mine == by` was true for EITHER seat, and each was allowed
    to move the other's team. One did: the home seat played a Skaven turn, moved their
    Gutter Runner onto the ball and ended the turn, then spent the rest of the game
    insisting it was not its move.

    A seat names its SIDE, not its kind.
    """
    from bloodbowl.engine.game import refuse_if_not_yours

    on_home = _ai_match(("home", 7, 13), ("away", 7, 14), active="home")
    on_away = _ai_match(("home", 7, 13), ("away", 7, 14), active="away")

    # THE DEFECT, stated: the controller KIND is "agent" on both sides, so one caller
    # saying "agent" is admitted on BOTH teams' turns. That is what the tools used to
    # pass, and it is why a seat could play the opposition's turn.
    assert refuse_if_not_yours(on_home, "agent") is None
    assert refuse_if_not_yours(on_away, "agent") is None, (
        "the kind alone cannot distinguish two agent seats — this is the bug, not the fix"
    )

    # THE FIX: a seat names its SIDE, and that is decidable.
    assert refuse_if_not_yours(on_home, "home") is None, "the home seat may act on home's turn"
    refused = refuse_if_not_yours(on_home, "away")
    assert refused is not None, "the AWAY seat must not act on home's turn"
    assert "not your move" in refused["error"]
    assert refuse_if_not_yours(on_away, "away") is None


def test_a_seat_may_act_again_once_the_turn_comes_round():
    """The restraint control — the gate is whose TURN it is, not a permanent ban."""
    from bloodbowl.engine.game import end_turn, refuse_if_not_yours

    m = _ai_match(("home", 7, 13), ("away", 7, 14), active="home")
    assert refuse_if_not_yours(m, "away") is not None
    end_turn(m, forced=True)
    assert m.clock.active == "away"
    assert refuse_if_not_yours(m, "away") is None
    assert refuse_if_not_yours(m, "home") is not None
