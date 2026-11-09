"""Fill the database with sample data.

- The usual D&D 5e races and classes, with their main traits and features.
- A test user and the Alforge campaign, described from CONTEXTO_MUNDO.md.
- Balasar, built from his level 10 druid character sheet, with his
  abilities, spells and equipment. His dart is a copy of the "Dardo"
  item template; his other items are generic and carried directly.

Run it with:  python manage.py populate
It is safe to run more than once: existing rows are updated, not duplicated.
"""
import datetime

from django.core.management.base import BaseCommand
from django.db import transaction

from CampaignManager.models import (
    Ability,
    Attack,
    Campaign,
    Character,
    CharacterClass,
    ClassAbility,
    Component,
    DiceRoll,
    Effect,
    Inventory,
    Item,
    Race,
    RaceAbility,
    Spell,
    User,
    Weapon,
)


def m(feet):
    """Feet to meters with the campaign's rule 6 ft = 2 m, rounded to the nearest meter."""
    return (feet * 2 + 3) // 6


# ---------------------------------------------------------------------------
# Abilities: name -> description
# ---------------------------------------------------------------------------

ABILITIES = {
    # Racial traits
    "Versatilidad humana": "Todas sus puntuaciones de característica aumentan en 1.",
    "Visión en la oscuridad": "Ve en la penumbra a 20 m como si hubiera luz brillante, y en la oscuridad como si hubiera penumbra, aunque sin distinguir colores.",
    "Sentidos agudos": "Competencia en la habilidad Percepción.",
    "Linaje feérico": "Ventaja en las tiradas de salvación para no quedar hechizado; la magia no puede dormirlo.",
    "Trance": "No necesita dormir: medita cuatro horas al día y obtiene el mismo descanso que con ocho horas de sueño.",
    "Pies veloces": "Su velocidad base aumenta a 35 pies (12 m).",
    "Máscara de la naturaleza": "Puede intentar esconderse aunque solo lo cubran ligeramente el follaje, la lluvia, la nieve, la niebla u otros fenómenos naturales.",
    "Resistencia enana": "Ventaja en las tiradas de salvación contra veneno y resistencia al daño de veneno.",
    "Afinidad con la piedra": "Duplica su bonificador de competencia en las pruebas de Historia sobre trabajos en piedra.",
    "Afortunado": "Cuando saca un 1 en una tirada de ataque, prueba o salvación, puede repetir la tirada.",
    "Valiente": "Ventaja en las tiradas de salvación para no quedar asustado.",
    "Agilidad de mediano": "Puede atravesar el espacio de criaturas de un tamaño mayor que el suyo.",
    "Astucia gnoma": "Ventaja en las tiradas de salvación de Inteligencia, Sabiduría y Carisma contra la magia.",
    "Linaje dracónico": "Desciende de un tipo de dragón, que determina su arma de aliento y su resistencia al daño.",
    "Arma de aliento": "Exhala energía destructiva del tipo de su linaje dracónico.",
    "Resistencia dracónica": "Resistencia al tipo de daño de su linaje dracónico.",
    "Versatilidad en habilidades": "Competencia en dos habilidades a su elección.",
    "Amenazador": "Competencia en la habilidad Intimidación.",
    "Aguante incansable": "Una vez por descanso prolongado, si sus puntos de golpe caen a 0 sin morir, se queda con 1.",
    "Ataques salvajes": "Al hacer un crítico con un arma cuerpo a cuerpo, tira un dado de daño adicional del arma.",
    "Resistencia infernal": "Resistencia al daño de fuego.",
    "Legado infernal": "Conoce el truco Taumaturgia y, al subir de nivel, puede lanzar Represión infernal y Oscuridad.",
    # Class features
    "Lanzamiento de conjuros": "Puede lanzar los conjuros de su clase usando su característica mágica.",
    "Furia": "En combate entra en furia: ventaja en las pruebas de Fuerza, daño adicional cuerpo a cuerpo y resistencia al daño contundente, cortante y perforante.",
    "Defensa sin armadura": "Sin armadura, su CA es 10 + su modificador de Destreza + el de Constitución (bárbaro) o el de Sabiduría (monje).",
    "Ataque temerario": "Puede atacar con ventaja, a cambio de que los ataques contra él también la tengan hasta su siguiente turno.",
    "Inspiración bárdica": "Con una acción adicional, da a una criatura un dado de inspiración que puede sumar a una tirada.",
    "Patrón de otro mundo": "Ha hecho un pacto con un ser de otro plano que le concede sus poderes.",
    "Magia del pacto": "Lanza conjuros con espacios que recupera tras un descanso breve.",
    "Dominio divino": "Elige un dominio de su deidad, que le otorga conjuros y rasgos adicionales.",
    "Canalizar divinidad": "Canaliza energía divina para producir efectos mágicos, como expulsar muertos vivientes.",
    "Druídico": "Conoce el druídico, el idioma secreto de los druidas, y puede dejar mensajes ocultos en él.",
    "Forma salvaje": "Dos veces por descanso, puede transformarse en una bestia que haya visto antes.",
    "Enemigo predilecto": "Ventaja para rastrear y recordar información sobre un tipo de enemigo elegido.",
    "Explorador nato": "Se desenvuelve con maestría en un tipo de terreno predilecto.",
    "Estilo de combate": "Adopta un estilo de combate que le da una ventaja concreta, como Defensa o Arquería.",
    "Tomar aliento": "Una vez por descanso, con una acción adicional, recupera 1d10 + su nivel de guerrero en puntos de golpe.",
    "Oleada de acción": "Una vez por descanso, puede realizar una acción más en su turno.",
    "Origen hechicero": "Su magia innata procede de un origen, como un linaje dracónico o la magia salvaje.",
    "Fuente de magia": "Dispone de puntos de hechicería para crear espacios de conjuro o aplicar metamagia.",
    "Recuperación arcana": "Una vez al día, tras un descanso breve, recupera parte de sus espacios de conjuro.",
    "Artes marciales": "Usa la Destreza en sus ataques desarmados y con armas de monje, y puede golpear desarmado con una acción adicional.",
    "Ki": "Dispone de puntos de ki para efectos como Ráfaga de golpes o Paso del viento.",
    "Sentidos divinos": "Detecta la presencia de celestiales, infernales y muertos vivientes cercanos.",
    "Imposición de manos": "Dispone de una reserva de curación que reparte tocando a otras criaturas.",
    "Ataque furtivo": "Una vez por turno, causa daño adicional si ataca con ventaja o con un aliado junto al objetivo.",
    "Pericia": "Duplica su bonificador de competencia en dos habilidades.",
    "Acción astuta": "Puede correr, retirarse o esconderse con una acción adicional.",
    # Balasar's own features (Circle of the Moon, feat and custom trait)
    "Forma salvaje de combate": "Usa Forma salvaje como acción adicional y, transformado, puede gastar espacios de conjuro para recuperar 1d8 puntos de golpe por nivel del espacio.",
    "Formas del círculo": "Puede transformarse en bestias de mayor valor de desafío: hasta un tercio de su nivel de druida.",
    "Golpe primitivo": "Sus ataques en forma de bestia cuentan como mágicos para superar resistencias e inmunidades.",
    "Forma salvaje elemental": "Gastando dos usos de Forma salvaje, se transforma en un elemental de aire, tierra, fuego o agua.",
    "Resiliente": "Dote: aumenta en 1 una característica y obtiene competencia en sus tiradas de salvación.",
    "Ser uno más con la naturaleza": "Rasgo personalizado: entiende a las bestias y se comunica con ellas sin gastar ningún conjuro.",
}

# ---------------------------------------------------------------------------
# Races: (name, speed in feet, description, racial traits granted at level 1)
# ---------------------------------------------------------------------------

RACES = [
    ("Humano", 30, "La raza más extendida y versátil, de vida corta y gran ambición.",
     ["Versatilidad humana"]),
    ("Elfo", 30, "Pueblo longevo y elegante, ligado a la magia y a la naturaleza.",
     ["Visión en la oscuridad", "Sentidos agudos", "Linaje feérico", "Trance"]),
    ("Elfo de los bosques", 35, "Subraza de elfo: rápidos, sigilosos y ligados a los bosques.",
     ["Visión en la oscuridad", "Linaje feérico", "Trance", "Pies veloces", "Máscara de la naturaleza"]),
    ("Enano", 25, "Pueblo robusto de las montañas, famoso por su resistencia y su trabajo en piedra y metal.",
     ["Visión en la oscuridad", "Resistencia enana", "Afinidad con la piedra"]),
    ("Mediano", 25, "Pueblo pequeño, afable y sorprendentemente afortunado.",
     ["Afortunado", "Valiente", "Agilidad de mediano"]),
    ("Gnomo", 25, "Pueblo pequeño, curioso e ingenioso.",
     ["Visión en la oscuridad", "Astucia gnoma"]),
    ("Dracónido", 30, "Descendientes de dragones, de aspecto dracónico y con arma de aliento.",
     ["Linaje dracónico", "Arma de aliento", "Resistencia dracónica"]),
    ("Semielfo", 30, "Hijos de humanos y elfos, que combinan rasgos de ambos.",
     ["Visión en la oscuridad", "Linaje feérico", "Versatilidad en habilidades"]),
    ("Semiorco", 30, "Hijos de humanos y orcos, fuertes y tenaces.",
     ["Visión en la oscuridad", "Amenazador", "Aguante incansable", "Ataques salvajes"]),
    ("Tiefling", 30, "Humanos con herencia infernal, marcados por cuernos y cola.",
     ["Visión en la oscuridad", "Resistencia infernal", "Legado infernal"]),
]

# ---------------------------------------------------------------------------
# Classes: (name, primary ability, description, [(feature, level required)])
# ---------------------------------------------------------------------------

CLASSES = [
    ("Bárbaro", "Fuerza", "Guerrero feroz que canaliza su furia en combate.",
     [("Furia", 1), ("Defensa sin armadura", 1), ("Ataque temerario", 2)]),
    ("Bardo", "Carisma", "Artista inspirador cuya música y palabras tienen poder mágico.",
     [("Lanzamiento de conjuros", 1), ("Inspiración bárdica", 1)]),
    ("Brujo", "Carisma", "Lanzador de conjuros que obtiene su magia de un pacto con un ser de otro mundo.",
     [("Patrón de otro mundo", 1), ("Magia del pacto", 1)]),
    ("Clérigo", "Sabiduría", "Sacerdote que canaliza el poder de una deidad.",
     [("Lanzamiento de conjuros", 1), ("Dominio divino", 1), ("Canalizar divinidad", 2)]),
    ("Druida", "Sabiduría", "Guardián de la naturaleza que lanza conjuros y adopta formas animales.",
     [("Druídico", 1), ("Lanzamiento de conjuros", 1), ("Forma salvaje", 2)]),
    ("Explorador", "Destreza y Sabiduría", "Rastreador y cazador experto en sobrevivir en tierras salvajes.",
     [("Enemigo predilecto", 1), ("Explorador nato", 1), ("Lanzamiento de conjuros", 2)]),
    ("Guerrero", "Fuerza o Destreza", "Experto en armas, armaduras y tácticas de combate.",
     [("Estilo de combate", 1), ("Tomar aliento", 1), ("Oleada de acción", 2)]),
    ("Hechicero", "Carisma", "Lanzador de conjuros con la magia en la sangre.",
     [("Lanzamiento de conjuros", 1), ("Origen hechicero", 1), ("Fuente de magia", 2)]),
    ("Mago", "Inteligencia", "Erudito que domina la magia arcana mediante el estudio.",
     [("Lanzamiento de conjuros", 1), ("Recuperación arcana", 1)]),
    ("Monje", "Destreza y Sabiduría", "Artista marcial que canaliza el ki de su cuerpo.",
     [("Defensa sin armadura", 1), ("Artes marciales", 1), ("Ki", 2)]),
    ("Paladín", "Fuerza y Carisma", "Guerrero sagrado ligado a un juramento.",
     [("Sentidos divinos", 1), ("Imposición de manos", 1), ("Lanzamiento de conjuros", 2)]),
    ("Pícaro", "Destreza", "Especialista en sigilo, engaño y golpes precisos.",
     [("Ataque furtivo", 1), ("Pericia", 1), ("Acción astuta", 2)]),
]

# ---------------------------------------------------------------------------
# Effects applied by spells: name -> (description, duration)
# ---------------------------------------------------------------------------

EFFECTS = {
    "Silueteado": ("Brilla con luz tenue, los ataques contra él tienen ventaja y no puede volverse invisible.", "1 minuto"),
    "Hechizado": ("Considera un conocido amistoso a quien lo ha hechizado.", "1 hora"),
    "Sin rastro": ("+10 a las pruebas de Destreza (Sigilo); solo se le puede rastrear con magia.", "1 hora"),
    "Escudo de fuego": ("Resistencia al fuego o al frío; quien le golpee cuerpo a cuerpo recibe 2d8 de daño.", "10 minutos"),
    "Polimorfado": ("Transformado en una bestia: no puede hablar ni lanzar conjuros.", "1 hora"),
}

# ---------------------------------------------------------------------------
# Balasar's spells, from his character sheet.
# Range in feet (0 = Personal or Toque). An attack is
# (name, [(dice amount, sides)], range in feet, area in feet).
# ---------------------------------------------------------------------------

SPELLS = [
    dict(name="Crear llama", level=0, casting_time="1 acción", range=0, duration="10 minutos", components="VS",
         description="Crea en la mano una llama que da luz y que puede lanzarse como ataque de conjuro a distancia para causar daño de fuego. El daño aumenta con el nivel del lanzador.",
         attack=("Llama lanzada", [(1, 8)], 30, 0)),
    dict(name="Escudo de piedra", level=0, casting_time="1 acción", range=0, duration="Concentración, 1 minuto", components="VSM",
         description="Fragmentos de piedra giran alrededor del lanzador y le dan +1 a la CA mientras mantiene el truco con una acción adicional."),
    dict(name="Reparar", level=0, casting_time="1 minuto", range=0, duration="Instantáneo", components="VSM",
         description="Repara una única rotura o desgarro de un objeto, de no más de 33 cm, sin dejar rastro."),
    dict(name="Saber druídico", level=0, casting_time="1 acción", range=30, duration="Instantáneo", components="VS",
         description="Pequeños efectos naturales: predecir el tiempo de las próximas 24 horas, hacer florecer una planta, crear un efecto sensorial inofensivo o encender y apagar una vela."),
    dict(name="Crear o destruir agua", level=1, casting_time="1 acción", range=30, duration="Instantáneo", components="VSM",
         description="Crea o destruye unos 40 litros de agua, o provoca una lluvia que apaga las llamas de la zona."),
    dict(name="Curar heridas", level=1, casting_time="1 acción", range=0, duration="Instantáneo", components="VS",
         description="Una criatura que el lanzador toca recupera 1d8 puntos de golpe más el modificador de característica mágica."),
    dict(name="Fuego feérico", level=1, casting_time="1 acción", range=60, duration="Concentración, 1 minuto", components="V",
         description="Perfila con luz a las criaturas y objetos de un cubo de 7 m que fallen una salvación de Destreza; los ataques contra ellos tienen ventaja.",
         effects=["Silueteado"]),
    dict(name="Hechizar persona", level=1, casting_time="1 acción", range=30, duration="1 hora", components="VS",
         description="Un humanoide que falle una salvación de Sabiduría queda hechizado y trata al lanzador como a un conocido amistoso.",
         effects=["Hechizado"]),
    dict(name="Palabra de curación", level=1, casting_time="1 acción adicional", range=60, duration="Instantáneo", components="V",
         description="Una criatura visible recupera 1d4 puntos de golpe más el modificador de característica mágica."),
    dict(name="Calentar metal", level=2, casting_time="1 acción", range=60, duration="Concentración, 1 minuto", components="VSM",
         description="Pone al rojo vivo un objeto de metal: quien esté en contacto con él recibe daño de fuego y puede verse obligado a soltarlo.",
         attack=("Metal al rojo", [(2, 8)], 60, 0)),
    dict(name="Crecimiento espinoso", level=2, casting_time="1 acción", range=150, duration="Concentración, 10 minutos", components="VSM",
         description="El suelo en un radio de 7 m se llena de espinas: se vuelve terreno difícil y causa daño perforante por cada 2 m recorridos.",
         attack=("Espinas", [(2, 4)], 150, 20)),
    dict(name="Pasar sin rastro", level=2, casting_time="1 acción", range=0, duration="Concentración, 1 hora", components="VSM",
         description="El lanzador y sus compañeros cercanos obtienen +10 a Sigilo y no dejan huellas.",
         effects=["Sin rastro"]),
    dict(name="Conjurar animales", level=3, casting_time="1 acción", range=60, duration="Concentración, 1 hora", components="VS",
         description="Invoca espíritus feéricos con forma de bestia que obedecen al lanzador."),
    dict(name="Hablar con las plantas", level=3, casting_time="1 acción", range=0, duration="10 minutos", components="VS",
         description="Las plantas cercanas cobran una conciencia limitada: pueden comunicarse con el lanzador y cumplir órdenes sencillas."),
    dict(name="Tierra en erupción", level=3, casting_time="1 acción", range=120, duration="Instantáneo", components="VSM",
         description="Una explosión de piedra y tierra estalla en un cubo de 7 m; las criaturas de la zona hacen una salvación de Destreza y el terreno se vuelve difícil.",
         attack=("Erupción", [(3, 12)], 120, 20)),
    dict(name="Escudo de fuego", level=4, casting_time="1 acción", range=0, duration="10 minutos", components="VSM",
         description="Llamas cálidas o gélidas envuelven al lanzador: le dan resistencia al frío o al fuego y dañan a quien le golpee cuerpo a cuerpo.",
         attack=("Llamas del escudo", [(2, 8)], 5, 0), effects=["Escudo de fuego"]),
    dict(name="Marchitar", level=4, casting_time="1 acción", range=30, duration="Instantáneo", components="VS",
         description="Un objetivo hace una salvación de Constitución y recibe daño necrótico, la mitad si la supera. Es más potente contra plantas.",
         attack=("Marchitar", [(8, 8)], 30, 0)),
    dict(name="Polimorfar", level=4, casting_time="1 acción", range=60, duration="Concentración, 1 hora", components="VSM",
         description="Transforma a una criatura en una bestia de valor de desafío igual o inferior al suyo.",
         effects=["Polimorfado"]),
    dict(name="Conjurar elemental", level=5, casting_time="1 minuto", range=90, duration="Concentración, 1 hora", components="VSM",
         description="Invoca un elemental de aire, tierra, fuego o agua que obedece al lanzador mientras mantenga la concentración."),
]

# ---------------------------------------------------------------------------
# The campaign, from CONTEXTO_MUNDO.md (sections 2, 3.1 and 10)
# ---------------------------------------------------------------------------

DART_DESCRIPTION = "Arma sencilla arrojadiza: 1d6 de daño perforante, alcance 7/20 m. Sutil."

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


def dice(amount, sides):
    return upsert(DiceRoll, {"amount": amount, "sides": sides})


class Command(BaseCommand):
    help = "Fill the database with D&D 5e races and classes, the Alforge campaign and Balasar."

    @transaction.atomic
    def handle(self, *args, **options):
        abilities = {
            name: upsert(Ability, {"name": name}, description=description)
            for name, description in ABILITIES.items()
        }

        # Races and their traits
        races = {}
        for name, speed, description, traits in RACES:
            race = upsert(Race, {"name": name}, speed=m(speed), description=description)
            for trait in traits:
                RaceAbility.objects.update_or_create(
                    race=race, ability=abilities[trait], defaults={"level_required": 1}
                )
            races[name] = race

        # Classes and their features
        classes = {}
        for name, primary, description, features in CLASSES:
            char_class = upsert(
                CharacterClass, {"name": name}, primary_ability=primary, description=description
            )
            for feature, level in features:
                ClassAbility.objects.update_or_create(
                    char_class=char_class, ability=abilities[feature],
                    defaults={"level_required": level},
                )
            classes[name] = char_class

        # Spells, with their components, attacks and effects
        components = {letter: upsert(Component, {"type": letter}) for letter in "VSM"}
        effects = {
            name: upsert(Effect, {"name": name}, description=description, duration=duration)
            for name, (description, duration) in EFFECTS.items()
        }
        spells = []
        for data in SPELLS:
            spell = upsert(
                Spell, {"name": data["name"]},
                level=data["level"], casting_time=data["casting_time"], range=m(data["range"]),
                duration=data["duration"], description=data["description"],
            )
            spell.components.set(components[letter] for letter in data["components"])
            spell.effects.set(effects[name] for name in data.get("effects", []))
            if "attack" in data:
                attack_name, attack_dice, attack_range, attack_area = data["attack"]
                attack = upsert(Attack, {"name": attack_name}, range=m(attack_range), area=m(attack_area))
                attack.dice.set(dice(*d) for d in attack_dice)
                spell.attacks.set([attack])
            spells.append(spell)
        classes["Druida"].spells.add(*spells)

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
            player=test_user, race=races["Elfo de los bosques"], char_class=classes["Druida"],
            is_npc=False, template=None,
        )
        balasar.campaigns.add(campaign)
        balasar.abilities.set(abilities[name] for name in [
            "Visión en la oscuridad", "Linaje feérico", "Trance", "Pies veloces", "Máscara de la naturaleza",
            "Druídico", "Lanzamiento de conjuros", "Forma salvaje", "Forma salvaje de combate",
            "Formas del círculo", "Golpe primitivo", "Forma salvaje elemental",
            "Resiliente", "Ser uno más con la naturaleza",
        ])
        balasar.spells.set(spells)

        # Equipment. Weapons are carried as copies of an item template, so the
        # "Dardo" template holds the generic dart (1d6, +0 to hit) and Balasar
        # carries his own copy with the values on his sheet (1d6 + 2, +8 to hit).
        dart_attack = upsert(Attack, {"name": "Dardo"}, extra=0, range=m(20), area=0)
        dart_attack.dice.set([dice(1, 6)])
        dart_template = (
            Item.objects.filter(name="Dardo", template__isnull=True, inventory__isnull=True).first()
            or Item.objects.create(name="Dardo")
        )
        dart_template.description = DART_DESCRIPTION
        dart_template.save()
        template_weapon, _ = Weapon.objects.update_or_create(
            item=dart_template, defaults={"to_hit": "+0", "equipped": False}
        )
        template_weapon.attacks.set([dart_attack])

        balasar_dart_attack = upsert(Attack, {"name": "Dardo (Balasar)"}, extra=2, range=m(20), area=0)
        balasar_dart_attack.dice.set([dice(1, 6)])
        dart = balasar.items.filter(name="Dardo").first() or dart_template.make_copy()
        dart.template = dart_template  # also turns a dart from an older run into a copy
        dart.description = dart_template.description
        dart.save()
        dart_weapon, _ = Weapon.objects.update_or_create(
            item=dart, defaults={"to_hit": "+8", "equipped": True}
        )
        dart_weapon.attacks.set([balasar_dart_attack])
        Inventory.objects.update_or_create(character=balasar, item=dart, defaults={"quantity": 1})

        # Generic items: templates carried directly
        for name in ("Bolsa de trucos de canela", "Cogollos infinitos"):
            item = upsert(Item, {"name": name, "template": None})
            Inventory.objects.update_or_create(character=balasar, item=item, defaults={"quantity": 1})

        self.stdout.write(self.style.SUCCESS(
            f"Done: {len(races)} races, {len(classes)} classes, {len(abilities)} abilities, "
            f"{len(spells)} spells, campaign '{campaign.name}' and character '{balasar.name}'."
        ))
