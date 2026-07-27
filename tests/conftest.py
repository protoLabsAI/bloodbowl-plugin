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
    they would leak placements into one another."""
    monkeypatch.setenv("BLOODBOWL_DIR", str(tmp_path))
    yield


class _Registry:
    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.tools: list = []
        self.routers: list[tuple[object, str]] = []
        self.surfaces: list = []
        self.skill_dirs: list = []

    def register_tool(self, t):
        self.tools.append(t)

    def register_tools(self, ts):
        self.tools.extend(ts)

    def register_router(self, router, prefix):
        self.routers.append((router, prefix))

    def register_surface(self, start, stop=None, name=None):
        self.surfaces.append(name)

    def register_skill_dir(self, path):
        self.skill_dirs.append(path)


@pytest.fixture
def registry():
    return _Registry()


@pytest.fixture
def app(registry):
    """A FastAPI app with the plugin's routers mounted EXACTLY as register() mounts
    them — so the tests exercise the real paths, not the ones the rules recommend."""
    import bloodbowl
    from fastapi import FastAPI

    bloodbowl.register(registry)
    application = FastAPI()
    for router, prefix in registry.routers:
        application.include_router(router, prefix=prefix)
    return application


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient

    return TestClient(app)
