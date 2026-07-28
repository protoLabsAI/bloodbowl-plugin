#!/usr/bin/env python3
"""Scrape the S3 Skills and Traits into JSON, so a coach can QUOTE one.

This exists because of a specific, observed failure, written up in
docs/HANDOFF.md §1. Asked to play a Foul, the agent drove the engine correctly
and quoted every roll — and then explained an unmodelled Skill unprompted, saying
Break Tackle was "an ST-based alternative" to the dodge roll. It is not: it is a
+1/+2/+3 modifier to the Agility Test, scaled by Strength. The correct text was in
the agent's own knowledge base and went unread.

Modelling a Skill changes the game; SHIPPING ITS TEXT changes what the agent can
honestly say, and that is a different, cheaper fix. A skill in this catalogue can
be quoted verbatim whether or not the engine applies it.

Two things about this page that a careless parse gets wrong:

* A TRAIT is marked with a trailing ASTERISK on the heading — `STUNTY* (PASSIVE)`
  versus `BLOCK (ACTIVE)`. Nothing else distinguishes them, and the distinction is
  real: Traits are not normally learnable and several rules key on it. A regex
  that does not expect the `*` silently drops all 25 of them, which reads as "the
  page only documents Skills" rather than as a bug. That is how the first pass
  here missed every Trait the rosters use most — Loner, Stunty, Right Stuff.
* Some names carry a PARAMETER: `LONER (X+)`, `ANIMOSITY (X)`, `HATRED (X)`. The
  roster prints the real value (`Loner (4+)`), so the catalogue is keyed on the
  bare name and records that a value belongs in the brackets.
* An ELITE Skill is marked ONLY by an `<img src=".../elite_skill_small.jpg">`
  inside the heading — "Additionally, an Elite Skill will be denoted by the
  symbol." There are four (Block, Dodge, Guard, Mighty Blow) and they are the four
  most common on the rosters, so losing them is not a rounding error. The
  flattened page text has no symbol at all, which is the concrete reason to parse
  the HTML here rather than read the same page out of the knowledge base.
* The CATEGORY (Agility, Devious, General, Mutation, Passing, Strength) is the
  enclosing `<h3>` section, and is likewise a symbol in the prose. The very first
  confabulation recorded in the handoff was an invented legend for these — so the
  real ones ship.

Parses the real <h4>/<p> structure rather than the flattened page text: the
flattened form is what the knowledge-base ingest produces, and it puts a heading
next to the wrong body at every chunk boundary.

    python tools_scrape_skills.py            # write data/skills.json
    python tools_scrape_skills.py --check    # parse and report, write nothing
"""

from __future__ import annotations

import html as _html
import json
import re
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "data" / "skills.json"
URL = "https://bloodbowlbase.ru/bb2025/core_rules/skills_and_traits/"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) bloodbowl-plugin-skill-scrape"

# NAME, an optional (X)/(X+) parameter, an optional * marking a Trait, then the
# ACTIVE/PASSIVE tag. Names may hold & and ! — "BALL & CHAIN", "TIMMM-BER!".
# `&amp;` is why the name class allows a bare `&` only after unescaping; match the
# raw heading loosely and clean it afterwards.
HEADING = re.compile(
    r"<h4[^>]*id=\"[^\"]*\"[^>]*>(.*?)\s*\((ACTIVE|PASSIVE)\)\s*(<img[^>]*>)?\s*</h4>",
    re.S,
)
NAME = re.compile(r"^(.*?)(\s*\(X\+?\))?(\*)?$")
CATEGORIES = ("agility", "devious", "general", "mutation", "passing", "strength", "traits_1")


def clean(s: str) -> str:
    """Text of a fragment. Struck-through content is DROPPED, not flattened —
    see tools_scrape_rosters.clean for why fusing an erratum is worse than
    either value on its own."""
    s = re.sub(r"(?is)<del>.*?</del>", " ", s)
    s = _html.unescape(re.sub(r"<[^>]+>", " ", s))
    return re.sub(r"\s+", " ", s).strip()


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def _sections(page: str) -> list[tuple[int, str]]:
    """(offset, category) for each <h3> that opens a category of the list."""
    out = []
    for m in re.finditer(r'<h3[^>]*id="([^"]+)"[^>]*>', page):
        if m.group(1) in CATEGORIES:
            out.append((m.start(), "Trait" if m.group(1) == "traits_1" else m.group(1).title()))
    return out


def parse(page: str) -> dict:
    """Every Skill and Trait, keyed on the bare name."""
    sections = _sections(page)
    marks = list(HEADING.finditer(page))
    out: dict[str, dict] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(page)
        body = page[m.end() : end]
        # Only the prose. Anything that is not a <p> — a nav block, an example
        # table — cannot bleed into the text.
        paras = [clean(p) for p in re.findall(r"(?is)<p[^>]*>(.*?)</p>", body)]
        raw, param, star = NAME.match(clean(m.group(1))).groups()
        name = raw.strip()
        category = "Trait"
        for offset, cat in sections:
            if offset < m.start():
                category = cat
        out[name] = {
            "name": name,
            "kind": "Trait" if star else "Skill",
            "category": category,
            "when": m.group(2).title(),  # Active | Passive
            # "Additionally, an Elite Skill will be denoted by the symbol."
            "elite": bool(m.group(3) and "elite_skill" in m.group(3)),
            "parameter": bool(param),
            "text": " ".join(p for p in paras if p),
        }
    return out


def main() -> int:
    check = "--check" in sys.argv
    page = fetch(URL)
    skills = parse(page)

    problems = []
    if len(skills) < 100:
        problems.append(f"only {len(skills)} entries — the heading pattern probably stopped matching")
    if not any(v["kind"] == "Trait" for v in skills.values()):
        problems.append("no Traits found — the asterisk marker is how they are distinguished")
    elite = sorted(n for n, v in skills.items() if v["elite"])
    if not elite:
        problems.append("no Elite skills found — they are marked only by an <img> in the heading")
    if {v["category"] for v in skills.values()} < {"Agility", "General", "Strength", "Trait"}:
        problems.append("categories missing — they come from the enclosing <h3> section")
    for name, v in skills.items():
        if len(v["text"]) < 30:
            problems.append(f"{name}: text is {len(v['text'])} chars, probably truncated")

    # Every Skill the shipped rosters actually use must be here, or a coach asking
    # about one gets nothing — which is the exact gap this file exists to close.
    rosters = json.loads((HERE / "data" / "rosters.json").read_text())
    used = {
        s.split("(")[0].strip()
        for team in rosters["teams"]
        for grp in ("positionals", "star_players")
        for p in (team.get(grp) or [])
        for s in (p.get("skills") or [])
        if s.split("(")[0].strip() not in ("", "-")
    }
    have = {n.casefold() for n in skills}
    missing = sorted(n for n in used if n.casefold() not in have)
    if missing:
        problems.append(f"{len(missing)} roster skills have no entry: {missing}")

    traits = sum(1 for v in skills.values() if v["kind"] == "Trait")
    print(f"{len(skills)} entries — {len(skills) - traits} Skills, {traits} Traits")
    print(f"elite: {elite}")
    by_cat: dict[str, int] = {}
    for v in skills.values():
        by_cat[v["category"]] = by_cat.get(v["category"], 0) + 1
    print("categories: " + ", ".join(f"{k} {n}" for k, n in sorted(by_cat.items())))
    print(f"roster coverage: {len(used) - len(missing)}/{len(used)}")
    for p in problems:
        print(f"  !! {p}")
    if problems:
        return 1
    if not check:
        OUT.write_text(json.dumps({"source": URL, "skills": skills}, indent=1, ensure_ascii=False) + "\n")
        print(f"wrote {OUT.relative_to(HERE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
