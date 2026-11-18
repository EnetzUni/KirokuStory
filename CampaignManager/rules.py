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


# Levelling up. Every class chooses its subclass at level 3 (SRD 5.2.1).
# House rule: every 4 levels, from level 4, the player chooses the ability score
# improvement (two +1, no score above 20) or a feat (an Ability marked as a feat).
# Spells are not chosen when levelling up: the master gives them.
SUBCLASS_LEVEL = 3
FEAT_LEVELS = (4, 8, 12, 16, 20)
MAX_SCORE = 20
# House rule: each new level adds 1d4 + the Constitution modifier to the maximum
# hit points, whatever the class. The die is rolled at the table, in front of the
# master, and the player types the result.
LEVEL_UP_HIT_DIE = 4


def level_for_xp(xp):
    """Level an amount of XP is enough for. The character reaches it when the
    player levels up from the sheet (game rule 16)."""
    level = 1
    for index, threshold in enumerate(XP_THRESHOLDS):
        if xp >= threshold:
            level = index + 1
    return level


def is_feat_level(level):
    """Levels where the player chooses the ability score improvement or a feat."""
    return level in FEAT_LEVELS


def level_up_hit_points(die_result, con_modifier):
    """Hit points a new level adds: the 1d4 rolled at the table + the Constitution modifier, at least 1."""
    return max(1, die_result + con_modifier)


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


# ---------------------------------------------------------------------------
# Magic points by class and level
# ---------------------------------------------------------------------------
# A class gives as many magic points as levels of the spell slots the SRD 5.2.1
# gives it: casting with a slot of level N costs N points, so the points are worth
# the same as the slots. The master adds or takes away points per character
# (Character.magic_points_bonus). The kinds are CharacterClass.Magic.

# Spell slots of a full caster (bard, cleric, druid, sorcerer, wizard): how many of
# levels 1, 2, 3... at character levels 1 to 20.
FULL_CASTER_SLOTS = [
    [2], [3], [4, 2], [4, 3], [4, 3, 2], [4, 3, 3], [4, 3, 3, 1], [4, 3, 3, 2], [4, 3, 3, 3, 1], [4, 3, 3, 3, 2],
    [4, 3, 3, 3, 2, 1], [4, 3, 3, 3, 2, 1], [4, 3, 3, 3, 2, 1, 1], [4, 3, 3, 3, 2, 1, 1],
    [4, 3, 3, 3, 2, 1, 1, 1], [4, 3, 3, 3, 2, 1, 1, 1], [4, 3, 3, 3, 2, 1, 1, 1, 1],
    [4, 3, 3, 3, 3, 1, 1, 1, 1], [4, 3, 3, 3, 3, 2, 1, 1, 1], [4, 3, 3, 3, 3, 2, 2, 1, 1],
]
# Pact magic (warlock): (slots, slot level) at character levels 1 to 20.
PACT_SLOTS = [(1, 1), (2, 1), (2, 2), (2, 2), (2, 3), (2, 3), (2, 4), (2, 4), (2, 5), (2, 5)] + [(3, 5)] * 6 + [(4, 5)] * 4


def class_magic_points(magic, level):
    """Magic points a class with this kind of magic gives at `level`:
    "completa" (full caster), "media" (half caster: a full caster of half the
    level, rounded up, like paladins and rangers), "pacto" (warlock) or "ninguna"."""
    if magic == "pacto":
        slots, slot_level = PACT_SLOTS[level - 1]
        return slots * slot_level
    if magic == "completa":
        slots = FULL_CASTER_SLOTS[level - 1]
    elif magic == "media":
        slots = FULL_CASTER_SLOTS[math.ceil(level / 2) - 1]
    else:
        return 0
    return sum(count * slot_level for slot_level, count in enumerate(slots, start=1))


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


def true_damage(damage, current_ap, armor_damages=()):
    """Damage that reaches the hit points: the damage minus the AP it meets
    (Damage - AP = True Damage), never below 0. Penetrante lowers the AP it meets."""
    return max(0, damage - ap_against_attack(current_ap, armor_damages))


# ---------------------------------------------------------------------------
# Knocked down and death (house rule)
# ---------------------------------------------------------------------------
# At 0 hit points or fewer a character is knocked down. It dies when its hit points
# reach minus half its maximum (90 -> -45; 45 -> -23, the first whole number past
# -22.5), or when it is knocked down for the second time in the same fight.

def death_hp(max_hp):
    """Hit points at which (or below which) the character dies."""
    return -((max(0, max_hp) + 1) // 2)
