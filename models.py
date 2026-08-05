"""The model library: one 3D mesh per positional, organised by team.

Uploads are USER DATA and live under the state dir beside the board, never in the repo —
a plugin installed from a git URL ships no models and a coach's collection is not ours to
version.

**No user input ever becomes a path.** A request names a team and a positional as slugs;
those are matched against the SHIPPED ROSTER and the canonical entry supplies the
filename. An unknown slug is a 404, not a traversal — there is no arrangement of dots and
slashes that resolves to something the roster does not already contain.
"""

from __future__ import annotations

import re
from pathlib import Path

#: What a browser will actually load. glTF's binary container is the one to prefer — a
#: .gltf can reference external .bin/textures that we would then have to serve too.
ALLOWED_SUFFIXES = {".glb", ".gltf"}

#: A mesh for a tabletop figure is a few MB at most; anything larger is a mistake or a
#: scan nobody wants to download per pawn. Stated rather than discovered at OOM time.
MAX_BYTES = 32 * 1024 * 1024


def slug(text: str) -> str:
    """A stable, filesystem-safe key for a roster name. Not reversible, and does not need
    to be: it is matched against the roster, never turned back into a name."""
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")


def models_dir() -> Path:
    from .store import state_dir

    d = state_dir() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _rosters() -> list[dict]:
    from .pitch import rosters

    return rosters().get("teams", [])


def resolve(team_slug: str, position_slug: str) -> tuple[str, str] | None:
    """Canonical ``(team, position)`` names for a slug pair, or None if unknown.

    This is the whole security boundary: everything downstream works from the names the
    roster gave us, so a path is only ever built from data we shipped.
    """
    for team in _rosters():
        if slug(team.get("name")) != slug(team_slug):
            continue
        for pos in team.get("positionals") or []:
            if slug(pos.get("position")) == slug(position_slug):
                return str(team["name"]), str(pos["position"])
        return None
    return None


def model_path(team: str, position: str, suffix: str = ".glb") -> Path:
    return models_dir() / slug(team) / f"{slug(position)}{suffix}"


def find_model(team: str, position: str) -> Path | None:
    """The stored mesh for a positional, whatever container it was uploaded as."""
    for suffix in sorted(ALLOWED_SUFFIXES):
        p = model_path(team, position, suffix)
        if p.is_file():
            return p
    return None


def save_model(team: str, position: str, filename: str, data: bytes) -> dict:
    """Store (or replace) a positional's mesh. Returns its catalogue row.

    Replacing removes the other container first, so a team cannot end up with both a .glb
    and a .gltf for one positional and a silent winner decided by sort order.
    """
    suffix = Path(str(filename or "")).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError(
            f"{suffix or 'that file'} is not a model — upload one of: {', '.join(sorted(ALLOWED_SUFFIXES))}"
        )
    if not data:
        raise ValueError("the upload was empty")
    if len(data) > MAX_BYTES:
        raise ValueError(f"{len(data) // (1024 * 1024)} MB is over the {MAX_BYTES // (1024 * 1024)} MB limit")

    for existing in ALLOWED_SUFFIXES:
        old = model_path(team, position, existing)
        if old.is_file():
            old.unlink()
    target = model_path(team, position, suffix)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return row(team, position)


def delete_model(team: str, position: str) -> bool:
    found = find_model(team, position)
    if found is None:
        return False
    found.unlink()
    return True


def row(team: str, position: str) -> dict:
    """One positional's entry in the catalogue."""
    p = find_model(team, position)
    return {
        "position": position,
        "slug": slug(position),
        "has_model": p is not None,
        "file": p.name if p else "",
        "bytes": p.stat().st_size if p else 0,
    }


def catalogue() -> list[dict]:
    """Every team with its positionals and which of them have a mesh — the whole view."""
    out = []
    for team in _rosters():
        name = str(team.get("name") or "")
        rows = [row(name, str(p.get("position") or "")) for p in (team.get("positionals") or [])]
        out.append(
            {
                "team": name,
                "slug": slug(name),
                "tier": team.get("tier"),
                "positionals": rows,
                "have": sum(1 for r in rows if r["has_model"]),
                "total": len(rows),
            }
        )
    return out
