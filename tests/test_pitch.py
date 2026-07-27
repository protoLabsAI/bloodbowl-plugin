"""Geometry, roster data and scenario logic — the parts that must be exactly right,
because a board drawn to the wrong dimensions is worse than no board."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bloodbowl import pitch

ROOT = Path(__file__).resolve().parent.parent


# --- geometry -------------------------------------------------------------


def test_pitch_is_26_by_15():
    assert pitch.LENGTH == 26
    assert pitch.WIDTH == 15


def test_the_zones_tile_the_width_exactly():
    # 4 + 7 + 4 = 15. If this ever fails the board has gaps or overlaps.
    assert 2 * pitch.WIDE_ZONE_WIDTH + pitch.CENTRE_FIELD_WIDTH == pitch.WIDTH


def test_zone_boundaries():
    assert pitch.zone_of(1) == "wide_left"
    assert pitch.zone_of(4) == "wide_left"
    assert pitch.zone_of(5) == "centre"
    assert pitch.zone_of(11) == "centre"
    assert pitch.zone_of(12) == "wide_right"
    assert pitch.zone_of(15) == "wide_right"


def test_end_zones_are_one_square_deep_at_each_end():
    assert pitch.is_end_zone(1)
    assert not pitch.is_end_zone(2)
    assert not pitch.is_end_zone(25)
    assert pitch.is_end_zone(26)


def test_line_of_scrimmage_splits_the_halves():
    assert pitch.half_of(13) == "home"
    assert pitch.half_of(14) == "away"
    assert pitch.on_line_of_scrimmage("home", 13)
    assert pitch.on_line_of_scrimmage("away", 14)
    assert not pitch.on_line_of_scrimmage("home", 14)


def test_bounds():
    assert pitch.in_bounds(1, 1)
    assert pitch.in_bounds(15, 26)
    assert not pitch.in_bounds(0, 1)
    assert not pitch.in_bounds(16, 1)
    assert not pitch.in_bounds(1, 27)


# --- roster data ----------------------------------------------------------


def test_roster_data_ships_and_parses():
    data = pitch.rosters()
    assert data["teams"], "no teams in the shipped roster data"
    assert len(data["teams"]) >= 28
    assert data["pitch"]["length"] == 26 and data["pitch"]["width"] == 15


def test_every_positional_has_a_full_statline():
    """A hover card with a blank stat is worse than no card — it looks authoritative."""
    missing = []
    for team in pitch.rosters()["teams"]:
        for p in team["positionals"]:
            for key in ("MA", "ST", "AG", "PA", "AV", "cost"):
                if not str(p.get(key, "")).strip():
                    missing.append(f"{team['name']}/{p['position']}.{key}")
    assert not missing, f"incomplete statlines: {missing[:12]}"


def test_amazon_roster_matches_the_verified_source():
    """Pinned against the published table. If the scrape regresses, this catches it."""
    team = pitch.find_team("Amazon")
    got = [
        (p["qty"], p["position"], p["MA"], p["ST"], p["AG"], p["PA"], p["AV"], p["cost"]) for p in team["positionals"]
    ]
    assert got == [
        ("0-16", "Eagle Warrior", "6", "3", "3+", "4+", "8+", "50K"),
        ("0-2", "Python Warrior", "6", "3", "3+", "3+", "8+", "80K"),
        ("0-2", "Piranha Warrior", "7", "3", "3+", "4+", "8+", "90K"),
        ("0-2", "Jaguar Warrior", "6", "4", "3+", "4+", "9+", "110K"),
    ]


def test_multiword_skills_survive_parsing():
    """'On the Ball' must be one skill, not three — the bullet is the separator."""
    team = pitch.find_team("Amazon")
    python = pitch.find_position(team, "Python Warrior")
    assert "On the Ball" in python["skills"]
    assert "Safe Pass" in python["skills"]
    assert "the" not in python["skills"]


def test_team_lookup_is_forgiving():
    assert pitch.find_team("amazon")["name"] == "Amazon"
    assert pitch.find_team("Amazons")["name"] == "Amazon"
    assert pitch.find_team("nope") is None


def test_no_non_breaking_hyphens_leak_into_the_data():
    """The site uses U+2011 in quantities; unnormalised it breaks every qty compare."""
    raw = (ROOT / "data" / "rosters.json").read_text(encoding="utf-8")
    assert "‑" not in raw


# --- scenario -------------------------------------------------------------


def test_place_and_replace():
    sc = pitch.Scenario()
    ok, _ = sc.place(pitch.Player(side="home", x=7, y=13, position="Jaguar Warrior"))
    assert ok and len(sc.players) == 1
    # Placing onto an occupied square replaces rather than stacking.
    sc.place(pitch.Player(side="away", x=7, y=13, position="Blitzer"))
    assert len(sc.players) == 1
    assert sc.at(7, 13).side == "away"


def test_place_rejects_off_pitch():
    sc = pitch.Scenario()
    ok, msg = sc.place(pitch.Player(side="home", x=99, y=1))
    assert not ok and "off the pitch" in msg


def test_clear_one_side_or_all():
    sc = pitch.Scenario()
    sc.place(pitch.Player(side="home", x=1, y=1))
    sc.place(pitch.Player(side="away", x=2, y=2))
    assert sc.clear("home") == 1
    assert [p.side for p in sc.players] == ["away"]
    assert sc.clear(None) == 1
    assert sc.players == []


def test_badge_defaults_to_initials():
    assert pitch.Player(side="home", x=1, y=1, position="Jaguar Warrior").badge() == "JW"
    assert pitch.Player(side="home", x=1, y=1, label="X").badge() == "X"


def test_player_from_roster_carries_real_stats():
    p, err = pitch.player_from_roster("home", 7, 13, "Amazon", "Jaguar Warrior")
    assert err == ""
    assert (p.MA, p.ST, p.AG, p.PA, p.AV) == ("6", "4", "3+", "4+", "9+")
    assert p.skills == ["Defensive", "Dodge"]


def test_player_from_roster_explains_a_bad_position():
    p, err = pitch.player_from_roster("home", 1, 1, "Amazon", "Treeman")
    assert p is None
    assert "Eagle Warrior" in err  # the error lists what IS available


def test_round_trip_through_dict():
    sc = pitch.Scenario(name="x", home_team="Amazon")
    sc.place(pitch.Player(side="home", x=7, y=13, position="Jaguar Warrior", MA="6"))
    again = pitch.Scenario.from_dict(json.loads(json.dumps(sc.to_dict())))
    assert again.name == "x" and again.home_team == "Amazon"
    assert again.players[0].position == "Jaguar Warrior" and again.players[0].MA == "6"


def test_to_dict_annotates_zone_and_los():
    sc = pitch.Scenario()
    sc.place(pitch.Player(side="home", x=2, y=13, position="A"))
    row = sc.to_dict()["players"][0]
    assert row["zone"] == "wide_left"
    assert row["on_los"] is True


# --- review (reports, never blocks) ---------------------------------------


def test_review_flags_a_thin_line_of_scrimmage():
    sc = pitch.Scenario()
    for x in (5, 6):
        sc.place(pitch.Player(side="home", x=x, y=13))
    r = sc.review("home")
    assert not r["legal"]
    assert any("Line of Scrimmage" in p for p in r["problems"])


def test_review_flags_a_crowded_wide_zone():
    sc = pitch.Scenario()
    for x in (5, 6, 7):
        sc.place(pitch.Player(side="home", x=x, y=13))
    for y in (10, 11, 12):
        sc.place(pitch.Player(side="home", x=1, y=y))
    r = sc.review("home")
    assert not r["legal"]
    assert any("Wide Zone" in p for p in r["problems"])


def test_review_passes_a_legal_setup():
    sc = pitch.Scenario()
    for x in (5, 6, 7):  # three on the line, centre field
        sc.place(pitch.Player(side="home", x=x, y=13))
    for x, y in ((2, 10), (13, 10), (8, 9)):
        sc.place(pitch.Player(side="home", x=x, y=y))
    r = sc.review("home")
    assert r["legal"], r["problems"]
    assert r["on_line_of_scrimmage"] == 3


def test_review_flags_deployment_past_the_line():
    sc = pitch.Scenario()
    for x in (5, 6, 7):
        sc.place(pitch.Player(side="home", x=x, y=13))
    sc.place(pitch.Player(side="home", x=8, y=20))  # into the away half
    r = sc.review("home")
    assert any("beyond the Line of Scrimmage" in p for p in r["problems"])


@pytest.mark.parametrize("side", ["home", "away"])
def test_review_of_an_empty_side_is_quiet(side):
    """An empty board is the starting state, not an error."""
    assert pitch.Scenario().review(side)["legal"]
