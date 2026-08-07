"""Drafting a team: the Team Draft List, costed and checked against the S3 rules.

Every limit below is QUOTED from the rulebook rather than recalled, because this is
exactly the kind of thing recall gets subtly wrong (the plugin exists partly to route
around that). The team-specific numbers — what a positional costs, how many you may take,
what a Team Re-roll costs, whether an Apothecary is available — are not here at all: they
live in `data/rosters.json`, scraped per team.

A saved roster is USER DATA and lives beside the board in the state dir, never in the
repo. It is a draft list, not a board: it says who is on the team, not where they stand.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

#: "you will usually have a Team Draft Budget of 1,000,000 gold pieces - this is the most
#: common value for a rookie team". Usually, so it is an input with a stated default
#: rather than a constant — Exhibition and Matched Play name their own.
DEFAULT_BUDGET = 1_000_000

#: "A team must have at least 11 players on their Team Draft List when it is first
#: drafted." / "A team may never have more than 16 players on their Team Draft List."
MIN_PLAYERS, MAX_PLAYERS = 11, 16

#: "Teams may purchase a maximum of 8 Team Re-rolls, though they may never have more than
#: 8 Team Re-rolls on their Team Draft Roster." Cost is per team (`reroll_cost`).
MAX_REROLLS = 8

#: "A team may hire up to a maximum of 6 Assistant Coaches. Each Assistant Coach costs
#: 10,000 gold pieces" — and the same sentence again for Cheerleaders.
MAX_COACHES = MAX_CHEERLEADERS = 6
COACH_COST = CHEERLEADER_COST = 10_000

#: "A team can only ever have a single Apothecary … An Apothecary costs a team 50,000 gold
#: pieces to hire." Availability is per team ("Whether or not a team can or cannot hire an
#: Apothecary will be listed in its Team Roster").
APOTHECARY_COST = 50_000

#: "Every team starts with a Dedicated Fans Characteristic of 1 … you may improve [it] up
#: to a maximum of 3 … at the cost of 5,000 gold pieces per Dedicated Fan improvement."
MIN_FANS, MAX_FANS_AT_DRAFT, FAN_COST = 1, 3, 5_000


def gold(text) -> int:
    """`"140K"` → 140000. The roster stores costs as the rulebook prints them."""
    if isinstance(text, (int, float)):
        return int(text)
    m = re.search(r"([\d,.]+)\s*([kKmM]?)", str(text or ""))
    if not m:
        return 0
    n = float(m.group(1).replace(",", ""))
    return int(n * {"k": 1_000, "m": 1_000_000}.get(m.group(2).lower(), 1))


def qty_limit(text) -> int:
    """`"0-16"` → 16. The maximum of a positional a team may take."""
    nums = re.findall(r"\d+", str(text or ""))
    return int(nums[-1]) if nums else 0


def _team(name: str) -> dict | None:
    from .pitch import find_team

    return find_team(name)


def team_options(name: str) -> dict | None:
    """Everything a coach may spend gold on for this team, with the limits that apply.

    One shape for the builder AND the validator, so the UI cannot offer something the
    checker would then refuse.
    """
    t = _team(name)
    if t is None:
        return None
    staff = {k.lower(): gold(v) for k, v in (t.get("staff") or {}).items()}
    return {
        "team": t["name"],
        "tier": t.get("tier"),
        "special_rules": t.get("special_rules") or [],
        "positionals": [
            {
                "position": p["position"],
                "max": qty_limit(p.get("qty")),
                "cost": gold(p.get("cost")),
                "role": p.get("role") or "",
                "MA": p.get("MA"),
                "ST": p.get("ST"),
                "AG": p.get("AG"),
                "PA": p.get("PA"),
                "AV": p.get("AV"),
                "skills": p.get("skills") or [],
            }
            for p in t.get("positionals") or []
        ],
        "reroll_cost": gold(t.get("reroll_cost")),
        "apothecary_allowed": "apothecary" in staff,
        "costs": {
            "apothecary": staff.get("apothecary", APOTHECARY_COST),
            "coach": staff.get("assistant coach", COACH_COST),
            "cheerleader": staff.get("cheerleader", CHEERLEADER_COST),
            "dedicated_fan": FAN_COST,
        },
        "limits": {
            "players": [MIN_PLAYERS, MAX_PLAYERS],
            "rerolls": MAX_REROLLS,
            "coaches": MAX_COACHES,
            "cheerleaders": MAX_CHEERLEADERS,
            "fans": [MIN_FANS, MAX_FANS_AT_DRAFT],
        },
        "default_budget": DEFAULT_BUDGET,
    }


def price(roster: dict) -> dict:
    """What a draft list costs, itemised. Unknown teams cost nothing and fail validation."""
    opts = team_options(str(roster.get("team") or ""))
    if opts is None:
        return {"total": 0, "lines": [], "budget": int(roster.get("budget") or DEFAULT_BUDGET), "treasury": 0}
    by_position = {p["position"]: p for p in opts["positionals"]}
    lines = []
    for position, n in (roster.get("players") or {}).items():
        n = int(n or 0)
        if n <= 0:
            continue
        each = by_position.get(position, {}).get("cost", 0)
        lines.append({"what": position, "n": n, "each": each, "total": each * n})

    for what, n, each in (
        ("Team Re-roll", int(roster.get("rerolls") or 0), opts["reroll_cost"]),
        ("Assistant Coach", int(roster.get("coaches") or 0), opts["costs"]["coach"]),
        ("Cheerleader", int(roster.get("cheerleaders") or 0), opts["costs"]["cheerleader"]),
        ("Apothecary", 1 if roster.get("apothecary") else 0, opts["costs"]["apothecary"]),
        # Fans are bought as IMPROVEMENTS above the free starting 1, not as a quantity.
        ("Dedicated Fans +1", max(0, int(roster.get("fans") or MIN_FANS) - MIN_FANS), opts["costs"]["dedicated_fan"]),
    ):
        if n > 0:
            lines.append({"what": what, "n": n, "each": each, "total": each * n})

    total = sum(line["total"] for line in lines)
    budget = int(roster.get("budget") or DEFAULT_BUDGET)
    return {"total": total, "lines": lines, "budget": budget, "treasury": budget - total}


def problems(roster: dict) -> list[str]:
    """Every rule this draft list breaks, in the rulebook's own terms. Empty = legal.

    Reported as a LIST rather than a first-failure, because a coach mid-draft is usually
    breaking several at once and fixing them one refusal at a time is miserable.
    """
    opts = team_options(str(roster.get("team") or ""))
    if opts is None:
        return [f"unknown team {roster.get('team')!r}"]
    out: list[str] = []
    by_position = {p["position"]: p for p in opts["positionals"]}
    players = {k: int(v or 0) for k, v in (roster.get("players") or {}).items() if int(v or 0) > 0}

    for position, n in players.items():
        if position not in by_position:
            out.append(f"{opts['team']} has no {position!r}")
        elif n > by_position[position]["max"]:
            out.append(f"{n} × {position} — the roster allows at most {by_position[position]['max']}")

    n = sum(players.values())
    if n < MIN_PLAYERS:
        out.append(f"{n} players — a team must have at least {MIN_PLAYERS} when first drafted")
    if n > MAX_PLAYERS:
        out.append(f"{n} players — a team may never have more than {MAX_PLAYERS}")

    if int(roster.get("rerolls") or 0) > MAX_REROLLS:
        out.append(f"{roster['rerolls']} Team Re-rolls — a team may never have more than {MAX_REROLLS}")
    if int(roster.get("coaches") or 0) > MAX_COACHES:
        out.append(f"{roster['coaches']} Assistant Coaches — at most {MAX_COACHES}")
    if int(roster.get("cheerleaders") or 0) > MAX_CHEERLEADERS:
        out.append(f"{roster['cheerleaders']} Cheerleaders — at most {MAX_CHEERLEADERS}")
    if roster.get("apothecary") and not opts["apothecary_allowed"]:
        out.append(f"{opts['team']} may not hire an Apothecary")
    fans = int(roster.get("fans") or MIN_FANS)
    if not MIN_FANS <= fans <= MAX_FANS_AT_DRAFT:
        out.append(f"Dedicated Fans {fans} — may be improved to at most {MAX_FANS_AT_DRAFT} when drafting")

    p = price(roster)
    if p["treasury"] < 0:
        out.append(f"over budget by {-p['treasury']:,} gold pieces")
    return out


# --- storage -------------------------------------------------------------------------


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")


def rosters_dir() -> Path:
    from .store import state_dir

    d = state_dir() / "rosters"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save(name: str, roster: dict) -> dict:
    key = slug(name)
    if not key:
        raise ValueError("a roster needs a name")
    body = dict(roster)
    body["name"] = str(name)
    (rosters_dir() / f"{key}.json").write_text(json.dumps(body, indent=2), encoding="utf-8")
    return body


def load(name: str) -> dict | None:
    p = rosters_dir() / f"{slug(name)}.json"
    if not p.is_file():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except Exception:  # noqa: BLE001 — a corrupt roster reads as absent, like the board's
        return None


def delete(name: str) -> bool:
    p = rosters_dir() / f"{slug(name)}.json"
    if not p.is_file():
        return False
    p.unlink()
    return True


def saved() -> list[dict]:
    out = []
    for p in sorted(rosters_dir().glob("*.json")):
        d = load(p.stem)
        if d is None:
            continue
        cost = price(d)
        out.append(
            {
                "name": d.get("name") or p.stem,
                "slug": p.stem,
                "team": d.get("team"),
                "players": sum(int(v or 0) for v in (d.get("players") or {}).values()),
                "spent": cost["total"],
                "treasury": cost["treasury"],
                "legal": not problems(d),
            }
        )
    return out


def squad(roster: dict) -> list[str]:
    """The draft list flattened to one position name per player, for placing on a board."""
    out: list[str] = []
    for position, n in (roster.get("players") or {}).items():
        out.extend([position] * int(n or 0))
    return out


#: Which tactical slot a preset square is, normalised. Shipped presets label squares by
#: ROLE IN THE SHAPE ("LOS", "screen", "back", "safety", "corner") rather than by Blood
#: Bowl position, because a shape has to transfer between teams.
_FRONT = ("los", "line", "corner")
_DEEP = ("safety", "back", "deep")


def assign(players: list[dict], slots: list[dict]) -> list[tuple[dict, dict]]:
    """Pair drafted players with the squares of a preset shape.

    **This is a stated DEFAULT, not a recommendation.** Which player stands where is a
    coaching decision and the engine does not make those — but filling a shape in draft
    order would put a ST1 Gnoblar on the Line of Scrimmage next to three idle Ogres, which
    is worse than having an opinion. So:

      * the LINE takes the highest Strength (Armour Value breaks a tie) — the front row
        exists to be hit;
      * DEEP squares take the highest Move Allowance — a safety has to cover ground;
      * everything else fills in the order drafted.

    Any player can be moved afterwards with the board or `bb_pitch_place`; this only
    decides where they start. Returns ``(player, square)`` pairs, shortest-list wins — a
    preset has at most 11 squares and a squad may have 16, and the rest are simply not on
    the pitch, which is what a reserves box is.
    """

    def num(v, default=0):
        try:
            return int(str(v).strip().rstrip("+") or default)
        except ValueError:
            return default

    left = list(players)
    out: list[tuple[dict, dict]] = []
    front = [s for s in slots if str(s.get("label", "")).lower() in _FRONT]
    deep = [s for s in slots if str(s.get("label", "")).lower() in _DEEP]
    rest = [s for s in slots if s not in front and s not in deep]

    for group, key in ((front, lambda p: (num(p.get("ST")), num(p.get("AV")))), (deep, lambda p: num(p.get("MA")))):
        for square in group:
            if not left:
                return out
            pick = max(left, key=key)
            left.remove(pick)
            out.append((pick, square))
    for square in rest:
        if not left:
            break
        out.append((left.pop(0), square))
    return out
