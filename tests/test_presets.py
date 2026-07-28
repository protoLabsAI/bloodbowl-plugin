"""Preset setups: the shipped reference shapes and the operator's saved ones.

The most valuable test here is the legality sweep. A shipped preset called
"Standard defence" that quietly breaks the S3 setup limits is worse than no
preset at all — it teaches the wrong shape, and the board's own review would
contradict it the moment anyone looked.
"""

from __future__ import annotations

import json

import pytest


def _board(*players, home="Orc", away="Skaven"):
    from bloodbowl.pitch import Player, Scenario
    from bloodbowl.store import save

    sc = Scenario(name="test", home_team=home, away_team=away)
    for side, x, y in players:
        sc.players.append(Player(side=side, x=x, y=y, position="Orc Lineman", team=home))
    save(sc)
    return sc


# --- the shipped shapes ----------------------------------------------------


def test_every_shipped_preset_is_legal_under_the_s3_setup_limits():
    """Asserted rather than commented. The board already knows the rules —
    11 players, 3+ on the Line of Scrimmage in the Centre Field, 2 per Wide
    Zone — so a reference layout should be checked against the same code the
    operator's own setup is checked against."""
    from bloodbowl.presets import BUILTIN, apply_to

    for preset in BUILTIN:
        if preset.kind != "setup":
            continue  # a cage is a mid-game shape and straddles the line on purpose
        sc = apply_to(preset)
        for side in ("home", "away"):
            if not [p for p in sc.players if p.side == side]:
                continue
            review = sc.review(side)
            assert review["legal"], f"{preset.name} ({side}): {review['problems']}"


def test_the_shipped_presets_cover_the_shapes_a_coach_asks_for_by_name():
    from bloodbowl.presets import BUILTIN

    names = {p.name for p in BUILTIN}
    assert {"Standard defence", "Cage", "Kick-off receive"} <= names
    kinds = {p.name: p.kind for p in BUILTIN}
    assert kinds["Cage"] == "formation", "a cage is not a kick-off setup"
    assert kinds["Standard defence"] == "setup"
    for p in BUILTIN:
        assert p.note, f"{p.name} has no note — a preset nobody can distinguish is not referenceable"
        assert p.builtin is True


def test_a_shipped_preset_stores_roles_not_one_teams_positionals():
    """A preset naming Orc positionals would be unusable for Skaven, and the
    point of a reference layout is that it transfers."""
    from bloodbowl.presets import BUILTIN

    for preset in BUILTIN:
        for p in preset.players:
            assert not p.get("position"), f"{preset.name} pins a positional: {p}"
            assert p.get("label"), f"{preset.name} has an unlabelled token: {p}"


# --- naming ----------------------------------------------------------------


def test_names_that_differ_only_by_case_or_punctuation_are_the_same_preset():
    from bloodbowl.presets import slug

    assert slug("Orc Defence") == slug("orc defence") == slug("Orc  --  Defence!")
    assert slug("") == "untitled"


def test_a_shipped_preset_cannot_be_overwritten_or_deleted():
    """A reference layout somebody has edited is no longer a reference."""
    from bloodbowl.presets import delete, save

    _board(("home", 7, 13))
    preset, err = save("standard defence", __import__("bloodbowl.store", fromlist=["load"]).load())
    assert preset is None and "shipped" in err

    done, err = delete("Standard defence")
    assert done is False and "shipped" in err


def test_a_saved_preset_cannot_shadow_a_shipped_one():
    from bloodbowl.presets import all_presets, presets_dir

    (presets_dir() / "standard-defence.json").write_text(
        json.dumps({"name": "Standard defence", "players": [{"side": "home", "x": 1, "y": 1}]})
    )
    matches = [p for p in all_presets() if p.name == "Standard defence"]
    assert len(matches) == 1 and matches[0].builtin is True


# --- saving and loading ----------------------------------------------------


def test_saving_and_loading_round_trips_the_board():
    from bloodbowl.presets import apply_to, find, save
    from bloodbowl.store import load

    _board(("home", 7, 13), ("home", 8, 13), ("away", 7, 14))
    preset, err = save("My wall", load(), note="three across")
    assert preset is not None, err
    assert preset.counts() == {"home": 2, "away": 1}

    back = find("my wall")
    assert back is not None and back.note == "three across"
    sc = apply_to(back)
    assert sorted((p.side, p.x, p.y) for p in sc.players) == [
        ("away", 7, 14),
        ("home", 7, 13),
        ("home", 8, 13),
    ]


def test_a_saved_preset_keeps_real_positionals_so_statlines_survive():
    """A saved board is a specific team's, unlike a shipped shape — so it should
    come back with working hover cards, not blank tokens."""
    from bloodbowl.presets import apply_to, find, save
    from bloodbowl.store import load

    _board(("home", 7, 13))
    save("Real players", load())
    sc = apply_to(find("Real players"))
    assert sc.players[0].position == "Orc Lineman"
    assert sc.players[0].MA, "the statline was not re-hydrated"


def test_an_empty_board_will_not_be_saved():
    from bloodbowl.pitch import Scenario
    from bloodbowl.presets import save

    preset, err = save("Nothing", Scenario())
    assert preset is None and "empty" in err


def test_a_preset_can_be_loaded_for_one_side_only():
    from bloodbowl.presets import apply_to, find, save
    from bloodbowl.store import load

    _board(("home", 7, 13), ("away", 7, 14))
    save("Both sides", load())
    sc = apply_to(find("Both sides"), side="home")
    assert [p.side for p in sc.players] == ["home"]


def test_mirroring_flips_a_home_shape_into_the_away_half():
    """One stored defence should serve both as a defence and as the thing you
    practise attacking into."""
    from bloodbowl.pitch import LENGTH
    from bloodbowl.presets import apply_to, find

    sc = apply_to(find("Standard defence"), mirror=True)
    assert sc.players, "mirroring produced an empty board"
    assert all(p.side == "away" for p in sc.players)
    assert all(p.y > LENGTH // 2 for p in sc.players), "a mirrored home shape belongs in the away half"


def test_a_mirrored_shipped_preset_is_still_legal():
    """The reflection is around the Line of Scrimmage, so legality must survive
    it — otherwise mirroring silently produces an illegal setup."""
    from bloodbowl.presets import BUILTIN, apply_to

    for preset in BUILTIN:
        if preset.kind != "setup":
            continue
        sc = apply_to(preset, mirror=True)
        if not [p for p in sc.players if p.side == "away"]:
            continue
        review = sc.review("away")
        assert review["legal"], f"{preset.name} mirrored: {review['problems']}"


def test_deleting_a_saved_preset():
    from bloodbowl.presets import delete, find, save
    from bloodbowl.store import load

    _board(("home", 7, 13))
    save("Scratch", load())
    assert find("Scratch") is not None
    done, err = delete("Scratch")
    assert done and not err
    assert find("Scratch") is None


def test_one_unreadable_file_does_not_hide_the_library():
    from bloodbowl.presets import all_presets, presets_dir, save
    from bloodbowl.store import load

    _board(("home", 7, 13))
    save("Good one", load())
    (presets_dir() / "broken.json").write_text("{ not json")
    names = [p.name for p in all_presets()]
    assert "Good one" in names


# --- the tools and the routes ---------------------------------------------


def _tool(registry, name):
    import bloodbowl

    if not registry.tools:
        bloodbowl.register(registry)
    return next(t for t in registry.tools if t.name == name)


def test_preset_tools_round_trip(registry):
    _board(("home", 7, 13), ("home", 8, 13))
    out = json.loads(_tool(registry, "bb_preset_save").invoke({"name": "Wall", "note": "n"}))
    assert out["ok"] and out["home"] == 2

    listed = json.loads(_tool(registry, "bb_presets").invoke({}))
    assert any(p["name"] == "Wall" for p in listed["presets"])
    assert any(p["builtin"] for p in listed["presets"]), "the shipped shapes should be listed too"

    loaded = json.loads(_tool(registry, "bb_preset_load").invoke({"name": "wall"}))
    assert loaded["ok"] and len(loaded["board"]["players"]) == 2


def test_loading_an_unknown_preset_lists_the_known_ones(registry):
    out = json.loads(_tool(registry, "bb_preset_load").invoke({"name": "nope"}))
    assert out["ok"] is False
    assert any(n == "Standard defence" for n in out["known"])


def test_preset_routes(client):
    base = "/api/plugins/bloodbowl"
    assert any(p["builtin"] for p in client.get(f"{base}/presets").json()["presets"])

    client.post(f"{base}/place", json={"side": "home", "team": "Orc", "position": "Orc Lineman", "x": 7, "y": 13})
    assert client.post(f"{base}/presets/save", json={"name": "Route test"}).status_code == 200

    r = client.post(f"{base}/presets/load", json={"name": "Standard defence"})
    assert r.status_code == 200 and len(r.json()["players"]) > 3

    assert client.post(f"{base}/presets/load", json={"name": "ghost"}).status_code == 404
    assert client.post(f"{base}/presets/delete", json={"name": "Standard defence"}).status_code == 400


@pytest.mark.parametrize("name", ["Standard defence", "Cage", "Kick-off receive", "Wide zone press"])
def test_each_shipped_preset_loads_onto_the_board(client, name):
    r = client.post("/api/plugins/bloodbowl/presets/load", json={"name": name})
    assert r.status_code == 200 and r.json()["players"], name


def test_loading_a_shipped_preset_keeps_the_teams_you_had_chosen():
    """A shipped preset stores ROLES and carries no teams, so it has no opinion
    about who is playing — overwriting Orc-vs-Skaven with two blanks throws away
    work the preset never touched. Found in the browser, where loading a defence
    silently emptied both team pickers."""
    from bloodbowl.presets import apply_to, find
    from bloodbowl.store import load

    _board(("home", 7, 13))
    sc = apply_to(find("Standard defence"), current=load())
    assert (sc.home_team, sc.away_team) == ("Orc", "Skaven")


def test_a_saved_preset_that_recorded_teams_still_restores_them():
    from bloodbowl.pitch import Scenario
    from bloodbowl.presets import apply_to, find, save
    from bloodbowl.store import load

    _board(("home", 7, 13), home="Orc", away="Skaven")
    save("Orc board", load())
    other = Scenario(home_team="Dwarf", away_team="Elven Union")
    sc = apply_to(find("Orc board"), current=other)
    assert (sc.home_team, sc.away_team) == ("Orc", "Skaven"), "a preset with teams wins over the current board"
