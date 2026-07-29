"""Test bootstrap — import the plugin with NO protoAgent host present.

Multi-module plugin, so register a synthetic package whose ``__path__`` is the repo
root; that is what makes the modules' relative imports (``from .pitch import ...``)
resolve standalone. Executing ``__init__.py`` is safe because every host-only import
lives inside ``register()`` / ``_tools()`` rather than at module top.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PKG = "bloodbowl"

# Isolate persisted board state BEFORE the package loads, so no test ever writes
# into the developer's real state/ dir.
os.environ.setdefault("BLOODBOWL_DIR", tempfile.mkdtemp(prefix="bb-test-"))

if PKG not in sys.modules:
    _spec = importlib.util.spec_from_file_location(PKG, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    assert _spec and _spec.loader
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[PKG] = _mod
    _spec.loader.exec_module(_mod)


@pytest.fixture(autouse=True)
def fresh_state(tmp_path, monkeypatch):
    """Every test gets its own board — the store is a real file, so without this
    they would leak placements into one another.

    The agent's PACE is turned off here too. It is a real wall-clock wait by
    design — see engine/pace.py — and a suite that sleeps two seconds per action
    is a suite nobody runs twice. One test turns it back on to prove it works.
    """
    from bloodbowl.engine import pace

    monkeypatch.setenv("BLOODBOWL_DIR", str(tmp_path))
    pace.configure(0)
    pace.reset()
    yield
    pace.configure(0)


class _Registry:
    def __init__(self, config: dict | None = None):
        # `register()` re-reads the pace from config, so a zero here is what keeps
        # it off for tests that register the plugin — the autouse fixture alone is
        # not enough, because registering overwrites it.
        self.config = {"agent_pace_s": 0, **(config or {})}
        self.tools: list = []
        self.routers: list[tuple[object, str]] = []
        self.surfaces: list = []
        self.skill_dirs: list = []
        # Bus subscriptions, so a test can assert WHEN a plugin wires one rather
        # than only that it did — `register()` is too early, and that failure is
        # silent on a real host.
        self.subscriptions: list = []

    def register_tool(self, t):
        self.tools.append(t)

    def register_tools(self, ts):
        self.tools.extend(ts)

    def register_router(self, router, prefix):
        self.routers.append((router, prefix))

    def register_surface(self, start, stop=None, name=None, reload=None):
        self.surfaces.append(name)

    def on(self, topic, handler):
        self.subscriptions.append((topic, handler))

    def emit(self, topic, data=None):
        """The bus, as far as a host-free test is concerned: it exists and swallows."""

    def register_skill_dir(self, path):
        self.skill_dirs.append(path)


@pytest.fixture
def registry():
    return _Registry()


@pytest.fixture
def app(registry):
    """A FastAPI app with the plugin's routers mounted as the HOST mounts them.

    Not merely "as register() hands them over": the host keys mounted routers on
    (plugin_id, prefix) and SKIPS any already mounted, so a second router for one
    prefix has all of its routes discarded. Mounting every router blindly made this
    fixture more forgiving than production and would have let a whole dead API
    pass its own tests.
    """
    import bloodbowl
    from fastapi import FastAPI

    bloodbowl.register(registry)
    application = FastAPI()
    mounted: set[str] = set()
    for router, prefix in registry.routers:
        if prefix in mounted:
            continue
        mounted.add(prefix)
        application.include_router(router, prefix=prefix)
    return application


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient

    return TestClient(app)
