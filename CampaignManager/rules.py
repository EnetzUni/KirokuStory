"""Game rules that do not depend on the database.

The values marked as open decisions (spec, section 11) are kept here so they
can be changed in one place.
"""
import math


def gettext_noop(text):
    """Marks texts that templates translate through a variable ({% translate sk.name %}),
    so makemessages finds them. Returns the text unchanged; defined here, not imported
    from Django, so this module still works without Django settings."""
    return text


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
# Names on the paper sheet (short ones where the sheet abbreviates) and full names.
ABILITY_SHEET_LABEL = {"strength": gettext_noop("Fuerza"), "dexterity": gettext_noop("Destreza"), "constitution": gettext_noop("Const."),
                       "intelligence": gettext_noop("Intel."), "wisdom": gettext_noop("Sabiduría"), "charisma": gettext_noop("Carisma")}
ABILITY_NAMES = {"strength": gettext_noop("Fuerza"), "dexterity": gettext_noop("Destreza"), "constitution": gettext_noop("Constitución"),
                 "intelligence": gettext_noop("Inteligencia"), "wisdom": gettext_noop("Sabiduría"), "charisma": gettext_noop("Carisma")}

# The 18 skills of the sheet and the ability each one uses. Proficiencies are not
# modelled yet, so a skill's bonus is just that ability's modifier.
SKILLS = [
    (gettext_noop("Acrobacias"), "dexterity"), (gettext_noop("Arcanos"), "intelligence"), (gettext_noop("Atletismo"), "strength"), (gettext_noop("Engañar"), "charisma"),
    (gettext_noop("Historia"), "intelligence"), (gettext_noop("Interpretación"), "charisma"), (gettext_noop("Intimidar"), "charisma"),
    (gettext_noop("Investigación"), "intelligence"), (gettext_noop("Juego de manos"), "dexterity"), (gettext_noop("Medicina"), "wisdom"),
    (gettext_noop("Naturaleza"), "intelligence"), (gettext_noop("Percepción"), "wisdom"), (gettext_noop("Perspicacia"), "wisdom"), (gettext_noop("Persuasión"), "charisma"),
    (gettext_noop("Religión"), "intelligence"), (gettext_noop("Sigilo"), "dexterity"), (gettext_noop("Supervivencia"), "wisdom"), (gettext_noop("Trato con animales"), "wisdom"),
]


def ability_from_name(text):
    """The first ability named in a text such as a class's primary ability («Sabiduría»,
    «Fuerza o Destreza»), or None."""
    words = (text or "").lower()
    found = [(words.find(name.lower()), key) for key, name in ABILITY_NAMES.items() if name.lower() in words]
    return min(found)[1] if found else None


ABILITY_BY_CODE = {"str": "strength", "dex": "dexterity", "con": "constitution",
                   "int": "intelligence", "wis": "wisdom", "cha": "charisma"}


# ---------------------------------------------------------------------------
# Rulesets. Each campaign plays with Aegis, our variant of the SRD 5.2.1 with the
# rules marked «Aegis» below, or with the standard SRD 5.2.1 (Campaign.ruleset).
# ---------------------------------------------------------------------------
AEGIS, SRD = "aegis", "srd"

# Levelling up. Every class chooses its subclass at level 3 (both rulesets).
# The player chooses the ability score improvement (two +1, no score above 20) or a
# feat (an Ability marked as a feat) at the feat levels:
#   Aegis: every 4 levels, from level 4, for every class.
#   SRD: 4, 8, 12 and 16, plus 6 and 14 for the fighter and 10 for the rogue, and
#   the epic boon at 19 (a feat: the epic boons have feat_min_level 19).
SUBCLASS_LEVEL = 3
FEAT_LEVELS = (4, 8, 12, 16, 20)
SRD_FEAT_LEVELS = (4, 8, 12, 16, 19)
SRD_EXTRA_FEAT_LEVELS = {"Guerrero": (6, 14), "Pícaro": (10,)}
MAX_SCORE = 20
# Hit points of a new level, + the Constitution modifier:
#   Aegis: 1d4, whatever the class, rolled at the table in front of the master; the
#   player types the result.
#   SRD: the class hit die, its fixed value (half + 1) or rolled.
LEVEL_UP_HIT_DIE = 4
# Spells: in Aegis only the master gives them. In the SRD the player also picks new
# ones when levelling up, as many as the class tables add at that level.


def level_for_xp(xp):
    """Level an amount of XP is enough for. The character reaches it when the
    player levels up from the sheet (game rule 16)."""
    level = 1
    for index, threshold in enumerate(XP_THRESHOLDS):
        if xp >= threshold:
            level = index + 1
    return level


def is_feat_level(level, ruleset=AEGIS, class_name=""):
    """Levels where the player chooses the ability score improvement or a feat."""
    if ruleset == SRD:
        return level in SRD_FEAT_LEVELS or level in SRD_EXTRA_FEAT_LEVELS.get(class_name, ())
    return level in FEAT_LEVELS


def fixed_hit_points(die):
    """SRD: the fixed value instead of rolling the hit die, half the die plus 1 (d8 -> 5)."""
    return die // 2 + 1


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


def highest_slot(magic, level):
    """Highest spell slot level a class with this kind of magic has at `level` (SRD 5.2.1)."""
    if magic == "pacto":
        return PACT_SLOTS[level - 1][1]
    if magic == "completa":
        return len(FULL_CASTER_SLOTS[level - 1])
    if magic == "media":
        return len(FULL_CASTER_SLOTS[math.ceil(level / 2) - 1])
    return 0


# ---------------------------------------------------------------------------
# SRD only: new spells when levelling up
# ---------------------------------------------------------------------------
# Prepared spells (of level 1 or higher) at character levels 1 to 20, from the SRD
# 5.2.1 class tables, and the cantrips at level 1: every one of these classes gets
# one more cantrip at levels 4 and 10.
_CASTER_PREPARED = [4, 5, 6, 7, 9, 10, 11, 12, 14, 15, 16, 16, 17, 17, 18, 18, 19, 20, 21, 22]
_HALF_CASTER_PREPARED = [2, 3, 4, 5, 6, 6, 7, 7, 9, 9, 10, 10, 11, 11, 12, 12, 14, 14, 15, 15]
SRD_PREPARED_SPELLS = {
    "Bardo": _CASTER_PREPARED, "Clérigo": _CASTER_PREPARED, "Druida": _CASTER_PREPARED,
    "Hechicero": [2, 4, 6, 7, 9, 10, 11, 12, 14, 15, 16, 16, 17, 17, 18, 18, 19, 20, 21, 22],
    "Mago": [4, 5, 6, 7, 9, 10, 11, 12, 14, 15, 16, 16, 17, 18, 19, 21, 22, 23, 24, 25],
    "Brujo": [2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 11, 11, 12, 12, 13, 13, 14, 14, 15, 15],
    "Paladín": _HALF_CASTER_PREPARED, "Explorador": _HALF_CASTER_PREPARED,
}
SRD_CANTRIPS = {"Bardo": 2, "Clérigo": 3, "Druida": 2, "Hechicero": 4, "Brujo": 2, "Mago": 3}
SRD_MORE_CANTRIPS_AT = (4, 10)


def srd_new_spells(class_name, level):
    """(cantrips, spells of level 1 or higher) a class adds going up to `level` (SRD)."""
    cantrips = 1 if class_name in SRD_CANTRIPS and level in SRD_MORE_CANTRIPS_AT else 0
    table = SRD_PREPARED_SPELLS.get(class_name)
    spells = table[level - 1] - table[level - 2] if table and level >= 2 else 0
    return cantrips, spells

