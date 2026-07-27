#!/usr/bin/env python3
"""Drive the pitch view in a real browser, so rendering bugs get caught here rather
than by the operator.

Everything before this was server-side: endpoints returning 200 and tools not
throwing. That says nothing about whether the board is legible, whether a drag
lands, or whether the hover card is positioned somewhere useful — which is exactly
the class of defect that shipped.

Serves the plugin's own routers on a throwaway port against an isolated board, so
it never touches the live agent. The DS kit is absent, so the view falls through to
its no-kit shim — which is itself worth exercising, since that is what an older host
would serve.

    python harness.py                 # screenshot the board to shots/
    python harness.py --check         # assert the things that broke before
    python harness.py --live          # point at the running agent instead (READ ONLY)
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SHOTS = ROOT / "shots"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def serve() -> tuple[str, object]:
    """Boot the plugin's routers exactly as register() mounts them, on an isolated
    board. Returns (base_url, server)."""
    import uvicorn
    from fastapi import FastAPI

    os.environ["BLOODBOWL_DIR"] = tempfile.mkdtemp(prefix="bb-harness-")
    sys.path.insert(0, str(ROOT.parent))

    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "bloodbowl", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bloodbowl"] = mod
    spec.loader.exec_module(mod)

    class _Reg:
        config: dict = {}
        routers: list = []

        def register_tool(self, t): ...
        def register_router(self, router, prefix):
            self.routers.append((router, prefix))

    reg = _Reg()
    mod.register(reg)
    app = FastAPI()
    for router, prefix in reg.routers:
        app.include_router(router, prefix=prefix)

    port = _free_port()
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(cfg)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        with contextlib.suppress(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        time.sleep(0.1)
    return f"http://127.0.0.1:{port}", server


def seed(base: str) -> None:
    """A board worth looking at: a legal home line plus an away line facing it."""
    import urllib.request

    def post(path, body):
        req = urllib.request.Request(
            base + "/api/plugins/bloodbowl" + path,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)

    post("/teams", {"home_team": "Orc", "away_team": "Skaven"})
    home = [
        ("Big Un Blocker", 6, 13),
        ("Troll", 7, 13),
        ("Big Un Blocker", 8, 13),
        ("Orc Blitzer", 3, 13),
        ("Orc Blitzer", 13, 13),
        ("Orc Lineman", 5, 11),
        ("Orc Thrower", 8, 4),
    ]
    away = [
        ("Skaven Clanrat", 6, 14),
        ("Skaven Clanrat", 7, 14),
        ("Skaven Clanrat", 8, 14),
        ("Gutter Runner", 2, 16),
        ("Gutter Runner", 14, 16),
        ("Skaven Blitzer", 8, 18),
    ]
    for pos, x, y in home:
        post("/place", {"side": "home", "team": "Orc", "position": pos, "x": x, "y": y})
    for pos, x, y in away:
        post("/place", {"side": "away", "team": "Skaven", "position": pos, "x": x, "y": y})


CHECKS: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((name, "PASS" if ok else "FAIL"))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)
    return ok


def drive(base: str, *, do_checks: bool, live: bool) -> int:
    from playwright.sync_api import sync_playwright

    SHOTS.mkdir(exist_ok=True)
    url = base + "/plugins/bloodbowl/view"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for label, w, h in (("panel", 760, 900), ("wide", 1400, 900)):
            page = browser.new_page(viewport={"width": w, "height": h})
            errors: list[str] = []

            def _pageerror(e, sink=errors):
                sink.append(str(e))

            page.on("pageerror", _pageerror)

            # The DS kit is a HOST resource; the harness does not serve it, and the
            # view is built to fall through to a shim without it. Those 404s are the
            # fallback working, not a defect — anything else is real.
            def _console(m, sink=errors):
                if m.type == "error" and "_ds/plugin-kit" not in m.text and "404" not in m.text:
                    sink.append(m.text)

            page.on("console", _console)

            def _reqfail(r, sink=errors):
                if "_ds/plugin-kit" not in r.url:
                    sink.append(f"request failed: {r.url}")

            page.on("requestfailed", _reqfail)
            page.goto(url, wait_until="networkidle")
            page.wait_for_selector(".cell", timeout=10000)
            # The DS kit is a host resource, so without this the harness renders
            # unthemed white — structurally right but nothing like what the operator
            # sees. Inject the same tokens the console would supply.
            theme = ROOT / "harness_theme.css"
            if theme.exists():
                page.add_style_tag(content=theme.read_text())
            page.wait_for_timeout(600)

            shot = SHOTS / f"board-{label}.png"
            page.screenshot(path=str(shot), full_page=True)
            print(f"  shot: {shot.relative_to(ROOT)}  ({w}x{h})")

            if do_checks:
                print(f"  -- {label} ({w}x{h}) --")
                check(f"{label}: no page errors", not errors, "; ".join(errors[:2]))
                check(
                    f"{label}: 26x15 = 390 squares",
                    page.locator(".cell").count() == 390,
                    f"{page.locator('.cell').count()}",
                )
                check(
                    f"{label}: both rulers render",
                    page.locator("#rtop div").count() == 26 and page.locator("#rleft div").count() == 15,
                )
                # The defect that made every player unreadable.
                cell_px = page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--cell')")
                pc = page.locator(".pc").first
                fs = 0
                if pc.count():
                    fs = page.evaluate("el => parseFloat(getComputedStyle(el).fontSize)", pc.element_handle())
                check(f"{label}: badge font >= 9px", fs >= 9, f"{fs:.1f}px (cell {cell_px.strip()})")
                # Hover card actually appears and is on-screen.
                if pc.count():
                    pc.hover()
                    page.wait_for_timeout(250)
                    card = page.locator("#card")
                    visible = card.is_visible()
                    box = card.bounding_box() if visible else None
                    on_screen = bool(
                        box
                        and box["x"] >= 0
                        and box["y"] >= 0
                        and box["x"] + box["width"] <= w + 1
                        and box["y"] + box["height"] <= h + 1
                    )
                    check(f"{label}: hover card shows", visible)
                    check(f"{label}: hover card stays on screen", on_screen, str(box))
                    check(f"{label}: hover card has a statline", "MA" in (card.inner_text() if visible else ""))
                    page.mouse.move(2, 2)
            page.close()

        if do_checks and not live:
            # Interaction, on a fresh page so the seeded board is untouched.
            page = browser.new_page(viewport={"width": 1200, "height": 900})
            page.goto(url, wait_until="networkidle")
            page.wait_for_selector(".cell", timeout=10000)
            page.wait_for_timeout(600)
            print("  -- interaction --")

            before = page.locator(".pc").count()
            page.locator(".pi").first.click()  # arm a position
            check("click-to-arm marks the palette item", page.locator(".pi.armed").count() == 1)
            page.locator('.cell[data-x="10"][data-y="6"]').click()  # place it
            page.wait_for_timeout(400)
            check(
                "click-to-place adds a player",
                page.locator(".pc").count() == before + 1,
                f"{before} -> {page.locator('.pc').count()}",
            )

            page.locator("#undo").click()
            page.wait_for_timeout(400)
            check("undo removes it again", page.locator(".pc").count() == before, f"-> {page.locator('.pc').count()}")

            # The coordinate readout — the fix for "count the squares yourself".
            page.locator('.cell[data-x="7"][data-y="13"]').hover()
            page.wait_for_timeout(200)
            check(
                "coordinate readout tracks the cursor",
                page.locator("#coord").inner_text().strip() == "(7,13)",
                page.locator("#coord").inner_text(),
            )
            check(
                "hover lights the rulers",
                page.locator("#rtop div.hot").count() == 1 and page.locator("#rleft div.hot").count() == 1,
            )

            # Opening the view must not have written to the board.
            state = page.evaluate("""async () => (await fetch("/api/plugins/bloodbowl/state")).json()""")
            check("teams survived a page load", bool(state["home_team"]), f"home={state['home_team']!r}")
            page.close()
        browser.close()

    failed = [c for c in CHECKS if c[1] == "FAIL"]
    if do_checks:
        print(f"\n{len(CHECKS) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="drive the Blood Bowl pitch view in a browser")
    ap.add_argument("--check", action="store_true", help="assert, don't just screenshot")
    ap.add_argument(
        "--live",
        metavar="URL",
        nargs="?",
        const="http://127.0.0.1:7878",
        help="drive the running agent instead of a throwaway (read-only)",
    )
    args = ap.parse_args()

    if args.live:
        print(f"driving LIVE {args.live} — read-only, no seeding")
        return drive(args.live, do_checks=args.check, live=True)

    base, server = serve()
    print(f"harness serving {base}")
    seed(base)
    try:
        return drive(base, do_checks=args.check, live=False)
    finally:
        server.should_exit = True


if __name__ == "__main__":
    sys.exit(main())
