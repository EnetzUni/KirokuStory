"""Build the demo campaigns of the reference prototype and save them as a fixture.

For maintainers: the demo is loaded with `python manage.py cargar_demo`, which
reads CampaignManager/fixtures/demo_campana.json. This command regenerates that
fixture. Run it on a database with only the two rules fixtures loaded:

    python manage.py migrate
    python manage.py loaddata datos_iniciales_srd armas_caseras
    python manage.py generar_demo --output CampaignManager/fixtures/demo_campana.json

The data is copied from the prototype (prototipo_referencia.dc.html, seed()).
What the models do not have (next session, attendance, NPC attitudes...) is left out.
"""
import datetime
import json
import re
from pathlib import Path

from django.apps import apps
from django.contrib.auth.models import Group
from django.core import serializers
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from CampaignManager import rules
from CampaignManager.campaign_models import BOARD_HEIGHT, BOARD_WIDTH
from CampaignManager.models import (
    Ability, ActiveEffect, Armor, ArmorType, BoardCard, BoardLink, Campaign, Character,
    CharacterAbility, CharacterClass, Combatant, Effect, Encounter, Inventory, Item, Map,
    MapLocation, Page, PartyMove, Post, Quest, QuestObjective, Race, Scene, Session, Spell,
    Subclass, Subrace, TimelineEvent, UniqueSkill, User, Weapon,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
DEMO_PASSWORD = "mesa-demo-2026"
GOLD = "Pieza de oro (po)"

USERS = {"carlos": "Carlos", "lucia": "Lucía", "iker": "Iker", "ane": "Ane", "unai": "Unai", "nerea": "Nerea", "mikel": "Mikel"}
# Staff users and their admin groups (migration 0018): carlos has both roles; nerea only
# edits the rules catalog and mikel only the campaigns, to show the two roles apart.
STAFF_GROUPS = {"carlos": ["Reglas", "Campañas"], "nerea": ["Reglas"], "mikel": ["Campañas"]}

ARMOR_TYPES = {"Ligera": (1, 1), "Media": (2, 1), "Pesada": (3, 0)}
# name, type, own AP, own magic AP
ARMORS = [
    ("Gambesón", "Ligera", 0, 0), ("Cuero tachonado", "Ligera", 1, 1), ("Brigantina", "Media", 1, 0),
    ("Cota de mallas", "Pesada", 2, 0), ("Túnica rúnica", "Ligera", 0, 3),
]
NEW_ITEMS = {
    "Libro de conjuros": "Las páginas de Aria, con sus conjuros copiados a mano.",
    "Tiza": "Para marcar paredes y copiar runas.",
    "Flechas": "Munición para arcos.",
    "Anillo de plata con runa": "Lo encontró Kaida en las ruinas de Vel-Ithar.",
}
# Abilities of the prototype that are not in the SRD: name -> (description, unique)
NEW_ABILITIES = {
    "Grito de guerra": ("Habilidad casera de prueba: los aliados adyacentes tienen +1 to hit hasta tu siguiente turno.", False),
    "Grito salvaje": ("Habilidad casera de prueba: +2 al daño en este turno.", False),
    "Pacto de la Ceniza": ("Habilidad única de Lady Morwen.", True),
}
SUBRACES = {"alto elfo": "Linaje élfico: alto elfo", "infernal": "Legado infernal: infernal", "abisal": "Legado infernal: abisal"}


def it(name, kind, qty=1, equipped=False):
    return {"name": name, "kind": kind, "qty": qty, "eq": equipped}


# Abilities with uses: (ability, max uses, recharge, cooldown rounds, uses left or cooldown left)
def uses(name, max_uses, recharge, left):
    return {"name": name, "max": max_uses, "rest": recharge, "left": left}


def cooldown(name, rounds, left):
    return {"name": name, "cd": rounds, "left": left}


ORC = dict(race="Orco", cls="Guerrero", xp=300, hp=15, thp=15, ac=13, tap=2, st=[16, 12, 16, 7, 11, 10],
           acts=[cooldown("Grito salvaje", 3, 0)],
           inv=[it("Hachuela", "weapon", equipped=True), it("Cuero tachonado", "armor", equipped=True)])

CHARACTERS = {
    "aria": dict(name="Aria", player="lucia", race="Elfo", sub="alto elfo", cls="Mago", scl="Evocador", xp=6500, hp=28, thp=24,
                 ac=12, tap=1, st=[8, 14, 13, 17, 12, 10], mp=9, notes="Preguntar a Oswin qué hay en la caja.",
                 unique=("Lectora de runas", "Entiende cualquier runa o escritura mágica que vea, aunque no conozca la lengua. Una vez por descanso largo, al tocar una runa, sabe quién la trazó y cuándo.", True),
                 bg="Aprendiz expulsada de la Academia de Puerto Gris por leer libros prohibidos. Busca a quien escribió las runas del pacto.",
                 acts=[uses("Recuperación arcana", 1, "largo", 1), cooldown("Alto elfo: paso brumoso", 3, 1)],
                 spells=["Luz", "Mano de mago", "Rayo de escarcha", "Toque helado", "Armadura de mago", "Detectar magia",
                         "Dormir", "Escudo", "Proyectil mágico", "Rayo abrasador", "Bola de fuego"],
                 inv=[it("Daga", "weapon", equipped=True), it("Túnica rúnica", "armor", equipped=True), it(GOLD, "item", 57),
                      it("Poción de curación", "item"), it("Libro de conjuros", "item"), it("Tiza", "item", 4)]),
    "brom": dict(name="Brom", player="iker", race="Enano", cls="Guerrero", scl="Campeón", xp=2700, hp=40, thp=33, ac=16, tap=3,
                 insp=1, st=[18, 12, 16, 9, 11, 10], notes="Le debo 20 po a Kaida.",
                 bg="Herrero de Robleda. Su hermano desapareció en el incendio de los muelles de Puerto Gris.",
                 acts=[uses("Tomar aliento", 2, "corto", 1), uses("Acción súbita", 1, "corto", 1), cooldown("Grito de guerra", 3, 0)],
                 inv=[it("Gran mazo", "weapon", equipped=True), it("Katana", "weapon"), it("Cota de mallas", "armor", equipped=True),
                      it("Brigantina", "armor"), it(GOLD, "item", 124), it("Poción de curación", "item", 2), it("Antorcha", "item", 3)]),
    "kaida": dict(name="Kaida", player="ane", race="Tiefling", sub="infernal", cls="Pícaro", scl="Ladrón", xp=2700, hp=27, thp=19,
                  ac=14, tap=2, st=[10, 17, 12, 13, 10, 14], fx=[("Envenenado", 2)],
                  unique=("Suerte de ladrona", "Una vez por descanso largo, cuando falla una tirada de Destreza, puede repetirla y quedarse con el nuevo resultado.", False),
                  bg="Ladrona con deudas en los muelles. Dice que la corona no le importa; nadie la cree.",
                  acts=[cooldown("Golpe astuto", 2, 0), uses("Legado infernal: reprensión infernal", 1, "largo", 0)],
                  inv=[it("Arco corto", "weapon", equipped=True), it("Daga", "weapon", equipped=True), it("Revolver", "weapon"),
                       it("Cuero tachonado", "armor", equipped=True), it(GOLD, "item", 210), it("Flechas", "item", 18),
                       it("Anillo de plata con runa", "item")]),
    "hana": dict(name="Hana", player="ane", race="Humano", cls="Monje", scl="Guerrero de la mano abierta", xp=900, hp=22, thp=22,
                 ac=15, tap=0, st=[12, 16, 13, 10, 15, 8],
                 bg="Monja del templo del río. Busca al ladrón que se llevó las linternas sagradas.",
                 acts=[cooldown("Golpe aturdidor", 1, 0)], inv=[it("Lanza", "weapon", equipped=True), it(GOLD, "item", 30)]),
    "orco": dict(name="Saqueador orco", npc=True, **ORC),
    "oswin": dict(name="Oswin, el mercader", npc=True, race="Mediano", cls="Bardo", xp=0, hp=9, thp=9, ac=10, tap=0,
                  st=[9, 11, 10, 14, 13, 15], mp=2, inv=[it(GOLD, "item", 900), it("Poción de curación", "item", 6)]),
    "morwen": dict(name="Lady Morwen", npc=True, race="Tiefling", sub="abisal", cls="Brujo", scl="Patrón infernal", xp=14000,
                   hp=44, thp=44, ac=15, tap=3, st=[10, 14, 14, 12, 13, 18], mp=6,
                   acts=[uses("Pacto de la Ceniza", 1, "largo", 1)],
                   inv=[it("Revolver", "weapon", equipped=True), it("Brigantina", "armor", equipped=True)]),
}

CAMPAIGNS = {
    "corona": dict(name="La Corona de Ceniza", status="activa", masters=["carlos"], players=["lucia", "iker", "ane", "unai"],
                   chars=["aria", "brom", "kaida", "oswin", "morwen"], day="Día 9 de Brumaluz", year="año 1203", start="2026-09-12",
                   notice="Traed la ficha al día: la sesión 7 empieza subiendo a nivel 5.",
                   description="Una corona partida en dos, un mercader que paga demasiado y una torre que vigila desde las ruinas."),
    "ecos": dict(name="Ecos de Kioto", status="pausada", masters=["carlos", "iker"], players=["lucia", "ane"], chars=["aria", "hana"],
                 day="Festival de las linternas", year="año del Tigre", start="2026-03-07", notice="En pausa hasta enero."),
    "prologo": dict(name="Prólogo en Puerto Gris", status="terminada", masters=["carlos"], players=["iker", "ane"],
                    chars=["brom", "kaida"], day="Último día de otoño", year="año 1202", start="2025-11-02", notice=""),
}

# number, date, world day, title, body, xp, loot, master notes
SESSIONS = {
    "corona": [
        (1, "2026-09-12", "Días 2 y 3 de Brumaluz", "Humo en Puerto Gris", "Aria, Brom y Kaida coincidieron en [[El Ancla Rota]] la noche en que ardieron los muelles. Brom buscaba a su hermano entre el humo; Kaida, a alguien que le debía dinero.\n\nA la mañana siguiente, [[Oswin, el mercader]] les ofreció 2.000 piezas de oro por recuperar [[La Corona de Ceniza]].", 300, "40 po", "Oswin miente sobre de dónde salió la corona."),
        (2, "2026-09-26", "Días 4 y 5 de Brumaluz", "El camino del faro", "Salieron de [[Puerto Gris]] por la costa. En el faro abandonado les esperaban los [[Saqueadores orcos]], con un emblema que nadie supo reconocer.\n\nLlegaron a [[Robleda]] al anochecer, con una hachuela de más y menos paciencia.", 450, "Una hachuela y 25 po", "Los orcos llevan el emblema de la torre."),
        (3, "2026-10-10", "Día 6 de Brumaluz", "La boda de Robleda", "El [[Alcalde Bertrán]] casaba a su hija y el grupo estaba invitado. Kaida brindó con él; poco después, el sello del alcalde ya no estaba en su bolsillo.\n\nLa novia habló de luces que se ven de noche en el [[Bosque de Ceniza]].", 300, "Sello del alcalde", "Bertrán sospecha de Kaida."),
        (4, "2026-10-24", "Día 7 de Brumaluz", "Entre los árboles quemados", "El bosque no termina de caer. [[Aria]] copió las runas grabadas en los troncos y descubrió que juntas forman una llave.", 450, "Nada", "Las runas son las mismas que las del pacto de Morwen."),
        (5, "2026-11-07", "Día 8 de Brumaluz", "Las puertas de Vel-Ithar", "Las runas abrieron la primera puerta de las [[Ruinas de Vel-Ithar]]. Desde lo alto de la escalera se veía la [[Torre de Morwen]].", 600, "Llave de hueso", "Morwen ya sabe que están aquí."),
        (6, "2026-11-14", "Día 9 de Brumaluz", "Lo que duerme bajo las ruinas", "Los guardianes de ceniza cayeron uno a uno. En la cámara central encontraron la primera mitad de [[La Corona de Ceniza]].\n\nEsa noche, [[Kaida]] no le contó a nadie lo del anillo.", 700, "Mitad de la Corona de Ceniza", "Si investigan el pozo, pista hacia el Santuario hundido."),
    ],
    "ecos": [(1, "2026-03-07", "Festival de las linternas", "Linternas en el río", "Hana y Aria llegaron a la ciudad durante el festival de las linternas.", 300, "Nada", "")],
    "prologo": [(1, "2025-11-02", "Último día de otoño", "Niebla en el puerto", "Brom y Kaida escaparon de Puerto Gris la noche del primer incendio.", 300, "10 po", "")],
}

# map key: (name, image, [locations: key, name, x, y, status], [moves: session, location])
MAPS = {
    "corona": [
        ("valcendra", "Valcendra", "demo/valcendra.svg", [
            ("pg", "Puerto Gris", 19, 68.8, "visitado"), ("faro", "Camino del Faro", 30, 52.8, "visitado"),
            ("robleda", "Robleda", 43, 60.8, "visitado"), ("bosque", "Bosque de Ceniza", 56, 43.2, "visitado"),
            ("ruinas", "Ruinas de Vel-Ithar", 72, 32, "visitado"), ("torre", "Torre de Morwen", 87, 17.6, "conocido"),
            ("cuervo", "Paso del Cuervo", 79, 68.8, "conocido"), ("santuario", "Santuario hundido", 10.5, 40, "oculto"),
        ], [(1, "pg"), (2, "faro"), (2, "robleda"), (4, "bosque"), (5, "ruinas")]),
        ("puertogris", "Puerto Gris", "demo/puerto-gris.svg", [
            ("pg_ancla", "El Ancla Rota", 33, 57.6, "visitado"), ("pg_mercado", "Tienda de Oswin", 50, 44.8, "visitado"),
            ("pg_muelles", "Muelles", 21.5, 79.2, "visitado"), ("pg_templo", "Templo de la Llama", 66, 32, "conocido"),
            ("pg_alto", "Barrio Alto", 82, 20.8, "oculto"),
        ], []),
    ],
}

# key, session, world day, title, kind, characters, location, hidden
EVENTS = {
    "corona": [
        ("e1", 1, "Día 2 de Brumaluz", "El grupo se conoce en El Ancla Rota", "social", ["aria", "brom", "kaida"], "pg_ancla", False),
        ("e2", 1, "Día 2 de Brumaluz", "Arden los muelles", "combate", ["brom", "kaida"], "pg_muelles", False),
        ("e3", 1, "Día 3 de Brumaluz", "Oswin les encarga recuperar la Corona de Ceniza", "mision", ["aria", "brom", "kaida"], "pg_mercado", False),
        ("e4", 2, "Día 4 de Brumaluz", "Salen por el Camino del Faro", "viaje", ["aria", "brom", "kaida"], "faro", False),
        ("e5", 2, "Día 5 de Brumaluz", "Emboscada de saqueadores orcos", "combate", ["aria", "brom", "kaida"], "faro", False),
        ("e6", 3, "Día 6 de Brumaluz", "Escena: La boda de Robleda", "social", ["aria", "brom", "kaida"], "robleda", False),
        ("e7", 3, "Día 6 de Brumaluz", "El sello del alcalde desaparece", "descubrimiento", ["kaida"], "robleda", False),
        ("e8", 4, "Día 7 de Brumaluz", "Aria copia las runas del bosque", "descubrimiento", ["aria"], "bosque", False),
        ("e9", 5, "Día 8 de Brumaluz", "Se abre la primera puerta de Vel-Ithar", "descubrimiento", ["aria", "brom", "kaida"], "ruinas", False),
        ("e10", 5, "Día 8 de Brumaluz", "Lady Morwen sabe que el grupo ha llegado", "secreto", [], "torre", True),
        ("e11", 6, "Día 9 de Brumaluz", "Combate contra los guardianes de ceniza", "combate", ["aria", "brom", "kaida"], "ruinas", False),
        ("e12", 6, "Día 9 de Brumaluz", "Encuentran la primera mitad de la corona", "mision", ["aria", "brom", "kaida"], "ruinas", False),
        ("e13", 6, "Día 9 de Brumaluz", "El pozo de las ruinas lleva al Santuario hundido", "descubrimiento", [], "santuario", True),
    ],
    "ecos": [("k1", 1, "Festival de las linternas", "Hana y Aria se conocen en el puente", "social", ["aria", "hana"], None, False)],
    "prologo": [("p1", 1, "Último día de otoño", "Brom y Kaida escapan del puerto en llamas", "viaje", ["brom", "kaida"], None, False)],
}

# key, category, title, body, secret, revealed, parent, map location
PAGES = {
    "corona": [
        ("w_valcendra", "lugar", "Valcendra", "Región costera donde transcurre la campaña, entre el Mar de las Brumas y la Sierra Gris. Su capital es [[Puerto Gris]].", "", True, None, None),
        ("w_pg", "lugar", "Puerto Gris", "Ciudad portuaria en la costa de Valcendra y punto de partida del grupo. Desde el incendio, la mitad de los muelles son tablones negros.\n\nAquí están [[El Ancla Rota]] y la tienda de [[Oswin, el mercader]]. Los sacerdotes del [[Templo de la Llama]] curan a quien puede pagar.", "En el Barrio Alto vive quien compró la corona hace años.", True, "w_valcendra", "pg"),
        ("w_ancla", "lugar", "El Ancla Rota", "Taberna junto al puerto de [[Puerto Gris]]. Buena sopa, peores clientes. Aquí se conoció el grupo.", "", True, "w_pg", "pg_ancla"),
        ("w_robleda", "lugar", "Robleda", "Aldea de leñadores a medio camino entre la costa y el [[Bosque de Ceniza]]. La gobierna el [[Alcalde Bertrán]].", "", True, "w_valcendra", "robleda"),
        ("w_bosque", "lugar", "Bosque de Ceniza", "Árboles quemados que no terminan de caer. En los troncos hay runas élficas que [[Aria]] copió en la sesión 4.", "Las runas son las mismas que las del pacto de [[Lady Morwen]].", True, "w_valcendra", "bosque"),
        ("w_ruinas", "lugar", "Ruinas de Vel-Ithar", "Ciudad élfica enterrada en ceniza. El grupo abrió su primera puerta con las runas del [[Bosque de Ceniza]] y acampa entre sus columnas desde la sesión 6.\n\nAquí encontraron la primera mitad de [[La Corona de Ceniza]].", "El pozo central baja hasta el [[Santuario hundido]].", True, "w_valcendra", "ruinas"),
        ("w_torre", "lugar", "Torre de Morwen", "Se ve desde las ruinas. En [[Robleda]] nadie se acerca a ella. Es la casa de [[Lady Morwen]].", "", True, "w_valcendra", "torre"),
        ("w_santuario", "lugar", "Santuario hundido", "Un templo bajo el mar, frente a la costa. Lo guarda [[Ithra]].", "Aquí está la otra mitad de [[La Corona de Ceniza]].", False, "w_valcendra", "santuario"),
        ("w_oswin", "pnj", "Oswin, el mercader", "Mercader de [[Puerto Gris]]. Paga bien y pregunta poco. Fue quien encargó al grupo recuperar [[La Corona de Ceniza]].", "Él mismo vendió la corona al ladrón hace años.", True, "w_pg", None),
        ("w_morwen", "pnj", "Lady Morwen", "Señora de la [[Torre de Morwen]]. En [[Robleda]] bajan la voz al decir su nombre.", "Sabe que el grupo tiene media corona y busca la otra mitad bajo el agua. Tiene una habilidad única: el Pacto de la Ceniza.", True, "w_torre", None),
        ("w_bertran", "pnj", "Alcalde Bertrán", "Alcalde de [[Robleda]]. Pagó al grupo por limpiar el camino del faro y desde la boda de su hija busca su sello.", "Sospecha de Kaida.", True, "w_robleda", None),
        ("w_ithra", "pnj", "Ithra", "Guardiana del [[Santuario hundido]].", "Lleva siglos esperando bajo el agua.", False, "w_santuario", None),
        ("w_orcos", "pnj", "Saqueadores orcos", "Banda que asalta caravanas entre [[Puerto Gris]] y [[Robleda]]. El grupo se los encontró en el faro abandonado.", "Trabajan para [[Lady Morwen]].", True, None, None),
        ("w_llama", "faccion", "Templo de la Llama", "Sacerdotes de [[Puerto Gris]] que curan heridas, por un precio.", "", True, "w_pg", None),
        ("w_corona", "objeto", "La Corona de Ceniza", "Una corona partida en dos. El grupo recuperó la primera mitad en las [[Ruinas de Vel-Ithar]] y [[Oswin, el mercader]] paga por ella.", "La otra mitad está en el [[Santuario hundido]].", True, None, None),
        ("w_anillo", "objeto", "Anillo de plata con runa", "[[Kaida]] lo encontró en las ruinas y no se lo ha dicho a nadie.", "", False, None, None),
        ("w_hespern", "lugar", "Hespern", "Reino al otro lado de la Sierra Gris. Sus ciudades principales son [[Rontal]] y [[Pirella]].", "", True, None, None),
        ("w_rontal", "lugar", "Rontal", "Capital de [[Hespern]], famosa por sus forjas.", "", True, "w_hespern", None),
        ("w_pirella", "lugar", "Pirella", "Puerto fluvial de [[Hespern]].", "", True, "w_hespern", None),
        ("w_sokoku", "lugar", "Sokoku", "Islas al otro lado del Mar de las Brumas. Se llega en barco desde [[Puerto Gris]]. Sus ciudades son [[Koto]] e [[Iga]].", "", True, None, None),
        ("w_koto", "lugar", "Koto", "Ciudad de templos de [[Sokoku]].", "", True, "w_sokoku", None),
        ("w_iga", "lugar", "Iga", "Aldea de montaña de [[Sokoku]].", "Esconde una escuela de espías.", True, "w_sokoku", None),
        ("w_academia", "faccion", "Academia de magia", "Escuela de magia de [[Puerto Gris]]. Expulsó a [[Aria]] por leer libros prohibidos. La dirigen [[LaPlace]] y [[Hopgkings]].", "", True, "w_pg", None),
        ("w_laplace", "pnj", "LaPlace", "Rectora de la [[Academia de magia]]. Firmó la expulsión de Aria.", "", True, "w_academia", None),
        ("w_hopgkings", "pnj", "Hopgkings", "Profesor de runas de la [[Academia de magia]]. Quizá sepa leer élfico antiguo.", "Fue él quien escribió las runas del pacto.", True, "w_academia", None),
    ],
    "ecos": [("k_rio", "lugar", "El puente de las linternas", "Puente sobre el río donde se conocieron Hana y Aria.", "", True, None, None)],
}

# title, status, giver page, description, objectives (text, done, hidden)
QUESTS = {
    "corona": [
        ("La Corona de Ceniza", "activa", "w_oswin", "Misión principal. Recompensa: 2.000 po.",
         [("Encontrar el rastro del ladrón", True, False), ("Llegar a las Ruinas de Vel-Ithar", True, False),
          ("Recuperar la primera mitad de la corona", True, False), ("Descubrir dónde está la otra mitad", False, False),
          ("Averiguar qué esconde Oswin", False, True), ("Entregar la corona a Oswin", False, False)]),
        ("Las runas del pacto", "activa", None, "Misión personal de Aria. Recompensa: respuestas.",
         [("Copiar las runas del bosque", True, False), ("Encontrar a alguien que lea élfico antiguo", False, False),
          ("Descubrir quién escribió las runas", False, True)]),
        ("Saqueadores en el Camino del Faro", "completada", "w_bertran", "Misión secundaria. Recompensa: 300 po.",
         [("Limpiar el camino", True, False)]),
        ("Una deuda en los muelles", "fallida", None, "Misión personal de Kaida, por encargo de un contrabandista. Recompensa: que la dejen en paz. El contrabandista huyó de Puerto Gris.",
         [("Pagar 200 po antes de la sesión 3", False, False)]),
    ],
    "ecos": [("El ladrón de linternas", "activa", None, "Misión principal, por encargo de la dama del río. Recompensa: un favor.",
              [("Encontrar al ladrón", False, False)])],
}

# Adventure title -> (session it starts in, session it ends in or None while in progress).
QUEST_SESSIONS = {
    "La Corona de Ceniza": (1, None), "Las runas del pacto": (4, None), "Saqueadores en el Camino del Faro": (2, 2),
    "Una deuda en los muelles": (1, 3), "El ladrón de linternas": (1, None),
}

# key, x, y, title, text, author, page, classified, revealed. x and y are on a 1100x640 area that
# is placed in the middle of the (bigger) board.
BOARD_OFFSET = ((BOARD_WIDTH - 1100) // 2, (BOARD_HEIGHT - 640) // 2)
BOARD = {
    "corona": ([
        ("b1", 440, 30, "La Corona de Ceniza", "Partida en dos. Tenemos una mitad.", "carlos", "w_corona", False, False),
        ("b2", 80, 50, "Oswin, el mercader", "¿Por qué paga tanto por ella?", "lucia", "w_oswin", False, False),
        ("b3", 820, 40, "Lady Morwen", "En Robleda bajan la voz al nombrarla.", "iker", "w_morwen", False, False),
        ("b4", 830, 300, "Saqueadores orcos", "Llevan un emblema que no reconocemos.", "iker", "w_orcos", False, False),
        ("b5", 450, 270, "Las runas del bosque", "Juntas forman una llave. ¿Quién las escribió?", "lucia", None, False, False),
        ("b8", 90, 225, "Barrio Alto", "Alguien de allí compró la corona.", "ane", None, False, False),
        ("b6", 70, 420, "El vendedor", "Oswin vendió la corona al ladrón hace años.", "carlos", None, True, False),
        ("b7", 450, 470, "La otra mitad", "Está bajo el mar, en el Santuario hundido.", "carlos", None, True, False),
        ("b9", 830, 480, "La torre vigila", "Morwen sabe que estamos en las ruinas.", "carlos", None, True, True),
    ], [("b2", "b1"), ("b1", "b3"), ("b3", "b4"), ("b5", "b1"), ("b5", "b3"), ("b6", "b2"), ("b7", "b1"), ("b8", "b2"), ("b9", "b3")]),
}

# title, open, location page, participants, posts: (author, as character or None, text, roll, whisper to)
SCENES = {
    "corona": [
        ("Guardia en las ruinas", True, "w_ruinas", ["aria", "brom", "kaida"], [
            ("carlos", None, "Cae la noche sobre Vel-Ithar. El fuego apenas calienta y algo cruje entre las columnas.", "", None),
            ("iker", "brom", "Yo hago la primera guardia. Si eso que cruje tiene dientes, que venga.", "", None),
            ("ane", "kaida", "¿Puedo mirar si hay huellas junto a las columnas?", "", None),
            ("carlos", None, "Claro. Tira Sabiduría (Percepción).", "", None),
            ("ane", "kaida", "Me agacho junto a la columna más cercana y paso los dedos por la ceniza.", "1d20 → 14", None),
            # A player's whisper to the masters: whispered to their own character.
            ("ane", "kaida", "Si encuentro algo brillante, me lo guardo sin decir nada.", "", "kaida"),
            ("carlos", None, "Encuentras un anillo de plata con una runa. Nadie te ha visto.", "", "kaida"),
            ("iker", "brom", "Aria, ¿tú no dormías? Llevas toda la noche con ese libro.", "", None),
        ]),
        ("Carta para Oswin", True, "w_pg", ["aria"], [
            ("lucia", "aria", "«Oswin: tenemos la mitad de lo que buscas. La otra mitad no está donde dijiste.»", "", None),
            ("carlos", "oswin", "«Mi querida Aria: entonces buscad mejor. Y no abráis la caja.»", "", None),
        ]),
        ("La boda de Robleda", False, "w_robleda", ["aria", "brom", "kaida"], [
            ("carlos", None, "La plaza huele a pan dulce y a cerveza. El alcalde Bertrán no deja de tocarse el bolsillo.", "", None),
            ("ane", "kaida", "Me acerco al alcalde con dos jarras. Una es para él.", "", None),
            ("lucia", "aria", "Mientras Kaida lo distrae, pregunto a la novia por las luces que se ven en el bosque.", "", None),
            ("carlos", None, "El alcalde Bertrán: «¡Nadie entra en ese bosque! Y usted, señorita, devuélvame la jarra.»", "", None),
        ]),
    ],
    "ecos": [("El puente de las linternas", True, "k_rio", ["aria", "hana"], [
        ("carlos", None, "El río está lleno de linternas de papel. Alguien grita en el puente.", "", None)])],
}

# Combat in progress: (character, initiative)
TURNS = {"corona": (2, 0, [("kaida", 19), ("orco1", 15), ("brom", 12), ("aria", 10), ("orco2", 6)])}


def first_sentence(text):
    plain = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text).split("\n")[0]
    match = re.match(r"^(.*?[.!?])(\s|$)", plain)
    return match.group(1) if match else plain


class Command(BaseCommand):
    help = "Build the demo campaigns of the prototype and save them as a fixture."

    def add_arguments(self, parser):
        parser.add_argument("--output", help="Write the demo rows to this fixture file")

    def handle(self, *args, **options):
        if not Item.objects.filter(name="Katana").exists() or not Race.objects.filter(name="Elfo").exists():
            raise CommandError("Load the rules first: python manage.py loaddata datos_iniciales_srd armas_caseras")
        if Campaign.objects.exists() or User.objects.exists():
            raise CommandError("Run this on a database with only the rules fixtures loaded (no campaigns, no users).")
        with transaction.atomic():
            self.build()
        if options["output"]:
            self.export(Path(options["output"]))

    # ------------------------------------------------------------------
    def build(self):
        self.users = {}
        for username, first_name in USERS.items():
            user = User(username=username, first_name=first_name, email=f"{username}@example.com", is_staff=username in STAFF_GROUPS)
            user.set_password(DEMO_PASSWORD)
            user.save()
            self.users[username] = user
        for username, group_names in STAFF_GROUPS.items():
            self.users[username].groups.add(*Group.objects.filter(name__in=group_names))

        types = {name: ArmorType.objects.create(type=name, ap=ap, magic_ap=map_) for name, (ap, map_) in ARMOR_TYPES.items()}
        for name, kind, ap, map_ in ARMORS:
            item = Item.objects.create(name=name, description=f"Armadura {kind.lower()}.")
            Armor.objects.create(item=item, armor_type=types[kind], ap=ap, magic_ap=map_)
        for name, description in NEW_ITEMS.items():
            Item.objects.create(name=name, description=description)
        for name, (description, unique) in NEW_ABILITIES.items():
            Ability.objects.create(name=name, description=description, unique=unique)

        self.campaigns = {}
        for key, data in CAMPAIGNS.items():
            campaign = Campaign.objects.create(
                name=data["name"], status=data["status"], description=data.get("description", ""),
                start_date=datetime.date.fromisoformat(data["start"]), notice=data["notice"],
                world_day=data["day"], world_year=data["year"],
            )
            campaign.masters.set(self.users[u] for u in data["masters"])
            campaign.players.set(self.users[u] for u in data["players"])
            self.campaigns[key] = campaign

        self.chars = {key: self.make_character(data) for key, data in CHARACTERS.items()}
        for key, data in CAMPAIGNS.items():
            for char_key in data["chars"]:
                self.chars[char_key].campaigns.add(self.campaigns[key])
        orc = self.chars["orco"]
        self.chars["orco1"] = orc.make_copy(campaigns=[self.campaigns["corona"]])
        self.chars["orco2"] = orc.make_copy(campaigns=[self.campaigns["corona"]])

        for key, campaign in self.campaigns.items():
            self.build_campaign(key, campaign)

    def make_character(self, data):
        sub = Subrace.objects.get(name=SUBRACES[data["sub"]]) if data.get("sub") else None
        scl = Subclass.objects.get(name=data["scl"]) if data.get("scl") else None
        st = data["st"]
        character = Character.objects.create(
            name=data["name"], player=self.users.get(data.get("player")), is_npc=data.get("npc", False),
            race=Race.objects.get(name=data["race"]), subrace=sub,
            char_class=CharacterClass.objects.get(name=data["cls"]), subclass=scl,
            xp=data["xp"], level=rules.level_for_xp(data["xp"]), hp=data["hp"], temp_hp=data["thp"], ac=data["ac"], temp_ap=0, inspiration=data.get("insp", 0),
            strength=st[0], dexterity=st[1], constitution=st[2], intelligence=st[3], wisdom=st[4], charisma=st[5],
            background=data.get("bg", ""), notes=data.get("notes", ""),
        )
        for act in data.get("acts", []):
            row = CharacterAbility(character=character, ability=Ability.objects.get(name=act["name"]))
            if "cd" in act:
                row.cooldown_rounds, row.cooldown_left = act["cd"], act["left"]
            else:
                row.max_uses, row.uses_left, row.recharge = act["max"], act["left"], act["rest"]
            row.full_clean()
            row.save()
        if "unique" in data:
            # Unique skill (rule 17): (name, description, approved by the master?).
            name, description, approved = data["unique"]
            skill = UniqueSkill(character=character)
            if approved:
                skill.approve(name, description)
                CharacterAbility.objects.filter(character=character, ability=skill.ability).update(
                    max_uses=1, uses_left=1, recharge=CharacterAbility.Recharge.LARGO)
            else:
                skill.propose(name, description)
                skill.save()
        character.spells.set(Spell.objects.get(name=n) for n in data.get("spells", []))
        for name, turns in data.get("fx", []):
            ActiveEffect.objects.create(character=character, effect=Effect.objects.get(name=name), remaining_turns=turns)
        for entry in data["inv"]:
            template = Item.objects.get(name=entry["name"], template__isnull=True)
            item = template.make_copy() if entry["kind"] in ("weapon", "armor") else template
            Inventory.objects.create(character=character, item=item, quantity=entry["qty"])
            if entry["eq"]:
                Weapon.objects.filter(item=item).update(equipped=True)
                Armor.objects.filter(item=item).update(equipped=True)
        character.temp_ap = min(data["tap"], character.max_ap)
        # Current magic points ("mp"); the maximum comes from the class and the level.
        character.magic_points = min(data.get("mp", 0), character.magic_points_max)
        character.save(update_fields=["temp_ap", "magic_points"])
        return character

    def build_campaign(self, key, campaign):
        sessions = {}
        for number, date, day, title, body, xp, loot, notes in SESSIONS.get(key, []):
            sessions[number] = Session.objects.create(
                campaign=campaign, number=number, played_on=datetime.date.fromisoformat(date), world_day=day,
                title=title, body=body, xp_awarded=xp, loot=loot, master_notes=notes, published=True,
            )
        locations = {}
        for order, (map_key, name, image, locs, moves) in enumerate(MAPS.get(key, [])):
            map_ = Map.objects.create(campaign=campaign, name=name, image=image, order=order)
            for loc_key, loc_name, x, y, status in locs:
                locations[loc_key] = MapLocation.objects.create(map=map_, name=loc_name, x=x, y=y, status=status)
            for index, (session, loc_key) in enumerate(moves):
                PartyMove.objects.create(map=map_, location=locations[loc_key], session=sessions[session], order=index)
        for _, session, day, title, kind, chars, loc_key, hidden in EVENTS.get(key, []):
            event = TimelineEvent.objects.create(
                campaign=campaign, session=sessions.get(session), world_day=day, title=title, kind=kind,
                location=locations.get(loc_key), hidden=hidden,
            )
            event.characters.set(self.chars[c] for c in chars)
        pages = {}
        for page_key, category, title, body, secret, revealed, parent, loc_key in PAGES.get(key, []):
            pages[page_key] = Page.objects.create(
                campaign=campaign, category=category, title=title, body=body, secret=secret, revealed=revealed,
                summary=first_sentence(body), parent=pages.get(parent), location=locations.get(loc_key),
            )
        for page in pages.values():
            page.full_clean()
        for title, status, giver, description, objectives in QUESTS.get(key, []):
            start, end = QUEST_SESSIONS.get(title, (None, None))
            quest = Quest.objects.create(campaign=campaign, title=title, status=status, giver=pages.get(giver), description=description,
                                         start_session=sessions.get(start), end_session=sessions.get(end))
            for order, (text, done, hidden) in enumerate(objectives):
                QuestObjective.objects.create(quest=quest, text=text, done=done, hidden=hidden, order=order)
        if key in BOARD:
            cards_data, links = BOARD[key]
            cards = {}
            for card_key, x, y, title, text, author, page, classified, revealed in cards_data:
                cards[card_key] = BoardCard.objects.create(
                    campaign=campaign, x=x + BOARD_OFFSET[0], y=y + BOARD_OFFSET[1], title=title, text=text,
                    author=self.users[author],
                    page=pages.get(page), classified=classified, revealed=revealed,
                )
            for a, b in links:
                BoardLink.objects.create(card_a=cards[a], card_b=cards[b])
        moment = timezone.make_aware(datetime.datetime(2026, 11, 15, 20, 0))
        for title, is_open, loc_page, participants, posts in SCENES.get(key, []):
            scene = Scene.objects.create(campaign=campaign, title=title, is_open=is_open, location=pages.get(loc_page))
            scene.participants.set(self.chars[c] for c in participants)
            for author, as_char, text, roll, whisper in posts:
                moment += datetime.timedelta(minutes=7)
                Post.objects.create(
                    scene=scene, author=self.users[author], as_character=self.chars.get(as_char), text=text, roll=roll,
                    whisper_to=self.chars.get(whisper), created_at=moment,
                )
        if key in TURNS:
            round_, turn, order = TURNS[key]
            encounter = Encounter.objects.create(campaign=campaign, active=True, round=round_, turn_index=turn)
            for index, (char_key, initiative) in enumerate(order):
                Combatant.objects.create(encounter=encounter, character=self.chars[char_key], initiative=initiative, order=index)

    # ------------------------------------------------------------------
    def export(self, path):
        """Everything except the rows of the two rules fixtures."""
        rules_rows = set()
        for name in ("datos_iniciales_srd.json", "armas_caseras.json"):
            for row in json.loads((FIXTURES / name).read_text(encoding="utf-8")):
                rules_rows.add((row["model"].lower(), row["pk"]))
        objects = []
        for model in [Group] + list(apps.get_app_config("CampaignManager").get_models()):
            label = model._meta.label_lower.lower()
            if model is Group:
                continue  # groups come from a migration
            for obj in model.objects.order_by("pk"):
                if (label, obj.pk) not in rules_rows:
                    objects.append(obj)
        # Natural keys for users and groups: the groups come from a migration, so their ids may differ.
        data = serializers.serialize("json", objects, indent=1, ensure_ascii=False, use_natural_foreign_keys=True)
        path.write_text(data + "\n", encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"Wrote {len(objects)} objects to {path}"))
