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
    python harness.py --live          # photograph a RUNNING agent's board (read-only)

`--live` needs BLOODBOWL_TOKEN for a token-gated agent: the VIEW is auth-exempt,
but everything it fetches is not, so without one the page renders an empty board.
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

    # CLEAR first. This is called again mid-run to "put the seeded board back",
    # and without it the preset loaded by the interaction checks stays underneath —
    # so every later section ran against sixteen home players, eleven of them
    # statless preset tokens. The screenshots showed it for weeks.
    post("/clear", {})
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


def _live_headers() -> dict:
    """A bearer for a token-gated agent, from BLOODBOWL_TOKEN.

    The VIEW is auth-exempt (the console iframes it with a plain navigation) but
    everything it fetches is not — so against a real agent the page renders an
    empty board and the data 401s. The throwaway server has no auth at all, which
    is why this never came up until `--live` was pointed at a real game.
    """
    tok = os.environ.get("BLOODBOWL_TOKEN", "").strip()
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def drive(base: str, *, do_checks: bool, live: bool) -> int:
    from playwright.sync_api import sync_playwright

    SHOTS.mkdir(exist_ok=True)
    url = base + "/plugins/bloodbowl/view"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        headers = _live_headers()
        if live and not headers:
            print("  !! no BLOODBOWL_TOKEN — a gated agent will render an empty board", flush=True)
        for label, w, h in (("panel", 760, 900), ("wide", 1400, 900)):
            page = browser.new_page(viewport={"width": w, "height": h}, extra_http_headers=headers)
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

            # `--live` DRIVES A REAL AGENT'S BOARD, and `_play` starts a NEW MATCH
            # — it clicks #newMatch. Running it against a live instance would wipe
            # a game in progress. The flag has always said "read-only"; it was only
            # true of the section above until this guard. Found while wanting a
            # screenshot of a match that was actually being played.
            _play(browser, url, w=1400, h=950)
            _drag(browser, url, w=1400, h=950)
            _choices(browser, url, w=1400, h=950)
            _versus(browser, url, w=1400, h=950)

        # …and the live path, which only LOOKS. It sits out here rather than in an
        # `else` because the block above is already gated on `not live` — an else
        # in there is unreachable, which is how the first attempt at this silently
        # did nothing at all.
        if live:
            _watch(browser, url, w=1400, h=950)
        browser.close()

    failed = [c for c in CHECKS if c[1] == "FAIL"]
    if do_checks:
        print(f"\n{len(CHECKS) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


def _get_past_any_question(page) -> None:
    """Four Kick-off Events in eleven stop and ask the Coach something, and while
    one is open the engine refuses every other action. Any section that is about
    something ELSE has to answer it first — otherwise it fails on the seeds that
    happen to roll a 4, 5, 9 or 10 and reads as a broken board rather than a coin
    toss in the setup. Declining is always legal."""
    if page.locator("#choice").is_visible():
        print("  (a Kick-off Event asked something — declining to get on with it)")
        page.locator("#choiceDecline").click()
        page.wait_for_timeout(600)


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

    _get_past_any_question(page)
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

    # THE ICONS. A server test proves the catalogue is served and the files are
    # on disk; only a browser says whether they arrived on the board. Both halves
    # matter and fail differently — a chip with no `sprited` class never asked for
    # one, and a chip that asked for a URL the server does not serve is a broken
    # image, which renders as nothing at all and looks exactly like the tile it
    # replaced. So the URL is fetched back.
    art = page.evaluate(
        """async () => {
          const chips = [...document.querySelectorAll('.pc')];
          const sprited = chips.filter((c) => c.classList.contains('sprited'));
          const urls = [...new Set(sprited.map((c) => {
            const m = /url\\("?([^")]+)"?\\)/.exec(getComputedStyle(c).backgroundImage || '');
            return m ? m[1] : null;
          }).filter(Boolean))];
          const codes = await Promise.all(urls.slice(0, 4).map(async (u) => {
            try { return (await fetch(u)).status; } catch { return 0; }
          }));
          const one = sprited[0] && getComputedStyle(sprited[0]);
          return {
            chips: chips.length,
            sprited: sprited.length,
            urls: urls.length,
            codes,
            size: one ? one.backgroundSize : null,
            badge: one ? one.fontSize : null,
          };
        }"""
    )
    check(
        "players are drawn as their icons, not coloured tiles",
        art["chips"] > 0 and art["sprited"] == art["chips"],
        f"{art['sprited']} of {art['chips']} chips sprited",
    )
    check(
        "and every icon the board asked for actually serves",
        bool(art["codes"]) and all(c == 200 for c in art["codes"]),
        f"{art['urls']} distinct sheets, statuses {art['codes']}",
    )
    check(
        "the sheet is sliced to a frame rather than squashed whole into the square",
        bool(art["size"]) and "%" in art["size"] and art["size"] != "100% 100%",
        f"background-size {art['size']}",
    )
    # The badge sits ON the figure now, so its own legibility floor still applies.
    check(
        "the badge stays readable on top of the art",
        bool(art["badge"]) and float(str(art["badge"]).replace("px", "")) >= 9,
        f"badge {art['badge']}",
    )
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
    _get_past_any_question(page)

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

        # And SHOWS it. A server test proves the payload carries the faces; only
        # a browser can say whether they arrived on screen as something a coach
        # can tell apart. Both halves are checked because they fail differently:
        # a die that renders with no glyph is an empty box, and a die whose ink
        # matches its own background is present in the DOM and invisible on the
        # panel — which is exactly how the odds tag was white-on-white for weeks.
        faces = page.evaluate("""() => {
          const dice = [...document.querySelectorAll('#log .die')];
          const blocks = [...document.querySelectorAll('#log .die-block')];
          const paint = (el) => {
            const cs = getComputedStyle(el);
            const box = el.getBoundingClientRect();
            return {ink: cs.color, bg: cs.backgroundColor, w: box.width, h: box.height};
          };
          return {
            dice: dice.length,
            blocks: blocks.length,
            drawn: blocks.filter((b) => b.querySelector('svg')).length,
            paints: dice.slice(0, 6).map(paint),
          };
        }""")
        check("the log DRAWS the dice it was sent", faces["dice"] > 0, f"{faces['dice']} die faces on screen")
        check(
            "a block die is drawn as a face, not an empty box",
            faces["blocks"] > 0 and faces["drawn"] == faces["blocks"],
            f"{faces['drawn']} of {faces['blocks']} block dice carry a glyph",
        )
        check(
            "the faces are visible against their own background",
            all(f["ink"] != f["bg"] and f["w"] >= 12 and f["h"] >= 12 for f in faces["paints"]),
            str(faces["paints"][:2]),
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
    _get_past_any_question(page)

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
      const fresh = await post("/game/new", {seed: 7});
      // The kick-off may have asked the Coach something, and nothing else can
      // happen until it is answered — including everything this loop tries.
      if (fresh.match && fresh.match.pending && fresh.match.pending.choice) {
        await post("/game/choose", {decline: true});
      }
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


def _drag(browser, url: str, w: int, h: int) -> None:
    """Drag a player to a square, with a real pointer.

    This is the section that could not be written against HTML5 drag-and-drop,
    and the reason `drag.js` uses Pointer Events instead: Playwright drives
    `mouse.move`/`down`/`up` natively. Everything here is the part a grep test
    cannot reach — whether the gesture LANDS.

    Three things it is really checking, all of which have shipped broken before
    on the setup board: that a short press is still a CLICK; that the poller does
    not tear the node out mid-drag; and that the square under the pointer is the
    square the move goes to.
    """
    page = browser.new_page(viewport={"width": w, "height": h})
    problems: list[str] = []
    page.on("pageerror", lambda e: problems.append(str(e)))
    page.goto(url, wait_until="networkidle")
    page.wait_for_selector(".cell", timeout=10000)
    theme = ROOT / "harness_theme.css"
    if theme.exists():
        page.add_style_tag(content=theme.read_text())
    print("  -- drag to move --")

    page.locator("#modePlay").click()
    page.wait_for_timeout(300)
    # A fixed seed, for the same reason the block section uses one: a dice-driven
    # move can end the turn and leave nobody to drag.
    page.evaluate("""async () => {
      await fetch("/api/plugins/bloodbowl/game/new", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({seed: 7}),
      });
    }""")
    page.reload(wait_until="networkidle")
    page.wait_for_selector(".cell", timeout=10000)
    # RE-INJECT: a reload throws the added style tag away, and without it the shot
    # is unthemed — every token falls back and the whole board photographs grey.
    # Which looks exactly like "the legal squares are not being highlighted".
    if theme.exists():
        page.add_style_tag(content=theme.read_text())
    page.wait_for_timeout(400)
    _get_past_any_question(page)

    # A MARKED player, for the same reason the play section picks one: somebody in
    # open field has eight free squares and no roll to show, so the odds badges
    # this section checks the survival of would legitimately not exist and the
    # check would pass by being vacuous.
    who = page.evaluate("""async () => {
      const m = (await (await fetch("/api/plugins/bloodbowl/game")).json()).match;
      const on = m.players.filter(p => p.place === "pitch" && p.down === "standing" && !p.acted);
      const mine = on.filter(p => p.side === m.clock.active);
      const foes = on.filter(p => p.side !== m.clock.active);
      const marked = mine.find(p => foes.some(
        f => Math.abs(f.x - p.x) <= 1 && Math.abs(f.y - p.y) <= 1));
      const me = marked || mine[0];
      return me ? {x: me.x, y: me.y, id: me.id, marked: !!marked} : null;
    }""")
    check("a draggable player is on the seeded board", who is not None)
    if who is None:
        page.close()
        return

    src = page.locator(f'.cell[data-x="{who["x"]}"][data-y="{who["y"]}"] .pc')

    # A PRESS THAT DOES NOT TRAVEL IS STILL A CLICK. If the drag layer swallowed
    # it, select-then-click — the accessible path, and what the rest of this
    # harness drives — would be dead and nothing would say so.
    box = src.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.up()
    page.wait_for_timeout(600)
    check(
        "a press that does not travel is still a click, so selection survives",
        page.locator(".cell.legal").count() > 0,
        "clicking a player no longer selects it — the drag layer ate the click",
    )

    # Now a real drag onto a legal square.
    dest = page.evaluate("""() => {
      const c = document.querySelector('.cell.legal');
      return c ? {x: +c.dataset.x, y: +c.dataset.y} : null;
    }""")
    check("the selected player has somewhere to be dragged", dest is not None)
    if dest is None:
        page.close()
        return
    tgt = page.locator(f'.cell[data-x="{dest["x"]}"][data-y="{dest["y"]}"]')
    tb = tgt.bounding_box()
    box = src.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    # In steps, because the drag only starts after the pointer clears the slop
    # threshold — a single jump to the destination is not a drag, it is a
    # teleport, and it would pass while a real gesture failed.
    page.mouse.move(tb["x"] + tb["width"] / 2, tb["y"] + tb["height"] / 2, steps=12)
    page.wait_for_timeout(150)
    check(
        "the square under the pointer is marked as the drop target",
        page.locator(".cell.droptarget").count() == 1,
        f"{page.locator('.cell.droptarget').count()} squares marked",
    )
    check(
        "the dragged player is shown following the pointer",
        page.locator(".pc.follower").count() == 1 and page.locator(".pc.ghost").count() == 1,
        "no follower or no ghost — the original should stay put, dimmed",
    )
    # THE MARKS MUST SURVIVE THE DRAG. `clearMarks` strips every odds badge on the
    # board whatever classes it is passed, so clearing the drop target through it
    # wiped the Dodge, Rush and dice tags — the marks a coach is dragging BY —
    # once per pointer move. The green stayed and only the numbers vanished, which
    # is exactly the kind of thing a passing element count does not notice.
    check(
        "the legal squares survive being dragged over",
        page.locator(".cell.legal").count() > 0,
        "the drag cleared the move list it is being aimed with",
    )
    if who["marked"]:
        check(
            "the odds badges survive being dragged over",
            page.locator(".cell .odds").count() > 0,
            "clearMarks removed every badge on the board mid-drag",
        )
    else:
        print("  (no Marked player on this board — skipping the odds-badge check rather than passing it vacuously)")
    # The drop target must be VISIBLE, not merely classed. Every mark on this
    # board that shipped broken shipped present-in-the-DOM and invisible on the
    # pitch — the odds tag was white on white for exactly this reason. An element
    # count cannot see that; the computed outline can.
    outline = page.evaluate(
        "() => { const n = document.querySelector('.cell.droptarget');"
        " if (!n) return ''; const s = getComputedStyle(n);"
        " return `${s.outlineStyle} ${s.outlineWidth} ${s.outlineColor}`; }"
    )
    check(
        "the drop target is actually drawn, not just classed",
        "none" not in outline and "0px" not in outline and bool(outline.strip()),
        f"computed outline {outline!r}",
    )
    # …and not drawn UNDERNEATH the follower. A mark that is present, styled and
    # covered by the very thing being dragged is invisible in the way that matters,
    # and no element count or computed style can tell — only the geometry can.
    fits = page.evaluate(
        "() => { const f = document.querySelector('.pc.follower');"
        " const c = document.querySelector('.cell.droptarget'); if (!f || !c) return null;"
        " const a = f.getBoundingClientRect(), b = c.getBoundingClientRect();"
        " return {follower: Math.round(a.width), cell: Math.round(b.width)}; }"
    )
    check(
        "the follower does not blot out the square it is aiming at",
        fits is not None and fits["follower"] < fits["cell"] * 0.85,
        f"follower {fits and fits['follower']}px inside a {fits and fits['cell']}px square",
    )
    page.screenshot(path=str(SHOTS / "drag.png"), full_page=True)
    page.mouse.up()
    page.wait_for_timeout(900)

    landed = page.evaluate(
        """async (want) => {
      const m = (await (await fetch("/api/plugins/bloodbowl/game")).json()).match;
      const p = m.players.find(q => q.id === want.id);
      return p ? {x: p.x, y: p.y, down: p.down} : null;
    }""",
        {"id": who["id"]},
    )
    # A failed Dodge leaves them on the floor where they started, which is a legal
    # outcome of the same gesture — so the check is "moved, or fell trying".
    arrived = landed is not None and landed["x"] == dest["x"] and landed["y"] == dest["y"]
    fell = landed is not None and landed["down"] != "standing"
    check(
        "the player is on the square it was dropped on",
        arrived or fell,
        f"dropped on {dest}, ended at {landed}",
    )
    check("the follower is cleaned up after the drop", page.locator(".pc.follower").count() == 0)
    check("no ghost is left behind", page.locator(".pc.ghost").count() == 0)

    # A RUN OF SEVERAL SQUARES. The whole point of dragging rather than clicking:
    # one gesture, a step at a time, halting the moment a step does not land.
    #
    # FROM A FRESH BOARD, for the reason the block section restarts too: the drag
    # above has already spent a player and may have ended the turn under everyone,
    # so picking "the first unmarked player" afterwards lands on somebody with no
    # movement left, whose first step is a Rush, which fails about half the time.
    # That failure is the engine being right and read as the run being broken.
    page.evaluate("""async () => {
      await fetch("/api/plugins/bloodbowl/game/new", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({seed: 7}),
      });
    }""")
    page.reload(wait_until="networkidle")
    page.wait_for_selector(".cell", timeout=10000)
    if theme.exists():
        page.add_style_tag(content=theme.read_text())
    page.wait_for_timeout(400)
    _get_past_any_question(page)

    runner = page.evaluate("""async () => {
      const m = (await (await fetch("/api/plugins/bloodbowl/game")).json()).match;
      const on = m.players.filter(p => p.place === "pitch" && p.down === "standing" && !p.acted);
      const foes = on.filter(p => p.side !== m.clock.active);
      // Somebody in the OPEN, so the run is not a chain of Dodges that ends on
      // the first bad die and makes this section a coin toss.
      const me = on.find(p => p.side === m.clock.active && !foes.some(
        f => Math.abs(f.x - p.x) <= 1 && Math.abs(f.y - p.y) <= 1));
      return me ? {x: me.x, y: me.y, id: me.id} : null;
    }""")
    check("an unmarked player is available for a run", runner is not None)
    if runner is not None:
        page.locator(f'.cell[data-x="{runner["x"]}"][data-y="{runner["y"]}"] .pc').click()
        page.wait_for_timeout(500)
        # Straight down the pitch, three squares, staying on the board.
        far = {"x": runner["x"], "y": runner["y"] + 3 if runner["y"] + 3 <= 26 else runner["y"] - 3}
        src2 = page.locator(f'.cell[data-x="{runner["x"]}"][data-y="{runner["y"]}"] .pc')
        b0 = src2.bounding_box()
        page.mouse.move(b0["x"] + b0["width"] / 2, b0["y"] + b0["height"] / 2)
        page.mouse.down()
        # Step through each intervening square, as a hand would.
        step = 1 if far["y"] > runner["y"] else -1
        for k in range(1, 4):
            cell = page.locator(f'.cell[data-x="{runner["x"]}"][data-y="{runner["y"] + step * k}"]')
            bb = cell.bounding_box()
            page.mouse.move(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2, steps=6)
            page.wait_for_timeout(60)
        trail = page.locator(".cell.path").count()
        check("the trail shows every square of the run", trail == 3, f"{trail} squares marked, wanted 3")
        check(
            "each square of the trail is numbered",
            page.locator(".cell .step").count() == 3,
            "a coach cannot tell the order of a trail without it",
        )
        page.screenshot(path=str(SHOTS / "drag-path.png"), full_page=True)
        page.mouse.up()
        page.wait_for_timeout(2200)
        ended = page.evaluate(
            """async (id) => {
          const m = (await (await fetch("/api/plugins/bloodbowl/game")).json()).match;
          const p = m.players.find(q => q.id === id);
          return p ? {x: p.x, y: p.y, down: p.down, used: p.ma_used} : null;
        }""",
            runner["id"],
        )
        moved = ended is not None and (ended["x"] != runner["x"] or ended["y"] != runner["y"])
        check(
            "the whole run is walked, not just the first square",
            moved and (ended["used"] or 0) >= 2,
            f"started {runner['x']},{runner['y']} ended at {ended} — one gesture should spend several squares",
        )
        check("the trail is cleaned up after the run", page.locator(".cell.path").count() == 0)

    # DROP ON AN OPPONENT. A fresh board again, and this time a blocker.
    page.evaluate("""async () => {
      await fetch("/api/plugins/bloodbowl/game/new", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({seed: 7}),
      });
    }""")
    page.reload(wait_until="networkidle")
    page.wait_for_selector(".cell", timeout=10000)
    if theme.exists():
        page.add_style_tag(content=theme.read_text())
    page.wait_for_timeout(400)
    _get_past_any_question(page)

    duel = page.evaluate("""async () => {
      const m = (await (await fetch("/api/plugins/bloodbowl/game")).json()).match;
      const on = m.players.filter(p => p.place === "pitch" && p.down === "standing" && !p.acted);
      const foes = on.filter(p => p.side !== m.clock.active);
      for (const me of on.filter(p => p.side === m.clock.active)) {
        const f = foes.find(q => Math.abs(q.x - me.x) <= 1 && Math.abs(q.y - me.y) <= 1);
        if (f) return {me: {x: me.x, y: me.y, id: me.id}, foe: {x: f.x, y: f.y, id: f.id}};
      }
      return null;
    }""")
    check("an adjacent pair is available to test a dropped block", duel is not None)
    if duel is not None:
        before = page.locator("#log").inner_text()
        src3 = page.locator(f'.cell[data-x="{duel["me"]["x"]}"][data-y="{duel["me"]["y"]}"] .pc')
        b1 = src3.bounding_box()
        tgt3 = page.locator(f'.cell[data-x="{duel["foe"]["x"]}"][data-y="{duel["foe"]["y"]}"]')
        b2 = tgt3.bounding_box()
        page.mouse.move(b1["x"] + b1["width"] / 2, b1["y"] + b1["height"] / 2)
        page.mouse.down()
        page.mouse.move(b2["x"] + b2["width"] / 2, b2["y"] + b2["height"] / 2, steps=10)
        page.wait_for_timeout(150)
        page.mouse.up()
        page.wait_for_timeout(1200)
        after = page.locator("#log").inner_text()
        check(
            "dropping onto an adjacent opponent throws a Block",
            ("Blocks" in after or "Blitzes" in after) and after != before,
            after[:110].replace("\n", " / "),
        )

    check("no page errors", not problems, "; ".join(problems[:2]))
    print(f"  shot: {(SHOTS / 'drag.png').relative_to(ROOT)}")
    page.close()


def _choices(browser, url: str, w: int, h: int) -> None:
    """The Kick-off Events that stop and ask the Coach a question.

    Three results in eleven do, and while one is pending the engine refuses every
    other action. So a board that does not surface the question is not merely
    missing a feature — it is a board where clicking has stopped working, with no
    visible reason. That is exactly the failure a server-side 200 cannot see.
    """
    page = browser.new_page(viewport={"width": w, "height": h})
    problems: list[str] = []
    page.on("pageerror", lambda e: problems.append(str(e)))
    page.goto(url, wait_until="networkidle")
    page.wait_for_selector(".cell", timeout=10000)
    theme = ROOT / "harness_theme.css"
    if theme.exists():
        page.add_style_tag(content=theme.read_text())
    page.locator("#modePlay").click()
    page.wait_for_timeout(300)
    print("  -- kick-off choices --")

    # Hunt for a seed per KIND, rather than hoping. Which seed asks what depends
    # on the board, so hard-coding one would rot the first time the seeded
    # formation changed — and taking only the first match would leave three of the
    # four kinds undriven, which is how the interesting one goes untested.
    catalogue = page.evaluate("""async () => {
      const seen = {};
      for (let seed = 1; seed < 220; seed++) {
        const r = await fetch("/api/plugins/bloodbowl/game/new", {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({seed}),
        });
        const m = (await r.json()).match;
        const q = m.pending && m.pending.choice;
        if (q && !seen[q]) seen[q] = {seed, ...m.pending};
      }
      return seen;
    }""")
    check(
        "the seeds turn up Kick-off Events that ask the Coach something",
        bool(catalogue),
        ", ".join(f"{k}@{v['seed']}" for k, v in sorted(catalogue.items())) or "none in 1..219",
    )
    for kind, found in sorted(catalogue.items()):
        print(f"  -- {kind} (seed {found['seed']}) --")
        page.evaluate(
            """async (seed) => { await fetch("/api/plugins/bloodbowl/game/new", {
                 method: "POST", headers: {"Content-Type": "application/json"},
                 body: JSON.stringify({seed}),
               }); }""",
            found["seed"],
        )
        page.reload(wait_until="networkidle")
        page.wait_for_selector(".cell", timeout=10000)
        if theme.exists():
            page.add_style_tag(content=theme.read_text())
        page.wait_for_timeout(600)
        _one_choice(page, kind, found, problems)

    print(f"  shot: {(SHOTS / 'choice.png').relative_to(ROOT)}")
    page.close()


def _one_choice(page, kind: str, found: dict, problems: list) -> None:
    """Drive one pending question from the board, all the way to answered."""
    check(f"{kind}: the question is on the board, not buried in the log", page.locator("#choice").is_visible())
    asked = page.locator("#choiceText").inner_text()
    check(f"{kind}: the bar states the rule, not just the event's name", len(asked) > 30, asked[:90])
    marked = page.eval_on_selector_all(".cell.choosable .pc", "ns => ns.map(n => n.dataset.id)")
    check(
        f"{kind}: the marks are exactly the players the engine named",
        set(marked) == set(found.get("eligible") or []),
        f"board={marked} engine={found.get('eligible')}",
    )
    page.screenshot(path=str(SHOTS / ("choice.png" if kind != "charge" else "charge-pick.png")), full_page=True)

    # A click on the board while a question is pending must not fire an action —
    # the engine would refuse it, and the coach would see an error they did not
    # cause. Clicking one of their OWN players is the natural first thing to try.
    if not marked:
        check(f"{kind}: at least one player was eligible to click", False, "nobody is Open")
        return
    page.locator(".cell.choosable .pc").first.click()
    page.wait_for_timeout(400)
    check(f"{kind}: clicking a player while a question is open does not throw", not problems, "; ".join(problems[:2]))

    if kind == "charge":
        # Charge stages PLAYERS and no squares, so the second click never comes:
        # the selection is complete the moment they are named.
        check(f"{kind}: the selection is marked as chosen, not left waiting", page.locator(".cell.chosen").count() == 1)
        page.locator("#choiceConfirm").click()
        page.wait_for_timeout(700)
        state = page.evaluate("""async () => (await (await fetch("/api/plugins/bloodbowl/game")).json()).match""")
        check(f"{kind}: sending them in starts the Charge", bool(state.get("charge")), str(state.get("charge"))[:80])
        # The ball is still up there for the whole Charge, and drawn solid it
        # reads as landed on a square it has not reached.
        check(
            f"{kind}: the ball is drawn as still in the air",
            page.locator(".ball.air").count() == 1,
            f"{page.locator('.ball').count()} ball(s), {page.locator('.ball.air').count()} airborne",
        )
        check(f"{kind}: the bar stays up to report it", page.locator("#choice").is_visible())
        check(
            f"{kind}: and offers the one thing the board cannot say — ending it",
            "End the Charge" in page.locator("#choiceDecline").inner_text(),
            page.locator("#choiceDecline").inner_text(),
        )
        page.screenshot(path=str(SHOTS / "charge.png"), full_page=True)
        page.locator("#choiceDecline").click()
        page.wait_for_timeout(800)
        after = page.evaluate("""async () => (await (await fetch("/api/plugins/bloodbowl/game")).json()).match""")
        check(f"{kind}: ending it hands the Drive back", not after.get("charge"), str(after.get("charge")))
        check(
            f"{kind}: and brings the ball down", bool((after.get("ball") or {}).get("in_play")), str(after.get("ball"))
        )

    elif kind == "high_kick":
        answered = page.evaluate("""async () => (await (await fetch("/api/plugins/bloodbowl/game")).json()).match""")
        check(
            f"{kind}: picking a player answers it outright", not answered.get("pending"), str(answered.get("pending"))
        )

    else:
        check(
            f"{kind}: picking a player is visibly staged, not silently swallowed",
            page.locator(".cell.picking").count() == 1,
            f"{page.locator('.cell.picking').count()} marked; bar says {page.locator('#choicePicked').inner_text()!r}",
        )
        # Stage a destination, then send. The staged square must be visibly
        # different from a merely-eligible one, or the coach cannot tell what
        # they have already decided.
        box = page.locator(".cell.choosable").first.bounding_box()
        page.mouse.click(box["x"] + box["width"] / 2, box["y"] - box["height"] / 2)
        page.wait_for_timeout(300)
        check(f"{kind}: the staged square is marked distinctly", page.locator(".cell.chosen").count() > 0)
        page.locator("#choiceConfirm").click()
        page.wait_for_timeout(700)
        state = page.evaluate("""async () => (await (await fetch("/api/plugins/bloodbowl/game")).json()).match""")
        check(f"{kind}: confirming answers the question", not state.get("pending"), str(state.get("pending"))[:80])

    check(f"{kind}: the bar goes away once it is answered", not page.locator("#choice").is_visible())
    check(f"{kind}: no page errors while answering", not problems, "; ".join(problems[:2]))


def _watch(browser, url: str, w: int, h: int) -> None:
    """Photograph a LIVE board without touching it.

    Everything else in this file drives the page. This only looks: no clicks, no
    posts, no new match. It is what `--live` should have been doing all along.
    """
    page = browser.new_page(viewport={"width": w, "height": h}, extra_http_headers=_live_headers())
    page.goto(url, wait_until="networkidle")
    page.wait_for_selector(".cell", timeout=20000)
    theme = ROOT / "harness_theme.css"
    if theme.exists():
        page.add_style_tag(content=theme.read_text())
    page.locator("#modePlay").click()  # a view switch, not a board change
    page.wait_for_timeout(1200)
    print("  -- live board --")
    state = page.evaluate("""async () => (await (await fetch("/api/plugins/bloodbowl/game")).json()).match""")
    if not state:
        check("there is a match to look at", False, "no match in progress")
        page.close()
        return
    c = state.get("clock") or {}
    check(
        "a live match is on the board",
        bool(state.get("players")),
        f"half {c.get('half')} turn {c.get('turn')} · {c.get('active')} to act · "
        f"{state.get('score')} · controllers {state.get('controllers')}",
    )
    page.mouse.move(4, 4)
    page.wait_for_timeout(200)
    page.screenshot(path=str(SHOTS / "live.png"), full_page=True)
    print(f"  shot: {(SHOTS / 'live.png').relative_to(ROOT)}")
    page.close()


def _versus(browser, url: str, w: int, h: int) -> None:
    """A head-to-head, from the board's side.

    The failure this guards is specific and quiet: in a head-to-head the board may
    only move ONE team, and a board that silently refuses half your clicks reads as
    broken rather than as a rule. So the check is not "does the engine refuse" — the
    suite covers that — it is "does the page SAY SO".
    """
    page = browser.new_page(viewport={"width": w, "height": h})
    problems: list[str] = []
    page.on("pageerror", lambda e: problems.append(str(e)))
    page.goto(url, wait_until="networkidle")
    page.wait_for_selector(".cell", timeout=10000)
    theme = ROOT / "harness_theme.css"
    if theme.exists():
        page.add_style_tag(content=theme.read_text())
    page.locator("#modePlay").click()
    page.wait_for_timeout(300)
    print("  -- head to head --")

    check("the board offers a game against the agent", page.locator("#versus").is_visible())
    page.locator("#versus").check()
    page.select_option("#mySide", "home")
    page.locator("#newMatch").click()
    page.wait_for_timeout(900)
    _get_past_any_question(page)

    state = page.evaluate("""async () => (await (await fetch("/api/plugins/bloodbowl/game")).json()).match""")
    check(
        "starting one claims both sides",
        (state.get("controllers") or {}) == {"home": "human", "away": "agent"},
        str(state.get("controllers")),
    )
    check("the board says whose move it is", page.locator("#whose").is_visible(), page.locator("#whose").inner_text())
    check(
        "and says it is YOURS while it is",
        "your move" in page.locator("#whose").inner_text(),
        page.locator("#whose").inner_text(),
    )
    page.screenshot(path=str(SHOTS / "versus.png"), full_page=True)

    # Hand over, and the board has to say it is waiting rather than just going quiet.
    page.locator("#endTurn").click()
    page.wait_for_timeout(900)
    said = page.locator("#whose").inner_text()
    check("handing over says the agent is playing", "agent" in said, said)
    check(
        "and the board dims so a refused click is not a surprise",
        page.evaluate("() => document.body.classList.contains('not-my-turn')"),
    )

    refused = page.evaluate("""async () => {
      const m = (await (await fetch("/api/plugins/bloodbowl/game")).json()).match;
      const theirs = m.players.find(p => p.side === "away" && p.place === "pitch");
      if (!theirs) return null;
      const r = await fetch("/api/plugins/bloodbowl/game/act", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({action: "move", player: theirs.id, x: theirs.x, y: theirs.y + 1}),
      });
      return r.json();
    }""")
    check(
        "the board cannot move the agent's players, and says why",
        refused is not None and refused.get("ok") is False and "not your move" in (refused.get("text") or ""),
        str((refused or {}).get("text"))[:80],
    )
    check("no page errors", not problems, "; ".join(problems[:2]))
    print(f"  shot: {(SHOTS / 'versus.png').relative_to(ROOT)}")
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
