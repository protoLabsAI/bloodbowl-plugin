"""Player icons, and — mostly — how they get REPLACED.

The shipped icons are the FFB project's, used with permission. They are also
meant to be temporary: the plan is our own art, and the thing worth testing is
that swapping it in takes no code change, no table edit, and no all-or-nothing
migration. So most of what follows is about the override chain rather than about
the icons we happen to ship today.

The other half of the contract is that a MISSING icon is ordinary. Two of the 159
positionals have none, a fork may add teams with none, and an older host may send
no catalogue at all. Every one of those draws the tile the board always drew.
"""

from __future__ import annotations

import json

import pytest
from bloodbowl import sprites


@pytest.fixture
def pack(tmp_path, monkeypatch):
    """An empty custom pack, pointed at like `bloodbowl.sprite_dir` would."""
    monkeypatch.setattr(sprites, "_CUSTOM", tmp_path)
    monkeypatch.setattr(sprites, "_CATALOGUE", None)
    return tmp_path


def png(path, width=112, height=168):
    """A real PNG header — `dimensions` reads IHDR, so the bytes have to be right."""
    import struct
    import zlib

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))  # noqa: E731
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b""))


# --- replacing the art ----------------------------------------------------


def test_a_file_named_in_our_own_words_needs_no_table_and_no_code(pack):
    """THE DROP-IN. Custom art should not have to learn FFB's naming to replace
    it, so a file named after OUR team and OUR position wins outright."""
    png(pack / "orc__orc-lineman.png")
    got = sprites.find("Orc", {"position": "Orc Lineman", "role": "Lineman, Orc"})
    assert got == "orc__orc-lineman.png"


def test_a_custom_pack_replaces_file_by_file_not_all_or_nothing(pack):
    """The property that makes this usable: redrawing one Troll must not mean
    redrawing the other 152. Anything the pack does not hold falls through to the
    shipped icon."""
    png(pack / "orc__orc-lineman.png")
    cat = sprites.catalogue()
    assert cat["Orc"]["Orc Lineman"]["file"] == "orc__orc-lineman.png", "the replacement"
    assert cat["Orc"]["Orc Blitzer"]["file"].startswith("orc_"), "and the rest still resolve"


def test_sprites_json_is_the_explicit_escape_hatch(pack):
    """When a rule guesses wrong, somebody says so outright and that wins."""
    png(pack / "whatever-i-like.png")
    (pack / "sprites.json").write_text(json.dumps({"Orc": {"Orc Lineman": {"file": "whatever-i-like.png"}}}))
    assert sprites.find("Orc", {"position": "Orc Lineman"}) == "whatever-i-like.png"


def test_custom_art_need_not_follow_the_four_column_convention(pack):
    """Four columns (two red, two blue) is FFB's convention, not a law. A single
    flat image is what a hand-drawn replacement most likely is."""
    png(pack / "flat.png", width=64, height=64)
    (pack / "sprites.json").write_text(json.dumps({"Orc": {"Orc Lineman": {"file": "flat.png", "columns": 1}}}))
    entry = sprites.catalogue()["Orc"]["Orc Lineman"]
    assert entry["file_columns"] == 1
    assert entry["cell"] == 64 and entry["rows"] == 1, entry


def test_an_override_naming_a_file_nobody_shipped_is_skipped_not_broken(pack):
    """A stale table pointing at a missing file must not put a broken image URL on
    the board. The tile is a working board; a 404 is not."""
    (pack / "sprites.json").write_text(json.dumps({"Orc": {"Orc Lineman": {"file": "gone.png"}}}))
    assert "Orc Lineman" not in sprites.catalogue().get("Orc", {})


def test_a_corrupt_override_file_reads_as_absent(pack):
    """It is decoration. A bad JSON file must not take the board down with it."""
    (pack / "sprites.json").write_text("{not json")
    assert sprites.overrides() == {}
    assert sprites.catalogue()["Orc"]["Orc Lineman"]["file"].startswith("orc_")


# --- the shipped pack -----------------------------------------------------


def test_every_shipped_sheet_is_four_columns_of_square_cells():
    """THE GEOMETRY IS DERIVED, NOT ASSUMED — `cell = width / 4`, and it ranges
    from 20 to 42 across the sheets because a Troll is drawn bigger than a Skink.
    Hardcoding the commonest (28) slices every Big Guy in half."""
    files = sorted(sprites.SHIPPED.glob("*.png"))
    assert len(files) > 100, f"only {len(files)} icons shipped"
    for f in files:
        w, h = sprites.dimensions(f)
        assert w % sprites.COLUMNS == 0, f"{f.name} is {w}px, not divisible into 4 columns"
        cell = w // sprites.COLUMNS
        assert h % cell == 0, f"{f.name} is {h}px tall, not a whole number of {cell}px rows"


def test_almost_every_positional_has_an_icon_and_the_rest_keep_their_tile():
    """Coverage is a fact worth pinning: it is what stops a re-scrape of the
    roster data quietly turning the board back into coloured squares."""
    from bloodbowl.pitch import rosters

    cat = sprites.catalogue()
    total = missing = 0
    for team in rosters()["teams"]:
        for p in team["positionals"]:
            total += 1
            if p["position"] not in cat.get(team["name"], {}):
                missing += 1
    assert total == 159, total
    assert missing <= 2, f"{missing} positionals lost their icon"


def test_the_role_is_what_finds_the_generically_named_ones():
    """FFB names many files by the JOB — `highelf_thrower` — and our roster data
    already records the job. That is why Phoenix Warrior resolves without anyone
    having to remember which S3 name replaced which BB2020 one."""
    cat = {"highelf": ["blitzer", "catcher", "lineman", "thrower"]}
    got = sprites.find("High Elf", {"position": "Phoenix Warrior", "role": "Thrower, Elf"}, cat)
    assert got == "highelf_thrower.png"


def test_an_irregular_plural_still_matches():
    """ "lineman" is not a substring of "linemen" — the vowel moves — and that one
    fact silently cost three Bretonnian positionals their icon."""
    cat = {"bretonnian": ["blitzers", "linemen", "yeomen"]}
    got = sprites.find("Bretonnian", {"position": "Bretonnian Squire", "role": "Lineman, Human"}, cat)
    assert got == "bretonnian_linemen.png"


# --- serving --------------------------------------------------------------


def test_no_request_can_walk_out_of_the_pack(pack):
    """`locate` turns a name from the catalogue into a path on disk, and the route
    that calls it is PUBLIC. It takes a bare filename or nothing."""
    for bad in ("../store.py", "..%2Fstore.py", "sub/dir.png", ".hidden.png", "", "a\\b.png"):
        assert sprites.locate(bad) is None, bad


def test_the_icons_are_served_and_the_catalogue_rides_meta(client):
    base = "/api/plugins/bloodbowl"
    meta = client.get(f"{base}/meta").json()
    assert meta["sprites"]["Orc"]["Orc Lineman"]["file"], meta["sprites"].get("Orc")

    name = meta["sprites"]["Orc"]["Orc Lineman"]["file"]
    r = client.get(f"/plugins/bloodbowl/static/sprites/{name}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"

    assert client.get("/plugins/bloodbowl/static/sprites/../../store.py").status_code == 404
    assert client.get("/plugins/bloodbowl/static/sprites/nope.png").status_code == 404
