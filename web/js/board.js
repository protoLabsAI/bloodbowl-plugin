/* The board itself: cells, rulers, zone overlay, hover crosshair.
 *
 * Every dimension comes from the geometry the engine reports. Nothing here
 * assumes 26 or 15 — including the SVG, whose viewBox is built from the same
 * numbers, so an overlay line cannot drift off the squares it is describing.
 * The old version hardcoded both and would have diverged silently.
 */

import { $ } from "./api.js";

export let GEO = null;
export let CELLS = [];

/* Pitch y (1..length) is the grid COLUMN; pitch x (1..width) is the grid ROW. */
export const at = (x, y) => CELLS[(x - 1) * GEO.length + (y - 1)];
export const key = (x, y) => `${x}:${y}`;

let hotX = 0;
let hotY = 0;
let onHover = null;

export function setGeometry(geo) {
  GEO = geo;
  // Published so the stylesheet can express every grid in terms of the real
  // pitch rather than repeating the numbers.
  const root = document.documentElement.style;
  root.setProperty("--cols", GEO.length);
  root.setProperty("--rows", GEO.width);
}

export function buildBoard(handlers = {}) {
  onHover = handlers.onHover || null;
  const b = $("#board");
  b.innerHTML = "";
  CELLS = [];
  const wz = GEO.wide_zone_width;
  const ez = GEO.end_zone_depth;
  const los = GEO.los_rows[0];

  for (let x = 1; x <= GEO.width; x++) {
    for (let y = 1; y <= GEO.length; y++) {
      const c = document.createElement("div");
      c.className = "cell";
      if (y <= ez) c.classList.add("ez-home");
      else if (y > GEO.length - ez) c.classList.add("ez-away");
      else if (y > los) c.classList.add("awayhalf");
      if (x <= wz || x > GEO.width - wz) c.classList.add("wide");
      c.dataset.x = x;
      c.dataset.y = y;
      c.addEventListener("mouseenter", () => hot(x, y));
      c.addEventListener("dragover", (ev) => {
        ev.preventDefault();
        c.classList.add("target");
      });
      c.addEventListener("dragleave", () => c.classList.remove("target"));
      c.addEventListener("drop", (ev) => {
        ev.preventDefault();
        c.classList.remove("target");
        handlers.onDrop && handlers.onDrop(ev, x, y);
      });
      c.addEventListener("click", () => handlers.onCellClick && handlers.onCellClick(x, y));
      CELLS.push(c);
      b.appendChild(c);
    }
  }

  $("#rtop").innerHTML = Array.from({ length: GEO.length }, (_, i) => `<div data-c="${i + 1}">${i + 1}</div>`).join("");
  $("#rleft").innerHTML = Array.from({ length: GEO.width }, (_, i) => `<div data-r="${i + 1}">${i + 1}</div>`).join("");

  const ov = $("#ov");
  ov.setAttribute("viewBox", `0 0 ${GEO.length} ${GEO.width}`);
  ov.innerHTML = [
    line(los, 0, los, GEO.width, "var(--pl-color-accent)", 0.1),
    line(0, wz, GEO.length, wz, "currentColor", 0.05, 0.5),
    line(0, GEO.width - wz, GEO.length, GEO.width - wz, "currentColor", 0.05, 0.5),
    line(ez, 0, ez, GEO.width, "currentColor", 0.05, 0.5),
    line(GEO.length - ez, 0, GEO.length - ez, GEO.width, "currentColor", 0.05, 0.5),
  ].join("");

  // Publish cell size so type scales with the BOARD, not the viewport. Scaling
  // off vw resolved to ~7px inside a rail panel.
  new ResizeObserver((es) => {
    const w = es[0].contentRect.width;
    document.documentElement.style.setProperty("--cell", `${w / GEO.length}px`);
  }).observe($("#board"));
}

function line(x1, y1, x2, y2, stroke, width, opacity = 1) {
  return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${stroke}" stroke-width="${width}" opacity="${opacity}"/>`;
}

function hot(x, y) {
  if (x === hotX && y === hotY) return;
  hotX = x;
  hotY = y;
  for (const c of CELLS) c.classList.remove("xhot", "yhot");
  for (let i = 1; i <= GEO.length; i++) at(x, i).classList.add("xhot");
  for (let i = 1; i <= GEO.width; i++) at(i, y).classList.add("yhot");
  $("#rtop")
    .querySelectorAll("div")
    .forEach((d) => d.classList.toggle("hot", +d.dataset.c === y));
  $("#rleft")
    .querySelectorAll("div")
    .forEach((d) => d.classList.toggle("hot", +d.dataset.r === x));
  $("#coord").textContent = `(${x},${y})`;
  onHover && onHover(x, y);
}

/**
 * Remove `classes` from every square, and the odds badge from the squares that
 * had one of them.
 *
 * IT USED TO REMOVE EVERY BADGE ON THE BOARD whatever classes it was handed,
 * which is a trap rather than a convenience: the badges belong to the marks, so a
 * caller clearing ONE mark silently stripped the Dodge modifiers, dice counts and
 * blitz distances belonging to all the others. It cost an afternoon during the
 * drag work — clearing the drop-target highlight on each pointer move wiped the
 * numbers a coach was dragging BY, and the board looked merely quiet rather than
 * wrong. Scoped to the squares actually being cleared, the two can no longer come
 * apart.
 */
export function clearMarks(...classes) {
  for (const c of CELLS) {
    if (!classes.some((k) => c.classList.contains(k))) continue;
    c.classList.remove(...classes);
    const o = c.querySelector(".odds");
    if (o) o.remove();
  }
}
