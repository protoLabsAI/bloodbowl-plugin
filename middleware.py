"""Putting the board in the prompt instead of making the coach ask for it.

A seat was reading `bb_game_state` nine to sixteen times a turn. Three quarters
of every read is the ROSTER — each player's position, team, badge and statline —
none of which can change once a match has started, all of it re-sent every time.
And between two reads the board moves, so a coach working from an earlier answer
is working from a board that no longer exists. That is not a hypothetical: one
spent a turn arguing with a board that disagreed with what it had just done.

So the board rides the prompt. `wrap_model_call` attaches it to the system
message for THAT CALL ONLY (ADR 0032, `registry.register_middleware`), which
means:

* it is always current, because it is rendered at the moment of the call rather
  than whenever the coach last asked;
* it costs no tool call and no round trip;
* it does not accumulate. A previous board block is stripped before the new one
  is attached, so sixteen model calls leave ONE board in the request rather than
  sixteen stale ones — which would be worse than the reads it replaces.

⚠️ THIS REPLACED `bb_game_state`, WHICH NO LONGER EXISTS. Asking a model not to
call a tool it has does not work — this codebase already learned that when
shipping `path` did not stop it moving one square at a time. A seat called the
state tool SIXTY-SIX TIMES in a single turn while the board sat in its prompt,
ran out of budget, and never finished the turn. The structural answer is that
there is nothing to call.

So everything the tool uniquely carried is here: the position, and the
unmodelled/partly-modelled Skill reporting, which is not decoration — a Skill the
engine does not apply changes what a player will really do.

Guarded like the nudge: no host, no middleware. The suite and the browser harness
register with no host at all, and losing this binding is exactly what "no host"
should cost.
"""

from __future__ import annotations

import logging

log = logging.getLogger("protoagent.plugins.bloodbowl")

#: Marks our own block so a later call can replace it rather than stack on it.
MARK = "⟦bloodbowl board⟧"


def render(match, session_id: str = "") -> str:
    """The position, small enough to send on every model call.

    Deliberately NOT `state_report`: this is read by a coach mid-decision, so it
    is laid out for reading rather than for parsing — sides together, the ball
    first, and the two facts that decide everything (which way you are running,
    how far the ball has to go) stated rather than derivable.
    """
    from .engine.game import situation

    s = situation(match)
    lines = [MARK]
    c = match.clock
    lines.append(f"Half {c.half}, turn {c.turn} of {s['turns_left_this_half'] + c.turn} — {c.active} to act.")
    lines.append(f"Score: home {match.score.get('home', 0)} — away {match.score.get('away', 0)}.")
    lines.append(f"home runs at row {s['scores_in']['home']}; away runs at row {s['scores_in']['away']}.")

    ball = s["ball"]
    if ball.get("carrier"):
        lines.append(
            f"BALL: carried by {ball['carrier']} ({ball.get('held_by')}) at "
            f"{tuple(ball.get('carrier_at', ()))}, {ball.get('to_score')} rows from scoring."
        )
    else:
        lines.append(f"BALL: loose at ({ball['x']},{ball['y']}).")

    # Only when there is actually a question. `pending` can be a dict that exists
    # and says nothing, and "WAITING ON AN ANSWER: None" is worse than silence —
    # it tells a coach to stop for something that is not there.
    question = (match.pending or {}).get("question") or (match.pending or {}).get("kind")
    if question:
        lines.append(
            f"⚠️ WAITING ON AN ANSWER FROM {str(match.pending.get('side') or '?').upper()} — the whole game is "
            f"stopped until it is given, including the ball landing. Answer with bb_game_choose; declining is "
            f"always legal and is a real answer. QUESTION: {question}"
        )

    for side in ("home", "away"):
        team = match.home_team if side == "home" else match.away_team
        who = []
        for p in match.players:
            if p.side != side or p.place != "pitch":
                continue
            flags = "".join(
                (
                    "" if p.down == "standing" else ("!" if p.down == "prone" else "*"),
                    "." if p.acted else "",
                )
            )
            who.append(f"{p.id}{flags}({p.x},{p.y}){p.player.position or ''}")
        lines.append(f"{side} ({team}) — {len(who)} on the pitch: " + ", ".join(who))
    lines.append("Flags: ! prone · * stunned · . already acted.")

    # THE HONESTY REPORTING COMES WITH THE BOARD, because there is no longer a
    # tool to ask for it. A Skill the engine does not apply, or applies in half,
    # changes what a player will actually do — and a coach reading a position that
    # quietly omits that is being misled by the thing it trusts most. It rides
    # here for the same reason the log carries its dice.
    try:
        from .engine.skills import partly_modelled_on_pitch, unmodelled_on_pitch

        gaps = unmodelled_on_pitch(match) or []
        half = partly_modelled_on_pitch(match) or []
        if gaps:
            lines.append("NOT MODELLED by this engine (do not plan around them): " + ", ".join(str(g) for g in gaps))
        if half:
            lines.append("PARTLY modelled (half a skill reads as all of it): " + ", ".join(str(g) for g in half))
    except Exception:  # noqa: BLE001 — decoration must never take a turn down
        pass

    return "\n".join(lines)


def attach(content, board_text: str) -> list | None:
    """Put the board into a system message's content, replacing our own previous
    copy. Returns the new block list, or None if there is nothing safe to attach to.

    A PLAIN FUNCTION on purpose: this is the part with the interesting rule in it
    (replace, never stack), and it must be testable without a host. The middleware
    class around it cannot be — `AgentMiddleware` comes from the host — so a test
    of the class alone SKIPS wherever the plugin's own suite runs, which is
    everywhere that matters for a regression.
    """
    block = {"type": "text", "text": board_text}
    if isinstance(content, str):
        return ([{"type": "text", "text": content}] if content else []) + [block]
    if isinstance(content, list):
        # Sixteen model calls must leave ONE board in the request. Sixteen stale
        # ones would be worse than the tool reads this replaces.
        kept = [b for b in content if not (isinstance(b, dict) and MARK in str(b.get("text", "")))]
        return kept + [block]
    return None


def factory(cfg: dict | None = None):
    """`(config) -> AgentMiddleware | None`, per `registry.register_middleware`."""
    try:
        from langchain.agents.middleware import AgentMiddleware
    except Exception:  # noqa: BLE001 — no host, no middleware. That is the cost of no host.
        log.info("[bloodbowl] no host middleware API; the board stays a tool call")
        return None

    log.info("[bloodbowl] board middleware built — the position will ride the prompt")

    class BoardMiddleware(AgentMiddleware):
        """Attaches the live board to the system message, when there is one."""

        def _transform(self, request):
            try:
                from .store import load_match

                match = load_match()
                if match is None or match.over:
                    return request
                sysmsg = getattr(request, "system_message", None)
                if sysmsg is None:
                    return request  # create_agent always supplies one; nothing safe to attach to

                blocks = attach(getattr(sysmsg, "content", None), render(match))
                if blocks is None:
                    return request
                # ⚠️ SAY SO IN THE LOG. The system message goes into the model
                # REQUEST, not into the checkpointed message list — so grepping a
                # checkpoint for the board proves nothing either way, and looking
                # there cost an iteration. This line is the observable.
                log.info(
                    "[bloodbowl] board attached to the prompt — H%st%s %s to act, %d chars",
                    match.clock.half,
                    match.clock.turn,
                    match.clock.active,
                    len(blocks[-1].get("text", "")),
                )
                return request.override(system_message=sysmsg.model_copy(update={"content": blocks}))
            except Exception:  # noqa: BLE001
                # A board that cannot be rendered must never take the turn down
                # with it — the coach still has bb_game_state.
                log.exception("[bloodbowl] board injection failed; continuing without it")
                return request

        def wrap_model_call(self, request, handler):
            return handler(self._transform(request))

        async def awrap_model_call(self, request, handler):
            return await handler(self._transform(request))

    return BoardMiddleware()
