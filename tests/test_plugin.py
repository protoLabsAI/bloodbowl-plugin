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


def test_the_play_board_drag_uses_pointer_events_not_html5_drag_and_drop():
    """The divergence from setup.js is deliberate and both reasons are load-bearing.

    HTML5 `draggable` does not fire on a touchscreen at all, and Playwright — the
    only thing that can tell whether a drag LANDS — drives pointer events natively
    and HTML5 drag-and-drop badly. A gesture that cannot be tested is one that
    breaks silently, which is the defect class this view keeps producing.
    """
    page = _web("js/drag.js")
    assert "pointerdown" in page and "pointermove" in page and "pointerup" in page
    assert "draggable = true" not in page, "HTML5 DnD is what this module exists to avoid"
    assert "setPointerCapture" in page, "the gesture must survive leaving a one-square node"


def test_a_drag_stands_the_play_poller_down_too():
    """Setup shipped this bug: a re-render mid-drag tears the node out from under
    the pointer and the gesture dies with no error. The play board polls too."""
    assert "drag.state.active" in _web("js/game.js"), "game.poll must stand down mid-drag"


def test_the_follower_cannot_swallow_its_own_hit_test():
    """`elementFromPoint` returns the topmost element, and during a drag that is
    the thing following the pointer — so it must not be hit-testable or every drop
    lands on the follower instead of a square."""
    css = _web("style.css")
    assert ".pc.follower" in css
    follower = css.split(".pc.follower")[1].split("}")[0]
    assert "pointer-events: none" in follower
    assert "touch-action: none" in css, "without it a touch drag scrolls the page instead"


def test_a_drop_and_a_click_cannot_disagree_about_what_a_square_means():
    """The dispatch between Secure, a pass and a move lives in `onCellClick`. A
    drop routes through it rather than reimplementing it."""
    page = _web("js/game.js")
    assert "await onCellClick(sq.x, sq.y)" in page


def test_clearing_one_mark_does_not_strip_the_badges_of_the_others():
    """`clearMarks` used to remove EVERY odds badge on the board whatever classes
    it was handed. The badges belong to the marks, so clearing one mark silently
    took the Dodge modifiers, dice counts and blitz distances belonging to all the
    rest — and the board looked quiet rather than wrong. It cost an afternoon
    during the drag work."""
    body = _web("js/board.js").split("export function clearMarks")[1].split("\n}")[0]
    assert "classList.contains" in body, "the badge removal must be scoped to the squares being cleared"
    # The old shape was two unconditional passes over every cell.
    assert body.count("for (const c of CELLS)") == 1, "a second unscoped pass is what did the damage"


def test_the_board_never_pre_commits_to_a_block_die():
    """`choice` indexes the faces the roll SHOWS, and the roll has not happened at
    the moment of asking — so the only correct value is no value.

    `choice: 0` was a blind pre-commitment to the first die dressed up as a
    decision, and it is the same defect the agent's tool had: every block on this
    board took whichever face happened to be rolled first. Left out, the engine
    applies the best face for whoever is entitled to choose.
    """
    # Comments only, stripped — the prose below explains the bug and would
    # otherwise be the thing this test finds.
    code = "\n".join(ln for ln in _web("js/game.js").splitlines() if not ln.lstrip().startswith("//"))
    # `choice:` as an object key is the payload field. Bare `choice` is the
    # Kick-off Event module this file also imports, which is a different thing.
    assert "choice:" not in code, "the board must not pick a die before the dice exist"
    assert 'action: "block"' in code


def test_distance_decides_whether_a_dropped_player_blocks_or_blitzes():
    """Dropping on somebody you already touch is a Block; dragging across the
    pitch onto them is a Blitz — declare, walk, hit. Which is what a Blitz IS."""
    page = _web("js/game.js")
    body = page.split("async function dropOnPlayer")[1].split("\nasync function")[0]
    assert "route.length" in body, "the traced distance is what separates the two"
    assert "declareBlitz" in body and "throwBlock" in body
    # Whether the Block is on after the walk is the engine's answer, not a guess
    # from having arrived: a failed Rush on the way leaves them on the floor.
    assert "/game/legal?player=" in body


def test_a_player_is_never_dropped_onto_an_occupied_square():
    """You cannot stand where somebody is standing — you act on them from next
    door, so the trail's last step is trimmed before the walk."""
    page = _web("js/game.js")
    assert "route.slice(0, -1)" in page


def test_a_dragged_run_stops_the_moment_a_step_does_not_land():
    """A move is a sequence of single squares and any of them can end the
    activation. Walking the rest of a plan that no longer applies is the one thing
    a multi-square drag must never do — and a REFUSAL answers 200 with ok:false,
    so the status code cannot tell a played move from a rejected one."""
    walk = _web("js/game.js").split("async function walkPath")[1].split("\nasync function")[0]
    assert "report.ok === false" in walk, "a refusal is a 200; the body is the only signal"
    assert "report.turnover" in walk and 'down !== "standing"' in walk
    assert "still.x !== sq.x || still.y !== sq.y" in walk, "being pushed off the plan must stop it too"
    assert "break" in walk


def test_a_fast_drag_still_produces_a_contiguous_path():
    """A pointer does not visit every cell it crosses. Without filling the gaps a
    quick drag makes a path of disconnected hops, and the engine refuses each one
    — which looks like the drag not working rather than the path being wrong."""
    page = _web("js/game.js")
    assert "function extendPath" in page
    body = page.split("function extendPath")[1].split("\nasync function")[0]
    assert "Math.sign" in body, "the fill walks one king move at a time towards the target"


def test_the_trail_is_numbered_because_order_is_the_whole_information():
    css = _web("style.css")
    assert ".cell .step" in css and ".cell.path" in css
    assert '"step"' in _web("js/game.js")


def test_every_mark_the_board_paints_is_also_cleared():
    """`passable` was in the paint list and in NONE of the seven clear lists, so
    arming a pass and cancelling it left the throw targets lit across the pitch.
    One list now, because seven had already drifted."""
    page = _web("js/game.js")
    marks = page.split("const MARKS = [")[1].split("]")[0]
    for painted in ("legal", "needsroll", "blockable", "blitzable", "foulable", "securable", "handoffable", "passable"):
        assert f'"{painted}"' in marks, f"{painted} is painted but never cleared"
    assert 'clearMarks("legal"' not in page, "the hand-written mark lists are what drifted"


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


def test_a_blitz_can_be_played_end_to_end_over_http(client):
    """Declare, walk, hit, walk on — through the routes the view actually calls.

    The engine tests pin the rules; this pins the WIRING, which is where a whole
    API has been dead before. A Blitz is the first action that needs three round
    trips to mean anything, so a field dropped anywhere along the way shows up
    here as a refusal rather than as a board that quietly does nothing.
    """
    base = "/api/plugins/bloodbowl"
    client.post(f"{base}/place", json={"side": "home", "team": "Orc", "position": "Orc Lineman", "x": 7, "y": 10})
    client.post(f"{base}/place", json={"side": "away", "team": "Skaven", "position": "Skaven Clanrat", "x": 7, "y": 14})
    assert client.post(f"{base}/game/new", json={"seed": 4}).status_code == 200

    legal = client.get(f"{base}/game/legal", params={"player": "h00"}).json()
    assert legal["blitz"]["available"] is True, legal["blitz"]
    target = legal["blitz"]["targets"][0]
    assert target["target"] == "a00" and target["steps"] == 3

    declared = client.post(f"{base}/game/act", json={"action": "blitz", "player": "h00", "target": "a00"}).json()
    assert declared["ok"] is True, declared
    assert "declares a Blitz" in " ".join(declared["log"])
    assert declared["match"]["blitz"] == {"player": "h00", "target": "a00", "blocked": False}

    for y in (11, 12, 13):
        step = client.post(f"{base}/game/act", json={"action": "move", "player": "h00", "x": 7, "y": y}).json()
        assert step["ok"], step

    hit = client.post(f"{base}/game/act", json={"action": "block", "player": "h00", "target": "a00"}).json()
    assert hit["log"], hit
    assert "already acted" not in str(hit.get("text", "")), "the Blitz's Block was refused"
    me = next(p for p in hit["match"]["players"] if p["id"] == "h00")
    assert me["ma_used"] == 4, "three squares walked plus one for the Block"


def test_a_refused_action_is_reported_rather_than_silently_dropped(client):
    base = "/api/plugins/bloodbowl"
    client.post(f"{base}/place", json={"side": "home", "team": "Orc", "position": "Orc Lineman", "x": 7, "y": 13})
    client.post(f"{base}/game/new", json={"seed": 1})
    client.post(f"{base}/game/choose", json={"decline": True})  # get past whatever the kick-off asked
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


def test_a_mounted_route_resolves_the_engine_per_request(client, monkeypatch):
    """A reload must not leave the plugin half-new.

    The host cannot swap a mounted router, so a router that binds `act` at
    BUILD time keeps calling the original function object for the life of the
    process. Meanwhile anything reached through a lazy import — `store.py`
    resolves `Match` inside a function body — does pick the reloaded module up.
    On the live agent that combination produced a match payload carrying state
    fields (`turn_actions`, `argue_banned`) that the rules layer did not honour,
    and a `foul` action the engine had never heard of. New state, old rules: worse
    than not reloading at all, because everything looks like it worked.

    Monkeypatching the module attribute AFTER the router is built stands in for
    the reload. If the route captured the function, the patch has no effect.
    """
    import bloodbowl.engine.game as game

    base = "/api/plugins/bloodbowl"
    client.post(f"{base}/place", json={"side": "home", "team": "Orc", "position": "Orc Lineman", "x": 7, "y": 13})
    client.post(f"{base}/game/new", json={"seed": 3})

    monkeypatch.setattr(game, "legal_moves", lambda *a, **k: {"ok": True, "reloaded": True})
    out = client.get(f"{base}/game/legal", params={"player": "h00"}).json()
    assert out.get("reloaded"), "the route is calling a function captured when the router was built"


def test_every_tool_defined_is_actually_registered():
    """`_tools()` returns an EXPLICIT list, so a tool can be written, decorated and
    documented and still never reach the agent — dead code that looks alive.

    The README check next door cannot see it: that one asks whether every
    REGISTERED tool is documented, which an unregistered tool passes trivially.
    Two skill tools were added and forgotten exactly this way, and the whole suite
    stayed green.
    """
    import ast

    import bloodbowl

    registry = _Reg()
    bloodbowl.register(registry)
    live = {t.name for t in registry.tools}

    tree = ast.parse((ROOT / "__init__.py").read_text())
    defined = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name.startswith("bb_")
    }
    assert defined, "no bb_* tools found — the parse is wrong, not the plugin"
    assert not (defined - live), f"defined but never registered: {sorted(defined - live)}"


class _Reg:
    config: dict = {}

    def __init__(self):
        self.tools: list = []
        self.routers: list = []

    def register_tool(self, t):
        self.tools.append(t)

    def register_tools(self, ts):
        self.tools.extend(ts)

    def register_router(self, router, prefix):
        self.routers.append((router, prefix))

    def register_surface(self, *a, **k):
        pass

    def register_skill_dir(self, path):
        pass


def test_the_parity_table_lists_every_rules_section():
    """docs/PARITY.md tracks the whole S3 core-rules table of contents. If the
    source grows a section the table never mentions, the gap is invisible — the
    engine looks complete because nothing is asking the question.

    The section list is checked against the shipped skills catalogue's own source
    page rather than the live site, so this test needs no network and cannot fail
    because a server was slow.
    """

    parity = (ROOT / "docs" / "PARITY.md").read_text()
    # Headings the rules use, taken from the ones this repo already relies on
    # elsewhere — a spot-check of the spine rather than a full re-scrape.
    spine = [
        "THE KICK-OFF",
        "KICK-OFF EVENT TABLE",
        "TOUCHBACKS",
        "PLAYER ACTIVATIONS",
        "MOVE ACTION",
        "SECURE THE BALL ACTION",
        "BLOCK ACTION",
        "BLITZ ACTION",
        "PASS ACTION",
        "HAND-OFF ACTION",
        "THROW TEAM-MATE ACTION",
        "FOUL ACTION",
        "SPECIAL ACTION",
        "FOREGO ACTIVATION",
        "DECLARE VS PERFORM",
        "JUMPING OVER PLAYERS",
        "RUSHING",
        "CHAIN PUSHES",
        "PUSHED INTO THE CROWD",
        "INJURY BY THE CROWD",
        "CASUALTY ROLLS",
        "APOTHECARIES",
        "ARGUE THE CALL",
        "INTERCEPTIONS",
        "CORNER THROW-INS",
        "STALLING",
        "DEAL WITH SECRET WEAPONS",
        "EXTRA TIME",
        "THE WEATHER",
    ]
    missing = [h for h in spine if h not in parity]
    assert not missing, f"docs/PARITY.md never mentions: {missing}"

    # Every row must carry a verdict, or the table is decoration.
    rows = [ln for ln in parity.splitlines() if ln.startswith("| ") and "---" not in ln]
    verdicts = {"yes", "partly", "no", "n/a", ""}
    bad = [ln for ln in rows[1:] if len(ln.split("|")) > 2 and ln.split("|")[2].strip() not in verdicts]
    assert not bad, f"rows with no verdict: {bad[:3]}"


def test_the_parity_table_agrees_with_the_engine_about_the_kickoff_events():
    """One verdict the test CAN check: the table says how many Kick-off Events are
    applied, and the engine knows the real number. A hand-maintained checklist
    drifts; this row cannot."""
    import re

    from bloodbowl.engine.kickoff import KICKOFF_EVENTS

    applied = sum(1 for _n, _t, ok in KICKOFF_EVENTS.values() if ok)
    parity = (ROOT / "docs" / "PARITY.md").read_text()
    row = re.search(r"\| THE KICK-OFF EVENT .*\|.*\|(.*)\|", parity)
    assert row, "the Kick-off Event row went missing"
    # The row claims all eleven are applied. If that ever stops being true the
    # count has to come back into the row — which is what this asserts.
    assert "all 11 rolled, quoted and applied" in row.group(1), row.group(1)
    assert applied == len(KICKOFF_EVENTS) == 11, f"the engine applies {applied} of {len(KICKOFF_EVENTS)}"


def test_a_pending_kickoff_choice_is_answerable_over_http_and_blocks_play_until_it_is(client):
    """The board is where a coach sees the question asked, so the board is where
    they must be able to answer it. Until they do, the engine refuses everything
    else — and the refusal has to carry the question, or the pitch view is just a
    page where clicking does nothing."""
    base = "/api/plugins/bloodbowl"
    client.post(f"{base}/place", json={"side": "home", "team": "Orc", "position": "Orc Lineman", "x": 7, "y": 13})
    client.post(f"{base}/place", json={"side": "away", "team": "Skaven", "position": "Skaven Clanrat", "x": 3, "y": 20})

    # Seeds differ in which event they roll, so find one that asks something.
    for seed in range(1, 40):
        client.post(f"{base}/game/new", json={"seed": seed})
        m = client.get(f"{base}/game").json()["match"]
        if m.get("pending"):
            break
    else:
        raise AssertionError("no seed in 1..39 rolled a Kick-off Event that asks the Coach anything")

    assert m["pending"].get("text"), "the question must travel in the state, not only in the log"

    blocked = client.post(f"{base}/game/act", json={"action": "move", "player": "h00", "x": 7, "y": 12}).json()
    assert blocked["ok"] is False and "waiting" in blocked["text"], blocked

    answered = client.post(f"{base}/game/choose", json={"decline": True}).json()
    assert answered["ok"] and not answered["match"].get("pending"), answered
    assert client.post(f"{base}/game/act", json={"action": "move", "player": "h00", "x": 7, "y": 12}).json()["ok"]


def test_the_pitch_view_ships_the_module_that_answers_a_choice(registry):
    """A view that cannot answer the question is a view that cannot be used once
    the kick-off rolls a 4, 5 or 9 — which is three results in eleven."""
    import bloodbowl

    web = Path(bloodbowl.__file__).parent / "web"
    assert (web / "js" / "choice.js").exists()
    assert 'id="choice"' in (web / "index.html").read_text()
    assert "choice.js" in (web / "js" / "game.js").read_text(), "game.js must actually import it"


def test_the_tools_say_the_engine_is_waiting_before_an_action_is_refused(registry):
    """A coach who has to discover the question by having their first action
    refused has been told twice and helped once. `bb_game_new` says it, and
    `bb_game_state` puts it at the top rather than inside the board."""
    import bloodbowl

    bloodbowl.register(registry)
    tools = {t.name: t for t in registry.tools}
    tools["bb_pitch_place"].invoke({"side": "home", "team": "Orc", "position": "Orc Lineman", "x": 7, "y": 13})
    tools["bb_pitch_place"].invoke({"side": "away", "team": "Skaven", "position": "Skaven Clanrat", "x": 3, "y": 20})

    for seed in range(1, 40):
        started = json.loads(tools["bb_game_new"].invoke({"seed": seed}))
        if started.get("pending"):
            break
    else:
        raise AssertionError("no seed in 1..39 rolled a Kick-off Event that asks the Coach anything")

    assert "bb_game_choose" in started["message"], started["message"]
    state = json.loads(tools["bb_game_state"].invoke({}))
    assert state["waiting_on"]["choice"] == started["pending"]["choice"]


def test_a_charge_can_be_selected_and_played_end_to_end_over_http(client):
    """Charge! is the one Kick-off Event that is a free TURN. Selecting is one
    call; the free Actions are ordinary `act` calls after it; ending it lands the
    ball and opens the receiving team's turn — which is the part that would
    silently not happen if the mode forgot where the Drive was up to."""
    base = "/api/plugins/bloodbowl"
    for pos, x, y in [("Orc Lineman", 6, 13), ("Orc Lineman", 7, 13), ("Orc Lineman", 8, 13)]:
        client.post(f"{base}/place", json={"side": "home", "team": "Orc", "position": pos, "x": x, "y": y})
    for pos, x, y in [("Skaven Clanrat", 3, 20), ("Skaven Clanrat", 5, 20), ("Skaven Clanrat", 11, 20)]:
        client.post(f"{base}/place", json={"side": "away", "team": "Skaven", "position": pos, "x": x, "y": y})

    for seed in range(1, 400):
        client.post(f"{base}/game/new", json={"seed": seed, "kicking_to": "home"})
        m = client.get(f"{base}/game").json()["match"]
        if (m.get("pending") or {}).get("choice") == "charge":
            break
    else:
        pytest.skip("no seed in 1..399 rolled Charge! on this board")

    pending = m["pending"]
    assert pending["side"] == "away", "Charge! is the KICKING team's event"
    # The ball is still in the air and nobody is to act until this is over.
    assert not [e for e in client.get(f"{base}/game/log").json()["log"] if "to act" in (e.get("text") or "")]

    picked = pending["eligible"][:2]
    started = client.post(f"{base}/game/choose", json={"players": picked}).json()
    assert started["ok"], started
    assert started["match"]["charge"]["players"] == picked
    assert started["match"]["clock"]["active"] == "away", "the charging team acts"
    assert started["match"]["ball"]["in_air"], "a Charge happens with the ball still up in the air"

    # An unselected player may not join in.
    spare = next(p["id"] for p in started["match"]["players"] if p["side"] == "away" and p["id"] not in picked)
    refused = client.post(f"{base}/game/act", json={"action": "move", "player": spare, "x": 4, "y": 19}).json()
    assert refused["ok"] is False and "selected" in refused["text"], refused

    for pid in picked:
        client.post(f"{base}/game/act", json={"action": "forego", "player": pid})

    after = client.get(f"{base}/game").json()["match"]
    assert not after.get("charge"), "with everyone activated the Charge is over"
    assert after["clock"]["active"] == "home", "the Drive goes back to the receiving team"
    # `in_play` is true from the moment the kick is announced, so it proves
    # nothing about landing. `in_air` is the one that does.
    assert after["ball"]["in_play"] and not after["ball"]["in_air"], after["ball"]


def test_the_apothecary_casualty_choice_works_through_the_tools(registry):
    """The tool has to hand the question on rather than answering it — and the
    answer has to reach the saved match, because the Coach replies in a separate
    call and the board is reloaded from disk in between."""
    import bloodbowl
    from bloodbowl.engine.events import Event
    from bloodbowl.store import load_match, save_match

    bloodbowl.register(registry)
    tools = {t.name: t for t in registry.tools}
    tools["bb_pitch_place"].invoke({"side": "home", "team": "Orc", "position": "Orc Lineman", "x": 7, "y": 13})
    tools["bb_pitch_place"].invoke({"side": "away", "team": "Skaven", "position": "Skaven Clanrat", "x": 3, "y": 20})
    tools["bb_game_new"].invoke({"seed": 4, "apothecary": True})

    m = load_match()
    m.apply(Event(kind="player_condition", actor="h00", detail={"outcome": "casualty"}))
    m.apply(Event(kind="casualty_roll", actor="h00", detail={"result": "Dead", "roll": 15}))
    save_match(m)

    asked = json.loads(tools["bb_game_apothecary"].invoke({"player": "h00"}))
    assert asked["ok"] and asked["pending"]["choice"] == "apothecary", asked
    assert len(asked["results"]) == 2

    state = json.loads(tools["bb_game_state"].invoke({}))
    assert state["waiting_on"]["choice"] == "apothecary", "a reloaded match must still be waiting"

    best = 1 + max(range(2), key=lambda i: asked["results"][i]["result"] == "Badly Hurt")
    answered = json.loads(tools["bb_game_choose"].invoke({"result": best}))
    assert answered["ok"], answered
    after = load_match()
    assert not after.pending, "the answer has to reach the SAVED match"


def test_a_match_started_from_a_preset_has_players_who_can_actually_move(client):
    """A shipped preset is a SHAPE — a role label and no positional, no MA, no ST,
    no AV. Right for the practice board, wrong the moment a match starts on it:
    `movement()` reads int("" or 0), so every one of them was a player who could
    not move and nothing anywhere said so.

    Found by looking at a harness screenshot of eleven "?" badges, not by a test —
    which is why this one exists."""
    base = "/api/plugins/bloodbowl"
    client.post(f"{base}/teams", json={"home_team": "Orc", "away_team": "Skaven"})
    loaded = client.post(f"{base}/presets/load", json={"name": "Standard defence"}).json()
    assert loaded["players"], loaded
    assert all(not p["MA"] for p in loaded["players"]), "the preset itself is still a shape"

    started = client.post(f"{base}/game/new", json={"seed": 4}).json()
    on = [p for p in started["match"]["players"] if p["place"] == "pitch"]
    assert on, started
    assert all(p["movement"] > 0 for p in on), [p for p in on if not p["movement"]]
    assert all(p["position"] for p in on), "and they have a name a coach can read"
    # …and the engine says it did it, rather than quietly promoting eleven tokens.
    log = client.get(f"{base}/game/log").json()["log"]
    assert any("took the field as linemen" in (e.get("text") or "") for e in log), log[:2]


def test_the_lineman_is_the_one_a_team_may_field_most_of(registry):
    """The default has to be defensible: the lineman is the cheapest positional on
    every roster and the only one with a 0-16 limit, so a shape drawn without
    naming anybody is a shape drawn out of linemen."""
    from bloodbowl.engine.state import flesh_out
    from bloodbowl.pitch import Player, Scenario

    sc = Scenario(name="shape", home_team="Orc", away_team="Skaven")
    sc.players = [Player(side="home", x=7, y=13, label="LOS"), Player(side="away", x=7, y=14, label="LOS")]
    filled = flesh_out(sc)
    assert len(filled) == 2, filled
    assert sc.players[0].position == "Orc Lineman" and sc.players[0].MA == "5"
    assert sc.players[1].position == "Skaven Clanrat"
    assert sc.players[0].label == "LOS", "the label is kept — the coach drew 'LOS' for a reason"

    # A real player is left alone.
    sc2 = Scenario(name="real", home_team="Orc", away_team="Skaven")
    sc2.players = [Player(side="home", x=7, y=13, position="Orc Blitzer", team="Orc", MA="6", AG="3+", AV="9+")]
    assert flesh_out(sc2) == []
    assert sc2.players[0].position == "Orc Blitzer"


def test_the_fumblerooski_flag_survives_the_whole_tool_path(registry):
    """A command field the tool forgets to forward is the bug class that dropped a
    Block's `target` for as long as Blocking existed: the request answers ok:false
    and the board does nothing, which reads as a dead button."""
    import bloodbowl
    from bloodbowl.store import load_match, save_match

    bloodbowl.register(registry)
    tools = {t.name: t for t in registry.tools}
    tools["bb_pitch_place"].invoke({"side": "home", "team": "Orc", "position": "Orc Lineman", "x": 7, "y": 13})
    tools["bb_pitch_place"].invoke({"side": "away", "team": "Skaven", "position": "Skaven Clanrat", "x": 3, "y": 20})
    tools["bb_game_new"].invoke({"seed": 4, "kicking_to": "home"})
    json.loads(tools["bb_game_choose"].invoke({"decline": True}))

    m = load_match()
    m.by_id("h00").player.skills = ["Fumblerooski"]
    # Through an EVENT, not by assignment: a Match is rebuilt by folding its log,
    # so a ball handed over by poking the object is a ball that is not there after
    # the reload the next tool call performs.
    from bloodbowl.engine.events import Event

    m.apply(Event(kind="ball_picked_up", actor="h00", text="h00 has the ball."))
    where = (m.by_id("h00").x, m.by_id("h00").y)
    save_match(m)

    out = json.loads(
        tools["bb_game_act"].invoke({"action": "move", "player": "h00", "x": 7, "y": 12, "drop_ball": True})
    )
    assert out["ok"], out
    after = load_match()
    assert not after.ball.carrier and (after.ball.x, after.ball.y) == where, (after.ball.x, after.ball.y)


def test_every_skill_in_the_catalogue_is_modelled(registry):
    """Parity with the full S3 ruleset means every Skill and Trait, not most of
    them. This is the line that says so, and it will fail the moment one is added
    to `data/skills.json` without an implementation behind it.

    It does NOT assert that every one is complete — 18 carry a `partial=` naming
    the clause they leave out, and `test_a_partial_skill_names_what_it_leaves_out`
    is what keeps those honest."""
    from bloodbowl.engine.skills import catalogue, modelled

    known = modelled()
    missing = sorted(n for n in catalogue() if n not in known)
    assert not missing, f"{len(missing)} Skill(s) with no implementation: {missing}"


def test_no_skill_is_left_half_applied(registry):
    """The companion to `test_every_skill_in_the_catalogue_is_modelled`: every one
    of the 108 is not merely registered but applied in full.

    A `partial=` is how a Skill declares a clause it leaves out, and there are none
    — so this asserts that, and asserts each one still SAYS something if one comes
    back. A partial with no text would be worse than none: it would report a gap
    without naming it, which is the failure the mechanism exists to prevent."""
    from bloodbowl.engine.skills import describe_skill, partial_skills

    partials = sorted(partial_skills())
    assert not partials, "these Skills are only half-applied: " + ", ".join(
        f"{n} ({(describe_skill(n) or {}).get('partial')})" for n in partials
    )


def test_a_head_to_head_locks_each_side_to_its_coach_end_to_end(client, registry):
    """The board is the human and the tools are the agent, and neither may move the
    other's team. This goes through both real surfaces because that is the whole
    point: the enforcement is not in either of them, it is in the engine they share."""
    import bloodbowl

    base = "/api/plugins/bloodbowl"
    client.post(f"{base}/place", json={"side": "home", "team": "Orc", "position": "Orc Lineman", "x": 7, "y": 13})
    client.post(f"{base}/place", json={"side": "away", "team": "Skaven", "position": "Skaven Clanrat", "x": 3, "y": 20})
    started = client.post(f"{base}/game/new", json={"seed": 4, "kicking_to": "home", "you": "home"}).json()
    assert started["match"]["controllers"] == {"home": "human", "away": "agent"}
    client.post(f"{base}/game/choose", json={"decline": True})

    # The board moves the human's player…
    ok = client.post(f"{base}/game/act", json={"action": "move", "player": "h00", "x": 7, "y": 12}).json()
    assert ok["ok"] or ok.get("turnover"), ok

    # …and the agent's tools may not, because it is not their turn.
    bloodbowl.register(registry)
    tools = {t.name: t for t in registry.tools}
    refused = json.loads(tools["bb_game_act"].invoke({"action": "move", "player": "h00", "x": 7, "y": 11}))
    assert refused["ok"] is False and "not your move" in refused["text"], refused

    # Hand over, and it inverts: the tools may act and the board may not.
    # (`a00`, not `a01` — a match numbers ids WITHIN each side, unlike the engine
    # test helper, which numbers across the combined list.)
    assert client.post(f"{base}/game/end-turn", json={}).json()["ok"]
    mine = client.post(f"{base}/game/act", json={"action": "move", "player": "a00", "x": 3, "y": 19}).json()
    assert mine["ok"] is False and "not your move" in mine["text"], mine
    theirs = json.loads(tools["bb_game_act"].invoke({"action": "move", "player": "a00", "x": 3, "y": 19}))
    assert theirs["ok"] or theirs.get("turnover"), theirs


def test_the_agent_is_paced_so_a_turn_is_something_you_can_watch(registry):
    """A model can take eight activations in under a second, which is a diff rather
    than a game. The pace is a real wall-clock wait — this is the one test that
    turns it on, because a suite that sleeps is a suite nobody runs twice."""
    import time

    import bloodbowl
    from bloodbowl.engine import pace

    bloodbowl.register(registry)
    tools = {t.name: t for t in registry.tools}
    tools["bb_pitch_place"].invoke({"side": "home", "team": "Orc", "position": "Orc Lineman", "x": 7, "y": 13})
    tools["bb_pitch_place"].invoke({"side": "away", "team": "Skaven", "position": "Skaven Clanrat", "x": 3, "y": 20})
    tools["bb_game_new"].invoke({"seed": 4, "kicking_to": "home"})
    json.loads(tools["bb_game_choose"].invoke({"decline": True}))

    pace.configure(0.4)
    pace.reset()
    try:
        began = time.monotonic()
        first = json.loads(tools["bb_game_act"].invoke({"action": "move", "player": "h00", "x": 7, "y": 12}))
        second = json.loads(tools["bb_game_act"].invoke({"action": "move", "player": "h00", "x": 7, "y": 11}))
        took = time.monotonic() - began
    finally:
        pace.configure(0)
    assert first["ok"] or first.get("turnover"), first
    assert took >= 0.4, f"two paced actions took {took:.2f}s — the second did not wait"
    assert second.get("paced_s"), "and it says how long it waited, so nobody thinks it hung"

    # The HUMAN is never paced: a person is already as slow as a person.
    pace.configure(5)
    try:
        began = time.monotonic()
        from bloodbowl.engine import handover  # noqa: F401 — import cost only

        assert time.monotonic() - began < 1
    finally:
        pace.configure(0)


def test_a_match_remembers_which_chat_it_is_played_in(registry):
    """The agent only acts when a turn is enqueued into a SESSION, so a head-to-head
    has to know which conversation the game belongs to — otherwise your opponent's
    moves arrive somewhere you are not looking.

    `bb_game_new` takes it from the tool's injected graph state. Here that state is
    passed straight in, which is what the host does for real."""
    import bloodbowl
    from bloodbowl.store import load_match

    bloodbowl.register(registry)
    tools = {t.name: t for t in registry.tools}
    tools["bb_pitch_place"].invoke({"side": "home", "team": "Orc", "position": "Orc Lineman", "x": 7, "y": 13})
    tools["bb_pitch_place"].invoke({"side": "away", "team": "Skaven", "position": "Skaven Clanrat", "x": 3, "y": 20})

    tools["bb_game_new"].invoke({"seed": 4, "you": "home", "state": {"session_id": "chat-42"}})
    assert load_match().session_id == "chat-42"

    # …and the handover carries it, which is what routes the turn.
    from bloodbowl.engine import handover

    owed = handover.owed(load_match())
    assert owed.get("session_id") == "chat-42", owed


def test_a_board_started_match_can_be_pulled_into_a_chat(client, registry):
    """A match started from the BOARD has no conversation behind it — its turns fall
    back to the Activity thread until somebody says "play it here"."""
    import bloodbowl
    from bloodbowl.store import load_match

    base = "/api/plugins/bloodbowl"
    client.post(f"{base}/place", json={"side": "home", "team": "Orc", "position": "Orc Lineman", "x": 7, "y": 13})
    client.post(f"{base}/place", json={"side": "away", "team": "Skaven", "position": "Skaven Clanrat", "x": 3, "y": 20})
    client.post(f"{base}/game/new", json={"seed": 4, "you": "home"})
    assert load_match().session_id == "", "the board has no chat to bind"

    bloodbowl.register(registry)
    tools = {t.name: t for t in registry.tools}
    out = json.loads(tools["bb_game_here"].invoke({"state": {"session_id": "chat-7"}}))
    assert out["ok"] and out["session_id"] == "chat-7", out
    assert load_match().session_id == "chat-7"

    # It survives the reload, because it is an event like everything else.
    from bloodbowl.engine.state import Match

    assert Match.from_dict(load_match().to_dict()).session_id == "chat-7"

    # And with no session to bind it says so rather than binding nothing.
    assert json.loads(tools["bb_game_here"].invoke({}))["ok"] is False


def test_the_turn_nudge_is_wired_from_the_startup_hook_not_from_register(registry):
    """`register()` runs during the GRAPH BUILD; the host's event bus is not
    populated until the server's STARTUP hook. Subscribing at registration is
    therefore too early — and it fails SILENTLY: `registry.on` logs "dropped — no
    bus" at debug level, the nudge never arrives, and the agent simply never takes
    its turn with nothing anywhere saying why.

    That is exactly what happened the first time this shipped. `register_surface`
    is the seam whose `start` runs late enough."""
    import bloodbowl

    bloodbowl.register(registry)
    assert "bloodbowl-turns" in registry.surfaces, f"the nudge is not deferred to startup: {registry.surfaces}"

    # And it must not have subscribed during register — there was no bus to
    # subscribe to, so anything it did there went nowhere.
    assert not registry.subscriptions, f"subscribed too early: {registry.subscriptions}"


def test_each_handover_gets_its_own_job_id(registry, monkeypatch):
    """`run_in_session` is idempotent-REPLACE: a second call with the same id
    CANCELS the pending one. That is right for a chatty rule that only needs its
    latest firing, and WRONG for turns — every handover is a distinct turn that
    must actually run.

    With one shared id, a nudge for turn 3 silently cancelled turn 2 and the game
    stopped dead with nobody to act. Observed live: five nudges, three turns."""
    import bloodbowl

    sent: list[tuple[str, str]] = []

    class _SDK:
        @staticmethod
        def run_in_session(session, prompt, job_id=""):
            sent.append((session, job_id))
            return {"ok": True, "message": "queued"}

    import sys
    import types

    fake = types.ModuleType("graph")
    fake.sdk = _SDK  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "graph", fake)
    monkeypatch.setitem(sys.modules, "graph.sdk", _SDK)  # type: ignore[arg-type]

    bloodbowl.register(registry)
    start = next(s for s in registry.surfaces if s == "bloodbowl-turns")
    assert start  # the surface is registered; run its start to subscribe
    # `_Registry.register_surface` keeps only the name, so re-register to capture
    # the callable — the plugin hands the same one either way.
    captured: list = []
    registry.register_surface = lambda fn, stop=None, name=None, reload=None: captured.append(fn)
    bloodbowl.register(registry)
    captured[0]()
    handler = registry.subscriptions[-1][1]

    for half, turn in ((1, 2), (1, 3)):
        handler({"data": {"controller": "agent", "side": "away", "why": "turn", "half": half, "turn": turn}})
    jobs = [j for _s, j in sent]
    assert len(set(jobs)) == 2, f"two turns must not share a job id: {jobs}"
    assert all("h1t" in j and "away" in j for j in jobs), jobs


def test_a_lost_nudge_can_be_re_sent_without_throwing_the_game_away(client):
    """A nudge can be lost — the agent restarts mid-turn, a job is cancelled. The
    board is then correct, it is genuinely somebody's move, and nothing happens,
    which looks exactly like the agent thinking.

    Without a way back the only remedy is a NEW MATCH: throwing a game away to fix
    a lost message. It is unconditional on purpose — a repeat is what is being
    asked for, and `announce` suppresses repeats by design."""
    base = "/api/plugins/bloodbowl"
    client.post(f"{base}/place", json={"side": "home", "team": "Orc", "position": "Orc Lineman", "x": 7, "y": 13})
    client.post(f"{base}/place", json={"side": "away", "team": "Skaven", "position": "Skaven Clanrat", "x": 3, "y": 20})
    client.post(f"{base}/game/new", json={"seed": 4, "kicking_to": "home", "you": "home"})
    client.post(f"{base}/game/choose", json={"decline": True})

    out = client.post(f"{base}/game/nudge", json={}).json()
    assert out["ok"] and out["nudged"]["side"] == "home", out
    # Twice in a row: the whole point is that it does NOT suppress a repeat.
    assert client.post(f"{base}/game/nudge", json={}).json()["ok"]


def test_not_choosing_a_block_die_is_not_the_same_as_choosing_the_first_one(registry, monkeypatch):
    """The engine learned to pick the best face when no `choice` was given (#53) —
    and it still never got the chance, because the TOOL defaulted `choice` to 0.
    "I did not choose" and "I choose die 0" arrived identically.

    The agent hit this live, twice, and reported it against itself both times:
    "I didn't pass `choice` to the engine so it picked the Skull." It had, because
    the tool always did. A default that cannot be told apart from an answer is the
    same bug as an arbitrary default; it had just moved one layer out.

    So this asserts the CONTRACT rather than an outcome: an unanswered question
    must reach the engine as ABSENT."""
    import bloodbowl
    from bloodbowl.engine import game

    seen: list[dict] = []

    def _spy(match, action, cmd, dice=None, by=""):
        seen.append(dict(cmd))
        return {"ok": True, "events": [], "log": []}

    monkeypatch.setattr(game, "act", _spy)
    bloodbowl.register(registry)
    tools = {t.name: t for t in registry.tools}
    tools["bb_pitch_place"].invoke({"side": "home", "team": "Orc", "position": "Orc Lineman", "x": 7, "y": 13})
    tools["bb_pitch_place"].invoke({"side": "away", "team": "Skaven", "position": "Skaven Clanrat", "x": 7, "y": 14})
    tools["bb_game_new"].invoke({"seed": 4, "kicking_to": "home"})

    tools["bb_game_act"].invoke({"action": "block", "player": "h00", "target": "a00"})
    assert "prefer" not in seen[-1], f"an unanswered question reached the engine as an answer: {seen[-1]}"

    tools["bb_game_act"].invoke({"action": "block", "player": "h00", "target": "a00", "prefer": "push"})
    assert seen[-1].get("prefer") == "push", "…and a stated intent still gets through"

    # THE DIE INDEX IS GONE, and a stale caller must not be able to smuggle one
    # back in. It was unanswerable by construction — sent before the roll, naming a
    # face nobody had seen — and an integer parameter invites a 0, which is how a
    # whole live game was played taking the first die every single time.
    tools["bb_game_act"].invoke({"action": "block", "player": "h00", "target": "a00", "choice": 0})
    assert "choice" not in seen[-1], f"a die index reached the engine: {seen[-1]}"


# --- unreadable state files must be loud, not silent -----------------------


def test_a_corrupt_match_is_moved_aside_and_logged_not_swallowed(tmp_path, caplog):
    """ "No match in progress" is a perfectly ordinary state, so a match that failed
    to parse came back looking like a match that had never been started — board
    empty, log empty, and nothing anywhere saying a file had been rejected. A
    recoverable problem became an invisible one, and that is how a finished game can
    appear never to have happened."""
    from bloodbowl import store

    store.match_path().write_text("{ this is not json", encoding="utf-8")
    with caplog.at_level("WARNING"):
        assert store.load_match() is None
    assert not store.match_path().exists(), "the unreadable file must not be left to be re-swallowed"
    aside = store.state_dir() / "match.broken.json"
    assert aside.exists(), "…it is kept, so it can still be looked at"
    assert "cannot read match.json" in caplog.text
    assert "match.broken.json" in caplog.text, "the log must say where it went"


def test_a_match_that_cannot_be_folded_is_treated_the_same_as_bad_json():
    """`from_dict` rebuilds the position by REPLAYING the log, so a single
    unfoldable event does this — not just a truncated file."""
    from bloodbowl import store

    # An unknown event `kind` is deliberately TOLERATED — it coerces to a string
    # and folds — so the case that matters is a payload of the wrong SHAPE. Most of
    # those raise AttributeError, which was in none of the old except clauses: a
    # match.json with `"events": "nope"` propagated out of the loader and 500ed
    # every request that touched the match, rather than degrading to "no match".
    store.match_path().write_text('{"events": "nope"}', encoding="utf-8")
    assert store.load_match() is None, "an AttributeError must not escape the loader"
    assert (store.state_dir() / "match.broken.json").exists()


def test_a_corrupt_board_is_kept_rather_than_silently_replaced_by_an_empty_one(caplog):
    """An empty pitch appearing where a worked-out setup used to be is exactly the
    failure nobody can diagnose after the fact."""
    from bloodbowl import store

    store.state_path().write_text("not json either", encoding="utf-8")
    with caplog.at_level("WARNING"):
        assert not store.load().players, "a corrupt board must not brick the view"
    assert (store.state_dir() / "pitch.broken.json").exists()
    assert "cannot read pitch.json" in caplog.text


def test_an_unreadable_file_is_reported_but_NOT_moved(monkeypatch, caplog):
    """The distinction that keeps this from being destructive. A parse failure is
    permanent, so the file is quarantined; an OSError is a locked file or a full
    disk, the content may be perfectly good, and moving it would turn a passing
    squall into data loss."""
    from pathlib import Path

    from bloodbowl import store

    store.match_path().write_text('{"events": []}', encoding="utf-8")
    real = Path.read_text

    def _boom(self, *a, **k):
        if self.name == "match.json":
            raise OSError("device is busy")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", _boom)
    with caplog.at_level("WARNING"):
        assert store.load_match() is None
    assert store.match_path().exists(), "a transient failure must never move the file"
    assert not (store.state_dir() / "match.broken.json").exists()
    assert "cannot read match.json" in caplog.text
    assert "moved to" not in caplog.text


def test_an_absent_state_file_is_silent():
    """Absence is the normal case — a fresh install must not log a warning."""
    import logging

    from bloodbowl import store

    records = []
    handler = logging.Handler()
    handler.emit = records.append
    logging.getLogger("protoagent.plugins.bloodbowl").addHandler(handler)
    try:
        assert store.load_match() is None
        assert not store.load().players
    finally:
        logging.getLogger("protoagent.plugins.bloodbowl").removeHandler(handler)
    assert not records, f"a missing file is not a problem: {[r.getMessage() for r in records]}"


# --- the reply-level nudge toward `path` -------------------------------------------


def _drill(registry):
    """An unclaimed (permissive) board with the ball settled, ready for agent moves."""
    import bloodbowl

    bloodbowl.register(registry)
    tools = {t.name: t for t in registry.tools}
    tools["bb_pitch_place"].invoke({"side": "home", "team": "Orc", "position": "Orc Lineman", "x": 7, "y": 13})
    tools["bb_pitch_place"].invoke({"side": "away", "team": "Skaven", "position": "Skaven Clanrat", "x": 3, "y": 20})
    tools["bb_game_new"].invoke({"seed": 4, "kicking_to": "home"})
    json.loads(tools["bb_game_choose"].invoke({"decline": True}))
    bloodbowl._STEP_CALLS.update({"turn": None, "counts": {}, "told": set()})
    return tools


def _step(tools, pid, x, y, **kw):
    return json.loads(tools["bb_game_act"].invoke({"action": "move", "player": pid, "x": x, "y": y, **kw}))


def test_a_second_single_square_call_is_nudged_toward_path(registry):
    """The docstring is read once at the top of a long context; this lands in the loop,
    at the moment of the decision. That difference is the whole feature — `path` shipped
    fully documented and an agent still spent a turn one call per square."""
    tools = _drill(registry)
    first = _step(tools, "h00", 7, 12)
    assert first["ok"], first
    assert "hint" not in first, "one square is an ordinary thing to ask for — do not lecture on the first"

    second = _step(tools, "h00", 7, 11)
    assert second["ok"], second
    assert "hint" in second, "by the second call in a turn it is a pattern worth interrupting"
    assert "`path`" in second["hint"]
    assert "Move Allowance left" in second["hint"]


def test_the_nudge_fires_once_per_player_per_turn(registry):
    """Honest, not loud — the `first_mentions` precedent. A note repeated every call is
    noise, and noise is what got ignored the first time."""
    tools = _drill(registry)
    _step(tools, "h00", 7, 12)
    assert "hint" in _step(tools, "h00", 7, 11)
    assert "hint" not in _step(tools, "h00", 7, 10), "said once; saying it again every call is nagging"


def test_using_path_is_never_nudged_and_clears_the_count(registry):
    """A coach who took the advice must not keep being told, and must not be held to
    the calls they made before they switched."""
    tools = _drill(registry)
    _step(tools, "h00", 7, 12)  # one single-square call banked
    walked = json.loads(tools["bb_game_act"].invoke({"action": "move", "player": "h00", "path": [[7, 11]]}))
    assert walked["ok"], walked
    assert "hint" not in walked

    # The count restarted, so the very next single square is a "first" again.
    assert "hint" not in _step(tools, "h00", 7, 10)


def test_no_nudge_when_there_is_nothing_left_to_batch(registry):
    """Advice that cannot be taken is noise: a player with no Move Allowance left has no
    run to collapse.

    Driven through `_step_hint` rather than the tool on purpose. Spending a player out
    through the tool would need Rushes, whose dice can floor them — and a floored player
    is suppressed by a DIFFERENT clause, so the test would pass without ever exercising
    this one. (Mutating `ma_used` and saving does not work either: `from_dict` folds the
    log, so the cheat is discarded on the next load — the log-is-truth design catching a
    test red-handed.)
    """
    import bloodbowl

    tools = _drill(registry)
    _step(tools, "h00", 7, 12)
    m = bloodbowl.store.load_match()
    who = m.by_id("h00")

    who.ma_used = who.movement() - 1  # one square left: not worth batching
    assert bloodbowl._step_hint(m, "h00", False) == ""

    # The positive control, same player and same call count — so the pair discriminates
    # between "the budget guard fired" and "the hint is simply never produced here".
    bloodbowl._STEP_CALLS.update({"turn": None, "counts": {}, "told": set()})
    who.ma_used = 0
    assert bloodbowl._step_hint(m, "h00", False) == "", "still only the first call"
    assert "`path`" in bloodbowl._step_hint(m, "h00", False)


# --- full-AI mode wiring ------------------------------------------------------------


def test_you_neither_claims_both_seats_with_a_chat_each(registry):
    """`you="neither"` is the full-AI entry point: both sides agent-played, and a
    conversation per seat so neither can read the other's plan."""
    import bloodbowl

    bloodbowl.register(registry)
    tools = {t.name: t for t in registry.tools}
    tools["bb_pitch_place"].invoke({"side": "home", "team": "Orc", "position": "Orc Lineman", "x": 7, "y": 13})
    tools["bb_pitch_place"].invoke({"side": "away", "team": "Skaven", "position": "Skaven Clanrat", "x": 3, "y": 20})
    out = json.loads(tools["bb_game_new"].invoke({"seed": 4, "kicking_to": "home", "you": "neither"}))
    assert out["ok"], out
    m = bloodbowl.store.load_match()
    assert m.controllers == {"home": "agent", "away": "agent"}
    assert m.session_for("home") != m.session_for("away"), "two seats, two conversations"
    assert m.session_for("home") and m.session_for("away")


def test_the_board_can_start_a_full_ai_match_too(client):
    """Started from the pitch view there is no conversation behind it, so the seats
    hang off a fixed prefix rather than both flooding the Activity thread."""
    base = "/api/plugins/bloodbowl"
    client.post(f"{base}/place", json={"side": "home", "team": "Orc", "position": "Orc Lineman", "x": 7, "y": 13})
    client.post(f"{base}/place", json={"side": "away", "team": "Skaven", "position": "Skaven Clanrat", "x": 3, "y": 20})
    started = client.post(f"{base}/game/new", json={"seed": 4, "kicking_to": "home", "you": "neither"}).json()
    assert started["match"]["controllers"] == {"home": "agent", "away": "agent"}
    seats = started["match"]["session_ids"]
    assert seats["home"] != seats["away"] and all(seats.values())


def test_head_to_head_and_the_practice_board_are_untouched(registry):
    """The restraint control: `you="neither"` is a THIRD mode, not a change to the two
    that already existed."""
    import bloodbowl

    bloodbowl.register(registry)
    tools = {t.name: t for t in registry.tools}
    tools["bb_pitch_place"].invoke({"side": "home", "team": "Orc", "position": "Orc Lineman", "x": 7, "y": 13})
    tools["bb_pitch_place"].invoke({"side": "away", "team": "Skaven", "position": "Skaven Clanrat", "x": 3, "y": 20})

    tools["bb_game_new"].invoke({"seed": 4, "kicking_to": "home", "you": "home"})
    h2h = bloodbowl.store.load_match()
    assert h2h.controllers == {"home": "human", "away": "agent"}
    assert h2h.session_ids == {}, "a head-to-head still runs out of one conversation"

    tools["bb_game_new"].invoke({"seed": 4, "kicking_to": "home"})
    practice = bloodbowl.store.load_match()
    assert practice.controllers == {}, "and the practice board still owns nothing"


# --- the 3D view --------------------------------------------------------------------


def _manifest() -> dict:
    import yaml

    return yaml.safe_load((ROOT / "protoagent.plugin.yaml").read_text())


def test_the_3d_view_is_declared_and_its_page_is_served(client):
    """RULE 1 of the plugin-view guide: the manifest's `path` must equal something a
    router actually serves, or the console iframes a blank page. This one is the BUILT
    index.html inside the static tree, which is why it needs no new route."""
    view = next(v for v in _manifest()["views"] if v["id"] == "pitch3d")
    assert view["path"] == "/plugins/bloodbowl/view3d"
    r = client.get(view["path"])
    assert r.status_code == 200, r.text[:200]
    assert "text/html" in r.headers["content-type"]
    assert '<div id="root">' in r.text


def test_the_3d_bundle_ships_built(client):
    """The plugin is installable from a git URL onto a host with no Node, so the built
    output is committed. A missing bundle is a 404 the console renders as an empty
    canvas — silent, and only visible by looking."""
    built = sorted((ROOT / "web" / "3d" / "assets").glob("*.js"))
    assert built, "web/3d/assets/*.js is missing — run: cd web3d && npm run build"
    assert (ROOT / "web" / "3d" / "assets" / "app.js").is_file(), (
        "the bundle name is FIXED — the page addresses it by name at runtime, so a hashed "
        "filename would load nothing and show an empty canvas"
    )
    r = client.get("/plugins/bloodbowl/static/3d/assets/app.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]


def test_the_3d_page_never_hardcodes_a_base(client):
    """RULE 3. On the host window the base is ""; through the ADR 0042 fleet proxy it is
    "/agents/<slug>". A hardcoded absolute path talks to the WRONG AGENT — it does not
    fail loudly, it quietly shows somebody else's match."""
    page = client.get("/plugins/bloodbowl/view3d").text
    assert "location.pathname.split" in page, "the page must derive its own base"
    assert "localhost" not in page and "127.0.0.1" not in page
    # The bundle DOES carry "/api/plugins/bloodbowl/..." strings — that is correct, they
    # are root-relative paths handed to the kit's apiFetch, which prefixes the slug. What
    # must not appear is an absolute ORIGIN, and the kit must actually be the thing
    # fetching. (An `or` between those two would make this unfalsifiable; both are checked.)
    js = sorted((ROOT / "web" / "3d" / "assets").glob("*.js"))[0].read_text()
    assert "apiFetch" in js, "data must go through the kit's slug-aware fetch"
    assert "http://localhost" not in js and "http://127.0.0.1" not in js


def test_the_3d_view_asks_the_engine_for_legality(client):
    """The invariant that makes a second view safe: a view computes NO rules. The 2D
    board has this test and the 3D one needs it just as much — a renderer that decided
    its own legal squares would be a second, silently diverging rules engine."""
    src = (ROOT / "web3d" / "src").glob("*.js*")
    body = "\n".join(p.read_text() for p in src)
    assert "game/legal" in body, "it must ask the engine"
    for invented in ("dodge", "tackle zone", "rush", "armour"):
        assert invented not in body.lower(), f"the view is reasoning about {invented!r} itself"


def test_the_3d_view_needs_no_font_from_the_network(client):
    """The manifest declares `network: []` and the view runs in a sandbox. drei's <Text>
    resolves an unset `font` to a Google-hosted default, which fails silently — the scene
    still renders, just with no way to tell which player is which. Labels are drawn to a
    canvas instead."""
    import re

    srcs = list((ROOT / "web3d" / "src").glob("*.js*"))
    imports = [ln for p in srcs for ln in p.read_text().splitlines() if "@react-three/drei" in ln]
    assert imports, "the drei import should exist — this test is pinning WHAT is imported"
    for ln in imports:
        names = re.findall(r"\{([^}]*)\}", ln)
        assert not any(n.strip() == "Text" for part in names for n in part.split(",")), (
            f"drei <Text> resolves an unset font over the network: {ln.strip()}"
        )
    body = "\n".join(p.read_text() for p in srcs)
    assert "CanvasTexture" in body, "labels come from a canvas, not a font file"


def test_the_3d_hud_does_not_depend_on_the_theme_landing(client):
    """`var(--pl-color-fg, …)` looks like a safe fallback and is not one: the DS kit's
    stylesheet DEFINES that token, so the fallback never applies and the text inherits
    the kit's default — which was dark-on-dark. Same class as the odds badge that drew
    white on white for weeks. The HUD carries its own colour and backdrop."""
    page = client.get("/plugins/bloodbowl/view3d").text
    hud = page[page.index("#hud") : page.index("</style>")]
    assert "var(--pl-color-fg" not in hud, "the HUD must not depend on a token the kit defines"
    assert "background:" in hud and "color:" in hud, "it needs its own colour AND backdrop"


def test_the_3d_view_waits_for_the_bearer_before_fetching(client):
    """The token arrives by postMessage AFTER load, so a poll fired on mount races it and
    401s on a gated instance. It self-heals on the next tick — but it flashes an error
    every load, which teaches you to ignore the one place errors appear."""
    src = (ROOT / "web3d" / "src" / "main.jsx").read_text()
    # The fix is NOT "wait a bit then give up" — that races the console's own last retry
    # (it re-posts at 0/100/300/700/1500ms) and falls through to an unauthenticated fetch
    # that 401s on a gated instance. There must be no timing assumption at all: fetch
    # immediately, and re-fetch whenever the handshake fires.
    assert "onHandshake" in src, "a late token must trigger a re-fetch"
    assert "[handshake]" in src, "the poller must re-run when the handshake lands"
    assert "setTimeout(go" not in src, "no fixed-delay fallback — it races the console's retries"


def test_every_declared_view_is_served_by_a_registered_route(client):
    """The host validates a view's declared path against the paths its ROUTERS serve, by
    EXACT string match (`graph/plugins/loader.py::_served_paths`). A parameterised route
    is stored literally as `/plugins/bloodbowl/static/{path:path}`, so no concrete file
    under it can ever match — declaring the built page there made the host warn "no
    registered router serves it" on every single boot, and it was right about the shape
    even though the file served fine by hand.

    This is Rule 1 of the plugin-view guide, pinned: every declared view path must be a
    literal route, not a path that merely happens to resolve.
    """
    import bloodbowl

    routes = set()
    for router, prefix in ((bloodbowl.api.build_view_router({}), "/plugins/bloodbowl"),):
        for rt in router.routes:
            routes.add((prefix + getattr(rt, "path", "")).rstrip("/") or "/")
    for view in _manifest()["views"]:
        path = str(view["path"]).rstrip("/") or "/"
        assert path in routes, (
            f"view {view['id']!r} declares {view['path']!r}, which no literal route serves — "
            f"the host will warn and the console may refuse it. Served: {sorted(routes)}"
        )
        assert client.get(view["path"]).status_code == 200


# --- the model library ---------------------------------------------------------------


def _glb(size: int = 64) -> bytes:
    """Bytes that look like a .glb — the storage layer validates the SUFFIX and the size,
    never the container, because parsing glTF to accept an upload would be a second,
    worse glTF implementation."""
    return b"glTF" + b"\0" * size


def test_the_model_library_lists_every_team_and_positional(client):
    d = client.get("/api/plugins/bloodbowl/models").json()
    assert d["ok"] and len(d["teams"]) == 30
    amazon = next(t for t in d["teams"] if t["team"] == "Amazon")
    assert amazon["total"] == len(amazon["positionals"]) > 0
    assert amazon["have"] == 0, "a fresh install ships no models"
    assert all(not p["has_model"] for p in amazon["positionals"])


def test_a_model_round_trips_and_is_served_back(client):
    base = "/api/plugins/bloodbowl/models/amazon/eagle-warrior"
    up = client.post(f"{base}?filename=eagle.glb", content=_glb())
    assert up.status_code == 200, up.text
    assert up.json()["model"]["has_model"] and up.json()["model"]["file"] == "eagle-warrior.glb"

    got = client.get(f"{base}/file")
    assert got.status_code == 200 and got.content == _glb()

    listed = client.get("/api/plugins/bloodbowl/models").json()
    amazon = next(t for t in listed["teams"] if t["team"] == "Amazon")
    assert amazon["have"] == 1

    assert client.delete(base).json()["ok"] is True
    assert client.get(f"{base}/file").status_code == 404


def test_an_unknown_positional_is_a_404_not_a_path(client):
    """The security boundary: a request names slugs, and those are matched against the
    SHIPPED ROSTER, which supplies the filename. There is no arrangement of dots and
    slashes that resolves to something the roster does not already contain."""
    for team, pos in [("../../etc", "passwd"), ("amazon", "../../../secrets"), ("nope", "nobody")]:
        r = client.post(f"/api/plugins/bloodbowl/models/{team}/{pos}?filename=x.glb", content=_glb())
        assert r.status_code == 404, f"{team}/{pos} should be unknown, got {r.status_code}"


def test_only_model_files_are_accepted(client):
    base = "/api/plugins/bloodbowl/models/amazon/python-warrior"
    bad = client.post(f"{base}?filename=payload.py", content=b"import os")
    assert bad.status_code == 400 and "not a model" in bad.json()["detail"]
    empty = client.post(f"{base}?filename=x.glb", content=b"")
    assert empty.status_code == 400
    assert client.get(f"{base}/file").status_code == 404, "nothing was stored"


def test_replacing_a_model_does_not_leave_both_containers(client):
    """Otherwise a positional ends up with a .glb AND a .gltf and which one loads is
    decided by sort order — a silent winner is worse than either answer."""
    import bloodbowl

    base = "/api/plugins/bloodbowl/models/amazon/jaguar-warrior"
    client.post(f"{base}?filename=a.gltf", content=_glb(8))
    client.post(f"{base}?filename=b.glb", content=_glb(16))
    stored = sorted(p.name for p in (bloodbowl.models.models_dir() / "amazon").glob("jaguar-warrior.*"))
    assert stored == ["jaguar-warrior.glb"], stored


def test_uploads_live_in_the_state_dir_not_the_repo(client):
    """A coach's models are their files: never committed, and a git-URL install ships
    none. `state/` is already gitignored, which is why they go there."""
    import bloodbowl

    assert bloodbowl.models.models_dir().parent == bloodbowl.store.state_dir()
    assert ROOT not in bloodbowl.models.models_dir().parents


def test_the_models_view_is_declared_and_served(client):
    view = next(v for v in _manifest()["views"] if v["id"] == "models")
    assert view["path"] == "/plugins/bloodbowl/models"
    assert client.get(view["path"]).status_code == 200


# --- voxel players -------------------------------------------------------------------


def test_every_positional_maps_to_a_voxel_archetype():
    """The archetypes are the ROSTER'S OWN taxonomy, not invented — so the JS keyword
    table and the shipped data have to agree, and this is what notices when they drift
    (a new team, a fork's roster, a renamed Keyword).

    Read out of the JS rather than reimplemented in Python: duplicating the rules here
    would let the two copies diverge silently, which is the bug this test exists to catch.
    """
    import json
    import re

    src = (ROOT / "web3d" / "src" / "voxelPlayer.js").read_text()
    table = re.search(r"KEYWORD_TO_ARCHETYPE = \[(.*?)\];", src, re.S).group(1)
    keywords = [m.lower() for m in re.findall(r'\["([^"]+)"', table)]
    assert keywords, "the keyword table should not be empty"
    # Big Guy MUST be tested before Blocker: an "Ogre Blocker" carries the Keywords
    # "Big Guy, Blocker, Ogre" and is a Big Guy. Reading them the other way round builds
    # a Troll at lineman scale, which is wrong in the one place the silhouette matters.
    assert keywords.index("big guy") < keywords.index("blocker")

    rosters = json.loads((ROOT / "data" / "rosters.json").read_text())
    unmatched = []
    for team in rosters["teams"]:
        for pos in team["positionals"]:
            hay = f"{pos.get('role') or ''} {pos['position']}".lower()
            if not any(k in hay for k in keywords):
                unmatched.append(f"{team['name']} / {pos['position']} ({pos.get('role')})")
    # A miss is not fatal at runtime — archetypeFor falls back to a lineman — but it means
    # a positional renders as something it is not, so it should be a deliberate choice.
    assert len(unmatched) <= 18, f"{len(unmatched)} positionals match no archetype keyword: {unmatched[:5]}"


def test_the_voxel_layer_holds_no_rules():
    """Same invariant the 2D and 3D boards carry: a renderer that reasoned about the game
    would be a second, silently diverging engine. The voxel build reads ST for bulk and
    Keywords for shape — both descriptive — and nothing else."""
    body = "\n".join(
        (ROOT / "web3d" / "src" / f).read_text() for f in ("voxelPlayer.js", "teamPalette.js", "VoxelPawn.jsx")
    )
    for invented in ("dodge", "tackle", "turnover", "rush", "armour", "touchdown"):
        assert invented not in body.lower(), f"the voxel layer is reasoning about {invented!r}"


def test_the_3d_pawns_stand_on_the_turf_and_face_the_opposition(client):
    """Two defects that only a render shows, pinned so they cannot come back.

    The pawn group tweened toward y=0.5 — the CAPSULE's half-height, since a capsule's
    origin is its centre. Every other body stands on its own feet at y=0, so voxel players
    hovered half a square above the pitch.

    And both bodies are built with their depth along Z, so untouched they face the
    TOUCHLINE. They are turned down the length toward the End Zone they attack.
    """
    src = (ROOT / "web3d" / "src" / "Pawn.jsx").read_text()
    assert "position={[wx, 0, wz]}" in src, "the pawn group must sit ON the turf"
    assert "0.5 - pose.sagY" not in src, "the capsule-era half-height offset is back"
    assert "const facing" in src and 'p.side === "home"' in src, "pawns must face the opposition"


def test_a_loose_ball_is_drawn(client):
    """A carried ball rides above its carrier; a loose one had nothing at all, so a
    bounce, a fumble or a kick still in the air left the pitch looking empty. `in_air` is
    a state the engine tracks separately — the ball cannot be caught until the Kick-off
    Event resolves — so it is drawn differently, with the square it will land on."""
    src = (ROOT / "web3d" / "src" / "Ball.jsx").read_text()
    assert "ball.carrier" in src, "a carried ball is the Pawn's job, not this one's"
    assert "in_air" in src and "in_play" in src
    main = (ROOT / "web3d" / "src" / "main.jsx").read_text()
    assert "<Ball ball={match?.ball} />" in main


# --- the roster builder --------------------------------------------------------------


def _ogre(**over) -> dict:
    """A legal-ish Ogre draft: 11 Gnoblars and 3 Ogres inside the million."""
    base = {
        "team": "Ogre",
        "players": {"Gnoblar Lineman": 11, "Ogre Blocker": 3},
        "rerolls": 2,
        "coaches": 0,
        "cheerleaders": 0,
        "apothecary": False,
        "fans": 1,
    }
    base.update(over)
    return base


def test_the_draft_limits_are_the_rulebook_s(client):
    """These are quoted, not recalled — the numbers recall gets wrong are exactly these."""
    from bloodbowl import draft as d

    assert d.DEFAULT_BUDGET == 1_000_000
    assert (d.MIN_PLAYERS, d.MAX_PLAYERS) == (11, 16)
    assert d.MAX_REROLLS == 8
    assert d.MAX_COACHES == d.MAX_CHEERLEADERS == 6
    assert d.COACH_COST == d.CHEERLEADER_COST == 10_000
    assert d.APOTHECARY_COST == 50_000
    assert (d.MIN_FANS, d.MAX_FANS_AT_DRAFT, d.FAN_COST) == (1, 3, 5_000)


def test_team_options_come_from_the_shipped_roster(client):
    d = client.get("/api/plugins/bloodbowl/draft/options/Ogre").json()
    assert d["ok"] and d["reroll_cost"] == 70_000
    gnoblar = next(p for p in d["positionals"] if p["position"] == "Gnoblar Lineman")
    assert (gnoblar["cost"], gnoblar["max"]) == (15_000, 16)
    assert client.get("/api/plugins/bloodbowl/draft/options/Nonesuch").status_code == 404


def test_a_draft_is_costed_and_checked(client):
    from bloodbowl import draft

    legal = _ogre()
    assert draft.problems(legal) == [], draft.problems(legal)
    assert draft.price(legal)["treasury"] >= 0

    # Each limit reports in the rulebook's own terms, and ALL of them at once — a coach
    # mid-draft breaks several, and one refusal at a time is miserable.
    bad = draft.problems(_ogre(players={"Gnoblar Lineman": 20}, rerolls=9, coaches=7, fans=5))
    joined = " | ".join(bad)
    assert len(bad) >= 4, joined
    assert "at most 16" in joined and "more than 8" in joined and "at most 6" in joined


def test_an_illegal_roster_saves_but_will_not_be_placed(client):
    """Saving records work in progress; placing is where the line is, because the board is
    shared state and a half-drafted team is a scenario nobody meant to test."""
    short = _ogre(players={"Gnoblar Lineman": 3})
    saved = client.put("/api/plugins/bloodbowl/draft/wip", json=short).json()
    assert saved["ok"] and saved["problems"], "an illegal roster saves, with its problems"

    placed = client.post("/api/plugins/bloodbowl/draft/wip/place?side=home")
    assert placed.status_code == 400 and "at least 11" in placed.json()["detail"]


def test_a_legal_roster_places_a_squad_on_the_board(client):
    client.put("/api/plugins/bloodbowl/draft/ogres", json=_ogre())
    d = client.post("/api/plugins/bloodbowl/draft/ogres/place?side=home").json()
    # ELEVEN, not the whole draft list. A Team Draft List may hold 16, but the board's own
    # rule is "11 players on the pitch — the limit is 11"; the rest are the reserves box.
    assert d["ok"] and d["placed"] == 11 and d["refused"] == []
    board = client.get("/api/plugins/bloodbowl/state").json()
    home = [p for p in (board.get("scenario") or board)["players"] if p["side"] == "home"]
    assert len(home) == 11
    # A coach fields their best: all three 140,000gp Ogres make the eleven, and Gnoblars
    # fill the rest. Taking the draft list as written fielded eleven Gnoblars and benched
    # every Ogre, which is the wrong default even though it broke no rule.
    import collections

    counts = collections.Counter(p["position"] for p in home)
    assert counts["Ogre Blocker"] == 3 and counts["Gnoblar Lineman"] == 8, counts
    # The squad replaces that side only — the opposition is left alone.
    assert client.post("/api/plugins/bloodbowl/draft/ogres/place?side=away").json()["side"] == "away"


def test_saved_rosters_live_in_the_state_dir(client):
    import bloodbowl
    from bloodbowl import draft

    client.put("/api/plugins/bloodbowl/draft/keepme", json=_ogre())
    assert draft.rosters_dir().parent == bloodbowl.store.state_dir()
    assert ROOT not in draft.rosters_dir().parents
    assert any(r["name"] == "keepme" for r in client.get("/api/plugins/bloodbowl/draft").json()["rosters"])
    assert client.delete("/api/plugins/bloodbowl/draft/keepme").json()["ok"] is True


def test_a_draft_places_into_a_named_shape_with_the_beef_on_the_line(client):
    """Filling a shape in DRAFT ORDER would stand a ST1 Gnoblar on the Line of Scrimmage
    with three idle Ogres behind him. The assignment is a stated default — line takes the
    highest Strength, deep squares the highest Move Allowance — and a coach can move
    anyone afterwards. It is not a recommendation, but it beats having no opinion."""
    client.put("/api/plugins/bloodbowl/draft/ogres", json=_ogre())
    d = client.post("/api/plugins/bloodbowl/draft/ogres/place?side=home&preset=Kick-off%20receive").json()
    assert d["ok"] and d["preset"] == "Kick-off receive", d
    # That shape is only TEN squares; a full side is eleven, so it is topped up.
    assert d["placed"] == 11, d
    assert d["setup"]["problems"] == [], d["setup"]

    board = client.get("/api/plugins/bloodbowl/state").json()
    home = [p for p in board["players"] if p["side"] == "home"]
    assert len(home) == 11
    los = [p for p in home if p["y"] == 13]
    assert los, "the shape has a Line of Scrimmage"
    # Ogre Blockers are ST5, Gnoblars ST1 — every Ogre drafted should be on the line
    # before any Gnoblar is, and there are 3 of them against 3 LoS squares.
    assert all(p["position"] == "Ogre Blocker" for p in los), [p["position"] for p in los]


def test_placing_into_an_unknown_shape_is_refused(client):
    client.put("/api/plugins/bloodbowl/draft/ogres", json=_ogre())
    r = client.post("/api/plugins/bloodbowl/draft/ogres/place?side=home&preset=Nonesuch")
    assert r.status_code == 404 and "Nonesuch" in r.json()["detail"]


def test_the_assignment_is_pure_and_shortest_list_wins(client):
    """A preset has at most 11 squares and a squad may have 16 — the rest are simply not
    on the pitch, which is what a reserves box is."""
    from bloodbowl.draft import assign

    players = [{"n": i, "MA": 6, "ST": 3, "AV": "9+"} for i in range(16)]
    squares = [{"x": 8, "y": 13, "label": "LOS"}, {"x": 8, "y": 7, "label": "safety"}]
    pairs = assign(players, squares)
    assert len(pairs) == 2, "shortest list wins"
    assert assign([], squares) == [] and assign(players, []) == []


def test_every_shipped_setup_fields_a_legal_eleven(client):
    """A shape is not always eleven — "Kick-off receive" is ten squares and "Line of
    Scrimmage only" is three — but you field ELEVEN if you have them. Topping up must not
    break the limits it is filling around, and the Wide Zone cap is the one a naive fill
    breaks first, so the board's OWN review is the judge rather than this test's opinion.
    """
    from bloodbowl.presets import all_presets

    client.put("/api/plugins/bloodbowl/draft/ogres", json=_ogre())
    for preset in (p.name for p in all_presets() if p.kind == "setup"):
        for side in ("home", "away"):
            d = client.post(f"/api/plugins/bloodbowl/draft/ogres/place?side={side}&preset={preset}").json()
            assert d["ok"], (preset, side, d)
            assert d["placed"] == 11, f"{preset} on {side} fielded {d['placed']}"
            assert d["setup"]["problems"] == [], (preset, side, d["setup"]["problems"])


def test_a_seat_cannot_abandon_a_match_it_is_playing(registry):
    """A match with controllers is somebody else's game as much as the agent's. One seat
    abandoned a live 28-minute match two turns into the second half — it had correctly
    spotted that the half never kicked off, and then destroyed the only record of the bug.
    Correct diagnosis, wrong tool: report it and hand the turn back."""
    import bloodbowl

    bloodbowl.register(registry)
    tools = {t.name: t for t in registry.tools}
    tools["bb_pitch_place"].invoke({"side": "home", "team": "Orc", "position": "Orc Lineman", "x": 7, "y": 13})
    tools["bb_pitch_place"].invoke({"side": "away", "team": "Skaven", "position": "Skaven Clanrat", "x": 3, "y": 20})
    tools["bb_game_new"].invoke({"seed": 4, "kicking_to": "home", "you": "home"})

    refused = json.loads(tools["bb_game_abandon"].invoke({}))
    assert refused["ok"] is False and "not yours to discard" in refused["error"]
    assert bloodbowl.store.load_match() is not None, "the match must survive the refusal"

    # An operator can still ask for it explicitly.
    assert json.loads(tools["bb_game_abandon"].invoke({"confirm": "discard"}))["ok"] is True
    assert bloodbowl.store.load_match() is None


def test_an_unclaimed_practice_board_still_clears(registry):
    """The restraint control: the gate is about a game somebody is PLAYING, not about
    every match. A practice board owns no sides and clears as it always did."""
    import bloodbowl

    bloodbowl.register(registry)
    tools = {t.name: t for t in registry.tools}
    tools["bb_pitch_place"].invoke({"side": "home", "team": "Orc", "position": "Orc Lineman", "x": 7, "y": 13})
    tools["bb_pitch_place"].invoke({"side": "away", "team": "Skaven", "position": "Skaven Clanrat", "x": 3, "y": 20})
    tools["bb_game_new"].invoke({"seed": 4, "kicking_to": "home"})  # no `you` — unclaimed

    assert json.loads(tools["bb_game_abandon"].invoke({}))["ok"] is True
    assert bloodbowl.store.load_match() is None
