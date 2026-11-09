"""Add the sample campaign data on top of the SRD 5.2.1 catalog.

The races, classes, spells, abilities, weapons and conditions come from the
fixture CampaignManager/fixtures/datos_iniciales_srd.json, which must be loaded
first. This command adds:

- A test user and the Alforge campaign, described from CONTEXTO_MUNDO.md.
- Balasar, from his level 10 druid character sheet, linked to the SRD rows
  (Elfo, wood-elf lineage, Druida, his spells, the "Dardo" item template).
- What the SRD does not include: the Círculo de la Luna subclass, the spells
  Escudo de piedra and Tierra en erupción, his other traits and his items.

Setup, in this order:
    python manage.py migrate
    python manage.py loaddata datos_iniciales_srd
    python manage.py populate

It is safe to run more than once: existing rows are updated, not duplicated.
"""
import datetime

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from CampaignManager.models import (
    Ability,
    Attack,
    Campaign,
    Character,
    CharacterClass,
    Component,
    DiceRoll,
    Inventory,
    Item,
    Race,
    Spell,
    Subclass,
    SubclassAbility,
    Subrace,
    User,
    Weapon,
)


def m(feet):
    """Feet to meters with the campaign's rule 6 ft = 2 m, rounded to the nearest meter."""
    return (feet * 2 + 3) // 6


# ---------------------------------------------------------------------------
# SRD rows Balasar uses
# ---------------------------------------------------------------------------

SRD_RACE = "Elfo"
SRD_SUBRACE = "Linaje élfico: elfo de los bosques"
SRD_CLASS = "Druida"
SRD_RACE_ABILITIES = ["Visión en la oscuridad (20 m)", "Linaje feérico", "Trance"]
SRD_CLASS_ABILITIES = ["Druídico", "Lanzamiento de conjuros", "Forma salvaje"]
SRD_SPELLS = [
    "Crear llama", "Reparar", "Saber druídico",
    "Crear o destruir agua", "Curar heridas", "Fuego feérico", "Hechizar persona", "Palabra de curación",
    "Calentar metal", "Crecimiento espinoso", "Pasar sin rastro",
    "Conjurar animales", "Hablar con las plantas",
    "Escudo de fuego", "Marchitar", "Polimorfar",
    "Conjurar elemental",
]
SRD_DART = "Dardo"

# ---------------------------------------------------------------------------
# What the SRD does not include
# ---------------------------------------------------------------------------

# Traits from Balasar's sheet: name -> description
OWN_ABILITIES = {
    "Pies veloces": "Su velocidad base aumenta a 35 pies (12 m).",
    "Máscara de la naturaleza": "Puede intentar esconderse aunque solo lo cubran ligeramente el follaje, la lluvia, la nieve, la niebla u otros fenómenos naturales.",
    "Resiliente": "Dote: aumenta en 1 una característica y obtiene competencia en sus tiradas de salvación.",
    "Ser uno más con la naturaleza": "Rasgo personalizado: entiende a las bestias y se comunica con ellas sin gastar ningún conjuro.",
}

# Druid subclass from the 2014 rules: (feature, description, level required)
MOON_CIRCLE = "Círculo de la Luna"
MOON_CIRCLE_DESCRIPTION = "Círculo druídico cuyos miembros dominan la Forma salvaje para adoptar formas de bestia poderosas."
MOON_CIRCLE_FEATURES = [
    ("Forma salvaje de combate", "Usa Forma salvaje como acción adicional y, transformado, puede gastar espacios de conjuro para recuperar 1d8 puntos de golpe por nivel del espacio.", 2),
    ("Formas del círculo", "Puede transformarse en bestias de mayor valor de desafío: hasta un tercio de su nivel de druida.", 2),
    ("Golpe primitivo", "Sus ataques en forma de bestia cuentan como mágicos para superar resistencias e inmunidades.", 6),
    ("Forma salvaje elemental", "Gastando dos usos de Forma salvaje, se transforma en un elemental de aire, tierra, fuego o agua.", 10),
]

# Spells on Balasar's sheet that are not in the SRD. Range and area in feet;
# each die is (amount, sides) or (amount, sides, True) for dice per extra level.
OWN_SPELLS = [
    dict(name="Escudo de piedra", level=0, casting_time="1 acción", range=0, duration="Concentración, 1 minuto",
         components=["Verbal", "Somático", "Material"],
         description="Conjuro de la casa. Fragmentos de piedra giran alrededor del lanzador y le dan +1 a la CA mientras mantiene el truco con una acción adicional."),
    dict(name="Tierra en erupción", level=3, casting_time="1 acción", range=120, duration="Instantáneo",
         components=["Verbal", "Somático", "Material"],
         description="Una explosión de piedra y tierra estalla en un cubo de 7 m; las criaturas de la zona hacen una salvación de Destreza y el terreno se vuelve difícil.",
         attack=("Erupción", [(3, 12), (1, 12, True)], 120, 20)),
]

# Generic items from his sheet, carried directly
OWN_ITEMS = ["Bolsa de trucos de canela", "Cogollos infinitos"]

# ---------------------------------------------------------------------------
# The campaign, from CONTEXTO_MUNDO.md (sections 2, 3.1 and 10)
# ---------------------------------------------------------------------------

CAMPAIGN_NAME = "Alforge"
CAMPAIGN_DESCRIPTION = """\
Campaña de D&D ambientada en el continente de Alforge, que reúne regiones como Iretrone, Sokoku, Yarikawa, Hespern, Sarinthya, Lantared, Nordjem, Weida y BlackRake.

Comenzó con misiones de aventura relativamente convencionales y evolucionó hacia una historia de intriga política, espionaje, conspiraciones, magia, inmortales, dioses y experimentos relacionados con la resurrección. Las tramas personales de los personajes se entrelazan con conflictos de escala regional y con figuras cuyo poder supera ampliamente al de los aventureros. El tono combina fantasía épica, misterio, peligro y momentos de humor o descanso.

Situación tras la sesión 54, primera del arco 7: Hayate y Azog han muerto y Kenja ha abandonado el grupo. Thalion, Balasar, Iron, Isa y Pol, junto a Balatro y Plota, han llegado de noche a una playa desconocida mientras Kaledric, el heraldo de Aelyth, se acerca. Yarikawa ha entrado en Iga tras la traición de Kenshiro, Hattori está desaparecido y Raidriar se dispone a ocupar el trono vacío de Iretrone."""


def upsert(model, lookup, **values):
    """Get the first row matching `lookup` and update it, or create it."""
    obj = model.objects.filter(**lookup).first()
    if obj is None:
        return model.objects.create(**lookup, **values)
    for field, value in values.items():
        setattr(obj, field, value)
    obj.save()
    return obj


def dice(amount, sides, per_extra_level=False):
    return upsert(DiceRoll, {"amount": amount, "sides": sides, "per_extra_level": per_extra_level})


def srd(model, **lookup):
    """Get an SRD row, with a clear error if the fixture is not loaded."""
    try:
        return model.objects.get(**lookup)
    except model.DoesNotExist:
        raise CommandError(
            f"{model.__name__} {lookup} not found. Load the SRD first: "
            "python manage.py loaddata datos_iniciales_srd"
        )


class Command(BaseCommand):
    help = "Add the Alforge campaign and Balasar on top of the SRD 5.2.1 data."

    @transaction.atomic
    def handle(self, *args, **options):
        race = srd(Race, name=SRD_RACE)
        subrace = srd(Subrace, name=SRD_SUBRACE, race=race)
        druid = srd(CharacterClass, name=SRD_CLASS)
        race_abilities = [srd(Ability, name=n, raceability__race=race) for n in SRD_RACE_ABILITIES]
        class_abilities = [srd(Ability, name=n, classability__char_class=druid) for n in SRD_CLASS_ABILITIES]
        srd_spells = [srd(Spell, name=n) for n in SRD_SPELLS]
        dart_template = srd(Item, name=SRD_DART, template__isnull=True)

        # What the SRD does not include
        own_abilities = [
            upsert(Ability, {"name": name}, description=description)
            for name, description in OWN_ABILITIES.items()
        ]
        moon = upsert(Subclass, {"name": MOON_CIRCLE, "char_class": druid}, description=MOON_CIRCLE_DESCRIPTION)
        moon_abilities = []
        for name, description, level in MOON_CIRCLE_FEATURES:
            ability = upsert(Ability, {"name": name}, description=description)
            SubclassAbility.objects.update_or_create(
                subclass=moon, ability=ability, defaults={"level_required": level}
            )
            moon_abilities.append(ability)

        own_spells = []
        for data in OWN_SPELLS:
            spell = upsert(
                Spell, {"name": data["name"]},
                level=data["level"], casting_time=data["casting_time"], range=m(data["range"]),
                duration=data["duration"], description=data["description"],
            )
            spell.components.set(srd(Component, type=c) for c in data["components"])
            if "attack" in data:
                attack_name, attack_dice, attack_range, attack_area = data["attack"]
                attack = upsert(Attack, {"name": attack_name}, range=m(attack_range), area=m(attack_area))
                attack.dice.set(dice(*d) for d in attack_dice)
                spell.attacks.set([attack])
            own_spells.append(spell)
        druid.spells.add(*own_spells)

        # Test user and campaign
        test_user = User.objects.filter(username="testuser").first()
        if test_user is None:
            test_user = User(username="testuser")
            test_user.set_unusable_password()
            test_user.save()
        campaign = upsert(
            Campaign, {"name": CAMPAIGN_NAME},
            description=CAMPAIGN_DESCRIPTION, status=Campaign.Status.ACTIVA,
            start_date=datetime.date(2026, 11, 7),  # placeholder: the real start date is unknown
        )
        campaign.masters.add(test_user)
        campaign.players.add(test_user)

        # Balasar, from his level 10 druid character sheet
        balasar = upsert(
            Character, {"name": "Balasar"},
            xp=50250, hp=56, temp_hp=56, ac=14, temp_ap=0, inspiration=0,
            strength=8, dexterity=14, constitution=17, intelligence=12, wisdom=22, charisma=10,
            player=test_user, race=race, subrace=subrace, char_class=druid, subclass=moon,
            is_npc=False, template=None,
        )
        balasar.campaigns.add(campaign)
        balasar.abilities.set(race_abilities + class_abilities + moon_abilities + own_abilities)
        balasar.spells.set(srd_spells + own_spells)

        # His dart: a copy of the SRD "Dardo" template, with the values on his
        # sheet (+8 to hit, 1d6 + 2). His other items are generic and carried directly.
        balasar_dart_attack = upsert(Attack, {"name": "Dardo (Balasar)"}, extra=2, range=m(20), area=0)
        balasar_dart_attack.dice.set([dice(1, 6)])
        dart = balasar.items.filter(name=SRD_DART).first() or dart_template.make_copy()
        Weapon.objects.update_or_create(item=dart, defaults={"to_hit": "+8", "equipped": True})
        dart.weapon.attacks.set([balasar_dart_attack])
        Inventory.objects.update_or_create(character=balasar, item=dart, defaults={"quantity": 1})
        for name in OWN_ITEMS:
            item = upsert(Item, {"name": name, "template": None}, price=None)
            Inventory.objects.update_or_create(character=balasar, item=item, defaults={"quantity": 1})

        self.stdout.write(self.style.SUCCESS(
            f"Done: campaign '{campaign.name}' and character '{balasar.name}' "
            f"({len(srd_spells)} SRD spells + {len(own_spells)} of his own, subclass '{moon.name}')."
        ))
