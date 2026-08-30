/* Painting a player as their FFB icon instead of a coloured tile.
 *
 * The icons ship with the plugin (see the Credits section of the README — they
 * are the FFB project's, used with permission). This module only decides WHICH
 * cell of which sheet a given player gets; the catalogue itself, including each
 * sheet's geometry, arrives from `/meta` so the view never measures an image to
 * work out how to slice it.
 *
 * A MISSING ICON IS THE ORDINARY CASE, not an error. Two of the 159 positionals
 * have no FFB icon at all, a fork may add teams that have none, and a host may
 * be running an older plugin whose /meta says nothing about sprites. Every one
 * of those falls back to the coloured tile the board has always drawn, which is
 * the same contract the 3D board's mesh library has: it can only ever upgrade
 * the board, never break it.
 */

let CAT = {};

/** Handed the `sprites` block from /meta. Absent is fine — everything degrades. */
export function setCatalogue(catalogue) {
  CAT = catalogue && typeof catalogue === "object" ? catalogue : {};
}

/* THE SHIPPED SHEETS ARE FOUR COLUMNS: 0-1 the red kit, 2-3 the blue. Rows are
 * cosmetic variants so eleven Linemen are not eleven identical figures.
 *
 * NONE OF THAT IS ASSUMED HERE. `file_columns`, `cell` and `rows` all travel
 * with each catalogue entry, because the cell is `width / columns` and is not a
 * constant even in the shipped pack — 20 to 42 across 153 sheets, since a Troll
 * is drawn bigger than a Skink. Custom art can be a single flat image
 * (`"columns": 1` in the pack's sprites.json) and this code does not change. */
const DEFAULT_COLUMNS = 4;

/* A player keeps the SAME variant for the whole match, so the board does not
 * reshuffle its faces every render. Derived from the id rather than stored:
 * the row is decoration, and decoration has no business in the event log. */
function variant(id, rows) {
  if (rows <= 1) return 0;
  let h = 0;
  for (let i = 0; i < String(id).length; i++) h = (h * 31 + String(id).charCodeAt(i)) >>> 0;
  return h % rows;
}

/** The icon for one player, or null to leave the tile alone. */
export function forPlayer(teamName, player) {
  const team = CAT[teamName];
  if (!team) return null;
  const entry = team[player.position];
  if (!entry || !entry.file) return null;

  const columns = entry.file_columns || DEFAULT_COLUMNS;
  const rows = entry.rows || 1;

  // With the shipped four, 0-1 are red and 2-3 blue, so a side gets half the
  // sheet and the pair within it is another axis of variety. With fewer — custom
  // art that is one flat image, or one column per side — the same split still
  // means "their half", which is why it is computed rather than tabulated.
  const half = Math.max(1, Math.floor(columns / 2));
  const base = player.side === "home" ? 0 : columns > 1 ? half : 0;
  const col = Math.min(columns - 1, base + (half > 1 ? variant(player.id + "c", half) : 0));
  const row = variant(player.id, rows);

  return {
    file: entry.file,
    // Percentage background-position is relative to the FREE space, so the
    // divisor is (count - 1), not count. Getting that wrong slides every frame
    // progressively further off — the first and last look right and the middle
    // ones are half a player.
    x: columns > 1 ? (col / (columns - 1)) * 100 : 0,
    y: rows > 1 ? (row / (rows - 1)) * 100 : 0,
    columns,
    rows,
  };
}

/** Apply it to a `.pc` node. Returns true if the node became a sprite. */
export function paint(node, base, teamName, player) {
  const icon = forPlayer(teamName, player);
  if (!icon) return false;
  node.classList.add("sprited");
  node.style.backgroundImage = `url("${base}/plugins/bloodbowl/static/sprites/${icon.file}")`;
  node.style.backgroundSize = `${icon.columns * 100}% ${icon.rows * 100}%`;
  node.style.backgroundPosition = `${icon.x}% ${icon.y}%`;
  return true;
}
