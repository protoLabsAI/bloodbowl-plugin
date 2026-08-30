"""Importing a FUMBBL team into a Team Draft List.

FUMBBL is where Blood Bowl is actually played online, and a coach who has a team
there does not want to retype it. `https://fumbbl.com/api/team/get/<id>` returns
that team as JSON; this module turns one into the draft list `draft.py` already
knows how to cost, check and place.

NOTHING HERE TOUCHES THE NETWORK, and that is deliberate rather than incidental.
The plugin declares `network: []` and says so in its README; an importer that
fetched would spend that claim for a convenience the coach can supply with a
copy-paste. So the JSON arrives as an argument. The hard part was never the
fetch — it is the naming, below — and that part is identical either way.

⚠️ THE TWO CATALOGUES DISAGREE ABOUT NAMES, IN BOTH DIRECTIONS AT ONCE.
Measured against four real teams, not imagined:

    FUMBBL                      ours (S3)          what moved
    Underworld Troll         →  Troll*             their prefix, not ours
    Blitzer        (Dwarf)   →  Dwarf Blitzer      our prefix, not theirs
    Norse Raider Lineman     →  Norse Raider       "Lineman" added
    Underworld Snotlings     →  Snotling Lineman   "Lineman" dropped, AND plural
    Dwarf Blocker Lineman    →  Dwarf Lineman      a word inserted in the middle

So there is no strip rule and no append rule — every one of those breaks another
line in the table. What works is normalising both sides and matching within the
TEAM, which is also what makes it safe: Dwarf's `Troll Slayer` and Underworld's
`Troll*` are one edit apart and belong to different teams, so a match scoped to
the identified roster can never confuse them. A global name index could.

⚠️ AND AN UNMATCHED PLAYER IS NAMED, NEVER GUESSED OR DROPPED. Putting the wrong
positional on the board gives a coach the wrong STATS, silently, in the one place
they are trusting a table instead of their memory — the exact failure this plugin
exists to prevent. An ambiguous name reports its candidates and stays unmatched;
the resulting list is then genuinely short, `draft.problems()` says so in the
rulebook's own terms, and nothing pretends the import was clean.
"""

from __future__ import annotations

import re

from . import draft

#: FUMBBL's `ruleset` id for the edition a team was built under. Ours is S3 /
#: BB2025 throughout, so an older team maps by NAME and may differ in stats,
#: costs and skills. Reported, never silently reconciled.
KNOWN_RULESETS = {4: "BB2020"}

#: Words that carry no identity of their own. They are dropped only as a LAST
#: resort, because "Lineman" is the whole difference between two of our
#: positionals on some rosters and pure noise on others.
FILLER = {"lineman", "linemen"}


def _norm(text: str) -> str:
    """Lowercase, strip the Big Guy asterisk, drop punctuation, collapse space.

    The trailing `*` on `Troll*` is the source page's Big Guy marker — the same
    fact is already in `role`, so it is decoration in the name and would only
    ever block a match.
    """
    s = str(text or "").lower().replace("*", " ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _tokens(text: str) -> set[str]:
    """Words, singularised. `Snotlings` and `Snotling` are the same positional."""
    out = set()
    for word in _norm(text).split():
        # Crude on purpose: the only plurals in either catalogue are regular ones
        # ("Snotlings"), and a real stemmer would start folding "Runner"/"Runners"
        # into things it should not.
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        out.add(word)
    return out


def find_team(roster_name: str) -> str | None:
    """Our team whose name is FUMBBL's.

    `pitch.find_team` is already the forgiving one ("amazons" / "wood elves"), so
    this defers to it rather than growing a second team matcher to drift from it.
    A FUMBBL roster we do not ship — Chaos Pact, Slann — resolves to nothing and
    is REPORTED; there is no nearest-team fallback, because importing a coach's
    Chaos Pact side as Chaos Renegades would hand them a roster they never picked.
    """
    from .pitch import find_team as _find

    t = _find(str(roster_name or ""))
    return t["name"] if t else None


def match_position(fumbbl_name: str, positionals: list[dict], team_name: str) -> tuple[str | None, str, list[str]]:
    """One FUMBBL position → one of ours. Returns (position, how, candidates).

    Three passes, each needing exactly ONE winner. A pass that finds several has
    found an ambiguity, and the honest answer to an ambiguity is to say so — the
    next pass is looser, so letting it run on would only pick more confidently
    from a set we already know we cannot separate.
    """
    ours = [p["position"] for p in positionals]
    team_words = _tokens(team_name)

    # 1. The names simply agree.
    exact = [p for p in ours if _norm(p) == _norm(fumbbl_name)]
    if len(exact) == 1:
        return exact[0], "exact", exact

    # 2. They agree once the team's own name is out of the way — this is the
    #    prefix disagreement, and it runs in both directions at once.
    def bare(name: str) -> set[str]:
        return _tokens(name) - team_words

    want = bare(fumbbl_name)
    if want:
        hits = [p for p in ours if bare(p) == want]
        if len(hits) == 1:
            return hits[0], "team name differs", hits
        if len(hits) > 1:
            return None, "ambiguous", hits

    # 3. One is the other plus filler — "Norse Raider" inside "Norse Raider
    #    Lineman", "Snotling" inside "Snotling Lineman".
    want_core = want - FILLER
    if want_core:
        hits = [p for p in ours if (bare(p) - FILLER) == want_core]
        if len(hits) == 1:
            return hits[0], "wording differs", hits
        if len(hits) > 1:
            return None, "ambiguous", hits

    # 4. OUR whole name sits inside theirs. This is the edition rename — BB2020's
    #    "Dwarf Blocker Lineman" is S3's "Dwarf Lineman", and the extra word is
    #    not filler and is not in `role` either, so nothing above can see it.
    #
    #    THE LOOSEST PASS, AND SAFE ONLY BECAUSE AMBIGUITY IS REPORTED: on a
    #    roster carrying both "Lineman" and "Something Lineman" this finds two
    #    and refuses, which is the right answer. It is reported as a partial
    #    match so a coach can check the one match they might want to argue with.
    if want:
        hits = [p for p in ours if bare(p) and bare(p) < want]
        if len(hits) == 1:
            return hits[0], "partial — theirs carries an extra word", hits
        if len(hits) > 1:
            return None, "ambiguous", hits

    return None, "no match", []


def import_team(payload: dict, name: str = "") -> dict:
    """A FUMBBL team JSON → a draft list, plus an honest account of what did not map."""
    if not isinstance(payload, dict) or not payload.get("roster"):
        return {
            "ok": False,
            "text": "that does not look like a FUMBBL team — expected the JSON from /api/team/get/<id>",
            "roster": None,
            "matched": [],
            "unmatched": [],
            "notes": [],
        }

    fumbbl_roster = str((payload.get("roster") or {}).get("name") or "")
    team = find_team(fumbbl_roster)
    notes: list[str] = []

    ruleset = payload.get("ruleset")
    if ruleset is not None and ruleset in KNOWN_RULESETS:
        notes.append(
            f"Built under {KNOWN_RULESETS[ruleset]}; this engine plays S3 (BB2025). "
            "Players are matched by NAME — stats, costs and skills come from the S3 roster."
        )

    if team is None:
        return {
            "ok": False,
            "text": f"no S3 team is named {fumbbl_roster!r} — the 30 shipped rosters are in bb_list_teams",
            "roster": None,
            "source": _source(payload),
            "matched": [],
            "unmatched": [],
            "notes": notes,
        }

    opts = draft.team_options(team) or {}
    positionals = opts.get("positionals") or []

    # Count by position rather than per player: a draft list is "how many of
    # each", and FUMBBL's per-player identity (name, number, SPP) is a LEAGUE
    # fact that an exhibition match has nowhere to put.
    counts: dict[str, int] = {}
    order: list[str] = []
    skipped: dict[int, int] = {}
    for p in payload.get("players") or []:
        # 0 is the active status. What the others MEAN is FUMBBL's business and
        # is not documented here, so they are skipped and COUNTED BY CODE rather
        # than explained — a guess at "journeyman" that turned out to be "missing
        # next game" would be exactly the confident-and-wrong gloss this plugin
        # exists to avoid. A real Wood Elf team turned up two of status 6.
        status = _int(p.get("status"))
        if status != 0:
            skipped[status] = skipped.get(status, 0) + 1
            continue
        pos = str(p.get("position") or "")
        if pos not in counts:
            order.append(pos)
        counts[pos] = counts.get(pos, 0) + 1

    matched, unmatched, players = [], [], {}
    for pos in order:
        n = counts[pos]
        ours, how, candidates = match_position(pos, positionals, team)
        if ours is None:
            unmatched.append(
                {
                    "fumbbl": pos,
                    "n": n,
                    "why": (
                        f"several {team} positionals fit: {', '.join(candidates)}"
                        if how == "ambiguous"
                        else f"no {team} positional matches"
                    ),
                }
            )
            continue
        matched.append({"fumbbl": pos, "position": ours, "n": n, "how": how})
        players[ours] = players.get(ours, 0) + n

    roster = {
        # A FUMBBL team may be called "!!!" or be named entirely in symbols, and
        # `draft.save` slugs the name and REFUSES an empty one — so taking the
        # team's own name on trust turned `save=True` into an unhandled
        # ValueError (a 500 from the route). Fall back to the roster, which is
        # always a real team name.
        "name": _name_for(name, payload, fumbbl_roster),
        "team": team,
        "players": players,
        # Every one of these is already an INPUT this engine takes and states a
        # default for — the Team Re-roll count, and the roster numbers two of the
        # Kick-off Events add to a D6. A real team is simply a better source for
        # them than a default.
        "rerolls": _int(payload.get("rerolls")),
        "coaches": _int(payload.get("assistantCoaches")),
        "cheerleaders": _int(payload.get("cheerleaders")),
        "apothecary": str(payload.get("apothecary") or "").strip().lower() in ("yes", "true", "1"),
        "fans": max(draft.MIN_FANS, _int(payload.get("fanFactor"), draft.MIN_FANS)),
    }

    if skipped:
        notes.append(
            "Skipped "
            + ", ".join(f"{n} player(s) with FUMBBL status {code}" for code, n in sorted(skipped.items()))
            + " — only status 0 was taken. What the other codes mean is not documented here."
        )
    if unmatched:
        short = sum(u["n"] for u in unmatched)
        notes.append(
            f"{short} player(s) did not map and are NOT on the list — it is that many short. "
            "They are named in `unmatched`; add them by hand if the right positional is obvious to you."
        )
    if any(m["how"].startswith("partial") for m in matched):
        notes.append("Some positions matched only partly — check the `how` on each before playing them.")
    # An ESTABLISHED team legitimately breaks the drafting rules, and saying so
    # is the difference between a useful warning and a wrong one: Dedicated Fans
    # cap at 3 WHEN DRAFTING and grow past it in a league, so `problems` will
    # report a five-fan team that is entirely legal where it came from.
    if roster["fans"] > draft.MAX_FANS_AT_DRAFT or _int(payload.get("teamValue")) > draft.DEFAULT_BUDGET:
        notes.append(
            "This is a played team, so it may break DRAFTING limits it was never subject to "
            "(Dedicated Fans above 3, a Team Value above the draft budget). `problems` reports "
            "them against the draft rules; they are not import errors."
        )

    return {
        "ok": True,
        "team": team,
        "source": _source(payload),
        "roster": roster,
        "matched": matched,
        "unmatched": unmatched,
        "notes": notes,
        # The draft list is deliberately returned even when it breaks rules. A
        # team is over budget and short of players for most of the time it is
        # being drafted, and an import that refused would be unusable — the board
        # is where the line is drawn, not here.
        "problems": draft.problems(roster),
        "price": draft.price(roster),
    }


def _name_for(given: str, payload: dict, fumbbl_roster: str) -> str:
    """The first candidate that survives being slugged into a filename."""
    from .draft import slug

    for candidate in (given, payload.get("name"), fumbbl_roster):
        text = str(candidate or "").strip()
        if text and slug(text):
            return text
    return "imported team"


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _source(payload: dict) -> dict:
    """Where the list came from, kept so an imported roster can be told from a drafted one."""
    return {
        "fumbbl_team_id": payload.get("id"),
        "name": payload.get("name"),
        "roster": (payload.get("roster") or {}).get("name"),
        "coach": (payload.get("coach") or {}).get("name"),
        "ruleset": payload.get("ruleset"),
        "division": payload.get("division"),
        "team_value": payload.get("teamValue"),
    }
