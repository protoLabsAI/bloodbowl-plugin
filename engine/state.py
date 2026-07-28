"""Match state, and the fold that rebuilds it from a log.

The setup board (``pitch.Scenario``) stays exactly as it was — permissive, an
illegal position is a legitimate thing to want while working a shape out. A Match
is the strict half: once a game starts, the engine refuses illegal actions rather
than reporting them, because a game you can talk your way through is not a game.

State is derived, never edited directly. ``apply`` is the only mutation, and it
takes a recorded Event, so ``fold(events)`` rebuilds any position exactly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..pitch import LENGTH, Player, in_bounds

# Where a player can be. A knocked-out player has to go somewhere, and "off the
# pitch" is three different places with three different rules.
PLACES = ("pitch", "reserves", "knocked_out", "casualty")
UPRIGHT = ("standing", "prone", "stunned")

TURNS_PER_HALF = 8


@dataclass
class PlayerState:
    """A player in a match: who they are, plus everything a turn can change.

    Composes ``Player`` rather than extending it, so the roster identity and the
    square stay in ONE place. Duplicating position into a parallel structure is
    how a board and its engine start disagreeing.
    """

    player: Player
    id: str
    down: str = "standing"  # standing | prone | stunned
    place: str = "pitch"
    ma_used: int = 0
    acted: bool = False  # has taken an action this turn
    # S3 status: a Standing player that has lost its Tackle Zone. Separate from
    # `down` because such a player is still standing for every other purpose.
    distracted: bool = False
    # The Dodge Skill re-rolls once per TURN, not per activation.
    dodge_reroll_used: bool = False

    @property
    def side(self) -> str:
        return self.player.side

    @property
    def x(self) -> int:
        return self.player.x

    @property
    def y(self) -> int:
        return self.player.y

    def move_to(self, x: int, y: int) -> None:
        self.player.x, self.player.y = x, y

    def movement(self) -> int:
        """MA as a number. Roster values are strings and a missing one must not
        silently become a player who cannot move."""
        try:
            return int(str(self.player.MA).strip() or 0)
        except ValueError:
            return 0

    def name(self) -> str:
        """What to call this player in the log.

        A board built from a preset holds labelled TOKENS with no positional, so
        `position` is empty for them — and "Touchback:  is given the ball" is a
        sentence with a hole in it. Falls back through label to id so a log line
        always names somebody.
        """
        return self.player.position or self.player.label or self.id

    def has_skill(self, name: str) -> bool:
        want = name.casefold()
        return any(s.casefold().startswith(want) for s in (self.player.skills or []))

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "down": self.down,
            "place": self.place,
            "ma_used": self.ma_used,
            "acted": self.acted,
            "distracted": self.distracted,
            "movement": self.movement(),
        }
        d.update(
            {
                "side": self.player.side,
                "x": self.player.x,
                "y": self.player.y,
                "position": self.player.position,
                "team": self.player.team,
                "badge": self.player.badge(),
                "MA": self.player.MA,
                "ST": self.player.ST,
                "AG": self.player.AG,
                "PA": self.player.PA,
                "AV": self.player.AV,
                "skills": list(self.player.skills or []),
            }
        )
        return d


@dataclass
class Ball:
    x: int = 0
    y: int = 0
    carrier: str = ""  # player id, empty when loose
    in_play: bool = False

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "carrier": self.carrier, "in_play": self.in_play}

    @classmethod
    def from_dict(cls, d: dict) -> Ball:
        return cls(
            x=int(d.get("x") or 0),
            y=int(d.get("y") or 0),
            carrier=str(d.get("carrier") or ""),
            in_play=bool(d.get("in_play")),
        )


@dataclass
class Clock:
    half: int = 1
    turn: int = 1  # 1..8 within the half, for the active team
    active: str = "home"

    def to_dict(self) -> dict:
        return {"half": self.half, "turn": self.turn, "active": self.active, "turns_per_half": TURNS_PER_HALF}

    @classmethod
    def from_dict(cls, d: dict) -> Clock:
        return cls(
            half=int(d.get("half") or 1),
            turn=int(d.get("turn") or 1),
            active=str(d.get("active") or "home"),
        )


@dataclass
class Match:
    """One game in progress. Mutated only through ``apply``."""

    name: str = "Match"
    home_team: str = ""
    away_team: str = ""
    seed: int = 0
    players: list[PlayerState] = field(default_factory=list)
    ball: Ball = field(default_factory=Ball)
    clock: Clock = field(default_factory=Clock)
    score: dict = field(default_factory=lambda: {"home": 0, "away": 0})
    over: bool = False
    drive: int = 0
    # Where everyone stood when this drive kicked off. A new drive puts the teams
    # back rather than asking the operator to rebuild the board after every score.
    setup: list = field(default_factory=list)
    events: list = field(default_factory=list)

    # --- lookup -----------------------------------------------------------

    def by_id(self, pid: str) -> PlayerState | None:
        return next((p for p in self.players if p.id == pid), None)

    def at(self, x: int, y: int) -> PlayerState | None:
        """Who is standing on a square. Only players ON the pitch occupy one —
        a player in the KO box does not block a square they used to be on."""
        return next((p for p in self.players if p.place == "pitch" and p.x == x and p.y == y), None)

    def on_pitch(self, side: str | None = None) -> list[PlayerState]:
        return [p for p in self.players if p.place == "pitch" and (side is None or p.side == side)]

    def opponent(self, side: str) -> str:
        return "away" if side == "home" else "home"

    def carrier(self) -> PlayerState | None:
        return self.by_id(self.ball.carrier) if self.ball.carrier else None

    # --- the only mutation ------------------------------------------------

    def apply(self, event) -> None:
        """Apply one recorded fact. No judgement, no dice — the decision was
        already made and frozen when the event was created. This is what lets
        ``fold`` rebuild a match under rules that have since changed."""
        d = event.detail
        kind = event.kind

        if kind == "match_started":
            self.clock = Clock(half=1, turn=1, active=str(d.get("kicking_to") or "home"))

        elif kind == "turn_started":
            self.clock.active = str(d.get("side") or self.clock.active)
            self.clock.half = int(d.get("half") or self.clock.half)
            self.clock.turn = int(d.get("turn") or self.clock.turn)
            for p in self.players:
                if p.side == self.clock.active:
                    p.ma_used = 0
                    p.acted = False
                    p.dodge_reroll_used = False

        elif kind == "player_moved":
            p = self.by_id(event.actor)
            if p is not None:
                p.move_to(int(d["x"]), int(d["y"]))
                p.ma_used = int(d.get("ma_used", p.ma_used))
                p.acted = True
                if self.ball.carrier == p.id:
                    self.ball.x, self.ball.y = p.x, p.y

        elif kind == "player_stood_up":
            p = self.by_id(event.actor)
            if p is not None:
                p.down = "standing"
                p.ma_used = int(d.get("ma_used", p.ma_used))

        elif kind == "activation_ended":
            # An Action that is over. Recorded rather than set on the player,
            # because `acted` is state and ALL state here comes from the log —
            # the actions used to assign it directly and emit a note saying so,
            # which meant a folded match had everyone free to act again.
            p = self.by_id(event.actor)
            if p is not None:
                p.acted = True

        elif kind in ("player_fell", "player_placed_prone"):
            p = self.by_id(event.actor)
            if p is not None:
                p.down = str(d.get("down") or "prone")
                p.acted = True

        elif kind in ("player_pushed", "player_followed_up"):
            # A shove and a follow-up relocate a player identically; only the
            # reason differs, and the log already records that.
            p = self.by_id(event.actor)
            if p is not None:
                p.move_to(int(d["x"]), int(d["y"]))
                if self.ball.carrier == p.id:
                    self.ball.x, self.ball.y = p.x, p.y

        elif kind == "player_condition":
            p = self.by_id(event.actor)
            if p is not None:
                outcome = str(d.get("outcome") or "")
                if outcome == "stunned":
                    p.down = "stunned"
                elif outcome in ("knocked_out", "casualty"):
                    # Leaving the pitch drops the ball where they stood; the box
                    # they go to is not a square, so they must stop occupying one.
                    p.place = "knocked_out" if outcome == "knocked_out" else "casualty"
                    p.down = "prone"
                    if self.ball.carrier == p.id:
                        self.ball.carrier = ""

        elif kind == "ball_moved":
            self.ball.x, self.ball.y = int(d["x"]), int(d["y"])
            self.ball.carrier = str(d.get("carrier") or "")
            self.ball.in_play = True

        elif kind == "ball_picked_up":
            self.ball.carrier = event.actor
            p = self.by_id(event.actor)
            if p is not None:
                self.ball.x, self.ball.y = p.x, p.y

        elif kind == "ball_dropped":
            self.ball.carrier = ""
            self.ball.x, self.ball.y = int(d.get("x", self.ball.x)), int(d.get("y", self.ball.y))

        elif kind == "drive_started":
            self.drive = int(d.get("drive") or self.drive + 1)
            self.setup = [dict(row) for row in (d.get("setup") or [])]
            for row in self.setup:
                p = self.by_id(str(row.get("id") or ""))
                if p is None:
                    continue
                p.move_to(int(row["x"]), int(row["y"]))
                p.down = "standing"
                p.place = "pitch"
                p.ma_used, p.acted, p.dodge_reroll_used = 0, False, False
            # A Knocked-out player misses the drive; a Casualty misses the match.
            for p in self.players:
                if p.place == "knocked_out" and not any(r.get("id") == p.id for r in self.setup):
                    p.place = "reserves"

        elif kind == "clock_adjusted":
            delta = int(d.get("delta") or 0)
            self.clock.turn = max(1, min(TURNS_PER_HALF, self.clock.turn + delta))

        elif kind == "half_time":
            self.clock.half = int(d.get("half") or self.clock.half + 1)
            self.clock.turn = 1

        elif kind == "match_over":
            self.over = True

        elif kind == "touchdown":
            # Scoring is a Turnover and the end of a Drive. The ball leaves play
            # until the next kick-off, so nothing downstream can keep scoring with
            # a ball that is notionally in someone's hands in an End Zone.
            side = str(d.get("side") or self.clock.active)
            self.score[side] = self.score.get(side, 0) + 1
            self.ball = Ball()

        elif kind == "ball_out_of_bounds":
            self.ball.carrier = ""
            self.ball.x, self.ball.y = int(d.get("x", self.ball.x)), int(d.get("y", self.ball.y))

        elif kind == "turnover":
            for p in self.players:
                if p.side == self.clock.active:
                    p.acted = True

        elif kind == "turn_ended":
            self._advance_clock()

        self.events.append(event)

    def _advance_clock(self) -> None:
        nxt = self.opponent(self.clock.active)
        # A half ends once BOTH sides have had their eight turns, which is why the
        # turn number advances on the home side only.
        if nxt == "home":
            if self.clock.turn >= TURNS_PER_HALF:
                if self.clock.half >= 2:
                    self.over = True
                    return
                self.clock.half += 1
                self.clock.turn = 1
            else:
                self.clock.turn += 1
        self.clock.active = nxt

    # --- serialization ----------------------------------------------------

    def to_dict(self, include_log: bool = True) -> dict:
        d = {
            "name": self.name,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "seed": self.seed,
            "players": [p.to_dict() for p in self.players],
            "ball": self.ball.to_dict(),
            "clock": self.clock.to_dict(),
            "score": dict(self.score),
            "over": self.over,
            "drive": self.drive,
        }
        if include_log:
            d["events"] = [e.to_dict() for e in self.events]
        return d

    @classmethod
    def from_dict(cls, data: dict) -> Match:
        """Rebuild from a saved match.

        The LOG is the truth. Everything else in the file is a cache of the fold,
        so when a log is present the position is recomputed from it rather than
        trusted — a hand-edited or half-written board cannot then disagree with
        its own history.
        """
        from .events import Event

        m = cls(
            name=str(data.get("name") or "Match"),
            home_team=str(data.get("home_team") or ""),
            away_team=str(data.get("away_team") or ""),
            seed=int(data.get("seed") or 0),
        )
        known = set(Player.__dataclass_fields__)  # type: ignore[attr-defined]
        for i, raw in enumerate(data.get("players") or []):
            player = Player(**{k: v for k, v in raw.items() if k in known})
            m.players.append(
                PlayerState(
                    player=player,
                    id=str(raw.get("id") or f"p{i}"),
                    down=str(raw.get("down") or "standing"),
                    place=str(raw.get("place") or "pitch"),
                    ma_used=int(raw.get("ma_used") or 0),
                    acted=bool(raw.get("acted")),
                )
            )

        events = [Event.from_dict(e) for e in (data.get("events") or [])]
        if events:
            return fold(m, events)

        m.ball = Ball.from_dict(data.get("ball") or {})
        m.clock = Clock.from_dict(data.get("clock") or {})
        m.score = dict(data.get("score") or {"home": 0, "away": 0})
        m.over = bool(data.get("over"))
        return m


def fold(match: Match, events: list) -> Match:
    """Re-watch a match: apply every recorded fact to a starting position.

    THE replay mechanism. It rolls no dice and consults no rule, so a match
    recorded under older rules rebuilds exactly as it was played. Anything that
    needs judgement belongs in an action's resolve, never here.
    """
    # Rebuilding means starting from the log's own beginning, not appending to
    # whatever the caller had.
    match.events = []
    for e in events:
        match.apply(e)
    return match


def starting_positions(scenario, seed: int = 0) -> Match:
    """Begin a match from a set-up practice board.

    Player ids are assigned by a stable ordering rather than by list position, so
    that two boards holding the same players in a different order start the same
    match — an id that shifts with list order would make replays position-
    dependent, which is precisely the drift the log exists to prevent.
    """
    m = Match(
        name=scenario.name or "Match",
        home_team=scenario.home_team,
        away_team=scenario.away_team,
        seed=seed,
    )
    # Numbered WITHIN each side, so h00 is the first home player. Numbering across
    # a combined list makes the first home player h01 whenever an away player
    # happens to sort ahead of it — stable, but a coach reading it aloud would be
    # right to think something was wrong.
    for side in ("home", "away"):
        mine = sorted(
            (p for p in scenario.players if p.side == side and in_bounds(p.x, p.y)),
            key=lambda p: (p.y, p.x, p.position),
        )
        for i, p in enumerate(mine):
            m.players.append(PlayerState(player=p, id=f"{side[0]}{i:02d}"))
    return m


def touchdown_row(side: str) -> int:
    """The row a side scores in — the opponent's End Zone."""
    return LENGTH if side == "home" else 1
