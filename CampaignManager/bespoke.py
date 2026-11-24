"""Pieces a master makes by hand for one character: a race, a class, a unique weapon
or an armor of their own. Used by the NPC creator and by the master's sheet editor.

They are catalog entries marked `bespoke`, so players never see them in their lists;
weapons and armor are templates the character carries a copy of, as the rules ask.
"""
import re

from django.db import transaction

from .models import Armor, ArmorDamage, ArmorType, Attack, CharacterClass, DiceRoll, Inventory, Item, Race, Weapon

ARMOR_TYPE_NAMES = ["Ligera", "Media", "Pesada"]
DICE = re.compile(r"^\s*(\d+)\s*d\s*(\d+)\s*$", re.IGNORECASE)


def armor_type_names():
    """The usual three plus any other type the catalog has."""
    names = list(ARMOR_TYPE_NAMES)
    for name in ArmorType.objects.order_by("type").values_list("type", flat=True):
        if name not in names:
            names.append(name)
    return names


def parse_dice(text):
    """«2d6 + 1d4» -> [(2, 6), (1, 4)]. Empty text: no dice. ValueError if it is not dice."""
    dice = []
    for part in (text or "").split("+"):
        if not part.strip():
            continue
        match = DICE.match(part)
        if match is None:
            raise ValueError(part.strip())
        amount, sides = int(match.group(1)), int(match.group(2))
        if not (1 <= amount <= 50 and 2 <= sides <= 100):
            raise ValueError(part.strip())
        dice.append((amount, sides))
    return dice


def race(name, speed):
    """A race of the master's own, reused if one with that name was already made."""
    existing = Race.objects.filter(bespoke=True, name__iexact=name.strip()).first()
    return existing or Race.objects.create(name=name.strip(), speed=speed or 9, bespoke=True,
                                           description="Raza creada por el máster.")


def char_class(name, magic):
    existing = CharacterClass.objects.filter(bespoke=True, name__iexact=name.strip()).first()
    if existing:
        return existing
    return CharacterClass.objects.create(name=name.strip(), primary_ability="—", magic=magic or CharacterClass.Magic.NINGUNA,
                                         bespoke=True, description="Clase creada por el máster.")


@transaction.atomic
def weapon(data):
    """A unique weapon from the form data (BespokeWeaponFields.weapon_data). Returns the template."""
    item = Item.objects.create(name=data["name"], description=data.get("description", ""), bespoke=True)
    bonus, stat = data.get("hit_bonus") or 0, data.get("hit_stat") or "str"
    to_hit = f"{bonus} + {stat}" if bonus else stat
    new = Weapon.objects.create(item=item, to_hit=to_hit)
    attack = Attack.objects.create(name=data["name"], extra=data.get("extra") or 0, range=data.get("range"),
                                   stat_bonus=data.get("stat_bonus") or None)
    attack.dice.set([DiceRoll.objects.create(amount=amount, sides=sides) for amount, sides in data.get("dice", [])])
    traits = []
    if data.get("pierce"):
        traits.append(ArmorDamage.objects.create(type=ArmorDamage.Type.PENETRATING, amount=data["pierce"]))
    if data.get("shred"):
        traits.append(ArmorDamage.objects.create(type=ArmorDamage.Type.SHREDDING, amount=data["shred"]))
    attack.armor_damages.set(traits)
    new.attacks.add(attack)
    return item


@transaction.atomic
def armor(data):
    """An armor of the master's own: its type and its total AP and magic AP. Returns the template."""
    armor_type, _ = ArmorType.objects.get_or_create(type=data["type"], defaults={"ap": 0, "magic_ap": 0})
    item = Item.objects.create(name=data["name"], description=data.get("description", ""), bespoke=True)
    # The character's AP is the piece's AP plus its type's: the piece takes the rest.
    Armor.objects.create(item=item, armor_type=armor_type, ap=(data.get("ap") or 0) - armor_type.ap,
                         magic_ap=(data.get("magic_ap") or 0) - armor_type.magic_ap)
    return item


@transaction.atomic
def give(character, template, equip=True):
    """The character carries a copy of a weapon or armor template, equipped if asked.
    An armor equipped this way takes the place of the one worn before (one armor at a time)."""
    copy = template.make_copy()
    Inventory.objects.create(character=character, item=copy, quantity=1)
    if equip:
        if hasattr(copy, "armor"):
            Armor.objects.filter(equipped=True, item__inventory__character=character).update(equipped=False)
            Armor.objects.filter(item=copy).update(equipped=True)
        Weapon.objects.filter(item=copy).update(equipped=True)
    return copy
