"""Persistence for the current pitch.

One scenario, one file. The agent tools and the console view are two writers onto
the same board — that shared state is the whole point, so it round-trips through
disk rather than living in a module global that a reload would drop.

Path resolution is deliberately dependency-free: ``BLOODBOWL_DIR`` if set (the
tests use it), else the plugin's own ``state/`` directory. No host import, so this
module is unit-testable with nothing installed.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path

from .pitch import Scenario


def state_dir() -> Path:
    raw = os.environ.get("BLOODBOWL_DIR", "").strip()
    d = Path(raw).expanduser() if raw else Path(__file__).resolve().parent / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def state_path() -> Path:
    return state_dir() / "pitch.json"


def load() -> Scenario:
    p = state_path()
    if not p.exists():
        return Scenario()
    try:
        return Scenario.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        # A corrupt board must not brick the view — start clean rather than 500.
        return Scenario()


def previous_path() -> Path:
    return state_dir() / "pitch.prev.json"


def load_previous() -> Scenario | None:
    p = previous_path()
    if not p.exists():
        return None
    try:
        return Scenario.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return None


def save(scenario: Scenario) -> None:
    """Atomic write, keeping the outgoing board as ``pitch.prev.json``.

    Atomic because the view polls this file's contents, so a half-written board would
    render as an empty pitch mid-save. The one-deep backup exists because the write
    path is destructive and reachable from several directions at once — the agent's
    tools, the operator's drags, and a whole-board ``/replace``. Losing a worked-out
    setup to one careless call and having no way back is worse than the disk cost of
    a second file. In-session undo lives in the view; this survives a restart."""
    p = state_path()
    if p.exists():
        # A failed backup must never block the save itself.
        with contextlib.suppress(OSError):
            os.replace(p, previous_path())
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".pitch-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(scenario.to_dict(), fh, indent=2, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


# --- the match ------------------------------------------------------------
#
# Kept beside the practice board rather than replacing it. The two are different
# things: a scenario is a position you are working out, permissively; a match is a
# game in progress, strictly. Starting a match must not cost you the board you set
# it up from.


def match_path() -> Path:
    return state_dir() / "match.json"


def load_match():
    """The match in progress, or None.

    The saved file's log is authoritative — ``Match.from_dict`` rebuilds the
    position by folding it, so a hand-edited board cannot disagree with its own
    history.
    """
    from .engine.state import Match

    p = match_path()
    if not p.exists():
        return None
    try:
        return Match.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError, TypeError, ValueError, KeyError):
        return None


def save_match(match) -> None:
    """Atomic, same reasoning as the board: the view polls this file."""
    p = match_path()
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".match-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(match.to_dict(), fh, indent=2, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def clear_match() -> bool:
    p = match_path()
    if p.exists():
        p.unlink()
        return True
    return False
