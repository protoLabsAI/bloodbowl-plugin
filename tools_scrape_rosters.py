#!/usr/bin/env python3
"""Scrape the S3 team rosters and star players from Blood Bowl Base into JSON.

The site renders each roster as a real <table>, so we parse cells rather than the
flattened text the knowledge-base ingest produces — column alignment is exactly
what a hover stat-card needs and exactly what flattening destroys.

Two layouts have to be handled or the data goes silently wrong rather than empty:

* Staff and re-roll costs are an <h3 id="staff"> section holding a <ul> of
  "<a>Cheerleader</a> - 10K" links — NOT a table. An earlier pass looked for a
  two-column table and so found staff for 0 of 30 teams without ever erroring.
* A solo star's stat table carries the cost in the header's first cell
  ("300K | MA | ST | ..."), but a PAIRED star (Grak and Crumbleberry) prices the
  pair in a <p><strong>250K</strong></p> and gives each member its own table
  headed plainly "MA | ST | ...". Assuming the first cell is a cost would read
  "MA" as the price and shift every stat one column left.
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
# The plugin loads data/rosters.json. This script used to write rosters.json beside
# itself, so a re-scrape looked like it worked and changed nothing that shipped.
OUT = HERE / "data" / "rosters.json"
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


COST_RE = re.compile(r"^(\d+)\s*K$", re.I)


def is_cost(s: str) -> bool:
    return bool(COST_RE.match(s.strip()))


def list_items(ul_html: str) -> list[str]:
    """The <li> texts of one <ul>, each flattened.

    Taken whole rather than per-anchor: the site writes qualifiers OUTSIDE the
    link ("<a>Loner</a> (4+)"), so reading anchor text alone drops the value that
    makes the skill mean anything.
    """
    return [clean(li) for li in re.findall(r"(?is)<li[^>]*>(.*?)</li>", ul_html) if clean(li)]


def sections(page: str) -> dict[str, list[str]]:
    """Map each <h3 id="..."> to the <li> texts of the <ul> that follows it.

    Stops at the next heading so a section without a list yields [] instead of
    swallowing the rest of the page.
    """
    out: dict[str, list[str]] = {}
    for m in re.finditer(r'(?is)<h3[^>]*\bid="([^"]+)"[^>]*>(.*?)</h3>', page):
        rest = page[m.end() :]
        nxt = re.search(r"(?is)<h[1-3][\s>]", rest)
        block = rest[: nxt.start()] if nxt else rest
        ul = re.search(r"(?is)<ul[^>]*>(.*?)</ul>", block)
        out[m.group(1).lower()] = list_items(ul.group(1)) if ul else []
    return out


def split_cost(item: str) -> tuple[str, str | None]:
    """ "Cheerleader - 10K" -> ("Cheerleader", "10K"). Cost is optional."""
    m = re.match(r"^(.*?)\s*[-–]\s*(\d+\s*K)$", item.strip(), re.I)
    if m:
        return m.group(1).strip(), m.group(2).replace(" ", "").upper()
    return item.strip(), None


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

    positionals = []
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
    if not positionals:
        return None

    sec = sections(page)
    staff: dict[str, str] = {}
    for item in sec.get("staff", []):
        label, cost = split_cost(item)
        if label and cost:
            staff[label] = cost

    stars = []
    for item in sec.get("star-players", []):
        label, cost = split_cost(item)
        if label:
            stars.append({"name": label, "cost": cost})

    return {
        "name": name,
        "tier": tier,
        "positionals": positionals,
        # A team's re-roll price is the number that decides most draft questions,
        # so it is lifted out of the staff map rather than left to be looked up.
        "reroll_cost": staff.get("Re-roll"),
        "staff": staff,
        "star_players": stars,
        "league": sec.get("league", []),
        "special_rules": sec.get("special-rules", []),
        "source": url,
    }


STATS = ("MA", "ST", "AG", "PA", "AV")


def parse_statline(table_html: str) -> tuple[dict[str, str], str | None]:
    """One star's stat table -> ({MA..AV}, cost-or-None).

    A solo star heads the table with its price ("300K | MA | ST | ..."); a member
    of a pair heads it plainly ("MA | ST | ..."), the pair being priced once
    elsewhere on the page. Detecting that from the header — rather than assuming
    a fixed column offset — is what keeps a pair's stats from sliding left.
    """
    rs = rows_of(table_html)
    if len(rs) < 2:
        return {}, None
    head, body = rs[0], rs[1]
    cost = None
    if head and head[0].strip().upper() != "MA":
        cost = head[0].strip().upper().replace(" ", "") if is_cost(head[0]) else None
        head, body = head[1:], body[1:]
    stats = {}
    # strict: a header/body length mismatch IS the misalignment this function
    # exists to catch. Raising sends the star to the FAIL list; being lenient
    # would hand back a plausible, wrong statline.
    for h, v in zip(head, body, strict=True):
        key = h.strip().upper()
        if key in STATS:
            stats[key] = v.strip()
    return stats, cost


def parse_star(url: str) -> dict | None:
    page = fetch(url)
    m = re.search(r"(?is)<h1[^>]*>(.*?)</h1>", page)
    if not m:
        return None
    name = clean(m.group(1))
    body = page[m.end() :]

    # A pair prices itself once, in a bare <p><strong>250K</strong></p> ahead of
    # the per-member tables.
    pair_cost = None
    mp = re.search(r"(?is)<p[^>]*>\s*<strong>\s*(\d+\s*K)\s*</strong>\s*</p>", body)
    if mp:
        pair_cost = mp.group(1).replace(" ", "").upper()

    members = []
    for tbl in tables_of(body):
        stats, cost = parse_statline(tbl)
        if not stats:
            continue
        after = body[body.find(tbl) + len(tbl) :]
        role = None
        mr = re.search(r"(?is)<p[^>]*>\s*<em>\s*\(([^)]*)\)\s*</em>", after)
        if mr:
            role = clean(mr.group(1))
        skills, specials = [], []
        mu = re.search(r"(?is)<ul[^>]*>(.*?)</ul>", after)
        if mu:
            for li in re.findall(r"(?is)<li[^>]*>(.*?)</li>", mu.group(1)):
                text = clean(li)
                if not text:
                    continue
                # A <strong> item is the star's own named rule, not a skill from
                # the common list; its prose follows the <ul>.
                (specials if re.search(r"(?is)<strong>", li) else skills).append(text)
        members.append(
            {
                "name": name,
                "role": role,
                "stats": stats,
                "cost": cost,
                "skills": skills,
                "special_rules": specials,
            }
        )

    if not members:
        return None

    # Sub-headed members (a pair) carry their own names.
    subs = re.findall(r'(?is)<h3[^>]*\bid="([^"]+)"[^>]*>(.*?)</h3>', body)
    sub_names = [clean(t) for sid, t in subs if sid not in ("plays-for", "accept-to-play-for")]
    if len(members) > 1 and len(sub_names) >= len(members):
        for member, sub in zip(members, sub_names[: len(members)], strict=True):
            member["name"] = sub

    # The prose for every named rule on the page, keyed by rule name.
    rule_text = {}
    for mrule in re.finditer(r"(?is)<li[^>]*>\s*<strong>(.*?)</strong>\s*</li>\s*</ul>\s*<p[^>]*>(.*?)</p>", body):
        rule_text[clean(mrule.group(1))] = clean(mrule.group(2))

    sec = sections(page)
    cost = members[0].get("cost") or pair_cost
    return {
        "name": name,
        "cost": cost,
        "members": members,
        "rule_text": rule_text,
        "plays_for": sec.get("plays-for", []),
        "teams": [t for t in sec.get("accept-to-play-for", [])],
        "source": url,
    }


SITEMAP = "https://bloodbowlbase.ru/bb2025/sitemap.xml"


def site_urls(kind: str) -> list[str]:
    """Every /teams/ or /starplayers/ page, from the sitemap, index page dropped.

    The sitemap is the enumeration of record: the site's own nav is JS-rendered,
    so link-scraping the index finds nothing.
    """
    req = urllib.request.Request(SITEMAP, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        xml = r.read().decode("utf-8", errors="replace")
    urls = re.findall(r"<loc>(.*?)</loc>", xml)
    want = [u for u in urls if f"/{kind}/" in u]
    return sorted(u for u in want if not u.rstrip("/").endswith(f"/{kind}"))


def main() -> int:
    urls = site_urls("teams")
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
        print(
            f"  {t['name']:24} tier={t['tier']}  {len(t['positionals'])} positionals  "
            f"reroll={t['reroll_cost']}  {len(t['staff'])} staff  {len(t['star_players'])} stars"
        )

    stars = []
    for u in site_urls("starplayers"):
        try:
            s = parse_star(u)
        except Exception as exc:  # noqa: BLE001
            failed.append((u, f"{type(exc).__name__}: {exc}"))
            print(f"  FAIL {u} — {exc}", file=sys.stderr)
            continue
        if s is None:
            failed.append((u, "no statline"))
            print(f"  FAIL {u} — no statline", file=sys.stderr)
            continue
        stars.append(s)
        who = f"{len(s['members'])} members" if len(s["members"]) > 1 else (s["members"][0]["role"] or "")
        print(f"  ★ {s['name']:34} {s['cost'] or '?':>6}  {who}")

    teams.sort(key=lambda t: t["name"])
    stars.sort(key=lambda s: s["name"])
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
        "stars": stars,
    }
    (OUT).write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(f"\n{len(teams)} teams, {len(stars)} stars -> {OUT.relative_to(HERE)} ({OUT.stat().st_size // 1024} KB)")

    # Coverage, printed rather than assumed. Staff read 0/30 for a whole release
    # because nothing ever said out loud how many teams had it.
    no_staff = [t["name"] for t in teams if not t["staff"]]
    no_reroll = [t["name"] for t in teams if not t["reroll_cost"]]
    no_cost = [s["name"] for s in stars if not s["cost"]]
    short = [f"{s['name']}/{m['name']}" for s in stars for m in s["members"] if len(m["stats"]) != len(STATS)]
    print(f"  staff missing:      {len(no_staff)} {no_staff}")
    print(f"  re-roll missing:    {len(no_reroll)} {no_reroll}")
    print(f"  star cost missing:  {len(no_cost)} {no_cost}")
    print(f"  incomplete statline:{len(short)} {short}")

    if failed:
        print(f"{len(failed)} FAILED:")
        for u, why in failed:
            print("  ", u, "—", why)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
