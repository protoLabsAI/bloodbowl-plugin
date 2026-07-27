"""Persistence for the current pitch.

One scenario, one file. The agent tools and the console view are two writers onto
the same board — that shared state is the whole point, so it round-trips through
disk rather than living in a module global that a reload would drop.

Path resolution is deliberately dependency-free: ``BLOODBOWL_DIR`` if set (the
tests use it), else the plugin's own ``state/`` directory. No host import, so this
module is unit-testable with nothing installed.
"""

from __future__ import annotations

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


def save(scenario: Scenario) -> None:
    """Atomic write: the view polls this file's contents, so a half-written board
    would render as an empty pitch mid-save."""
    p = state_path()
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".pitch-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(scenario.to_dict(), fh, indent=2, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise
