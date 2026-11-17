"""Game rules that do not depend on the database.

The values marked as open decisions (spec, section 11) are kept here so they
can be changed in one place.
"""
import math

# Open decision: XP -> level uses the SRD table until the group fixes its own.
XP_THRESHOLDS = [
    0, 300, 900, 2700, 6500, 14000, 23000, 34000, 48000, 64000,
    85000, 100000, 120000, 140000, 165000, 195000, 225000, 265000, 305000, 355000,
]
MAX_LEVEL = len(XP_THRESHOLDS)

# Character creator: point buy (27 points, from 8 to 15) or the standard array.
POINT_BUY_TOTAL = 27
POINT_BUY_COST = {8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9}
STANDARD_ARRAY = [15, 14, 13, 12, 10, 8]

ABILITY_KEYS = ["strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"]
ABILITY_ABBR = {"strength": "FUE", "dexterity": "DES", "constitution": "CON",
                "intelligence": "INT", "wisdom": "SAB", "charisma": "CAR"}
ABILITY_BY_CODE = {"str": "strength", "dex": "dexterity", "con": "constitution",
                   "int": "intelligence", "wis": "wisdom", "cha": "charisma"}

DEATH_SAVES = 3  # successes or failures needed


def level_for_xp(xp):
    """Character level for an amount of XP (game rule 9)."""
    level = 1
    for index, threshold in enumerate(XP_THRESHOLDS):
        if xp >= threshold:
            level = index + 1
    return level


def xp_for_next_level(level):
    """XP needed to reach the next level, or None at the maximum level."""
    return XP_THRESHOLDS[level] if level < MAX_LEVEL else None


def ability_modifier(score):
    """(score - 10) / 2, rounded down: 8 -> -1, 14 -> +2."""
    return (score - 10) // 2


def max_spell_slot(level):
    """Highest spell slot level, as in the prototype: half the level, rounded up, up to 9."""
    return min(9, math.ceil(level / 2))


def magic_point_cost(slot_level):
    """Open decision: casting with a slot of level N costs N magic points; cantrips cost nothing."""
    return max(0, slot_level)


def point_buy_spent(scores):
    return sum(POINT_BUY_COST[score] for score in scores)


def clamp(value, low, high):
    return max(low, min(high, value))


# ---------------------------------------------------------------------------
# Armor damage (game rule 3)
# ---------------------------------------------------------------------------
# Penetrante ignores `amount` AP while the damage is dealt and never changes the
# AP. Triturante subtracts `amount` AP after the damage is dealt. With both, the
# damage is dealt against the unchanged AP and the triturante AP are subtracted
# afterwards. How much damage the AP stop is not defined yet, so these functions
# only work out the AP.

def ap_against_attack(current_ap, armor_damages):
    """AP the damage is dealt against: the current AP minus the penetrante amounts."""
    ignored = sum(amount for kind, amount in armor_damages if kind == "penetrating")
    return max(0, current_ap - ignored)


def ap_after_attack(current_ap, armor_damages):
    """Current AP once the attack is over: minus the triturante amounts, never below 0."""
    shredded = sum(amount for kind, amount in armor_damages if kind == "shredding")
    return max(0, current_ap - shredded)
