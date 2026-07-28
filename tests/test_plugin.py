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


def test_register_mounts_the_routers_on_the_right_prefixes(registry):
    """The page is PUBLIC; everything that reads or writes state is GATED. The
    match router shares the gated prefix — it is state like any other."""
    import bloodbowl

    bloodbowl.register(registry)
    prefixes = sorted(p for _, p in registry.routers)
    # ONE router per prefix — the host discards a second one for the same prefix.
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
    """Still all four rules — but the page is a shell now, so the kit import and
    the authed fetch live in the module the shell loads."""
    page = client.get("/plugins/bloodbowl/view").text
    modules = client.get("/plugins/bloodbowl/static/js/api.js").text
    # Rule 3 — slug-aware base derived from the served path.
    assert 'location.pathname.split("/plugins/")[0]' in page
    # Rule 4 — the DS kit, CSS off BASE and JS via dynamic import (it is an ES module).
    assert "/_ds/plugin-kit.css" in page
    assert 'import(window.BASE + "/_ds/plugin-kit.js")' in modules
    # Rule 2 — data through the kit's authed fetch, on the gated prefix.
    assert "kit.apiFetch" in modules
    assert "/api/plugins/bloodbowl" in modules
    # Don't hand-roll what the kit owns.
    both = page + (WEB / "style.css").read_text()
    assert ":root{" not in both.replace(" ", "")
    assert 'addEventListener("message"' not in page + modules


def test_the_view_never_hardcodes_a_theme_colour():
    """Theming comes from --pl-* tokens so the board repaints with the agent's theme."""
    import re

    body = (ROOT / "web" / "style.css").read_text()
    # rgba() neutrals for grid lines/shadows are fine; a hex brand colour is not.
    # Nor is a hex FALLBACK: --pl-color-status-{success,warning,error,info} all
    # exist in the kit, so a fallback would only ever mask a token that moved.
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


# --- the view, now split into real files -----------------------------------
#
# These assertions used to grep one PAGE string. The view is now index.html plus
# a stylesheet and ES modules, so they read whichever file owns the behaviour —
# but each still pins the same defect it always did, found by driving the board.

WEB = ROOT / "web"


def _web(*names) -> str:
    """Concatenate web assets. No name = everything the page ships."""
    paths = [WEB / n for n in names] if names else sorted(WEB.rglob("*.[hcj][tsa]*"))
    return "\n".join(p.read_text() for p in paths if p.is_file())


# --- v2 regressions: every defect found in live use ------------------------


def test_opening_the_view_does_not_mutate_the_board():
    """The first version POSTed both teams on load, stomping the agent's setup."""
    page = _web("js/main.js")
    boot = page.split("async function boot()", 1)[1].split("\n}", 1)[0]
    assert 'api("/teams"' not in boot, "boot() must reflect the board, never write to it"
    assert "NEVER write to it" in page


def test_badge_type_scales_with_the_board_not_the_viewport():
    """`.85vw` resolved to ~7px in a rail panel and made every player unreadable."""
    css = (ROOT / "web" / "style.css").read_text()
    assert "vw)" not in css, "viewport units make badges unreadable in a panel"
    assert "--cell" in css and "ResizeObserver" in _web("js/board.js")


def test_the_board_has_coordinate_rulers():
    page = _web()
    for probe in ("ruler-top", "ruler-left", '$("#coord")'):
        assert probe in page, f"missing {probe} — you should not have to count squares to find (7,13)"


def test_removal_is_an_explicit_target_not_the_whole_document():
    """Dropping on the palette used to silently delete a player."""
    page = _web("js/setup.js")
    assert 'trash.addEventListener("drop"' in page
    assert 'document.addEventListener("drop"' not in page


def test_render_is_incremental_so_a_poll_cannot_tear_out_the_drag_target():
    assert "dataset.sig === sig" in _web("js/setup.js"), "render must skip nodes that did not change"
    assert "state.dragging" in _web("js/main.js"), "the poller must stand down mid-drag"


def test_undo_exists_and_posts_a_whole_board():
    page = _web("js/setup.js")
    assert "state.undo" in page and 'api("/replace"' in page


def test_replace_endpoint_round_trips_a_board(client):
    base = "/api/plugins/bloodbowl"
    client.post(f"{base}/place", json={"side": "home", "team": "Amazon", "position": "Eagle Warrior", "x": 5, "y": 13})
    snapshot = client.get(f"{base}/state").json()
    client.post(f"{base}/clear", json={})
    assert client.get(f"{base}/state").json()["players"] == []
    restored = client.post(f"{base}/replace", json=snapshot).json()
    assert len(restored["players"]) == 1
    assert restored["players"][0]["position"] == "Eagle Warrior"


def test_replace_rejects_an_off_pitch_board(client):
    r = client.post(
        "/api/plugins/bloodbowl/replace",
        json={"players": [{"side": "home", "x": 99, "y": 1}]},
    )
    assert r.status_code == 400


def test_palette_rebuilds_when_the_agent_changes_teams():
    """The poller lives in main.js now, but the rebuild it triggers is setup's."""
    assert "await setup.poll()" in _web("js/main.js")
    poll = _web("js/setup.js").split("export async function poll()", 1)[1]
    assert "teamsChanged" in poll and "buildPalette()" in poll


def test_a_write_keeps_the_outgoing_board_as_a_backup(client):
    """One careless whole-board write should not be unrecoverable."""
    base = "/api/plugins/bloodbowl"
    client.post(f"{base}/place", json={"side": "home", "team": "Amazon", "position": "Eagle Warrior", "x": 5, "y": 13})
    client.post(f"{base}/replace", json={"players": []})  # the wipe
    prev = client.get(f"{base}/previous").json()["scenario"]
    assert prev and len(prev["players"]) == 1
    assert prev["players"][0]["position"] == "Eagle Warrior"


def test_replace_rehydrates_stats_from_the_roster(client):
    """A trimmed payload (position only) must come back with working hover cards."""
    r = client.post(
        "/api/plugins/bloodbowl/replace",
        json={
            "home_team": "Amazon",
            "players": [{"side": "home", "x": 7, "y": 13, "position": "Jaguar Warrior"}],
        },
    )
    p = r.json()["players"][0]
    assert (p["MA"], p["ST"], p["AV"]) == ("6", "4", "9+")
    assert p["skills"] == ["Defensive", "Dodge"]


def test_replace_refreshes_a_stale_statline_from_the_roster(client):
    """A board placed before a roster-data fix must pick the corrected data up,
    not keep the stale blanks forever."""
    r = client.post(
        "/api/plugins/bloodbowl/replace",
        json={
            "home_team": "Ogre",
            "players": [
                # Deliberately stale: right position, wrong/blank stats.
                {"side": "home", "x": 5, "y": 13, "position": "Ogre Blocker", "team": "Ogre", "MA": "9", "skills": []}
            ],
        },
    )
    p = r.json()["players"][0]
    assert p["MA"] == "5", "roster must win over a stale stored statline"
    assert "Mighty Blow" in p["skills"]


# --- staff, re-roll and Star Players --------------------------------------
#
# Every assertion here pins a defect that shipped, not a hypothetical. Staff read
# 0/30 for a whole release because the parser looked for a two-column table while
# the site wrote a <ul> of links, and nothing failed — it just came back empty.


def test_every_team_has_staff_and_a_reroll_price():
    """The empty-staff bug was invisible: no error, just {} on all 30 teams."""
    from bloodbowl.pitch import rosters

    teams = rosters()["teams"]
    missing_staff = [t["name"] for t in teams if not t.get("staff")]
    missing_reroll = [t["name"] for t in teams if not t.get("reroll_cost")]
    assert not missing_staff, f"teams with no staff data: {missing_staff}"
    assert not missing_reroll, f"teams with no re-roll price: {missing_reroll}"


def test_reroll_price_actually_varies_by_team():
    """A constant would mean we scraped one page and copied it everywhere."""
    from bloodbowl.pitch import rosters

    prices = {t["reroll_cost"] for t in rosters()["teams"]}
    assert len(prices) > 1, f"every team priced re-rolls the same ({prices}) — suspect a parse fallback"


def test_staff_costs_are_prices_not_link_text():
    from bloodbowl.pitch import find_team

    orc = find_team("Orc")
    assert orc["staff"]["Re-roll"] == "60K"
    assert orc["staff"]["Apothecary"] == "50K"
    assert orc["reroll_cost"] == "60K"


def test_team_costs_tool(registry):
    out = json.loads(_tool(registry, "bb_team_costs").invoke({"team": "Orc"}))
    assert out["ok"] and out["reroll_cost"] == "60K"
    assert "Brawlin' Brutes" in out["special_rules"]


def test_a_solo_star_parses_cost_from_the_table_header():
    """A solo star's table is headed by its PRICE, not by "MA"."""
    from bloodbowl.pitch import find_star

    griff = find_star("Griff Oberwald")
    assert griff["cost"] == "300K"
    (m,) = griff["members"]
    assert m["stats"] == {"MA": "7", "ST": "4", "AG": "2+", "PA": "3+", "AV": "9+"}


def test_a_paired_star_does_not_slide_its_stats_one_column_left():
    """THE silent-corruption case. A pair prices itself in a <p><strong>, and each
    member's table is headed plainly "MA | ST | ...". Assuming a leading cost cell
    reads "MA" as the price and shifts every stat left."""
    from bloodbowl.pitch import find_star

    pair = find_star("Grak and Crumbleberry")
    assert pair["cost"] == "250K"
    assert [m["name"] for m in pair["members"]] == ["Grak", "Crumbleberry"]
    grak, crumb = pair["members"]
    assert grak["stats"] == {"MA": "5", "ST": "5", "AG": "4+", "PA": "4+", "AV": "10+"}
    assert crumb["stats"] == {"MA": "5", "ST": "2", "AG": "3+", "PA": "5+", "AV": "7+"}


def test_no_star_stat_is_a_stat_name():
    """The shifted-column failure leaves a header word sitting in a value slot."""
    from bloodbowl.pitch import stars

    for s in stars():
        for m in s["members"]:
            assert len(m["stats"]) == 5, f"{s['name']}/{m['name']} has {len(m['stats'])} stats"
            for key, val in m["stats"].items():
                assert val.upper() not in ("MA", "ST", "AG", "PA", "AV"), f"{s['name']}: {key}={val}"
                assert not val.upper().endswith("K"), f"{s['name']}: {key}={val} looks like a price"


def test_every_star_has_a_price():
    from bloodbowl.pitch import stars

    assert len(stars()) >= 60
    broke = [s["name"] for s in stars() if not s["cost"]]
    assert not broke, f"stars with no cost: {broke}"


def test_a_skill_qualifier_outside_the_anchor_survives():
    """The site writes "<a>Loner</a> (4+)" — reading anchor text alone drops the
    number that makes Loner mean anything."""
    from bloodbowl.pitch import find_star

    (m,) = find_star("Griff Oberwald")["members"]
    assert "Loner (3+)" in m["skills"]


def test_a_star_special_rule_keeps_its_text():
    from bloodbowl.pitch import find_star

    griff = find_star("Griff Oberwald")
    (m,) = griff["members"]
    assert m["special_rules"] == ["Consummate Professional"]
    assert "once per game" in griff["rule_text"]["Consummate Professional"].lower()


def test_stars_are_findable_through_the_punctuation_a_coach_will_not_type():
    from bloodbowl.pitch import find_star

    assert find_star("morg n thorg")["name"].startswith("Morg")
    assert find_star("GRIFF OBERWALD")["name"] == "Griff Oberwald"
    assert find_star("Crumbleberry")["name"] == "Grak and Crumbleberry"
    assert find_star("nobody at all") is None


def test_stars_for_a_team_are_priced_and_sorted(registry):
    out = json.loads(_tool(registry, "bb_list_stars").invoke({"team": "Orc"}))
    assert out["ok"] and out["count"] > 0
    costs = [int(s["cost"].rstrip("K")) for s in out["stars"]]
    assert costs == sorted(costs), "cheapest first, so a budget answer reads off the top"
    assert all(s["known"] for s in out["stars"]), "a team lists a star we have no page for"


def test_get_star_tool_reports_a_miss_rather_than_guessing(registry):
    out = json.loads(_tool(registry, "bb_get_star").invoke({"name": "Sir Not Appearing"}))
    assert out["ok"] is False and any("Griff" in n for n in out["known"])


# --- errata ---------------------------------------------------------------
#
# The site publishes corrections in place: the old value is wrapped in <del> and
# the new one printed beside it. Flattening tags without dropping the struck
# CONTENT fuses them, and the result reads as a real value.


def test_no_stat_or_quantity_cell_holds_two_fused_values():
    """ "<del>3+</del> 4+" flattens to "3+ 4+" — ambiguous, and shaped like data."""
    from bloodbowl.pitch import rosters

    fused = [
        (t["name"], p["position"], k, p[k])
        for t in rosters()["teams"]
        for p in t["positionals"]
        for k in ("qty", "MA", "ST", "AG", "PA", "AV")
        if " " in str(p.get(k, "")).strip()
    ]
    assert not fused, f"cells holding a superseded value beside its correction: {fused}"


def test_an_erratad_stat_keeps_only_the_correction():
    from bloodbowl.pitch import find_position, find_team

    gobbo = find_position(find_team("Orc"), "Goblin Lineman")
    assert gobbo["PA"] == "4+", "the struck 3+ is the OLD value"


def test_an_erratad_skill_access_is_actually_removed():
    """The silent half of the bug: struck letters in a skill-access column fuse
    invisibly, leaving a team with an access it no longer has."""
    from bloodbowl.pitch import find_position, find_team

    for team in ("Human", "Imperial Nobility"):
        ogre = find_position(find_team(team), "Ogre")
        assert "M" not in ogre["secondary"], f"{team} Ogre keeps erratad Mutation access"


def test_an_erratad_skill_qualifier_keeps_only_the_correction():
    from bloodbowl.pitch import find_position, find_team

    ogre = find_position(find_team("Chaos Renegades"), "Ogre*")
    assert "Loner (3+)" in ogre["skills"]
    assert not any("4+" in s for s in ogre["skills"] if s.startswith("Loner"))


# --- knowledge-base documents ---------------------------------------------


def test_kb_docs_label_every_stat():
    """The whole point of generating these rather than ingesting the team pages.
    A flattened row reads "6 2 3+ 3+ 4+ 8+" — six values for five stats, nothing
    saying which is which. Labelled, it cannot be misread."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("kbdocs", ROOT / "tools_kb_docs.py")
    kbdocs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kbdocs)

    docs = kbdocs.build()
    assert len(docs) >= 28
    # Exact filename: "Orc.md" also matches "Black_Orc.md", which is a different team.
    orc = next(body for name, _t, body in docs if name == "bloodbowl-team-Orc.md")
    assert "Statline: MA 6, ST 2, AG 3+, PA 4+, AV 8+" in orc, "Goblin Lineman must carry the erratad PA"
    assert "Team Re-roll for Orc costs 60K" in orc
    for _name, _title, body in docs:
        for line in body.splitlines():
            if line.startswith("- Statline:"):
                assert line.count(",") == 4, f"a statline lost a label: {line!r}"


# --- the view is real files now --------------------------------------------


def test_the_view_ships_as_real_files_not_one_python_string():
    """The whole point of the restructure. A game UI does not fit in a string
    literal, and a file you cannot diff is a file nobody refactors."""
    assert not (ROOT / "view.py").exists(), "view.py is gone; the page lives in web/"
    for name in ("index.html", "style.css", "js/main.js", "js/board.js", "js/game.js", "js/setup.js"):
        assert (WEB / name).is_file(), f"missing {name}"


def test_the_static_route_serves_the_modules(client):
    for path, ctype in (
        ("style.css", "text/css"),
        ("js/main.js", "text/javascript"),
        ("js/board.js", "text/javascript"),
    ):
        r = client.get(f"/plugins/bloodbowl/static/{path}")
        assert r.status_code == 200, path
        assert ctype in r.headers["content-type"]


def test_the_static_route_refuses_to_escape_the_web_directory(client):
    """It is PUBLIC, so it must not be able to hand out the plugin's source or
    the roster data, however the path is spelled."""
    for path in ("../api.py", "../../store.py", "../data/rosters.json", "../protoagent.plugin.yaml"):
        r = client.get(f"/plugins/bloodbowl/static/{path}")
        assert r.status_code == 404, f"{path} was served with {r.status_code}"


def test_the_page_loads_its_assets_through_the_slug_aware_base(client):
    """A root-absolute src 404s behind a fleet proxy that mounts the instance on
    a sub-path — the bug that took a whole plugin down once before."""
    page = client.get("/plugins/bloodbowl/view").text
    assert 'location.pathname.split("/plugins/")' in page
    assert 'src="/plugins/' not in page, "a root-absolute module src breaks under a proxy"
    assert 'href="/plugins/' not in page


def _decomment(text: str) -> str:
    """Strip /* */ and // comments.

    Needed because the files EXPLAIN the hardcoding they removed — a naive scan
    trips over the comment describing the bug it is checking for.
    """
    import re

    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"^\s*//.*$", " ", text, flags=re.M)


def test_no_geometry_is_hardcoded_anywhere_in_the_view():
    """THE DRY invariant. The old view read the real numbers in JS while its CSS
    and SVG hardcoded 26 and 15, so changing the pitch would have drawn a board
    that did not match the one being played on."""
    import re

    css = _decomment((WEB / "style.css").read_text())
    assert "repeat(26" not in css and "repeat(15" not in css
    assert "var(--cols)" in css and "var(--rows)" in css
    assert "aspect-ratio: var(--cols) / var(--rows)" in css

    board = _decomment((WEB / "js" / "board.js").read_text())
    assert "GEO.length" in board and "GEO.width" in board
    for f in sorted((WEB / "js").glob("*.js")):
        body = _decomment(f.read_text())
        found = re.findall(r"(?<![\w.\-])(?:26|15)(?![\w.\-])", body)
        assert not found, f"{f.name} hardcodes the pitch dimension {found}"


def test_the_geometry_is_published_as_css_custom_properties():
    js = _web("js/board.js")
    assert 'setProperty("--cols"' in js and 'setProperty("--rows"' in js


def test_play_mode_asks_the_engine_what_is_legal_rather_than_working_it_out():
    """The view must not re-derive the rules. Two implementations of a dodge
    modifier agree right up until they don't, and then the board lies."""
    js = _web("js/game.js")
    assert "/game/legal" in js
    for forbidden in ("dodge_modifier =", "function dodgeModifier", "MAX_RUSHES"):
        assert forbidden not in js, f"game.js is computing rules itself: {forbidden}"


def test_the_log_is_rendered_from_the_engines_rolls():
    js = _web("js/game.js")
    assert "/game/log" in js and "e.rolls" in js


def test_the_two_modes_hand_the_board_over_cleanly():
    """Both renderers appending to the same cells is how you get a player that
    cannot be dragged and nobody can explain why."""
    main = _web("js/main.js")
    assert "setup.teardown()" in main and "game.teardown()" in main
    for mod in ("js/setup.js", "js/game.js"):
        assert "export function teardown()" in _web(mod), f"{mod} must be able to release the board"


def test_the_static_asset_tree_is_declared_auth_exempt():
    """The host auto-exempts a declared VIEW path, not its siblings. The page is a
    plain iframe navigation carrying no bearer, so its stylesheet and modules must
    be exempt too — otherwise the page loads 200 and every asset 401s, which is an
    unstyled, dead board on any token-gated deployment. Found on the live agent,
    not in the harness, which mounts the routers with no auth middleware at all."""
    declared = MANIFEST.get("public_paths") or []
    assert "/plugins/bloodbowl/static/" in declared, f"static tree not exempt: {declared}"
    # Data stays gated. Exempting the api namespace would hand the board to anyone.
    assert not any(p.startswith("/api/") for p in declared), f"data must stay gated: {declared}"


def test_the_act_route_forwards_the_whole_command(client):
    """It used to name the fields it passed through, so a Block's `target` was
    dropped the moment Blocking was added: the request answered 200 with ok:false
    and the board did nothing, which reads as a dead button rather than a bug.
    Found by capturing the response BODY in the browser harness — the status code
    alone says a refusal and a success are the same thing."""
    base = "/api/plugins/bloodbowl"
    client.post(f"{base}/place", json={"side": "home", "team": "Orc", "position": "Big Un Blocker", "x": 7, "y": 13})
    client.post(f"{base}/place", json={"side": "away", "team": "Skaven", "position": "Skaven Clanrat", "x": 7, "y": 14})
    assert client.post(f"{base}/game/new", json={"seed": 4}).status_code == 200

    legal = client.get(f"{base}/game/legal", params={"player": "h00"}).json()
    assert legal["blocks"], "the blocker should have a target"
    target = legal["blocks"][0]["target"]

    r = client.post(f"{base}/game/act", json={"action": "block", "player": "h00", "target": target}).json()
    assert r["ok"] or r["turnover"], r
    assert r["log"], "a played block must produce log lines"
    assert "no target" not in str(r.get("text", "")), r


def test_a_refused_action_is_reported_rather_than_silently_dropped(client):
    base = "/api/plugins/bloodbowl"
    client.post(f"{base}/place", json={"side": "home", "team": "Orc", "position": "Orc Lineman", "x": 7, "y": 13})
    client.post(f"{base}/game/new", json={"seed": 1})
    r = client.post(f"{base}/game/act", json={"action": "block", "player": "h00", "target": "nobody"}).json()
    assert r["ok"] is False and "no target" in r["text"]


def test_the_readme_and_handoff_describe_what_actually_ships(registry):
    """Docs drift silently and a handoff is exactly when that costs someone a day.
    The README listed seven tools for a long while after there were twenty-four."""
    import bloodbowl

    bloodbowl.register(registry)
    names = {t.name for t in registry.tools}
    readme = (ROOT / "README.md").read_text()
    handoff = (ROOT / "docs" / "HANDOFF.md").read_text()

    missing = sorted(n for n in names if n not in readme)
    assert not missing, f"tools the README never mentions: {missing}"
    assert (ROOT / "docs" / "HANDOFF.md").is_file()
    for probe in ("engine adjudicates", "Look them up", "unmodelled"):
        assert probe in handoff, f"the handoff lost its section on {probe!r}"


def test_the_version_moved_past_the_first_release():
    """Nine merged PRs turned a board into a rules engine; 0.1.0 was a lie.

    Parsed by hand rather than with `packaging`, which is not in
    requirements-dev.txt — it is installed here transitively, so importing it
    would pass locally and fail CI.
    """
    parts = tuple(int(n) for n in str(MANIFEST["version"]).split("."))
    assert parts >= (0, 5, 0), MANIFEST["version"]


def test_the_plugin_registers_at_most_one_router_per_prefix(registry):
    """The host mounts plugin routers keyed on (plugin_id, prefix) and SKIPS any
    already mounted, so a second router on a shared prefix has every route
    silently discarded. That is exactly what happened: the whole game API 404'd on
    the live agent while the board's routes worked, because the data router was
    registered first.

    The harness could not catch it — it mounted every router blindly, making it
    more forgiving than production. It now mimics the host's dedup."""
    import bloodbowl

    bloodbowl.register(registry)
    prefixes = [p for _, p in registry.routers]
    assert len(prefixes) == len(set(prefixes)), f"two routers share a prefix: {prefixes}"


def test_the_game_routes_are_actually_reachable(client):
    """The regression, end to end through the mounted app rather than the router
    object — a route that exists but is never mounted looks identical from inside."""
    base = "/api/plugins/bloodbowl"
    assert client.get(f"{base}/game").status_code == 200, "the match API must be mounted"
    assert client.get(f"{base}/presets").status_code == 200, "and so must the board's"
    assert client.post(f"{base}/game/abandon", json={}).status_code == 200
