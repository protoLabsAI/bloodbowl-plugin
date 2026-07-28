/* Boot, mode switching, and the poll.
 *
 * Two modes share one board: Setup is the permissive practice board, Play is the
 * match. Only one owns the squares at a time — each tears its own nodes down when
 * it loses the board, because two renderers appending to the same cells is how
 * you get a player that cannot be dragged and nobody can explain why.
 *
 * The poll exists so the agent's moves appear without a refresh. It never runs
 * mid-drag, and never re-renders when nothing changed.
 */

import { $, $$, api, fail, kit, ok } from "./api.js";
import { buildBoard, setGeometry } from "./board.js";
import * as game from "./game.js";
import * as setup from "./setup.js";

let mode = "setup";

function applyMode() {
  $$(".mode-setup").forEach((n) => (n.hidden = mode !== "setup"));
  $$(".mode-play").forEach((n) => (n.hidden = mode !== "play"));
  $("#modeSetup").classList.toggle("is-on", mode === "setup");
  $("#modePlay").classList.toggle("is-on", mode === "play");
}

async function enter(next) {
  if (mode === next) return;
  // Hand the board over cleanly rather than letting both renderers hold nodes.
  if (mode === "setup") setup.teardown();
  else game.teardown();
  mode = next;
  applyMode();
  if (mode === "play") {
    await game.refresh();
    game.render();
    await game.renderLog();
    if (!game.has()) {
      $("#sel").innerHTML = '<span class="muted">No match yet — press <b>New match</b> to start one from the board.</span>';
    }
  } else {
    setup.render();
  }
}

async function boot() {
  try {
    const meta = await api("/meta");
    setGeometry(meta.geometry);
    setup.setScenario(meta.scenario);

    buildBoard({
      onCellClick: (x, y) => (mode === "setup" ? setup.onCellClick(x, y) : game.onCellClick(x, y)),
      onDrop: (ev, x, y) => (mode === "setup" ? setup.onDrop(ev, x, y) : null),
    });

    for (const id of ["#homeTeam", "#awayTeam"]) {
      $(id).innerHTML = meta.teams.map((t) => `<option>${t}</option>`).join("");
    }
    // Reflect the board; NEVER write to it. Opening a view must not mutate state —
    // an earlier version POSTed both teams on load and stomped the agent's setup.
    if (meta.scenario.home_team) $("#homeTeam").value = meta.scenario.home_team;
    if (meta.scenario.away_team) $("#awayTeam").value = meta.scenario.away_team;
    await setup.ensureRoster(meta.scenario.home_team);
    await setup.ensureRoster(meta.scenario.away_team);
    setup.buildPalette();
    setup.wire();
    game.wire(() => applyMode());

    $("#modeSetup").addEventListener("click", () => enter("setup"));
    $("#modePlay").addEventListener("click", () => enter("play"));

    // A match already in progress is the more useful thing to show on open.
    await game.refresh();
    if (game.has()) await enter("play");
    else {
      applyMode();
      setup.render();
    }
    ok();
  } catch (e) {
    fail(e);
  }
}

setInterval(async () => {
  if (setup.state.dragging || document.hidden) return;
  try {
    if (mode === "setup") await setup.poll();
    else await game.poll();
  } catch {
    /* transient — the next tick will catch up */
  }
}, 2500);

let booted = false;
function go() {
  if (booted) return;
  booted = true;
  boot();
}
kit.initPluginView(go);
setTimeout(go, 800);
