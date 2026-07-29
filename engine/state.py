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
# A Sent-off player is its own place, not a Casualty: "immediately removed from
# the pitch and will play no further part in the game" — no Apothecary, no return
# between drives, and a league would treat the two completely differently.
PLACES = ("pitch", "reserves", "knocked_out", "casualty", "sent_off")
UPRIGHT = ("standing", "prone", "stunned")

TURNS_PER_HALF = 8

# Player flags a `skill_spent` event may set. An allowlist rather than a bare
# setattr, so the log cannot reach into a player and set anything it likes.
ONCE_PER_TURN_FLAGS = ("dodge_reroll_used", "break_tackle_used")


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
    # Two different facts, and conflating them is why a player could Block and
    # then stroll away. ``acted`` means an activation has BEGUN, so no second
    # Action may be declared — a step of movement sets it. ``done`` means the
    # activation is OVER, which is what stops further movement. Most Actions end
    # the activation the moment they resolve; a Blitz's Block is the one that
    # does not, because "after the player has performed the Block Action, they
    # can continue their Move Action".
    acted: bool = False
    done: bool = False
    # The Action this player declared for this activation, if any. Only ever set
    # before `done` by a Blitz, which is the one Action that outlives its own
    # resolution — it is what stops a blitzing player deciding halfway through
    # that they meant to Pass.
    action: str = ""
    # S3 status: a Standing player that has lost its Tackle Zone. Separate from
    # `down` because such a player is still standing for every other purpose.
    # "…they will remain Distracted UNTIL THEY ARE NEXT ACTIVATED" — so a new turn
    # does not clear it; the player's own next activation does.
    distracted: bool = False
    # Take Root: "cannot perform Move Actions, may not Follow-up after performing
    # a Block Action, cannot be Pushed Back, and may not leave their current square
    # for any reason". Cleared at the end of a Drive or by hitting the ground.
    rooted: bool = False
    # Monstrous Mouth: "Whilst Chomped, the opposition player cannot leave the
    # square they are in whilst this player remains Marking them." Holds the
    # CHOMPER's id, because the condition "ends immediately if this player is no
    # longer Marking the opposition player for any reason" — so it has to be
    # re-checked against a live board rather than remembered as a bare flag.
    chomped_by: str = ""
    # Skills that are "Once per Turn" rather than once per activation — the Dodge
    # Skill's re-roll and Break Tackle's modifier. Set through a recorded event
    # (see `skill_spent`), never assigned: they are state, and state that only the
    # live object knows is state a folded match plays without.
    dodge_reroll_used: bool = False
    break_tackle_used: bool = False

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
            "done": self.done,
            "action": self.action,
            "dodge_reroll_used": self.dodge_reroll_used,
            "break_tackle_used": self.break_tackle_used,
            "distracted": self.distracted,
            "rooted": self.rooted,
            "chomped_by": self.chomped_by,
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
    # "At this point the ball is still HIGH UP IN THE AIR and cannot be caught."
    # `in_play` has always meant "the ball is on the board somewhere", which was
    # indistinguishable from "it has landed" only because the kick used to land it
    # in the same call. A Kick-off Event that stops to ask the Coach something
    # holds it up there, and a board that drew it on the square it is heading for
    # says it has arrived.
    in_air: bool = False

    def to_dict(self) -> dict:
        return {
            "x": self.x,
            "y": self.y,
            "carrier": self.carrier,
            "in_play": self.in_play,
            "in_air": self.in_air,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Ball:
        return cls(
            x=int(d.get("x") or 0),
            y=int(d.get("y") or 0),
            carrier=str(d.get("carrier") or ""),
            in_play=bool(d.get("in_play")),
            in_air=bool(d.get("in_air")),
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
    # The Blitz declared this turn, if any: {"player", "target", "blocked"}.
    # A team gets ONE per turn, so this is per-turn state and turn_started clears
    # it. Kept on the Match rather than the player because the limit belongs to
    # the TEAM — a second player declaring one is what has to be refused.
    # Team Re-rolls remaining, per side, and the full complement each half starts
    # with. Two numbers rather than one because "a team will always start each
    # half with its FULL complement" and "unused Team Re-rolls do NOT carry over".
    rerolls: dict = field(default_factory=dict)
    rerolls_max: dict = field(default_factory=dict)
    # Brilliant Coaching grants "a free Team Re-roll FOR THE DRIVE AHEAD", which
    # is a different thing from the ones a team bought — it expires. Kept apart so
    # it can be spent FIRST and simply dropped at the next kick-off; folding it
    # into `rerolls` makes "was the bonus or a bought one spent?" unanswerable.
    drive_rerolls: dict = field(default_factory=dict)
    # Assistant Coaches, Cheerleaders and Fan Factor: roster facts a practice
    # board never bought, so they are inputs with a stated default of zero.
    staff: dict = field(default_factory=dict)
    # The Weather Table result in force. Modifies named rolls through the same
    # hook Skills use, so nothing has to ask the sky twice.
    weather: str = "perfect"
    # "they can use them ONCE PER GAME" — so this is a boolean per side, spent
    # rather than counted, and never replenished at half-time.
    apothecary: dict = field(default_factory=dict)
    # Set-ups declared for the NEXT Drive, per side. "The kicking team must set up
    # first followed by the receiving team", so the order is recorded too.
    setups: dict = field(default_factory=dict)
    # A choice the engine is waiting on. Several Kick-off Events say "the Coach
    # selects…", and the engine cannot block mid-resolution and ask — so it stops,
    # records what it is waiting for, and the coach answers with a second call.
    # Exactly the shape of the Blitz declaration: split the compound thing into
    # steps the coach drives, rather than choosing on their behalf.
    pending: dict = field(default_factory=dict)
    # CHARGE!, the one Kick-off Event that is a free TURN rather than a question.
    # "The selected players may then be activated one at a time, EXACTLY AS IF IT
    # WAS THEIR TEAM'S TURN" — so it borrows the whole action machinery, and the
    # only things that make it not a turn live here: who may act, which of the
    # three one-off Actions are still going, and where the Drive was up to.
    charge: dict = field(default_factory=dict)
    # Cheering Fans: which side's next Turn gets a free Offensive Assist on its
    # first Block, and whether that Turn has begun yet.
    cheer: dict = field(default_factory=dict)
    blitz: dict = field(default_factory=dict)
    # Coaches ejected for arguing: "may not attempt to Argue the Call for the
    # remainder of the game". Per SIDE and per MATCH — one of the few things here
    # that a new turn does not clear.
    argue_banned: list = field(default_factory=list)
    # Which Actions this team has spent this turn: action name -> player id. S3
    # caps most Actions at one per team per Turn ("Only a single Pass Action can
    # be declared each Turn"), with Move and Block named as the exceptions.
    # Cleared by turn_started, like everything else that is per-turn.
    turn_actions: dict = field(default_factory=dict)
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
            full = d.get("rerolls") or {}
            self.rerolls_max = {"home": int(full.get("home", 0)), "away": int(full.get("away", 0))}
            self.rerolls = dict(self.rerolls_max)
            self.staff = {side: dict(vals) for side, vals in (d.get("staff") or {}).items()}
            self.weather = str(d.get("weather") or "perfect")
            apo = d.get("apothecary") or {}
            self.apothecary = {"home": bool(apo.get("home")), "away": bool(apo.get("away"))}

        elif kind == "turn_started":
            self.clock.active = str(d.get("side") or self.clock.active)
            self.clock.half = int(d.get("half") or self.clock.half)
            self.clock.turn = int(d.get("turn") or self.clock.turn)
            self.blitz = {}
            self.turn_actions = {}  # one Blitz, one Pass, one Foul… per team per turn
            # Cheering Fans applies to "the Coach with the highest roll's NEXT
            # Turn": arm it when that Turn starts, and drop it when the one after
            # begins, used or not.
            if self.cheer.get("side") == self.clock.active:
                self.cheer = {} if self.cheer.get("ready") else {**self.cheer, "ready": True}
            for p in self.players:
                if p.side == self.clock.active:
                    p.ma_used = 0
                    p.acted = p.done = False
                    p.action = ""
                    p.dodge_reroll_used = p.break_tackle_used = False

        elif kind == "player_status":
            # Distracted and Rooted, the two standing-but-impaired states.
            p = self.by_id(event.actor)
            if p is not None:
                if "distracted" in d:
                    p.distracted = bool(d["distracted"])
                if "rooted" in d:
                    p.rooted = bool(d["rooted"])
                if "chomped" in d:
                    p.chomped_by = str(d["chomped"] or "")
                if d.get("reserves"):
                    # TOO MANY PLAYERS, and Dodgy Snack's worse result.
                    p.place, p.down = "reserves", "standing"

        elif kind == "player_left_pitch":
            # Pushed into the Crowd. They land in the Reserves Box unless the
            # Injury Roll that follows moves them somewhere worse — "If the player
            # would be Stunned, place them in their team's Reserve Box." This
            # event used to be emitted and never applied, so a player shoved into
            # the stands stayed standing in the square they were shoved out of.
            p = self.by_id(event.actor)
            if p is not None:
                p.place = "reserves"
                p.down = "standing"
                if self.ball.carrier == p.id:
                    self.ball.carrier = ""

        elif kind == "player_sent_off":
            p = self.by_id(event.actor)
            if p is not None:
                p.place = "sent_off"
                p.acted = p.done = True
                if self.ball.carrier == p.id:
                    self.ball.carrier = ""

        elif kind == "player_reinstated":
            # Argue the Call, on a 6: "placed back in the square they were in".
            p = self.by_id(event.actor)
            if p is not None:
                p.place = "pitch"
                p.move_to(int(d.get("x", p.x)), int(d.get("y", p.y)))

        elif kind == "argue_the_call":
            if str(d.get("outcome")) == "ejected_coach":
                side = str(d.get("side") or "")
                if side and side not in self.argue_banned:
                    self.argue_banned.append(side)

        elif kind == "team_reroll_used":
            # Spent whether or not it bought anything — a failed Loner roll loses
            # it "just as if it had been used". The Drive-scoped one goes FIRST,
            # because it is the one that expires.
            side = str(d.get("side") or "")
            if self.drive_rerolls.get(side):
                self.drive_rerolls[side] -= 1
            elif side in self.rerolls:
                self.rerolls[side] = max(0, self.rerolls[side] - 1)

        elif kind == "choice_pending":
            self.pending = {k: v for k, v in d.items()}

        elif kind == "choice_made":
            self.pending = {}

        elif kind == "charge_started":
            # The kicking team acts, so the clock's active side moves to them for
            # the duration. `was` remembers whose Drive this is; charge_ended puts
            # it back. Nothing else in the engine needs to know a Charge is on.
            self.charge = {k: v for k, v in d.items()}
            self.clock.active = str(d.get("side") or self.clock.active)

        elif kind == "charge_ended":
            back = str(self.charge.get("was") or "")
            self.charge = {}
            if back:
                self.clock.active = back

        elif kind == "charge_action":
            # Which of the three once-only Actions this Charge has spent.
            used = list(self.charge.get("used") or [])
            used.append(str(d.get("action") or ""))
            self.charge["used"] = used

        elif kind == "drive_setup":
            side = str(d.get("side") or "")
            self.setups[side] = [dict(row) for row in (d.get("squares") or [])]

        elif kind == "apothecary_declared":
            # Spent on DECLARATION, not on the result: "they can use them once per
            # game", and the second roll may come back worse than the first.
            self.apothecary[str(d.get("side") or "")] = False

        elif kind == "apothecary_result":
            # "If a BADLY HURT result is selected, then the player is successfully
            # Patched-up and placed into their Reserves Box instead of the Casualty
            # Box." Anything else and the Casualty stands.
            p = self.by_id(event.actor)
            if p is not None and str(d.get("result") or "") == "Badly Hurt":
                p.place, p.down = "reserves", "standing"

        elif kind == "apothecary_used":
            p = self.by_id(event.actor)
            side = str(d.get("side") or "")
            self.apothecary[side] = False
            if p is not None:
                # "the player is NOT removed from the pitch … Instead, the player
                # will become Stunned in the square they are in" — unless the crowd
                # was what got them, in which case they are not on a square at all.
                if d.get("crowd"):
                    p.place, p.down = "reserves", "standing"
                else:
                    p.place, p.down = "pitch", "stunned"

        elif kind == "weather_changed":
            self.weather = str(d.get("weather") or "perfect")
            apo = d.get("apothecary") or {}
            self.apothecary = {"home": bool(apo.get("home")), "away": bool(apo.get("away"))}

        elif kind == "kickoff_bonus":
            side = str(d.get("side") or "")
            if d.get("cheer_used"):
                self.cheer = {}
            if d.get("reroll") and side:
                self.drive_rerolls[side] = self.drive_rerolls.get(side, 0) + 1
            if d.get("cheer") and side:
                self.cheer = {"side": side, "ready": False}

        elif kind == "blitz_declared":
            self.blitz = {"player": event.actor, "target": str(d.get("target") or ""), "blocked": False}
            self.turn_actions["blitz"] = event.actor
            p = self.by_id(event.actor)
            if p is not None:
                p.action = "blitz"

        elif kind == "move_allowance_spent":
            # Move Allowance going somewhere other than a square — the point a
            # Blitz's Block costs. Its own event rather than a silent adjustment
            # so the fold charges it too and the log can show where it went.
            p = self.by_id(event.actor)
            if p is not None:
                p.ma_used = int(d.get("ma_used", p.ma_used))
                p.acted = True

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

        elif kind == "block_rolled":
            # A Blitz's Block is spent once thrown. Recorded off the Block's own
            # event rather than a separate one, because the two can never be
            # allowed to disagree about whether the Block happened.
            if d.get("blitz") and self.blitz.get("player") == event.actor:
                self.blitz["blocked"] = True

        elif kind == "skill_spent":
            # A Once-per-Turn Skill being used up. The event names the flag and
            # apply only honours ones it knows, so a malformed event cannot write
            # an arbitrary attribute onto a player.
            p = self.by_id(event.actor)
            flag = str(d.get("flag") or "")
            if p is not None and flag in ONCE_PER_TURN_FLAGS:
                setattr(p, flag, True)

        elif kind == "activation_ended":
            # An Action that is over. Recorded rather than set on the player,
            # because `acted` is state and ALL state here comes from the log —
            # the actions used to assign it directly and emit a note saying so,
            # which meant a folded match had everyone free to act again.
            p = self.by_id(event.actor)
            if p is not None:
                p.acted = p.done = True
                if d.get("action"):
                    p.action = str(d["action"])
            # No judgement here: the ACTION module decided whether its Action is
            # capped at one per turn and said so in the event.
            if d.get("once_per_turn") and d.get("action"):
                self.turn_actions.setdefault(str(d["action"]), event.actor)

        elif kind in ("player_fell", "player_placed_prone"):
            p = self.by_id(event.actor)
            if p is not None:
                p.down = str(d.get("down") or "prone")
                # Falling Over ends the activation: "their activation immediately
                # ends", whichever roll put them on the floor.
                p.acted = p.done = True
                # "A Rooted player will immediately stop being Rooted … if they
                # are ever Knocked Down or Placed Prone."
                p.rooted = False

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
                    # …unless they are already off the pitch: a player Pushed into
                    # the Crowd who "would be Stunned" goes to the Reserves Box
                    # rather than lying Stunned on a square they no longer occupy.
                    if p.place == "pitch":
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
            # Only the kick's own events claim the air; everything else brings it
            # down, so the flag clears by default rather than needing to be undone.
            self.ball.in_air = bool(d.get("air"))

        elif kind == "ball_picked_up":
            self.ball.in_air = False
            self.ball.carrier = event.actor
            p = self.by_id(event.actor)
            if p is not None:
                self.ball.x, self.ball.y = p.x, p.y

        elif kind == "ball_dropped":
            self.ball.carrier = ""
            self.ball.x, self.ball.y = int(d.get("x", self.ball.x)), int(d.get("y", self.ball.y))

        elif kind == "drive_started":
            self.drive = int(d.get("drive") or self.drive + 1)
            # A Drive-scoped re-roll lasts exactly one Drive, and a set-up is
            # consumed by the Drive it was declared for.
            self.drive_rerolls = {}
            self.cheer = {}
            self.setups = {}
            self.pending = {}
            self.charge = {}
            self.setup = [dict(row) for row in (d.get("setup") or [])]
            for row in self.setup:
                p = self.by_id(str(row.get("id") or ""))
                if p is None or p.place in ("casualty", "sent_off"):
                    continue
                p.move_to(int(row["x"]), int(row["y"]))
                p.down = "standing"
                p.place = "pitch"
                p.ma_used, p.acted, p.done, p.dodge_reroll_used = 0, False, False, False
                p.action, p.break_tackle_used = "", False
                # "…stop being Rooted at the end of a Drive", and a Distracted
                # player is set up afresh with everyone else.
                p.rooted = p.distracted = False
            # A Knocked-out player misses the drive; a Casualty misses the match;
            # a Sent-off player is gone for good and must never be set up again.
            for p in self.players:
                if p.place == "knocked_out" and not any(r.get("id") == p.id for r in self.setup):
                    p.place = "reserves"

        elif kind == "clock_adjusted":
            delta = int(d.get("delta") or 0)
            self.clock.turn = max(1, min(TURNS_PER_HALF, self.clock.turn + delta))

        elif kind == "half_time":
            # The clock has ALREADY turned over in _advance_clock — this event is
            # the log's record of it, so applying it must be idempotent. (It was
            # declared in events.py and handled here for a long time without ever
            # being emitted, which is the third dead event kind this codebase has
            # turned up: declared, handled, never produced.)
            self.clock.half = int(d.get("half") or self.clock.half)
            self.clock.turn = 1
            self.rerolls = dict(self.rerolls_max)

        elif kind == "match_over":
            self.over = True

        elif kind == "extra_time":
            # "Extra Time is played exactly like a normal half" — a third half, so
            # the clock restarts — "however, Team Re-rolls will not be replenished
            # like they would be at half-time." Which is why this is NOT half_time.
            self.over = False
            self.clock.half += 1
            self.clock.turn = 1
            self.clock.active = str(d.get("receiving") or self.clock.active)

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
                    p.acted = p.done = True

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
                # Half-time. "Any used during the first half will be replenished
                # … Unused Team Re-rolls do not carry over." One assignment does
                # both halves of that sentence, up or down.
                self.rerolls = dict(self.rerolls_max)
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
            "blitz": dict(self.blitz),
            "rerolls": dict(self.rerolls),
            "rerolls_max": dict(self.rerolls_max),
            "drive_rerolls": dict(self.drive_rerolls),
            "staff": {k: dict(v) for k, v in self.staff.items()},
            "weather": self.weather,
            "apothecary": dict(self.apothecary),
            "setups": {k: [dict(r) for r in v] for k, v in self.setups.items()},
            "pending": dict(self.pending),
            "charge": dict(self.charge),
            "cheer": dict(self.cheer),
            "argue_banned": list(self.argue_banned),
            "turn_actions": dict(self.turn_actions),
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
                    done=bool(raw.get("done")),
                    action=str(raw.get("action") or ""),
                    dodge_reroll_used=bool(raw.get("dodge_reroll_used")),
                    break_tackle_used=bool(raw.get("break_tackle_used")),
                    distracted=bool(raw.get("distracted")),
                    rooted=bool(raw.get("rooted")),
                    chomped_by=str(raw.get("chomped_by") or ""),
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
