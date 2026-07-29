"""The Weather Table, and what each condition does to a roll.

    "To determine what the weather is like at the start of the game, each Coach
     rolls a D6 and adds the two rolls together."

    WEATHER TABLE (2D6)
      2     SWELTERING HEAT — "At the end of each Drive whilst this weather
            condition is in effect, one Coach rolls a D3 and each Coach randomly
            selects that many of their players that were on the pitch when the
            Drive ended. The selected players are placed in the Reserves Box and
            cannot be set up on the pitch for the next Drive."
      3     VERY SUNNY — "Whenever a player makes a Passing Ability Test, apply a
            -1 modifier to the roll."
      4-10  PERFECT CONDITIONS — "There is no additional effect."
      11    POURING RAIN — "Whenever a player attempts to pick up or Catch the
            ball, or Intercept a Pass Action, they suffer a -1 modifier."
      12    BLIZZARD — "Whenever a player attempts to Rush, apply an additional -1
            modifier to the roll. Additionally, when a player makes a Pass Action,
            they may only attempt to make a Quick Pass or a Short Pass."

Three of the five are modifiers on named tests, which is exactly what the
``roll_modifier`` hook already carries for Skills — so the weather rides the same
rail rather than being sprinkled through the roll sites. The engine asks one
question, "what modifies a `catch` right now", and the answer happens to include
the sky.

Blizzard's second clause is a LEGALITY, not a modifier, so it lives in the Pass
Action's validate: a Long Pass in a blizzard is refused with a reason rather than
thrown at a penalty.
"""

from __future__ import annotations

# 2D6 -> (key, name, rule text)
WEATHER_TABLE = {
    2: ("sweltering_heat", "Sweltering Heat", "Players faint at the end of each Drive."),
    3: ("very_sunny", "Very Sunny", "-1 to every Passing Ability Test."),
    11: ("pouring_rain", "Pouring Rain", "-1 to pick up, Catch and Intercept."),
    12: ("blizzard", "Blizzard", "-1 to Rush, and only a Quick or Short Pass may be attempted."),
}
PERFECT = ("perfect", "Perfect Conditions", "No additional effect.")

# condition -> the tests it modifies, and by how much. One table rather than five
# branches, so a new condition is a row.
MODIFIERS = {
    "very_sunny": {"pass": -1},
    "pouring_rain": {"pick_up": -1, "catch": -1, "intercept": -1},
    "blizzard": {"rush": -1},
}


def from_roll(total: int) -> tuple[str, str, str]:
    """(key, name, text) for a 2D6 total. 4-10 is the wide band in the middle."""
    return WEATHER_TABLE.get(total, PERFECT)


def modifier(condition: str, test: str) -> int:
    return int(MODIFIERS.get(condition or "", {}).get(test, 0))


def name_of(condition: str) -> str:
    for key, name, _text in list(WEATHER_TABLE.values()) + [PERFECT]:
        if key == condition:
            return name
    return PERFECT[1]


def bands_allowed(condition: str) -> tuple[str, ...] | None:
    """Pass bands this weather permits, or None for all of them.

    Blizzard: "when a player makes a Pass Action, they may only attempt to make a
    Quick Pass or a Short Pass."
    """
    if condition == "blizzard":
        return ("Quick Pass", "Short Pass")
    return None
