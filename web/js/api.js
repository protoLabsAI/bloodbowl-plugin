/* Talking to the plugin, and the handful of helpers everything else needs.
 *
 * The design-system kit supplies a slug-aware authed fetch. When it cannot be
 * loaded — an older host, or a page opened directly — we fall back to a plain
 * fetch against BASE so the board still renders rather than showing nothing.
 */

const API = "/api/plugins/bloodbowl";

let kit;
try {
  kit = await import(window.BASE + "/_ds/plugin-kit.js");
} catch {
  kit = { initPluginView() {}, apiFetch: (p, i) => fetch(window.BASE + p, i) };
}

export { kit };

export const $ = (s) => document.querySelector(s);
export const $$ = (s) => Array.from(document.querySelectorAll(s));

export const esc = (s) =>
  String(s ?? "").replace(/[&<>"]/g, (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[m]);

/** POST body helper. */
export const json = (body) => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export async function api(path, init) {
  const r = await kit.apiFetch(API + path, init);
  if (!r.ok) {
    let d = "";
    try {
      d = (await r.json()).detail || "";
    } catch {
      /* a non-JSON error body is still an error */
    }
    throw new Error(d || `${path} -> ${r.status}`);
  }
  return r.json();
}

/** Same, but a 404 is an answer rather than a failure — used for "is there a
 *  match?", where absence is the normal case and not worth an error banner. */
export async function apiOrNull(path, init) {
  try {
    return await api(path, init);
  } catch {
    return null;
  }
}

export function fail(e) {
  const el = $("#err");
  el.hidden = false;
  el.textContent = String((e && e.message) || e);
}

export function ok() {
  $("#err").hidden = true;
}
