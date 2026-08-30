"""Importing a FUMBBL team.

The fixtures are FOUR REAL TEAMS off `fumbbl.com/api/team/get/<id>`, reduced to the
fields the importer reads — coach and player names are other people's and a naming
rule needs none of them. They are real because the naming disagreements are not
guessable: every one of the five shapes below was found by looking, and three of
them contradict the rule the other two suggest.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bloodbowl import fumbbl

FIXTURES = Path(__file__).parent / "fixtures"


def team(name: str) -> dict:
    return json.loads((FIXTURES / f"team_{name}.json").read_text())


def _tools(registry):
    """The `registry` fixture is a bare recorder — registering is the caller's job."""
    import bloodbowl

    bloodbowl.register(registry)
    return {t.name: t for t in registry.tools}


@pytest.mark.parametrize(
    "fixture,expect_team,squad",
    [
        ("underworld", "Underworld Denizens", 15),
        ("norse", "Norse", 12),
        ("wood_elf", "Wood Elf", 11),  # two of the thirteen are not status 0
        ("dwarf", "Dwarf", 11),  # three of the fourteen are not status 0
    ],
)
def test_every_real_team_maps_completely(fixture, expect_team, squad):
    """No unmatched positions across four real rosters. This is the test that
    would fail if the S3 roster data were re-scraped with different wording."""
    r = fumbbl.import_team(team(fixture))
    assert r["ok"] and r["team"] == expect_team, r
    assert not r["unmatched"], r["unmatched"]
    assert sum(r["roster"]["players"].values()) == squad, r["roster"]["players"]


@pytest.mark.parametrize(
    "fixture,fumbbl_name,ours",
    [
        # Their prefix, not ours — and the `*` is a Big Guy marker, not a name.
        ("underworld", "Underworld Troll", "Troll*"),
        # Our prefix, not theirs. The SAME disagreement, running the other way.
        ("dwarf", "Blitzer", "Dwarf Blitzer"),
        # "Lineman" added by them …
        ("norse", "Norse Raider Lineman", "Norse Raider"),
        # … and dropped by them, plural into the bargain.
        ("underworld", "Underworld Snotlings", "Snotling Lineman"),
        # A word inserted mid-name: BB2020's name for the S3 positional.
        ("dwarf", "Dwarf Blocker Lineman", "Dwarf Lineman"),
    ],
)
def test_the_naming_disagreements_that_actually_occur(fixture, fumbbl_name, ours):
    """Five shapes, no two of which obey the same rule — which is why there is no
    strip rule and no append rule, only normalise-and-match-within-the-team."""
    r = fumbbl.import_team(team(fixture))
    hit = next((m for m in r["matched"] if m["fumbbl"] == fumbbl_name), None)
    assert hit, f"{fumbbl_name} did not match anything: {r['matched']} {r['unmatched']}"
    assert hit["position"] == ours, hit


def test_a_troll_slayer_is_never_a_troll():
    """THE COLLISION THE TEAM SCOPING EXISTS FOR. Dwarf's `Troll Slayer` and
    Underworld's `Troll*` are one token apart, and a global name index would have
    to choose between them. Matching inside the identified roster never sees the
    other team's positionals at all."""
    dwarf = fumbbl.import_team(team("dwarf"))
    slayer = next(m for m in dwarf["matched"] if m["fumbbl"] == "Troll Slayer")
    assert slayer["position"] == "Troll Slayer"

    under = fumbbl.import_team(team("underworld"))
    troll = next(m for m in under["matched"] if m["fumbbl"] == "Underworld Troll")
    assert troll["position"] == "Troll*"


def test_an_unmatched_position_is_named_and_never_guessed():
    """A wrong positional means wrong STATS, silently, in the one place a coach is
    trusting a table over their memory. Short and honest beats full and wrong."""
    payload = team("dwarf")
    payload["players"].append({"number": 99, "status": 0, "position": "Star Player Nobody"})
    r = fumbbl.import_team(payload)
    assert r["ok"], r
    bad = next(u for u in r["unmatched"] if u["fumbbl"] == "Star Player Nobody")
    assert "no Dwarf positional matches" in bad["why"]
    assert "Star Player Nobody" not in r["roster"]["players"]
    assert any("did not map" in n for n in r["notes"])


def test_an_ambiguous_name_refuses_rather_than_picking():
    """Two candidates is not a near miss, it is a question. The looser passes are
    only safe because this is what happens when they find more than one."""
    positionals = [{"position": "Dwarf Lineman"}, {"position": "Dwarf Blocker"}]

    # Not ambiguous, and worth pinning as the CONTRAST: the cores agree exactly
    # ({blocker}), so the more specific candidate wins outright at pass 3.
    pos, how, _ = fumbbl.match_position("Dwarf Blocker Lineman", positionals, "Dwarf")
    assert pos == "Dwarf Blocker", (pos, how)

    # This one only resolves at the loosest pass, and finds both inside it.
    pos, how, candidates = fumbbl.match_position("Big Nasty Blocker Lineman", positionals, "Dwarf")
    assert pos is None and how == "ambiguous", (pos, how)
    assert sorted(candidates) == ["Dwarf Blocker", "Dwarf Lineman"]


def test_the_staff_numbers_become_the_inputs_the_engine_already_takes():
    """Team Re-rolls, Fan Factor, Assistant Coaches and Cheerleaders are all inputs
    with stated defaults — two Kick-off Events add the roster numbers to a D6. A
    real team is simply a better source for them than a default."""
    r = fumbbl.import_team(team("underworld"))
    got = r["roster"]
    assert got["rerolls"] == 3
    assert got["fans"] == 2
    assert got["apothecary"] is True
    assert got["coaches"] == 0 and got["cheerleaders"] == 0

    dwarf = fumbbl.import_team(team("dwarf"))["roster"]
    assert dwarf["apothecary"] is False, "Dwarfs may not hire one and the import must not invent it"


def test_players_who_are_not_active_are_counted_not_dropped():
    """Silently shrinking a squad is the same failure as silently mis-matching one.
    What FUMBBL's status codes MEAN is not documented here, so they are reported by
    code rather than explained — a confident gloss is exactly what to avoid."""
    r = fumbbl.import_team(team("wood_elf"))
    note = next((n for n in r["notes"] if "Skipped" in n), None)
    assert note and "status 6" in note, r["notes"]
    assert "2 player(s)" in note


def test_an_established_team_says_why_it_breaks_the_drafting_rules():
    """Dedicated Fans cap at 3 WHEN DRAFTING and grow past it in a league, so
    `problems` will flag a five-fan team that is perfectly legal where it came
    from. Reporting that without the reason would be a wrong warning."""
    r = fumbbl.import_team(team("norse"))
    assert any("Dedicated Fans" in p for p in r["problems"])
    assert any("never subject to" in n for n in r["notes"])


def test_the_edition_gap_is_stated_rather_than_reconciled():
    r = fumbbl.import_team(team("norse"))
    assert any("BB2020" in n and "S3" in n for n in r["notes"])


def test_a_roster_we_do_not_ship_is_refused_not_approximated():
    """Importing a coach's Chaos Pact side as Chaos Renegades would hand them a
    roster they never picked."""
    payload = team("norse")
    payload["roster"] = {"id": 0, "name": "Chaos Pact"}
    r = fumbbl.import_team(payload)
    assert r["ok"] is False and "Chaos Pact" in r["text"]
    assert r["roster"] is None


def test_rubbish_in_is_refused_politely():
    for payload in ({}, {"players": []}, {"roster": None}):
        r = fumbbl.import_team(payload)
        assert r["ok"] is False and "FUMBBL team" in r["text"]


def test_the_import_reaches_the_agent_and_the_view(registry, client):
    """Both surfaces, because a tool that works and a route that 404s is how a
    whole API stayed dead here for weeks."""
    import json as j

    tools = _tools(registry)
    assert "bb_roster_import_fumbbl" in tools
    out = j.loads(tools["bb_roster_import_fumbbl"].invoke({"team_json": j.dumps(team("dwarf"))}))
    assert out["ok"] and out["team"] == "Dwarf" and not out["unmatched"]

    r = client.post("/api/plugins/bloodbowl/draft/import/fumbbl", json={"team": team("dwarf")})
    assert r.status_code == 200, r.text
    assert r.json()["team"] == "Dwarf"


def test_saving_is_opt_in(registry):
    """An import is worth reading before it becomes a stored roster."""
    import json as j

    from bloodbowl.draft import saved

    tools = _tools(registry)
    tools["bb_roster_import_fumbbl"].invoke({"team_json": j.dumps(team("norse"))})
    assert not saved(), "an import must not store anything unless asked"

    tools["bb_roster_import_fumbbl"].invoke({"team_json": j.dumps(team("norse")), "save": True})
    assert [s["name"] for s in saved()], "save=True must store it"


def test_a_team_whose_name_cannot_be_a_filename_still_saves(registry):
    """`draft.save` slugs the name and refuses an empty one, so a FUMBBL team
    called "!!!" — or named entirely in symbols — turned `save=True` into an
    unhandled ValueError, which is a 500 from the route. The roster name is
    always a real team name, so it is the fallback."""
    import json as j

    from bloodbowl.draft import saved

    payload = team("norse")
    payload["name"] = "!!!"
    r = fumbbl.import_team(payload)
    assert r["roster"]["name"] == "Norse", r["roster"]["name"]

    tools = _tools(registry)
    out = j.loads(tools["bb_roster_import_fumbbl"].invoke({"team_json": j.dumps(payload), "save": True}))
    assert out["ok"] and out.get("saved"), out
    assert [s["name"] for s in saved()] == ["Norse"]
