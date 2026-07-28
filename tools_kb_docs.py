#!/usr/bin/env python3
"""Generate one knowledge-base document per team from the parsed roster JSON.

WHY this exists rather than ingesting the team pages directly. The site renders a
roster as a table; the ingest flattens it. Orc's Goblin Lineman row arrives in the
knowledge base as

    Goblin Lineman (Lineman, Goblin) 6 2 3+ 3+ 4+ 8+

— six values for five stats, because the site marks errata by striking the old
value in place and the flattener keeps both. Nothing in that text says which
number is which stat, and "3+" appearing twice makes the wrong reading the
natural one. For an agent whose known failure is confident prose around correct
data, that is the worst possible input.

These documents carry the same facts with every stat LABELLED, generated from the
errata-corrected JSON, so a retrieved passage cannot be misread the way a
flattened row can. Positional data is the tools' job (bb_get_roster and friends
answer exactly); what retrieval adds is the cross-team question no single-team
tool answers — "which teams get Mutation access", "who can take a Troll".

    python tools_kb_docs.py --out build/kb          # write the files
    python tools_kb_docs.py --post http://127.0.0.1:7878 --token "$TOK"
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data" / "rosters.json"
STATS = ("MA", "ST", "AG", "PA", "AV")


def _statline(p: dict) -> str:
    """ "MA 6, ST 2, AG 3+, PA 4+, AV 8+" — labelled, so no column can be misread."""
    return ", ".join(f"{k} {p.get(k, '?')}" for k in STATS)


def team_doc(team: dict, edition: str) -> str:
    out: list[str] = []
    name = team["name"]
    out.append(f"# {name} — Blood Bowl team reference ({edition})")
    out.append("")

    facts = []
    if team.get("tier") is not None:
        facts.append(f"Tier {team['tier']}")
    if team.get("league"):
        facts.append("League: " + ", ".join(team["league"]))
    if team.get("special_rules"):
        facts.append("Special rules: " + ", ".join(team["special_rules"]))
    if facts:
        out.append(f"{name} is a Blood Bowl team. " + ". ".join(facts) + ".")
        out.append("")

    if team.get("reroll_cost"):
        out.append(f"A Team Re-roll for {name} costs {team['reroll_cost']}.")
        out.append("")
    if team.get("staff"):
        out.append(f"## {name} staff costs")
        out.append("")
        for label, cost in team["staff"].items():
            out.append(f"- {label}: {cost}")
        out.append("")

    out.append(f"## {name} positionals")
    out.append("")
    for p in team["positionals"]:
        role = f" ({p['role']})" if p.get("role") else ""
        out.append(f"### {p['position']}{role}")
        out.append("")
        out.append(f"- Team: {name}")
        out.append(f"- Cost: {p.get('cost') or 'unknown'}")
        out.append(f"- Allowed per team: {p.get('qty') or 'unknown'}")
        out.append(f"- Statline: {_statline(p)}")
        skills = p.get("skills") or []
        out.append("- Starting skills: " + (", ".join(skills) if skills else "none"))
        out.append("- Primary skill access: " + (", ".join(p.get("primary") or []) or "none"))
        out.append("- Secondary skill access: " + (", ".join(p.get("secondary") or []) or "none"))
        out.append("")

    if team.get("star_players"):
        out.append(f"## Star Players {name} may hire")
        out.append("")
        for s in team["star_players"]:
            out.append(f"- {s['name']}: {s.get('cost') or 'cost unknown'}")
        out.append("")

    out.append(f"Source: {team.get('source', '')}")
    out.append(
        "Stat values are taken from the parsed roster table with the site's errata "
        "applied, so each figure above is the current one."
    )
    return "\n".join(out).rstrip() + "\n"


def build() -> list[tuple[str, str, str]]:
    """(filename, title, markdown) per team. The filename becomes the knowledge
    base's ``source``, which is what makes a later delete-by-source possible."""
    data = json.loads(DATA.read_text(encoding="utf-8"))
    edition = data.get("edition", "S3")
    docs = []
    for team in data["teams"]:
        slug = team["name"].replace(" ", "_").replace("'", "")
        docs.append(
            (f"bloodbowl-team-{slug}.md", f"{team['name']} — Blood Bowl team reference", team_doc(team, edition))
        )
    return docs


def _post_multipart(base: str, token: str, filename: str, title: str, body: str) -> dict:
    boundary = uuid.uuid4().hex
    ctype = mimetypes.guess_type(filename)[0] or "text/markdown"
    parts: list[bytes] = []

    def field(name: str, value: str) -> None:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())

    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n".encode()
    )
    parts.append(body.encode("utf-8"))
    parts.append(b"\r\n")
    field("title", title)
    field("domain", "bloodbowl")
    parts.append(f"--{boundary}--\r\n".encode())

    req = urllib.request.Request(
        base.rstrip("/") + "/api/knowledge/ingest",
        data=b"".join(parts),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", help="write the documents to this directory")
    ap.add_argument("--post", help="base URL of a running instance to ingest into")
    ap.add_argument("--token", default="", help="bearer token for --post")
    args = ap.parse_args()

    docs = build()
    if args.out:
        d = Path(args.out).expanduser()
        d.mkdir(parents=True, exist_ok=True)
        for filename, _title, body in docs:
            (d / filename).write_text(body, encoding="utf-8")
        print(f"{len(docs)} documents -> {d}")

    if args.post:
        total = 0
        for filename, title, body in docs:
            try:
                res = _post_multipart(args.post, args.token, filename, title, body)
            except Exception as exc:  # noqa: BLE001 — report and keep going
                print(f"  FAIL {filename} — {exc}", file=sys.stderr)
                continue
            n = len(res.get("ids") or [])
            total += n
            print(f"  {filename:44} {n:3} chunks")
        print(f"\n{total} chunks ingested")

    if not args.out and not args.post:
        print(docs[0][2])
    return 0


if __name__ == "__main__":
    sys.exit(main())
