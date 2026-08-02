// Pure pitch maths — no three.js, no React, so it unit-tests cleanly.
// The MechArena visual-engine pattern: derivation lives in plain .ts/.js with zero
// runtime imports, and the R3F components only consume it. That split is what makes a
// WebGL view testable at all — there is no DOM per square to assert against, and
// getComputedStyle sees nothing, which is exactly how this repo's worst view bugs
// (invisible legal squares, white-on-white odds badges) went unnoticed for weeks.

/** Pitch geometry, verified against the source and asserted by the Python suite:
 *  26 long x 15 wide, End Zones 1 deep (rows 1 and 26), Wide Zones 4 (x 1-4, 12-15),
 *  Centre Field 7 (x 5-11) — 4+7+4=15 is the arithmetic check. */
export const GEO = { length: 26, width: 15, endZone: 1, wideZone: 4 };

/** Board square (x 1..15 across, y 1..26 along) -> world position, centred on origin.
 *  One square = one world unit, so a camera framing the board needs no magic numbers.
 *
 *  THE LONG AXIS RUNS HORIZONTALLY — pitch `y` becomes world X, pitch `x` becomes world
 *  Z. Not arbitrary: the 2D view settled this already ("26 grid columns x 15 rows"), and
 *  a 26x15 board mapped the other way is TALLER than any landscape viewport, so it runs
 *  off the top and bottom no matter where the camera goes. Matching the 2D view also
 *  means a coach who knows one board can read the other. */
export function squareToWorld(x, y) {
  return [y - (GEO.length + 1) / 2, 0, x - (GEO.width + 1) / 2];
}

/** Which band a square is in — drives the pitch striping. */
export function zoneOf(x, y) {
  if (y === 1 || y === GEO.length) return "endzone";
  if (x <= GEO.wideZone || x > GEO.width - GEO.wideZone) return "wide";
  return "centre";
}

/** The Line of Scrimmage sits BETWEEN rows 13 and 14. */
export const LOS_Y = GEO.length / 2 + 0.5;

/** Adapted from MechArena's knockdownProne: a floored player pitches forward and
 *  settles slightly below the base plane so it reads as resting ON the pitch rather
 *  than clipping through it. Blood Bowl has THREE ways to be off your feet and only
 *  the poses differ — stunned is flatter and lower than prone, because a stunned
 *  player is a turn from standing and should look worse at a glance. */
export const PRONE_PITCH_RAD = 1.22; // ~70deg
export const STUNNED_PITCH_RAD = 1.5; // ~86deg — nearly flat
export const PRONE_SAG_Y = 0.06;

export function poseFor(down) {
  if (down === "prone") return { pitchX: PRONE_PITCH_RAD, sagY: PRONE_SAG_Y };
  if (down === "stunned") return { pitchX: STUNNED_PITCH_RAD, sagY: PRONE_SAG_Y * 1.6 };
  return { pitchX: 0, sagY: 0 };
}

/** Cosine ease, 0..1 — the same easing MechArena uses for its state transitions. */
export function easeInOut(t) {
  return 0.5 * (1 - Math.cos(Math.min(1, Math.max(0, t)) * Math.PI));
}

/** Team colours. Deliberately NOT theme tokens: this is a pitch, and the two sides
 *  have to read as opposed at a glance from any camera angle. */
export const SIDE_COLOR = { home: "#e8613c", away: "#4ea3e0" };
