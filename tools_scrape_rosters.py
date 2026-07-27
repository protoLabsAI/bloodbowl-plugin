#!/usr/bin/env python3
"""Scrape the 32 S3 team rosters from Blood Bowl Base into structured JSON.

The site renders each roster as a real <table>, so we parse cells rather than the
flattened text the knowledge-base ingest produces — column alignment is exactly
what a hover stat-card needs and exactly what flattening destroys.
"""

from __future__ import annotations

import html
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "team_html"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) bloodbowl-plugin-roster-scrape"

# The site uses U+2011 NON-BREAKING HYPHEN in quantities ("0‑16") and U+2013 in
# places. Normalise or every qty parse silently fails.
DASHES = {"‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-"}


def clean(s: str) -> str:
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    for bad, good in DASHES.items():
        s = s.replace(bad, good)
    return re.sub(r"\s+", " ", s).strip()


def fetch(url: str) -> str:
    slug = [p for p in url.split("/") if p][-1]
    cached = CACHE / f"{slug}.html"
    if cached.exists():
        return cached.read_text(encoding="utf-8", errors="replace")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read().decode("utf-8", errors="replace")
    CACHE.mkdir(exist_ok=True)
    cached.write_text(body, encoding="utf-8")
    time.sleep(1.5)  # be polite to a community-run site
    return body


def rows_of(table_html: str) -> list[list[str]]:
    out = []
    for tr in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", table_html):
        cells = [clean(c) for c in re.findall(r"(?is)<t[dh][^>]*>(.*?)</t[dh]>", tr)]
        if any(cells):
            out.append(cells)
    return out


def tables_of(page: str) -> list[str]:
    return re.findall(r"(?is)<table[^>]*>(.*?)</table>", page)


def parse_skills(cell: str) -> list[str]:
    # Skills come as "• Dodge • On the Ball • Pass". Split on the bullet, not on
    # spaces — several skills contain spaces ("On the Ball", "Hit and Run").
    parts = [p.strip().lstrip("•").strip() for p in re.split(r"[••]", cell)]
    return [p for p in (x.strip() for x in parts) if p]


def parse_team(url: str) -> dict | None:
    page = fetch(url)
    name = clean(re.search(r"(?is)<h1[^>]*>(.*?)</h1>", page).group(1)) if re.search(r"(?is)<h1", page) else None
    if not name:
        slug = [p for p in url.split("/") if p][-1]
        name = slug.replace("_", " ")
    text = clean(re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", page))
    tier = None
    m = re.search(r"\bTIER\s*(\d+)\b", text, re.I)
    if m:
        tier = int(m.group(1))

    positionals, staff = [], {}
    for t in tables_of(page):
        rs = rows_of(t)
        if not rs:
            continue
        head = [h.lower() for h in rs[0]]
        if "position" in head and "ma" in head:
            # Header names vary between pages: some say "Skills", others
            # "Skills & Traits". Match by prefix or every such page silently
            # yields empty skills — which is exactly what happened to 42 of 159
            # positionals on the first pass.
            idx = {}
            for i, h in enumerate(head):
                canon = "skills" if h.startswith("skills") else h
                idx.setdefault(canon, i)
            for r in rs[1:]:
                if len(r) < len(head) - 1:
                    continue

                def cell(key, row=r, idx=idx):
                    i = idx.get(key)
                    return row[i] if i is not None and i < len(row) else ""

                pos = cell("position")
                if not pos:
                    continue
                role = None
                mrole = re.search(r"\(([^)]*)\)", pos)
                if mrole:
                    role = mrole.group(1)
                positionals.append(
                    {
                        "qty": cell("qty"),
                        "position": re.sub(r"\s*\([^)]*\)", "", pos).strip(),
                        "role": role,
                        "MA": cell("ma"),
                        "ST": cell("st"),
                        "AG": cell("ag"),
                        "PA": cell("pa"),
                        "AV": cell("av"),
                        "skills": parse_skills(cell("skills")),
                        "primary": cell("primary").split(),
                        "secondary": cell("secondary").split(),
                        "cost": cell("cost"),
                    }
                )
        elif len(rs[0]) == 2 and any(k in " ".join(head) for k in ("staff", "cost", "re-roll", "reroll")):
            for r in rs[1:]:
                if len(r) >= 2 and r[0]:
                    staff[r[0]] = r[1]

    if not positionals:
        return None
    return {"name": name, "tier": tier, "positionals": positionals, "staff": staff, "source": url}


def main() -> int:
    urls = [u.strip() for u in (HERE / "bb_urls.txt").read_text().splitlines() if "/teams/" in u]
    urls = [u for u in urls if not u.rstrip("/").endswith("/teams")]
    teams, failed = [], []
    for u in urls:
        try:
            t = parse_team(u)
        except Exception as exc:  # noqa: BLE001
            t, exc_s = None, f"{type(exc).__name__}: {exc}"
            failed.append((u, exc_s))
            print(f"  FAIL {u} — {exc_s}", file=sys.stderr)
            continue
        if t is None:
            failed.append((u, "no roster table"))
            print(f"  FAIL {u} — no roster table", file=sys.stderr)
            continue
        teams.append(t)
        print(f"  {t['name']:24} tier={t['tier']}  {len(t['positionals'])} positionals  {len(t['staff'])} staff rows")

    teams.sort(key=lambda t: t["name"])
    out = {
        "edition": "Blood Bowl Third Season Edition (S3 / BB2025)",
        "source": "https://bloodbowlbase.ru/bb2025/teams/",
        "note": "Community transcription, not Games Workshop's own text. The printed rulebook settles disputes.",
        "pitch": {
            "length": 26,
            "width": 15,
            "end_zone_depth": 1,
            "wide_zone_width": 4,
            "centre_field_width": 7,
            "line_of_scrimmage_between_rows": [13, 14],
        },
        "teams": teams,
    }
    (HERE / "rosters.json").write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(f"\n{len(teams)} teams -> rosters.json ({(HERE / 'rosters.json').stat().st_size // 1024} KB)")
    if failed:
        print(f"{len(failed)} FAILED:")
        for u, why in failed:
            print("  ", u, "—", why)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
