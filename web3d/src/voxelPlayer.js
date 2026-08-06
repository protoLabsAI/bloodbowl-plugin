// Voxel players — pure geometry, no three.js and no React, so it unit-tests cleanly.
//
// The architecture is MechArena's `visual-engine/src/voxel`: sparse unit voxels tagged
// with a MATERIAL SLOT, composed per ARCHETYPE, with the palette resolved separately.
// Keeping derivation in a zero-import module is the same split that makes a WebGL view
// testable at all — there is no DOM per cube to assert against.
//
// The archetypes are the ROSTER'S OWN taxonomy, not invented: every positional carries
// Keywords under `role`, and Lineman / Big Guy / Blitzer / Thrower / Blocker / Runner /
// Catcher account for all 159 of them. Bulk comes from ST, which is likewise real data —
// a ST5 Troll and a ST1 Snotling should not be the same silhouette.

/** primary = jersey · secondary = boots, gloves, dark kit · accent = helmet + trim · skin */
export const SLOTS = ["primary", "secondary", "accent", "skin"];

/** Build parameters per archetype. One builder consumes these, so a new archetype is a
 *  row in this table rather than another block of geometry code. */
export const ARCHETYPES = {
  lineman: { height: 9, shoulders: 3, depth: 2, crest: false, armRaised: false },
  blitzer: { height: 9, shoulders: 4, depth: 2, crest: true, armRaised: false },
  blocker: { height: 8, shoulders: 5, depth: 3, crest: false, armRaised: false },
  thrower: { height: 9, shoulders: 3, depth: 2, crest: false, armRaised: true },
  runner: { height: 10, shoulders: 2, depth: 2, crest: false, armRaised: false },
  catcher: { height: 10, shoulders: 2, depth: 2, crest: true, armRaised: false },
  bigguy: { height: 13, shoulders: 6, depth: 4, crest: false, armRaised: false },
};

const KEYWORD_TO_ARCHETYPE = [
  ["big guy", "bigguy"],
  ["blitzer", "blitzer"],
  ["blocker", "blocker"],
  ["thrower", "thrower"],
  ["catcher", "catcher"],
  ["runner", "runner"],
  ["lineman", "lineman"],
];

/**
 * Which archetype a positional is built from, read off its roster Keywords.
 *
 * Order matters: a "Blitzer, Human" is a blitzer, and a positional carrying both a
 * species and a job keyword must resolve to the job. Anything unmatched — the 18
 * "Special" positionals, a fork's hand-rolled roster — falls back to a lineman, which is
 * the one build every team has.
 */
export function archetypeFor(role, position = "") {
  const hay = `${role || ""} ${position || ""}`.toLowerCase();
  for (const [needle, name] of KEYWORD_TO_ARCHETYPE) if (hay.includes(needle)) return name;
  return "lineman";
}

/** Strength widens and thickens a build without changing its archetype: the roster's ST
 *  runs 1..6 and the silhouette should say so before the hover card does. */
function bulkFor(st) {
  const n = Number.parseInt(String(st ?? 3), 10);
  return Number.isFinite(n) ? Math.max(-1, Math.min(3, Math.round((n - 3) / 1.5))) : 0;
}

const box = (out, x0, y0, z0, w, h, d, slot) => {
  for (let x = 0; x < w; x++)
    for (let y = 0; y < h; y++)
      for (let z = 0; z < d; z++) out.push({ x: x0 + x, y: y0 + y, z: z0 + z, slot });
};

/**
 * The voxels for one player, in grid units with the feet at y=0 and the body centred on
 * x=0. The renderer scales; nothing here knows about world units.
 */
export function buildVoxels(archetype, { st = 3 } = {}) {
  const a = ARCHETYPES[archetype] || ARCHETYPES.lineman;
  const bulk = bulkFor(st);
  const shoulders = a.shoulders + bulk; // half-width in voxels
  const depth = a.depth + Math.max(0, bulk - 1);
  const legH = Math.round(a.height * 0.4);
  const torsoH = a.height - legH;
  const v = [];

  // Legs — two columns, dark. Gapped so the silhouette reads as a figure at a glance
  // rather than a block, which matters more than detail at this size.
  const legW = Math.max(1, Math.round(shoulders / 2) - 1);
  box(v, -shoulders + 1, 0, -Math.floor(depth / 2), legW, legH, depth, "secondary");
  box(v, shoulders - legW - 1, 0, -Math.floor(depth / 2), legW, legH, depth, "secondary");

  // Torso — the jersey, and the bulk of what a coach sees.
  box(v, -shoulders + 1, legH, -Math.floor(depth / 2), shoulders * 2 - 2, torsoH - 2, depth, "primary");
  // Shoulder line, one voxel wider each side: the archetype's main tell from above.
  box(v, -shoulders, legH + torsoH - 3, -Math.floor(depth / 2), shoulders * 2, 1, depth, "accent");

  // Arms
  const armY = legH;
  const armH = torsoH - 2;
  box(v, -shoulders - 1, armY, -1, 1, armH, Math.min(2, depth), "primary");
  box(v, shoulders, a.armRaised ? armY + 2 : armY, -1, 1, armH, Math.min(2, depth), "primary");

  // Head + helmet. The helmet is `accent`, so it reads as kit rather than skin from any
  // camera angle — a bare head at this scale is a beige dot.
  const headY = legH + torsoH - 2;
  box(v, -1, headY, -1, 2, 2, 2, "skin");
  // A 4x4 helmet was wider than the head and read as a plate covering the figure from
  // above. Kept to the head's own footprint plus one voxel.
  box(v, -1, headY + 2, -1, 2, 1, 3, "accent");
  if (a.crest) box(v, 0, headY + 3, -1, 1, 1, 3, "accent");

  return v;
}

/**
 * How tall a build stands, in grid units, without building it.
 *
 * Derived from the same numbers `buildVoxels` uses: the head sits two voxels below the
 * nominal height, then two of head and one of helmet, plus a crest. Callers need this to
 * put the ball and the badge ABOVE a figure whose height varies from 10 (lineman) to 14
 * (Big Guy) — a fixed offset either buries the marker in a Troll's helmet or leaves it
 * floating over a Gnoblar.
 */
export function gridHeight(archetype) {
  const a = ARCHETYPES[archetype] || ARCHETYPES.lineman;
  return a.height + 1 + (a.crest ? 1 : 0);
}

/** Grid extents, so the renderer can centre and scale without knowing the build rules. */
export function boundsOf(voxels) {
  const f = (k, fn) => fn(...voxels.map((p) => p[k]));
  return voxels.length
    ? { minX: f("x", Math.min), maxX: f("x", Math.max), minY: f("y", Math.min), maxY: f("y", Math.max), minZ: f("z", Math.min), maxZ: f("z", Math.max) }
    : { minX: 0, maxX: 0, minY: 0, maxY: 0, minZ: 0, maxZ: 0 };
}
