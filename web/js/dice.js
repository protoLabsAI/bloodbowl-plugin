/* Die faces for the match log.
 *
 * The log has always carried the engine's arithmetic — "Dodge: needed 3+, rolled
 * 2 — FAILED" — and that sentence is still the thing the agent quotes and the
 * thing a coach can paste into an argument. This module only PAINTS what the
 * same roll already said, from `/game/log`'s structured `dice`, so there is
 * never a second opinion about what was rolled: the numeral on a face is the
 * recorded value, not a re-derivation.
 *
 * NOTHING HERE DECIDES ANYTHING. It renders no verdict it was not handed
 * (`passed` arrives from the server) and it deliberately does not colour a Block
 * face by whether it is "good" — which face a coach wants depends on who chooses
 * and on their Skills, and that is a ruling. A view that shaded POW! green would
 * be a second rules engine, which is the one thing this plugin exists not to be.
 *
 * The glyphs are our own abstract marks — an arrow for a push, a starburst for a
 * POW — for the same reason the pitch is drawn rather than photographed: the
 * printed Block die faces are somebody else's artwork, and an icon that says the
 * same thing costs nothing to draw.
 */

import { esc } from "./api.js";

const STROKE = 'fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"';

/* The six faces, keyed by the names the ENGINE records (engine/dice.py's
 * BLOCK_FACES). Reusing those strings rather than a second vocabulary is what
 * stops a renamed face from silently rendering as a blank chip.
 *
 * THE GLYPHS ARE CHOSEN TO BE TOLD APART AT 16px, not to be self-explanatory.
 * The log line above already says "Player Down, Stumble" in words, so the face's
 * job is to be SCANNABLE — and the first cut drew little figures lying down,
 * which at this size resolved into identical grey smudges. Direction carries
 * that load instead: DOWN means somebody hits the floor, ALONG means somebody
 * gets moved. A doubled chevron for Both Down and a dashed tail for Stumble then
 * read as what they are — the same outcome, twice over or unsteadily. */
const BLOCK = {
  player_down: { label: "Player Down", glyph: chevrons(1) },
  both_down: { label: "Both Down", glyph: chevrons(2) },
  push_back: { label: "Push Back", glyph: arrow(false) },
  stumble: { label: "Stumble", glyph: arrow(true) },
  pow: { label: "POW!", glyph: pow() },
};

/* Down. One chevron for the player who fell, two for a Both Down. */
function chevrons(n) {
  const at = (y) => `<path d="M5 ${y}l6 5.4 6-5.4" ${STROKE}/>`;
  return n === 1 ? at(8) : at(3.6) + at(11);
}

/* Along. Stumble is the same push drawn unsteady — they are the same outcome
 * when the dodger has no Dodge skill, and the shared arrow says so. */
function arrow(stumbling) {
  const y = stumbling ? 8 : 11;
  const shaft = `<path d="M4 ${y}h11m0 0-4.4-4.4M15 ${y}l-4.4 4.4" ${STROKE}/>`;
  const trip = stumbling
    ? `<path d="M4 16.8h11" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-dasharray="0.1 4"/>`
    : "";
  return shaft + trip;
}

/* The starburst. Spokes rather than a filled star: at this size a filled star
 * turns into a blob and the spokes stay legible. */
function pow() {
  const spokes = [];
  for (let i = 0; i < 8; i++) {
    const a = (i * Math.PI) / 4;
    spokes.push(
      `<line x1="${(11 + Math.cos(a) * 3.4).toFixed(1)}" y1="${(11 + Math.sin(a) * 3.4).toFixed(1)}" ` +
        `x2="${(11 + Math.cos(a) * 8.6).toFixed(1)}" y2="${(11 + Math.sin(a) * 8.6).toFixed(1)}"/>`,
    );
  }
  return `<g ${STROKE}>${spokes.join("")}</g>`;
}

function svg(body) {
  return `<svg viewBox="0 0 22 22" aria-hidden="true" focusable="false">${body}</svg>`;
}

/* One die. A face name is a Block die; anything else is the number that came up.
 *
 * The numeral is deliberate. The engine rolls D3, D6, D8 and D16 and a `Roll`
 * does not record how many sides it had, so pips would mean GUESSING the die
 * from its result — six pips for an 8-sided 6. The number is what the rules are
 * written in anyway ("needed 3+"), so printing it is both honest and the thing
 * the coach is actually reading. */
export function face(value) {
  const known = BLOCK[value];
  if (known) {
    return `<span class="die die-block" title="${esc(known.label)}" aria-label="${esc(known.label)}">${svg(known.glyph)}</span>`;
  }
  const text = String(value);
  return `<span class="die" aria-label="rolled ${esc(text)}">${esc(text)}</span>`;
}

/* One recorded roll: its name, its faces, and — only when the engine tested it
 * against a number — the modifier and the verdict it already reached. */
export function roll(r) {
  if (!r || !Array.isArray(r.dice)) return "";
  const faces = r.dice.map(face).join("");
  const bits = [`<span class="rname">${esc(r.kind || "roll")}</span>`, `<span class="dice">${faces}</span>`];

  if (r.modifier) bits.push(`<span class="rmod">${r.modifier > 0 ? "+" : ""}${esc(String(r.modifier))}</span>`);
  if (r.target !== null && r.target !== undefined && r.passed !== null && r.passed !== undefined) {
    bits.push(`<span class="rneed">vs ${esc(String(r.target))}+</span>`);
    // The verdict is the server's. Recomputing `total >= target` here would be a
    // rule — Skills change what passing means, and the engine has already applied them.
    bits.push(
      `<span class="rverdict ${r.passed ? "is-pass" : "is-fail"}">${r.passed ? "passed" : "FAILED"}</span>`,
    );
  } else if (r.total !== null && r.total !== undefined && r.total !== sum(r.dice)) {
    bits.push(`<span class="rmod">= ${esc(String(r.total))}</span>`);
  }
  if (r.note) bits.push(`<span class="rnote">${esc(r.note)}</span>`);
  return `<div class="rollline">${bits.join("")}</div>`;
}

function sum(dice) {
  return dice.reduce((a, d) => (typeof d === "number" ? a + d : a), 0);
}

/* Every roll behind one log entry. */
export function rolls(list) {
  if (!Array.isArray(list) || !list.length) return "";
  return list.map(roll).join("");
}
