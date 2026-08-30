"""Player icons: mapping our positionals onto the FFB icon sets.

The board drew coloured tiles with initials on them. These are the icons the
FUMBBL client uses, vendored under `web/sprites/` with christerk's permission
(see the Credits section of README.md), so a Troll looks like a Troll.

**A MISSING ICON IS NOT A FAILURE HERE, AND THAT IS THE WHOLE DIFFERENCE FROM
`fumbbl.py`.** Importing a team wrong gives a coach the wrong STATS, so that
module refuses to guess and names what it could not map. An icon is decoration:
the worst case is the tile the board already drew, which is why this one is
allowed to be generous — near-matches are taken, two positionals may share an
icon, and anything unresolved simply has none. The library can only ever upgrade
the board, never break it. Same contract as the 3D mesh library.

THREE PASSES, and the middle one is the one worth keeping:

1. **The names agree** once both are reduced to letters — FFB writes
   `amazon_jaguarwarriorblocker`, we write "Jaguar Warrior", and one contains
   the other.

2. **The ROLE agrees.** FFB names many positionals by their job — `blitzer`,
   `thrower`, `lineman`, `catcher` — and `data/rosters.json` already carries that
   job in `role` ("Thrower, Elf"). So High Elf's Phoenix Warrior finds
   `highelf_thrower` because our own data says it is a Thrower, not because
   somebody remembered that it is. That matters: the alternative was me deciding
   from memory which S3 name replaced which BB2020 one, which is exactly the kind
   of confident recall this plugin exists to route around.

3. **A short alias table** for the genuine renames the first two cannot see,
   every entry with the reason it exists. It is deliberately small; if it starts
   growing, the answer is a better rule, not a longer table.
"""

from __future__ import annotations

import re
from pathlib import Path

#: The icons that ship with the plugin.
SHIPPED = Path(__file__).resolve().parent / "web" / "sprites"

#: An operator's own icons, from `bloodbowl.sprite_dir`. Searched FIRST, and it
#: only needs to contain what it replaces — resolution falls through per
#: positional, so redrawing one Troll does not mean redrawing the other 152.
_CUSTOM: Path | None = None


def use_pack(directory: str | None) -> None:
    """Point at a custom icon directory (config `bloodbowl.sprite_dir`)."""
    global _CUSTOM, _CATALOGUE
    _CUSTOM = Path(directory).expanduser() if str(directory or "").strip() else None
    _CATALOGUE = None  # the answer changes, so the cache cannot stand


def packs() -> list[Path]:
    """Where to look, best first."""
    return [d for d in (_CUSTOM, SHIPPED) if d and d.is_dir()]


def locate(filename: str) -> Path | None:
    """The actual file behind a catalogue entry — custom pack wins.

    A bare PNG filename or nothing. This resolves a name straight off a PUBLIC
    route, so it enforces the type here rather than trusting the caller to — the
    route checks too, and both checks are cheap. Without it a pack's own
    `sprites.json` would be resolvable by name.
    """
    if not filename or "/" in filename or "\\" in filename or filename.startswith("."):
        return None
    if not filename.lower().endswith(".png"):
        return None
    for d in packs():
        candidate = d / filename
        if candidate.is_file():
            return candidate
    return None


#: The sheet's geometry. FOUR columns always: 0-1 are the red kit, 2-3 the blue.
#: The rows are cosmetic variants, so eleven Linemen are not eleven identical
#: figures.
#:
#: ⚠️ THE CELL IS NOT A FIXED SIZE. It is `width / 4`, and it ranges from 20 to 42
#: across the 153 sheets we ship, because a Troll is drawn bigger than a Skink.
#: Assuming 28 (the commonest, 54 of them) puts two thirds of the roster off by a
#: few pixels and slices the Big Guys in half. Rows are then `height / cell`, 1 to
#: 16 of them. Verified to hold for every file by `test_every_sprite_sheet_is_four_columns`.
COLUMNS = 4
HOME_COLUMNS = (0, 1)
AWAY_COLUMNS = (2, 3)


def dimensions(path: Path) -> tuple[int, int]:
    """(width, height) of a PNG, from its IHDR.

    Read by hand rather than with Pillow: this plugin ships NO runtime pip
    dependencies, and a header is 8 bytes of big-endian ints.
    """
    import struct

    with path.open("rb") as fh:
        head = fh.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"not a PNG: {path.name}")
    return struct.unpack(">II", head[16:24])


def geometry(path: Path, columns: int | None = None) -> dict:
    """How to slice one sheet: how many columns, its cell size, its variant rows.

    `columns` defaults to FFB's four (two red, two blue). CUSTOM ART NEED NOT
    FOLLOW THAT — pass `columns: 1` in `sprites.json` for a single flat image and
    the whole picture becomes the only frame, which is what a hand-drawn
    replacement most likely is.
    """
    cols = int(columns or COLUMNS)
    cols = cols if cols > 0 else COLUMNS
    width, height = dimensions(path)
    cell = width // cols
    return {
        "file_columns": cols,
        "cell": cell,
        "rows": (height // cell) if cell else 1,
        "w": width,
        "h": height,
    }


#: Renames neither the name nor the role can bridge. Keyed (team, our position).
#: EVERY ENTRY NAMES ITS REASON, because an alias is an assertion about two
#: editions of a rulebook and an unexplained one cannot be checked by the next
#: person.
ALIASES = {
    # BB2020 called the Dwarf lineman a "Blocker"; S3 calls it a Lineman. The same
    # rename `fumbbl.py` handles for imported teams.
    ("Dwarf", "Dwarf Lineman"): "blocker",
    ("Old World Alliance", "Dwarf Lineman"): "dwarfblocker",
    # Spelling. FFB writes "ulfwerenar", the S3 roster writes "Ulfwerener".
    ("Norse", "Ulfwerener"): "ulfwerenar",
    # FFB ships the Norse Yhetee under its older name.
    ("Norse", "Yhetee"): "snowtroll",
    # Underworld's icons are prefixed again inside the file name.
    ("Underworld Denizens", "Snotling Lineman"): "underworldsnotlings",
    ("Underworld Denizens", "Troll*"): "underworldtroll",
    ("Underworld Denizens", "Skaven Clanrat"): "skavenlineman",
    # S3's Skink Lineman is FFB's Skink Runner. Nothing shared but "skink", and a
    # rule loose enough to match on that would start matching anything.
    ("Lizardmen", "Skink Lineman"): "skinkrunner",
}

#: Team name → the prefix FFB files use, where reducing to letters is not enough.
TEAM_ALIASES = {
    "chaos renegades": "chaosrenegade",
}


def slug(text: str) -> str:
    """Letters and digits only. FFB runs words together (`triballinewoman`), so
    there is nothing to tokenise on — the comparison has to be on the reduction."""
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def roles(positional: dict) -> list[str]:
    """The job words from `role`, which is where FFB's generic names live.

    Each job also yields its MAN/MEN form. FFB names some files in the plural
    (`bretonnian_linemen`) and "lineman" is not a substring of "linemen" — the
    vowel moves — so an irregular plural silently costs a match that every other
    team gets. It cost three Bretonnian positionals before anyone noticed.
    """
    out = []
    for r in str(positional.get("role") or "").split(","):
        r = slug(r)
        if not r:
            continue
        out.append(r)
        if r.endswith("man"):
            out.append(r[:-3] + "men")
    return out


def team_prefix(team_name: str) -> str:
    key = str(team_name or "").strip().lower()
    return TEAM_ALIASES.get(key, slug(team_name))


def available() -> dict[str, list[str]]:
    """FFB-style names on disk, grouped by team prefix, custom pack merged in.

    Read from the FILES so a sprite that was never vendored cannot be promised by
    a table — the commonest way an icon library lies.
    """
    out: dict[str, list[str]] = {}
    for d in reversed(packs()):  # later packs win, so walk worst-first
        for p in sorted(d.glob("*.png")):
            if "__" in p.stem or "_" not in p.stem:
                continue  # `__` is the our-own-naming form, handled separately
            team, position = p.stem.split("_", 1)
            names = out.setdefault(team, [])
            if position not in names:
                names.append(position)
    return out


def overrides() -> dict:
    """`sprites.json` from the best pack that has one: {team: {position: entry}}.

    THE EXPLICIT ESCAPE HATCH. Everything else in this module is a rule for
    guessing which FFB file goes with which of our positionals; this is where
    somebody says so outright, and it wins. An entry is `{"file": "...",
    "columns": 4}` — `columns` only when custom art does not follow FFB's
    four-column red/blue convention.
    """
    import json

    for d in packs():
        f = d / "sprites.json"
        if f.is_file():
            try:
                loaded = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    return loaded
            except (OSError, ValueError):
                # A broken override file reads as absent rather than taking the
                # board down. It is decoration; the tile still draws.
                return {}
    return {}


def our_name(team_name: str, position: str) -> str:
    """The DROP-IN filename: our own team and position, `__`-joined.

    `orc__orc-lineman.png` beats any amount of matching, needs no table and no
    code change — which is the whole point. Custom art should not have to learn
    FFB's naming to replace it.
    """
    return f"{slug(team_name)}__{re.sub(r'[^a-z0-9]+', '-', str(position or '').lower()).strip('-')}.png"


def find(team_name: str, positional: dict, catalogue: dict[str, list[str]] | None = None) -> str | None:
    """The icon FILE for one positional, or None to leave the board's own tile.

    RESOLUTION ORDER, first hit wins — and the first two exist so that replacing
    the art never means touching this module:

    1. `sprites.json` says so outright.
    2. A file named by OUR names (`orc__orc-lineman.png`) is sitting in a pack.
    3. The FFB-derived match: name, then role, then the alias table.
    4. Nothing — and nothing is fine.
    """
    position = str(positional.get("position") or "")

    # 1. Somebody said so.
    explicit = (overrides().get(team_name) or {}).get(position)
    if isinstance(explicit, dict) and explicit.get("file"):
        return str(explicit["file"])
    if isinstance(explicit, str) and explicit:
        return explicit

    # 2. Drop-in by our own naming. No table, no code change.
    drop_in = our_name(team_name, position)
    if locate(drop_in):
        return drop_in

    # 3. The FFB naming, which is what the shipped pack uses.
    cat = available() if catalogue is None else catalogue
    prefix = team_prefix(team_name)
    candidates = cat.get(prefix) or cat.get(prefix.rstrip("s")) or cat.get(prefix + "s") or []
    if not candidates:
        return None

    alias = ALIASES.get((team_name, position))
    if alias and alias in candidates:
        return f"{prefix}_{alias}.png"

    ours = slug(position.replace("*", ""))
    bare = ours[len(prefix) :] if ours.startswith(prefix) else ours

    # The names agree, or one contains the other.
    for c in candidates:
        if c in (ours, bare):
            return f"{prefix}_{c}.png"
    for c in candidates:
        if bare and (bare in c or c in bare):
            return f"{prefix}_{c}.png"

    # Our own data says what the job is; FFB often names the file after it.
    # Longest first so "gutterrunner" beats a bare "runner" when both fit.
    for role in sorted(roles(positional), key=len, reverse=True):
        for c in sorted(candidates, key=len):
            if c == role:
                return f"{prefix}_{c}.png"
    for role in sorted(roles(positional), key=len, reverse=True):
        for c in sorted(candidates, key=len):
            if role and role in c:
                return f"{prefix}_{c}.png"

    return None


_CATALOGUE: dict | None = None


def catalogue() -> dict[str, dict[str, dict]]:
    """Every positional we ship an icon for: {team: {position: filename}}.

    Built once and served to the view, so the board does no matching of its own —
    the same reason it asks the engine what is legal instead of working it out.
    """
    from .pitch import rosters

    # Built once: /meta is fetched on every page load and this opens 153 files.
    # The sprites are shipped with the plugin and cannot change under a running
    # process, so there is nothing to invalidate.
    global _CATALOGUE
    if _CATALOGUE is not None:
        return _CATALOGUE

    cat = available()
    out: dict[str, dict[str, dict]] = {}
    for team in rosters().get("teams") or []:
        found = {}
        for p in team.get("positionals") or []:
            icon = find(team["name"], p, cat)
            if not icon:
                continue
            # The geometry rides WITH the file, so the view never measures an
            # image to work out how to slice it — same reason it asks the engine
            # what is legal instead of deriving it.
            path = locate(icon)
            if path is None:
                # Named but not on disk — an override or a stale table pointing at
                # a file nobody shipped. Skip it: the tile is a working board and
                # a broken image URL is not.
                continue
            explicit = (overrides().get(team["name"]) or {}).get(p["position"])
            columns = explicit.get("columns") if isinstance(explicit, dict) else None
            found[p["position"]] = {"file": icon, **geometry(path, columns)}
        if found:
            out[team["name"]] = found
    _CATALOGUE = out
    return out
