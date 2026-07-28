/* The hover stat card.
 *
 * It reads a parsed roster row, which is the point of the whole plugin: a stat
 * shown here came out of a table cell and cannot have been paraphrased on the way.
 * It is also kept on-screen deliberately — a card that renders past the right edge
 * is a card nobody can read.
 */

import { $, esc } from "./api.js";

export function showCard(ev, p) {
  const st = [
    ["MA", p.MA],
    ["ST", p.ST],
    ["AG", p.AG],
    ["PA", p.PA],
    ["AV", p.AV],
  ]
    .map(([k, v]) => `<div class="stat"><b>${esc(v || "–")}</b><span>${k}</span></div>`)
    .join("");

  const status = [];
  if (p.down && p.down !== "standing") status.push(p.down);
  if (p.distracted) status.push("distracted");
  if (p.acted) status.push("has acted");
  if (typeof p.ma_used === "number" && p.movement) status.push(`moved ${p.ma_used}/${p.movement}`);

  $("#card").innerHTML =
    `<h4>${esc(p.position || "Player")}</h4>` +
    `<div class="sub">${esc(p.team || "")}${p.role ? ` · ${esc(p.role)}` : ""}` +
    `${p.cost ? ` · ${esc(p.cost)}` : ""} · (${p.x},${p.y})</div>` +
    `<div class="stats">${st}</div>` +
    (status.length ? `<div class="sub">${esc(status.join(" · "))}</div>` : "") +
    (p.skills && p.skills.length ? `<div class="sk">${p.skills.map(esc).join(" · ")}</div>` : "");
  $("#card").style.display = "block";
  posCard(ev);
}

export function posCard(ev) {
  const c = $("#card");
  const pad = 14;
  const r = c.getBoundingClientRect();
  let x = ev.clientX + pad;
  let y = ev.clientY + pad;
  if (x + r.width > innerWidth) x = ev.clientX - r.width - pad;
  if (y + r.height > innerHeight) y = ev.clientY - r.height - pad;
  c.style.left = `${x}px`;
  c.style.top = `${y}px`;
}

export function hideCard() {
  $("#card").style.display = "none";
}
