// Text as a canvas texture, NOT a font file.
//
// drei's <Text> is the obvious choice and the wrong one here: troika resolves an unset
// `font` prop to a Google-hosted default, and this view runs in a sandboxed iframe inside
// a plugin whose manifest declares `network: []`. A label that silently fails to fetch is
// the worst kind of missing — the scene still renders, just without the one thing telling
// you which player you are looking at.
//
// A 2D canvas uses the browser's own fonts, needs no asset, adds nothing to the static
// route's suffix allowlist, and cannot fail offline.

import { CanvasTexture, LinearFilter } from "three";

const cache = new Map();

/** A texture of `text`, drawn to fill its bitmap. Cached — a badge repeats across a
 *  match and every uncached call allocates a canvas. */
export function labelTexture(text, { color = "#0b0f0c", px = 128, weight = 700 } = {}) {
  const key = `${text}|${color}|${px}|${weight}`;
  const hit = cache.get(key);
  if (hit) return hit;

  const c = document.createElement("canvas");
  c.width = px * 2;
  c.height = px;
  const g = c.getContext("2d");
  g.clearRect(0, 0, c.width, c.height);
  g.fillStyle = color;
  g.textAlign = "center";
  g.textBaseline = "middle";
  // Shrink to fit rather than overflow: a two-letter badge and "HOME SCORES HERE" go
  // through the same helper, and a clipped label is indistinguishable from a wrong one.
  let size = px * 0.62;
  do {
    g.font = `${weight} ${size}px system-ui, sans-serif`;
    if (g.measureText(text).width <= c.width * 0.92) break;
    size -= 2;
  } while (size > 8);
  g.fillText(text, c.width / 2, c.height / 2);

  const tex = new CanvasTexture(c);
  tex.minFilter = LinearFilter; // no mipmaps: these are read flat-on and mips just blur them
  tex.needsUpdate = true;
  cache.set(key, tex);
  return tex;
}
