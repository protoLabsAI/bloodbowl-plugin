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
    # Mount the way the HOST mounts: keyed on prefix, skipping any already
    # mounted. Blindly including every router made the harness more forgiving than
    # production, which is the one thing a harness must never be — it hid a second
    # router on a shared prefix whose routes the real host was discarding.
    mounted: set[str] = set()
    for router, prefix in reg.routers:
        if prefix in mounted:
            print(f"  !! host would DROP a second router on {prefix}", flush=True)
            continue
        mounted.add(prefix)
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

            # Presets, which only exist to be recalled by name — so the check is
            # that picking one actually repopulates the board.
            picked = page.locator("#presetPick option").count()
            check("the preset picker is populated", picked >= 4, f"{picked} options")
            page.select_option("#presetPick", "Standard defence")
            page.locator("#presetLoad").click()
            page.wait_for_timeout(700)
            loaded = page.locator(".pc").count()
            check("loading a preset fills the board", loaded >= 8, f"{loaded} players")
            legal = page.evaluate("""async () => {
              const s = await (await fetch("/api/plugins/bloodbowl/state")).json();
              return s.players.filter(p => p.side === "home" && p.on_los).length;
            }""")
            check("the loaded setup puts three on the Line of Scrimmage", legal >= 3, f"{legal} on the LoS")
            check(
                "loading a shipped preset keeps the chosen teams",
                page.locator("#homeTeam").input_value() == "Orc",
                page.locator("#homeTeam").input_value(),
            )
            # Put the seeded board back — everything after this expects it.
            seed(base)
            page.reload(wait_until="networkidle")
            page.wait_for_selector(".cell", timeout=10000)
            page.wait_for_timeout(500)

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

            _play(browser, url, w=1400, h=950)
        browser.close()

    failed = [c for c in CHECKS if c[1] == "FAIL"]
    if do_checks:
        print(f"\n{len(CHECKS) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


def _play(browser, url: str, w: int, h: int) -> None:
    """Play a turn in a real browser.

    Server-side tests can prove the engine refuses an illegal move. They cannot
    prove that a coach can SEE which squares cost a Dodge before committing to
    one, and that is the entire reason play mode exists. So: start a match, pick a
    player, and check the board actually says what the engine said.
    """
    page = browser.new_page(viewport={"width": w, "height": h})
    # Record what the page actually asks the server for. When a click does not do
    # what it should, "was the request even made" is the first question, and
    # guessing at it from the rendered result wastes a lot of time.
    calls: list[str] = []
    acted: list[dict] = []

    def _watch(r):
        calls.append(f"{r.status} {r.url.split('/api/plugins/bloodbowl')[-1]}")
        # An action that is REFUSED still answers 200 with ok:false, so the status
        # code alone cannot tell a played move from a rejected one. Keep the body.
        if "/game/act" in r.url:
            with contextlib.suppress(Exception):
                acted.append(r.json())

    page.on("response", _watch)
    problems: list[str] = []
    page.on("pageerror", lambda e: problems.append(str(e)))
    page.goto(url, wait_until="networkidle")
    page.wait_for_selector(".cell", timeout=10000)
    theme = ROOT / "harness_theme.css"
    if theme.exists():
        page.add_style_tag(content=theme.read_text())
    page.wait_for_timeout(400)
    print("  -- play mode --")

    page.locator("#modePlay").click()
    page.wait_for_timeout(300)
    check("play mode hides the setup palette", not page.locator("#trash").is_visible())

    page.locator("#newMatch").click()
    page.wait_for_timeout(800)
    check("a match starts", page.locator(".pc").count() > 0, f"{page.locator('.pc').count()} players")
    clock = page.locator("#clock").inner_text()
    check("the clock renders the half, turn and drive", "H1" in clock and "drive" in clock, clock)
    log0 = page.locator("#log").inner_text()
    check(
        "the match opens with a kick-off, not a ball that appeared",
        "Kick-off Event" in log0 and "deviates" in log0,
        log0[:110].replace("\n", " / "),
    )

    # Pick a home player who is actually Marked. Clicking whichever happens to be
    # first proves nothing about the highlighting: an unmarked player in open
    # field legitimately has eight free squares and no roll to show.
    marked = page.evaluate("""async () => {
      const m = (await (await fetch("/api/plugins/bloodbowl/game")).json()).match;
      const on = m.players.filter(p => p.place === "pitch");
      const foes = on.filter(p => p.side === "away");
      const me = on.find(p => p.side === "home" && foes.some(
        f => Math.abs(f.x - p.x) <= 1 && Math.abs(f.y - p.y) <= 1 && !(f.x === p.x && f.y === p.y)));
      return me ? {x: me.x, y: me.y} : null;
    }""")
    check("the seeded board has a Marked home player to test with", marked is not None)
    if marked is None:
        page.close()
        return
    page.locator(f'.cell[data-x="{marked["x"]}"][data-y="{marked["y"]}"] .pc').click()
    page.wait_for_timeout(600)
    legal = page.locator(".cell.legal").count()
    check("selecting a player highlights its legal squares", legal > 0, f"{legal} squares")
    check(
        "squares needing a roll look different from free ones",
        page.locator(".cell.legal.needsroll").count() > 0,
        "no square was marked as needing a Dodge or Rush",
    )
    check("the odds badge is readable", page.locator(".cell .odds").count() > 0)
    # It is drawn ON TOP of a player badge, and `.pc.away` is filled with
    # --pl-color-fg — which is the colour the tag's text used to be. Every block's
    # "2D" was white on white, present in the DOM and invisible on the board.
    # Counting elements cannot see that; the computed background can.
    odds_bg = page.evaluate(
        "() => { const n = document.querySelector('.cell .odds');"
        " return n ? getComputedStyle(n).backgroundColor : ''; }"
    )
    check(
        "the odds tag has a background of its own, so it reads on a player badge",
        bool(odds_bg) and "rgba(0, 0, 0, 0)" not in odds_bg,
        f"computed background {odds_bg!r}",
    )
    sel = page.locator("#sel").inner_text()
    check("the selected pane names the player", "MA" in sel, sel[:60])

    target = page.locator(".cell.legal").first
    target.click()
    page.wait_for_timeout(900)
    log = page.locator("#log").inner_text()
    check("the move lands in the log", "moves to" in log or "Falls Over" in log, log[:90].replace("\n", " / "))
    check("the log quotes the dice", "rolled" in log or "moves to" in log, log[:90].replace("\n", " / "))

    # Blocking, which is the half a server test cannot judge: a coach has to see
    # BEFORE committing that a block hands the dice to the opponent.
    #
    # Restart from a FIXED seed first. The move above is dice-driven, and a failed
    # Dodge is a turnover — which flips the active side and leaves no home player
    # able to block, so the section failed roughly one run in three and looked
    # like a broken feature rather than a coin toss in the setup.
    page.evaluate("""async () => {
      await fetch("/api/plugins/bloodbowl/game/new", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({seed: 7}),
      });
    }""")
    page.reload(wait_until="networkidle")
    page.wait_for_selector(".cell", timeout=10000)
    if theme.exists():
        page.add_style_tag(content=theme.read_text())
    page.wait_for_timeout(500)

    blocker = page.evaluate("""async () => {
      const m = (await (await fetch("/api/plugins/bloodbowl/game")).json()).match;
      const on = m.players.filter(p => p.place === "pitch" && !p.acted && p.down === "standing");
      const foes = on.filter(p => p.side === "away");
      const me = on.find(p => p.side === "home" && foes.some(
        f => Math.abs(f.x - p.x) <= 1 && Math.abs(f.y - p.y) <= 1));
      return me ? {x: me.x, y: me.y, id: me.id} : null;
    }""")
    check("a blocker is available on the seeded board", blocker is not None)
    if blocker is not None:
        page.locator(f'.cell[data-x="{blocker["x"]}"][data-y="{blocker["y"]}"] .pc').click()
        page.wait_for_timeout(600)
        check("blockable opponents are marked on the board", page.locator(".cell.blockable").count() > 0)
        pane = page.locator("#sel").inner_text()
        check("the pane says how many dice and who chooses", "dice" in pane and "chooses" in pane, pane[:80])
        page.screenshot(path=str(SHOTS / "block.png"), full_page=True)
        # The click handler decides "is this a block target?" by matching the
        # clicked badge's id against the legal-block list. If those two ever drift
        # apart the click silently falls through to selecting the opponent, which
        # looks like nothing happening — so check the correspondence directly.
        wired = page.evaluate(
            """(pid) => Promise.resolve(
                 fetch(`/api/plugins/bloodbowl/game/legal?player=${pid}`).then(r => r.json())
               ).then(l => ({
                 targets: (l.blocks || []).map(b => b.target),
                 badges: [...document.querySelectorAll('.cell.blockable .pc')].map(n => n.dataset.id),
               }))""",
            blocker["id"],
        )
        check(
            "the blockable badges are the ids the engine named",
            bool(wired["badges"]) and set(wired["badges"]) <= set(wired["targets"]),
            f"badges={wired['badges']} targets={wired['targets']}",
        )
        before = page.locator("#log").inner_text()
        acts_before = len([c for c in calls if "/game/act" in c])
        page.locator(".cell.blockable .pc").first.click()
        # Wait for the log to CHANGE rather than sleeping a guessed interval. A
        # block is a POST plus two follow-up fetches, and a fixed wait that is
        # merely usually long enough produces a test that fails on a slow machine
        # and passes on yours — which reads as a broken feature, not a slow one.
        changed = True
        try:
            page.wait_for_function(
                "prev => document.querySelector('#log').innerText !== prev",
                arg=before,
                timeout=8000,
            )
        except Exception:  # noqa: BLE001 — a timeout here IS the failure
            changed = False
        after = page.locator("#log").inner_text()
        acts_after = len([c for c in calls if "/game/act" in c])
        last = acted[-1] if acted else {}
        check(
            "the block was played, not refused",
            acts_after > acts_before and bool(last.get("events") or last.get("log")),
            f"acts {acts_before}->{acts_after}; ok={last.get('ok')} text={str(last.get('text'))[:70]!r}",
        )
        check(
            "throwing the block writes a result to the log",
            changed and after != before,
            after[:80].replace("\n", " / "),
        )
        check(
            "the log names a block die face",
            any(w in after for w in ("Push Back", "POW", "Both Down", "Player Down", "Stumble")),
            after[:110].replace("\n", " / "),
        )

    # The Blitz. Two things a server test cannot judge: whether a coach can SEE
    # that an opponent four squares away is reachable at all, and whether the
    # board says so differently from a Block — because a Blitz spends the team's
    # only one for the turn, and unlike a bad Block it cannot be taken back.
    #
    # Fresh seeded match again, for the same reason as the block section: the
    # block above is dice-driven and its turnover would leave nobody to blitz.
    page.evaluate("""async () => {
      await fetch("/api/plugins/bloodbowl/game/new", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({seed: 7}),
      });
    }""")
    page.reload(wait_until="networkidle")
    page.wait_for_selector(".cell", timeout=10000)
    if theme.exists():
        page.add_style_tag(content=theme.read_text())
    page.wait_for_timeout(500)

    # Ask the ENGINE who can blitz rather than guessing from the board. A player
    # picked by eye might be adjacent to their target, in which case the view
    # offers a plain Block instead and the section would "pass" having tested it.
    runner = page.evaluate("""async () => {
      const m = (await (await fetch("/api/plugins/bloodbowl/game")).json()).match;
      for (const p of m.players.filter(q => q.side === "home" && q.place === "pitch")) {
        const l = await (await fetch(`/api/plugins/bloodbowl/game/legal?player=${p.id}`)).json();
        const far = ((l.blitz || {}).targets || []).filter(
          t => !(l.blocks || []).some(b => b.target === t.target));
        if (far.length) return {x: p.x, y: p.y, id: p.id, targets: far.length, steps: far[0].steps};
      }
      return null;
    }""")
    check("the seeded board has a player who could Blitz someone out of reach", runner is not None)
    if runner is not None:
        page.locator(f'.cell[data-x="{runner["x"]}"][data-y="{runner["y"]}"] .pc').click()
        page.wait_for_timeout(600)
        marks = page.locator(".cell.blitzable").count()
        check("Blitz targets are marked on the board", marks > 0, f"{marks} marked, engine offered {runner['targets']}")
        check(
            "a Blitz target is marked differently from a Block target",
            page.locator(".cell.blitzable:not(.blockable)").count() > 0,
            "every blitz mark also carried a block mark, so the two are indistinguishable",
        )
        pane = page.locator("#sel").inner_text()
        check(
            "the pane offers the Blitz and says it is one per turn", "Blitz" in pane and "per turn" in pane, pane[:100]
        )
        check(
            "the Blitz mark says how far away the target is",
            page.locator(".cell.blitzable .odds").first.inner_text().startswith("B"),
            page.locator(".cell.blitzable .odds").first.inner_text(),
        )
        page.screenshot(path=str(SHOTS / "blitz.png"), full_page=True)

        before = page.locator("#log").inner_text()
        acts_before = len([c for c in calls if "/game/act" in c])
        page.locator(".cell.blitzable:not(.blockable) .pc").first.click()
        with contextlib.suppress(Exception):
            page.wait_for_function(
                "prev => document.querySelector('#log').innerText !== prev", arg=before, timeout=8000
            )
        after = page.locator("#log").inner_text()
        last = acted[-1] if acted else {}
        check(
            "clicking a Blitz target declares it, and is not refused",
            len([c for c in calls if "/game/act" in c]) > acts_before and bool(last.get("ok")),
            f"ok={last.get('ok')} text={str(last.get('text'))[:70]!r}",
        )
        check("the declaration lands in the log", "declares a Blitz" in after, after[:110].replace("\n", " / "))
        pane = page.locator("#sel").inner_text()
        check("the pane now says who is being blitzed", "Blitzing" in pane, pane[:100])
        check(
            "the player stays selected — a declaration rolls nothing and ends nothing",
            page.locator(".pc.sel").count() == 1,
            f"{page.locator('.pc.sel').count()} selected",
        )
        check(
            "and can still move, because the Blitz is a Move Action",
            page.locator(".cell.legal").count() > 0,
            "no legal squares after declaring, so the player cannot walk to the target",
        )

    # The Foul. Three different decisions now share the board — a Block is free, a
    # Blitz spends the team's one per turn, a Foul risks losing the player for the
    # match — so the check that matters is whether a coach can tell them apart.
    # Put somebody on the floor first rather than hoping: engineering the state is
    # the only way this section tests fouling rather than testing the dice.
    floored = page.evaluate("""async () => {
      const post = (p, b) => fetch("/api/plugins/bloodbowl" + p, {
        method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(b),
      }).then(r => r.json());
      await post("/game/new", {seed: 7});
      for (let i = 0; i < 12; i++) {
        const m = (await (await fetch("/api/plugins/bloodbowl/game")).json()).match;
        const down = m.players.find(p => p.place === "pitch" && p.down !== "standing");
        if (down) {
          const near = m.players.find(p => p.place === "pitch" && p.side !== down.side
            && p.down === "standing" && !p.done && p.side === m.clock.active
            && Math.max(Math.abs(p.x - down.x), Math.abs(p.y - down.y)) === 1);
          if (near) return {x: near.x, y: near.y, id: near.id, victim: down.id};
        }
        // Nobody down yet: throw a block, or pass the turn and try again.
        let threw = false;
        for (const p of m.players.filter(q => q.side === m.clock.active && !q.done && q.down === "standing")) {
          const l = await (await fetch(`/api/plugins/bloodbowl/game/legal?player=${p.id}`)).json();
          if ((l.blocks || []).length) {
            await post("/game/act", {action: "block", player: p.id, target: l.blocks[0].target});
            threw = true; break;
          }
        }
        if (!threw) await post("/game/end-turn", {});
      }
      return null;
    }""")
    check("a downed player with a standing opponent beside them could be arranged", floored is not None)
    if floored is not None:
        page.reload(wait_until="networkidle")
        page.wait_for_selector(".cell", timeout=10000)
        if theme.exists():
            page.add_style_tag(content=theme.read_text())
        page.wait_for_timeout(400)
        page.locator(f'.cell[data-x="{floored["x"]}"][data-y="{floored["y"]}"] .pc').click()
        page.wait_for_timeout(600)
        check("Foul targets are marked on the board", page.locator(".cell.foulable").count() > 0)
        check(
            "a Foul target is marked differently from a Block target",
            page.locator(".cell.foulable.blockable").count() == 0,
            "one square carried both marks, so the two decisions are indistinguishable",
        )
        pane = page.locator("#sel").inner_text()
        check(
            "the pane names the risk rather than only the odds",
            "Foul" in pane and "natural double" in pane,
            pane[-120:].replace("\n", " / "),
        )
        page.mouse.move(4, 4)
        page.wait_for_timeout(200)
        page.screenshot(path=str(SHOTS / "foul.png"), full_page=True)

        before = page.locator("#log").inner_text()
        page.locator(".cell.foulable .pc").first.click()
        with contextlib.suppress(Exception):
            page.wait_for_function(
                "prev => document.querySelector('#log').innerText !== prev", arg=before, timeout=8000
            )
        after = page.locator("#log").inner_text()
        last = acted[-1] if acted else {}
        check(
            "clicking a Foul target puts the boot in, and is not refused",
            "Standing" not in str(last.get("text", "")) and "Fouls" in after,
            f"text={str(last.get('text'))[:80]!r}",
        )
        check(
            "and the log says what the referee made of it",
            "referee" in after,
            after[:120].replace("\n", " / "),
        )

    # The ball. A loose ball that renders under a player badge is indistinguishable
    # from no ball at all, which is the failure worth checking here.
    check(
        "a loose ball is drawn on the pitch",
        page.locator(".ball").count() == 1,
        f"{page.locator('.ball').count()} ball elements",
    )
    ballsq = page.evaluate("""async () => {
      const m = (await (await fetch("/api/plugins/bloodbowl/game")).json()).match;
      return m.ball && m.ball.in_play ? {x: m.ball.x, y: m.ball.y} : null;
    }""")
    if ballsq:
        occupied = page.locator(f'.cell[data-x="{ballsq["x"]}"][data-y="{ballsq["y"]}"] .pc').count()
        check("the loose ball is not hidden under a player", occupied == 0, f"{occupied} badge(s) on its square")

    # Re-select a Marked player and get the cursor off the board before shooting.
    # The hover card follows the mouse and will happily sit on top of the very
    # highlighting the shot exists to show.
    again = page.evaluate("""async () => {
      const m = (await (await fetch("/api/plugins/bloodbowl/game")).json()).match;
      const on = m.players.filter(p => p.place === "pitch" && !p.acted);
      const foes = on.filter(p => p.side === "away");
      const me = on.find(p => p.side === "home" && foes.some(
        f => Math.abs(f.x - p.x) <= 1 && Math.abs(f.y - p.y) <= 1 && !(f.x === p.x && f.y === p.y)));
      return me ? {x: me.x, y: me.y} : null;
    }""")
    if again:
        page.locator(f'.cell[data-x="{again["x"]}"][data-y="{again["y"]}"] .pc').click()
        page.wait_for_timeout(500)
    page.mouse.move(4, 4)
    page.wait_for_timeout(250)
    page.screenshot(path=str(SHOTS / "play.png"), full_page=True)
    print(f"  shot: {(SHOTS / 'play.png').relative_to(ROOT)}")
    page.close()


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
