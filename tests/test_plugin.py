"""Registration, the two routers, the four view rules, and the agent tools.

The view assertions check the paths the plugin ACTUALLY registers — mounting the
routers exactly as ``register()`` does — rather than the paths the rules say to use.
That distinction is the whole bug class this file exists to prevent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = yaml.safe_load((ROOT / "protoagent.plugin.yaml").read_text())


# --- manifest -------------------------------------------------------------


def test_manifest_basics():
    assert MANIFEST["id"] == "bloodbowl"
    assert MANIFEST["enabled"] is False, "a plugin must ship DISABLED — enabling is the operator's call"
    assert isinstance(MANIFEST["config_section"], str), "config_section must be a string, not a list"


def test_manifest_and_pyproject_versions_match():
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert f'version = "{MANIFEST["version"]}"' in pyproject


def test_declared_capabilities_are_honest():
    """The plugin makes no outbound calls — the roster data ships with it."""
    assert MANIFEST["capabilities"]["network"] == []


# --- registration ---------------------------------------------------------


def test_register_mounts_two_routers_on_the_right_prefixes(registry):
    import bloodbowl

    bloodbowl.register(registry)
    prefixes = sorted(p for _, p in registry.routers)
    assert prefixes == ["/api/plugins/bloodbowl", "/plugins/bloodbowl"]


def test_register_contributes_the_tools(registry):
    import bloodbowl

    bloodbowl.register(registry)
    names = {t.name for t in registry.tools}
    assert {
        "bb_list_teams",
        "bb_get_roster",
        "bb_pitch_show",
        "bb_pitch_setup",
        "bb_pitch_place",
        "bb_pitch_clear",
        "bb_pitch_review",
    } <= names


def test_every_tool_has_a_real_description(registry):
    """An f-string 'docstring' leaves __doc__ None and the tool ships undescribed."""
    import bloodbowl

    bloodbowl.register(registry)
    for t in registry.tools:
        assert t.description and len(t.description) > 20, f"{t.name} has no usable description"


# --- the four view rules --------------------------------------------------


def test_the_declared_view_path_is_actually_served(client):
    path = MANIFEST["views"][0]["path"]
    assert path == "/plugins/bloodbowl/view"
    r = client.get(path)
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_the_view_is_public_not_under_api(client):
    """An iframe navigation cannot carry a bearer, so the page must not be gated."""
    assert MANIFEST["views"][0]["path"].startswith("/plugins/")
    assert client.get("/api/plugins/bloodbowl/view").status_code == 404


def test_view_is_four_rules_compliant(client):
    page = client.get("/plugins/bloodbowl/view").text
    # Rule 3 — slug-aware base derived from the served path.
    assert 'location.pathname.split("/plugins/")[0]' in page
    # Rule 4 — the DS kit, CSS off BASE and JS via dynamic import (it is an ES module).
    assert "/_ds/plugin-kit.css" in page
    assert 'import(BASE + "/_ds/plugin-kit.js")' in page
    # Rule 2 — data through the kit's authed fetch, on the gated prefix.
    assert "kit.apiFetch" in page
    assert "/api/plugins/bloodbowl" in page
    # Don't hand-roll what the kit owns.
    assert ":root{" not in page.replace(" ", "")
    assert 'addEventListener("message"' not in page


def test_the_view_never_hardcodes_a_theme_colour():
    """Theming comes from --pl-* tokens so the board repaints with the agent's theme."""
    page = (ROOT / "view.py").read_text()
    body = page.split("PAGE = r", 1)[1]
    import re

    # rgba() neutrals for grid lines/shadows are fine; a hex brand colour is not.
    assert not re.search(r"#[0-9a-fA-F]{6}\b", body), "hardcoded hex colour in the view"


# --- data API -------------------------------------------------------------


def test_meta_serves_geometry_teams_and_board(client):
    m = client.get("/api/plugins/bloodbowl/meta").json()
    assert m["geometry"]["length"] == 26 and m["geometry"]["width"] == 15
    assert "Amazon" in m["teams"]
    assert m["scenario"]["players"] == []


def test_place_move_remove_and_clear_round_trip(client):
    base = "/api/plugins/bloodbowl"
    r = client.post(
        f"{base}/place", json={"side": "home", "team": "Amazon", "position": "Jaguar Warrior", "x": 7, "y": 13}
    )
    assert r.status_code == 200
    assert r.json()["players"][0]["MA"] == "6"

    r = client.post(f"{base}/move", json={"from": {"x": 7, "y": 13}, "to": {"x": 8, "y": 12}})
    assert r.status_code == 200
    p = r.json()["players"][0]
    assert (p["x"], p["y"]) == (8, 12)

    assert client.post(f"{base}/remove", json={"x": 8, "y": 12}).json()["players"] == []

    client.post(f"{base}/place", json={"side": "home", "team": "Amazon", "position": "Eagle Warrior", "x": 5, "y": 13})
    assert client.post(f"{base}/clear", json={}).json()["players"] == []


def test_place_off_pitch_is_a_400(client):
    r = client.post(
        "/api/plugins/bloodbowl/place",
        json={"side": "home", "team": "Amazon", "position": "Eagle Warrior", "x": 99, "y": 1},
    )
    assert r.status_code == 400


def test_unknown_team_is_a_400_not_a_silent_blank(client):
    r = client.post(
        "/api/plugins/bloodbowl/place",
        json={"side": "home", "team": "Nurglings United", "position": "Whatever", "x": 5, "y": 13},
    )
    assert r.status_code == 400


def test_moving_from_an_empty_square_404s(client):
    r = client.post("/api/plugins/bloodbowl/move", json={"from": {"x": 1, "y": 1}, "to": {"x": 2, "y": 2}})
    assert r.status_code == 404


def test_roster_endpoint(client):
    r = client.get("/api/plugins/bloodbowl/roster", params={"team": "Skaven"})
    assert r.status_code == 200 and r.json()["name"] == "Skaven"
    assert client.get("/api/plugins/bloodbowl/roster", params={"team": "nope"}).status_code == 404


def test_state_persists_across_requests(client):
    base = "/api/plugins/bloodbowl"
    client.post(f"{base}/place", json={"side": "home", "team": "Orc", "position": "Blitzer", "x": 6, "y": 13})
    assert len(client.get(f"{base}/state").json()["players"]) == 1


# --- tools ----------------------------------------------------------------


def _tool(registry, name):
    import bloodbowl

    if not registry.tools:
        bloodbowl.register(registry)
    return next(t for t in registry.tools if t.name == name)


def test_setup_tool_places_a_formation(registry):
    setup = _tool(registry, "bb_pitch_setup")
    players = json.dumps(
        [
            {"side": "home", "position": "Eagle Warrior", "x": 5, "y": 13},
            {"side": "home", "position": "Eagle Warrior", "x": 6, "y": 13},
            {"side": "home", "position": "Jaguar Warrior", "x": 7, "y": 13},
        ]
    )
    out = json.loads(setup.invoke({"home_team": "Amazon", "players": players}))
    assert out["ok"], out["errors"]
    assert out["placed"] == 3
    assert out["home"]["on_line_of_scrimmage"] == 3


def test_setup_tool_reports_a_bad_team_rather_than_guessing(registry):
    setup = _tool(registry, "bb_pitch_setup")
    out = json.loads(setup.invoke({"home_team": "Definitely Not A Team", "players": "[]"}))
    assert out["ok"] is False
    assert "known" in out


def test_setup_tool_rejects_malformed_players_json(registry):
    setup = _tool(registry, "bb_pitch_setup")
    out = json.loads(setup.invoke({"players": "not json"}))
    assert out["ok"] is False and "JSON" in out["error"]


def test_get_roster_tool_returns_structured_rows(registry):
    out = json.loads(_tool(registry, "bb_get_roster").invoke({"team": "Amazon"}))
    assert out["ok"]
    jag = next(p for p in out["team"]["positionals"] if p["position"] == "Jaguar Warrior")
    assert jag["ST"] == "4" and jag["cost"] == "110K"


def test_get_roster_tool_lists_known_teams_on_a_miss(registry):
    out = json.loads(_tool(registry, "bb_get_roster").invoke({"team": "Wharglebargle"}))
    assert out["ok"] is False and "Amazon" in out["known"]


def test_list_teams_tool(registry):
    out = json.loads(_tool(registry, "bb_list_teams").invoke({}))
    assert out["count"] >= 28
    assert any(t["name"] == "Skaven" for t in out["teams"])


def test_show_and_clear_tools(registry):
    place = _tool(registry, "bb_pitch_place")
    json.loads(place.invoke({"side": "home", "position": "Eagle Warrior", "x": 5, "y": 13, "team": "Amazon"}))
    shown = json.loads(_tool(registry, "bb_pitch_show").invoke({}))
    assert shown["geometry"]["length"] == 26
    assert len(shown["scenario"]["players"]) == 1
    assert json.loads(_tool(registry, "bb_pitch_clear").invoke({}))["removed"] == 1


def test_review_tool(registry):
    out = json.loads(_tool(registry, "bb_pitch_review").invoke({"side": "home"}))
    assert out["side"] == "home" and out["legal"] is True


@pytest.mark.parametrize("bad", ["", "sideways", "HOME"])
def test_review_tool_falls_back_to_home_on_a_bad_side(registry, bad):
    out = json.loads(_tool(registry, "bb_pitch_review").invoke({"side": bad}))
    assert out["side"] == "home"
