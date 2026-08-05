// Team colours — pure, deterministic, no data invented.
//
// The FUNCTIONAL requirement beats the cosmetic one: two sides must be tellable apart
// instantly from any camera angle, so the side's colour owns the jersey and the team only
// shifts it. Real Blood Bowl teams have real colour identities and we do not ship them —
// guessing thirty of them would be inventing data, which is the one thing this plugin
// exists to avoid. Instead the variation is DERIVED from the team's name, so it is stable
// across reloads and machines without claiming to be canonical.
//
// Same idea as MechArena's `determinism/cosmeticSeed`: a seed in, a palette out, no state.

/** FNV-1a. Small, stable, and not trying to be a hash function for anything that matters. */
function hash(text) {
  let h = 0x811c9dc5;
  for (let i = 0; i < String(text).length; i++) {
    h ^= String(text).charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h >>> 0;
}

const HOME = { h: 14, s: 0.72, l: 0.52 }; // warm
const AWAY = { h: 205, s: 0.66, l: 0.55 }; // cool

/**
 * COMMAS, not spaces. three.js's `Color.setStyle` only parses the LEGACY css syntax —
 * `hsl(14 72% 52%)` (CSS Color 4, what a browser and every modern stylesheet accept)
 * silently yields WHITE: no throw, no warning, and a Color that was constructed white
 * simply stays white. Every player rendered in the material's default colour with a
 * perfectly correct scene graph — 512 instances, `instanceColor` allocated, all of it
 * pointing at [1,1,1]. Only reading the instance buffer back showed it.
 */
function hsl(h, s, l) {
  return `hsl(${((h % 360) + 360) % 360}, ${Math.round(s * 100)}%, ${Math.round(l * 100)}%)`;
}

/**
 * The four material slots for a side's kit.
 *
 * The team shifts hue by at most ±18°, which keeps every home team unmistakably warm and
 * every away team unmistakably cool while giving Amazon and Ogre different reds. Wider
 * than that and the sides start to collide, which costs a coach a misread every turn.
 */
export function paletteFor(side, team) {
  const base = side === "away" ? AWAY : HOME;
  const n = hash(team || side);
  const hueShift = ((n % 37) - 18) * 1.0;
  const h = base.h + hueShift;
  return {
    primary: hsl(h, base.s, base.l),
    // Near-black boots and gloves, tinted toward the jersey so the figure reads as one kit.
    secondary: hsl(h, 0.3, 0.14),
    // Helmet and shoulder line. Lighter than the jersey so it separates, but still firmly
    // IN the team's hue — at l=0.82 it came out near-white, and since the helmet and
    // shoulders are most of what a top-down camera sees, the board read as two rows of
    // pale slabs with the team colour hidden underneath. The angle a board is mostly read
    // from is the one the palette has to be tuned for.
    accent: hsl(h, 0.62, 0.66),
    skin: hsl(28, 0.35, 0.62),
  };
}
