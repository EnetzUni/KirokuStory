"""Load the NPCs of the master's «Bestiario de NPCs — Sokoku» and the sheets of Zarek, Leah, Thalion
and Balasar into a campaign (by default «Infinity Blade»).

The data is CampaignManager/data/sokoku.json, made from the bestiary and from the players'
nivel20 sheets and «extra» files; the portraits are in data/sokoku_retratos.

    python manage.py cargar_sokoku                    # into «Infinity Blade»
    python manage.py cargar_sokoku --campana 4        # another campaign, by id or by name
    python manage.py cargar_sokoku --actualizar       # also rewrite the sheets already loaded

It needs the rules catalog first (datos_iniciales_srd, armas_caseras, and clases_aegis or
migration 0032). Safe to run again: sheets are found by name in the campaign and kept as they
are, unless --actualizar; catalog rows are reused. A sheet loaded before under another name
(`was_like` in the data: how that name started) is renamed and always rewritten, copies included.

- Each NPC is a template of the campaign, to copy into a fight from «Personajes». Its race is
  what it is (Mortal, Inmortal, Bestia, Reforjado) and its class its rank in the bestiary
  (Tropa, Veterano, Élite, Jefe...), both the master's own; it has its own speed. Its weapons
  are copies of the house weapons (armas_caseras) with its numbers, or pieces of its own; its
  traits and reactions are abilities, with uses or cooldown when the bestiary gives them.
- A pair of the bestiary that fights as one (Shayne and Aurox) acts with each other
  (`Character.acts_with`): in «Turnos» they take one turn and come into a fight together.
- The sheets are linked to their pages of the world: its own page if it has a name, and its side.
- What the catalog lacks for the four characters is added: the Artífice and its Artillero, the
  Acechador en la penumbra of the Arcane Warrior, the Círculo de la Luna (if the catalog does not
  have it yet), six feats and seven spells from outside the SRD (one of them, a house spell). A
  character is given to its player when that user plays the campaign.
"""
import json
import shutil
from pathlib import Path

from django.conf import settings
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from CampaignManager import bespoke, rules
from CampaignManager.models import (
    Ability, Armor, ArmorDamage, ArmorType, Attack, Campaign, Character, CharacterAbility, CharacterClass,
    ClassAbility, Component, DiceRoll, Effect, Inventory, Item, ItemEffect, Page, Race, Spell, Subclass,
    SubclassAbility, Subrace, UniqueSkill, Weapon,
)

DATA = Path(__file__).resolve().parents[2] / "data"
PORTRAITS = "retratos/sokoku"
GOLD = "Pieza de oro (po)"
# Speeds of the master's races; each NPC also has its own.
RACE_SPEED = {"Bestia": 13, "Mortal": 10, "Inmortal": 20, "Reforjado": 10, "Constructo": 10}
WEAPON_TEMPLATE_TEXT = "Ataque del bestiario de Sokoku: cada PNJ lleva el suyo, con sus números."


def to_hit_formula(hit, stat, multiplier, scores):
    """The bestiary gives the total to hit: the integer that, with the house's ability, adds up to it."""
    score = scores[0] if stat == "str" else scores[1]
    integer = hit - multiplier * rules.ability_modifier(score)
    ability = stat if multiplier == 1 else f"{stat} x{multiplier}"
    return f"{integer} + {ability}" if integer else ability


def new_attack(spec):
    attack = Attack.objects.create(name=spec["name"][:100], extra=spec["extra"], range=spec["range"],
                                   area=spec.get("area") or 0, cooldown=spec.get("cooldown") or 0,
                                   stat_bonus=spec.get("stat_bonus"))
    attack.dice.set([DiceRoll.objects.create(amount=amount, sides=sides) for amount, sides in spec["dice"]])
    damages = []
    if spec.get("pierce"):
        damages.append(ArmorDamage.objects.create(type=ArmorDamage.Type.PENETRATING, amount=spec["pierce"]))
    if spec.get("shred"):
        damages.append(ArmorDamage.objects.create(type=ArmorDamage.Type.SHREDDING, amount=spec["shred"]))
    attack.armor_damages.set(damages)
    return attack


class Command(BaseCommand):
    help = "Carga los PNJ del bestiario de Sokoku y las fichas de Zarek, Leah, Thalion y Balasar en una campaña."

    def add_arguments(self, parser):
        parser.add_argument("--campana", help="Id o nombre de la campaña (por defecto, «Infinity Blade»)")
        parser.add_argument("--actualizar", action="store_true",
                            help="Reescribe también las fichas que ya estaban cargadas (pierden lo cambiado en ellas)")

    def handle(self, *args, **options):
        data = json.loads((DATA / "sokoku.json").read_text(encoding="utf-8"))
        campaign = self.campaign(options.get("campana") or data["campaign"])
        if not Race.objects.filter(bespoke=False).exists():
            raise CommandError("Falta el catálogo de reglas: carga antes datos_iniciales_srd y armas_caseras.")
        self.update = options["actualizar"]
        self.missing_pages, self.renamed_sheets = [], []
        self.copy_portraits()
        with transaction.atomic():
            self.catalog(data)
            self.common = [(Ability.objects.filter(name=r["name"], description=r["text"]).first()
                            or Ability.objects.create(name=r["name"], description=r["text"]), r["for"])
                           for r in data["common_rules"]]
            npcs = [self.npc(spec, campaign) for spec in data["npcs"]]
            self.pair(campaign, data["npcs"], npcs)
            pcs = [self.pc(spec, campaign) for spec in data["pcs"]]
        self.report(campaign, npcs, pcs)

    # ---- Where ----
    def campaign(self, key):
        found = Campaign.objects.filter(pk=int(key)) if str(key).isdigit() else Campaign.objects.filter(name=key)
        campaign = found.first()
        if campaign is None:
            raise CommandError(f"No existe la campaña «{key}»: dila con --campana (su id o su nombre).")
        return campaign

    def copy_portraits(self):
        target = Path(settings.MEDIA_ROOT) / PORTRAITS
        target.mkdir(parents=True, exist_ok=True)
        for image in (DATA / "sokoku_retratos").iterdir():
            if not (target / image.name).exists():
                shutil.copyfile(image, target / image.name)

    # ---- What the catalog lacks ----
    def catalog(self, data):
        for spec in data["classes"]:
            char_class, _ = CharacterClass.objects.get_or_create(name=spec["name"], defaults={
                "primary_ability": spec["primary_ability"], "magic": spec["magic"], "bespoke": spec["bespoke"],
                "description": spec["description"]})
            for name, level, text in spec["abilities"]:
                if not ClassAbility.objects.filter(char_class=char_class, ability__name=name).exists():
                    ClassAbility.objects.create(char_class=char_class, ability=self.catalog_ability(name, text),
                                                level_required=level)
        for spec in data["subclasses"]:
            char_class = CharacterClass.objects.filter(name=spec["char_class"]).first()
            if char_class is None:
                raise CommandError(f"Falta la clase {spec['char_class']} en el catálogo.")
            subclass, _ = Subclass.objects.get_or_create(name=spec["name"], char_class=char_class,
                                                         defaults={"description": spec["description"]})
            for name, level, text in spec["abilities"]:  # a subclass already there keeps its own texts
                if not SubclassAbility.objects.filter(subclass=subclass, ability__name=name).exists():
                    SubclassAbility.objects.create(subclass=subclass, ability=self.catalog_ability(name, text),
                                                   level_required=level)
        for spec in data["spells"]:
            spell, created = Spell.objects.get_or_create(name=spec["name"], defaults={
                "level": spec["level"], "casting_time": spec["casting_time"], "range": spec["range"],
                "duration": spec["duration"], "description": spec["description"]})
            if created:
                spell.components.set(Component.objects.filter(type__in=spec["components"]))
                if spec["attack"]:
                    attack = new_attack({"name": spec["name"], "dice": spec["attack"]["dice"], "extra": 0,
                                         "range": spec["range"], "area": spec["attack"].get("area", 0)})
                    attack.dice.add(*[DiceRoll.objects.create(amount=amount, sides=sides, per_extra_level=True)
                                      for amount, sides in spec["attack"].get("per_level", [])])
                    spell.attacks.add(attack)
                for char_class in CharacterClass.objects.filter(name__in=spec["classes"]):
                    char_class.spells.add(spell)
        self.feats = {}
        for name in data["feats"]:
            self.feats[name] = (Ability.objects.filter(name=name, feat=True).first()
                                or Ability.objects.create(name=name, feat=True))

    def catalog_ability(self, name, text):
        """A class or subclass feature: the one with that text, or the catalog's own when no text is given."""
        if text is None:
            found = Ability.objects.filter(name=name, feat=False, unique=False).order_by("pk").first()
            if found is None:
                raise CommandError(f"Falta la habilidad {name} en el catálogo.")
            return found
        return Ability.objects.filter(name=name, description=text).first() or Ability.objects.create(name=name, description=text)

    # ---- One sheet ----
    def existing(self, campaign, name, npc):
        found = Character.objects.filter(campaigns=campaign, is_npc=npc, name=name)
        return found.filter(template__isnull=True).first() if npc else found.first()

    def renamed(self, campaign, spec):
        """A template loaded before under another name (`was_like`: how it started), or None."""
        if not spec.get("was_like"):
            return None
        return (Character.objects.filter(campaigns=campaign, is_npc=True, template__isnull=True,
                                         name__startswith=spec["was_like"]).exclude(name=spec["name"]).first())

    def reset(self, character):
        """--actualizar: the sheet loses what it carries and holds, and is made again from the data."""
        copies = list(Item.objects.filter(inventory__character=character, template__isnull=False).values_list("pk", flat=True))
        attacks = list(Attack.objects.filter(weapon__item__pk__in=copies).values_list("pk", flat=True))
        Inventory.objects.filter(character=character).delete()
        Item.objects.filter(pk__in=copies).delete()
        Attack.objects.filter(pk__in=attacks, weapon__isnull=True, spell__isnull=True).delete()
        skill = UniqueSkill.objects.filter(character=character).select_related("ability").first()
        if skill is not None:
            if skill.ability_id:
                skill.ability.delete()  # deletes the skill and its row too
            else:
                skill.delete()
        held = list(CharacterAbility.objects.filter(character=character).values_list("ability_id", flat=True))
        CharacterAbility.objects.filter(character=character).delete()
        # The sheet's own abilities that nobody holds any more go too (a trait the data no longer has);
        # the catalog's (classes, races, feats...) stay.
        Ability.objects.filter(pk__in=held, characterability__isnull=True, feat=False, unique=False, race__isnull=True,
                               subrace__isnull=True, characterclass__isnull=True, subclass__isnull=True,
                               raceability__isnull=True, subraceability__isnull=True, classability__isnull=True,
                               subclassability__isnull=True).delete()
        character.spells.clear()
        character.world_pages.clear()  # linked again from the data

    def sheet(self, campaign, spec, npc, fields):
        """The character made or updated, or None when it was already there and is kept."""
        character = self.existing(campaign, spec["name"], npc)
        old = self.renamed(campaign, spec) if npc and character is None else None
        if old is not None:
            # Its old name goes for good, on the template and on its copies («Old name 2» -> «Name 2»).
            for copy in old.copies.filter(name__startswith=old.name):
                copy.name = (spec["name"] + copy.name[len(old.name):])[:100]
                copy.save(update_fields=["name"])
            self.renamed_sheets.append(spec["name"])
            old.name, character = spec["name"], old
        elif character is not None and not self.update:
            return None
        if character is None:
            character = Character(name=spec["name"], is_npc=npc, status=Character.Status.APROBADO)
        else:
            self.reset(character)
            # Who plays it, and a portrait the master gave it, stay.
            fields = {k: v for k, v in fields.items() if k != "player" and not (k == "portrait" and not v)}
        for key, value in fields.items():
            setattr(character, key, value)
        character.save()
        character.campaigns.add(campaign)
        return character

    def scores(self, scores):
        return dict(zip(rules.ABILITY_KEYS, scores))

    def npc(self, spec, campaign):
        armor = spec["armor"]
        portrait = spec.get("portrait") if spec.get("portrait") and default_storage.exists(spec["portrait"]) else ""
        character = self.sheet(campaign, spec, True, {
            "race": bespoke.race(spec["race"], RACE_SPEED[spec["race"]]),
            "char_class": bespoke.char_class(spec["char_class"], CharacterClass.Magic.NINGUNA),
            "subrace": None, "subclass": None, "level": spec["level"], "xp": 0,
            "hp": spec["hp"], "temp_hp": spec["hp"], "ac": spec["ac"], "own_speed": spec["speed"],
            "temp_ap": armor["ap"] if armor else 0, "magic_points": 0, "magic_points_bonus": 0, "inspiration": 0,
            "background": spec["background"], "notes": spec["notes"], "portrait": portrait, "dead": False,
            **self.scores(spec["scores"]),
        })
        if character is None:
            return None
        if armor:
            self.give_armor(character, armor)
        for weapon in spec["weapons"]:
            self.give_weapon(character, weapon, spec["scores"])
        for row in spec["abilities"]:
            self.hold(character, self.own_ability(row["name"], row["text"]), row)
        immortal = spec["race"] == "Inmortal" or any(a["name"] in ("Inmortal", "Naturaleza inmortal") for a in spec["abilities"])
        for ability, kind in self.common:
            if kind == "all" or (kind == "immortal" and immortal):
                self.hold(character, ability, {})
        self.link(character, campaign, spec["pages"])
        return character

    def pair(self, campaign, specs, made):
        """Pairs that act at once: each template points to the other. Only for sheets made or rewritten now
        (the master may have changed a kept one)."""
        for spec, character in zip(specs, made):
            if character is None or not spec.get("acts_with"):
                continue
            partner = self.existing(campaign, spec["acts_with"], True)
            if partner is not None:
                character.acts_with = partner
                character.save(update_fields=["acts_with"])

    def pc(self, spec, campaign):
        race = Race.objects.filter(name=spec["race"]).order_by("bespoke", "pk").first()
        char_class = CharacterClass.objects.filter(name=spec["char_class"]).first()
        if race is None or char_class is None:
            raise CommandError(f"Faltan la raza {spec['race']} o la clase {spec['char_class']} en el catálogo.")
        subrace = Subrace.objects.filter(name=spec["subrace"], race=race).first() if spec["subrace"] else None
        subclass = Subclass.objects.filter(name=spec["subclass"], char_class=char_class).first() if spec["subclass"] else None
        armor = spec["armor"]
        player = campaign.players.filter(username=spec["player"]).first() if spec.get("player") else None
        character = self.sheet(campaign, spec, False, {
            "race": race, "subrace": subrace, "char_class": char_class, "subclass": subclass, "player": player,
            "level": spec["level"], "xp": spec["xp"], "hp": spec["hp"], "temp_hp": spec["temp_hp"], "ac": spec["ac"],
            "own_speed": spec.get("speed"), "temp_ap": armor.get("current_ap", armor["ap"]), "inspiration": spec["inspiration"],
            "magic_points_bonus": 0, "background": spec["background"], "notes": spec["notes"],
            "portrait": f"{PORTRAITS}/{spec['portrait']}", "dead": False, **self.scores(spec["scores"]),
        })
        if character is None:
            return None
        character.magic_points = character.magic_points_max  # rested: all of them
        character.save(update_fields=["magic_points"])
        self.give_armor(character, armor)
        for weapon in spec["weapons"]:
            self.give_weapon(character, weapon, spec["scores"])
        for row in spec["items"]:
            self.give_item(character, row)
        if spec["gold"]:
            gold = Item.objects.filter(name=GOLD, template__isnull=True).first()
            if gold is not None:
                Inventory.objects.create(character=character, item=gold, quantity=spec["gold"])
        for name in spec["spells"]:
            spell = Spell.objects.filter(name=name).first()
            if spell is None:
                raise CommandError(f"Falta el conjuro {name} en el catálogo.")
            character.spells.add(spell)
        for row in spec["feats"]:
            self.hold(character, self.feats[row["name"]], row)
            if not self.feats[row["name"]].description:
                self.feats[row["name"]].description = row["text"]
                self.feats[row["name"]].save(update_fields=["description"])
        for row in spec["abilities"]:
            self.hold(character, self.own_ability(row["name"], row["text"]), row)
        granted = character.all_ability_ids()
        for row in spec["granted_uses"]:
            ability = Ability.objects.filter(name=row["name"], pk__in=granted).first()
            if ability is not None:
                self.hold(character, ability, row)
        if spec["unique_skill"]:
            skill = spec["unique_skill"]
            UniqueSkill(character=character).approve(skill["name"], skill["text"])
            if skill.get("uses"):
                CharacterAbility.objects.filter(character=character, ability__unique_skill__character=character).update(
                    max_uses=skill["uses"], uses_left=skill["uses"], recharge=skill.get("recharge", "largo"))
        self.link(character, campaign, spec["pages"])
        return character

    # ---- What it carries and holds ----
    def own_ability(self, name, text):
        return (Ability.objects.filter(name=name[:100], description=text, unique=False, feat=False).first()
                or Ability.objects.create(name=name[:100], description=text))

    def hold(self, character, ability, row):
        uses = row.get("uses")
        CharacterAbility.objects.get_or_create(character=character, ability=ability, defaults={
            "max_uses": uses, "uses_left": uses or 0, "recharge": row.get("recharge") or CharacterAbility.Recharge.NINGUNO,
            "cooldown_rounds": row.get("cooldown")})

    def give_armor(self, character, spec):
        armor_type, _ = ArmorType.objects.get_or_create(type=spec["type"], defaults={"ap": 0, "magic_ap": 0})
        own_ap, own_magic = spec["ap"] - armor_type.ap, spec.get("magic_ap", 0) - armor_type.magic_ap
        template = Item.objects.filter(name=spec["name"], template__isnull=True, bespoke=True, armor__armor_type=armor_type,
                                       armor__ap=own_ap, armor__magic_ap=own_magic).first()
        if template is None:
            template = Item.objects.create(name=spec["name"], bespoke=True, description=spec.get("text", ""))
            Armor.objects.create(item=template, armor_type=armor_type, ap=own_ap, magic_ap=own_magic)
        bespoke.give(character, template, equip=True)

    def weapon_template(self, spec, formula):
        """The house weapon it is a copy of; or, for its own attacks, a piece of the master's (one per name)."""
        if spec["house"]:
            house = Item.objects.filter(name=spec["house"], template__isnull=True, weapon__isnull=False).first()
            if house is not None:
                return house
        template = Item.objects.filter(name=spec["name"], template__isnull=True, bespoke=True, weapon__isnull=False).first()
        if template is None:
            template = Item.objects.create(name=spec["name"][:100], bespoke=True, description=WEAPON_TEMPLATE_TEXT)
            weapon = Weapon.objects.create(item=template, to_hit=formula or "str")
            weapon.attacks.set([new_attack(a) for a in spec["attacks"] or []])
        return template

    def give_weapon(self, character, spec, scores):
        formula = spec.get("formula")
        if formula is None and spec.get("hit") is not None:
            formula = to_hit_formula(spec["hit"], spec["stat"], spec["mult"], scores)
        copy = self.weapon_template(spec, formula).make_copy()
        copy.name, copy.description = spec["name"][:100], spec["text"]
        copy.save(update_fields=["name", "description"])
        weapon = copy.weapon
        if formula:
            weapon.to_hit = formula
        weapon.equipped = spec["equipped"]
        weapon.save(update_fields=["to_hit", "equipped"])
        if spec.get("attacks") is not None:
            weapon.attacks.set([new_attack(a) for a in spec["attacks"]])
        if spec.get("traits") is not None:
            ItemEffect.objects.filter(item=copy).delete()
            effects = {e.name: e for e in Effect.objects.filter(name__in=[name for name, _ in spec["traits"]])}
            ItemEffect.objects.bulk_create(ItemEffect(item=copy, effect=effects[name], value=value)
                                           for name, value in spec["traits"] if name in effects)
        Inventory.objects.create(character=character, item=copy, quantity=1)

    def give_item(self, character, row):
        if "catalog" in row:
            item = Item.objects.filter(name=row["catalog"], template__isnull=True, weapon__isnull=True, armor__isnull=True).first()
            if item is None:
                return
            if row.get("text") and not item.description:  # what the player tells of it, if the catalog says nothing
                item.description = row["text"]
                item.save(update_fields=["description"])
        else:
            item = (Item.objects.filter(name=row["name"], template__isnull=True, bespoke=True).first()
                    or Item.objects.create(name=row["name"], bespoke=True, description=row["text"],
                                           consumable=row.get("consumable", False)))
        Inventory.objects.create(character=character, item=item, quantity=row["quantity"])

    def link(self, character, campaign, titles):
        for title in titles:
            page = Page.objects.filter(campaign=campaign, title=title).first()
            if page is None:
                self.missing_pages.append(title)
            else:
                page.sheets.add(character)

    def report(self, campaign, npcs, pcs):
        made = [c for c in npcs + pcs if c is not None]
        kept = len(npcs) + len(pcs) - len(made)
        links = Page.objects.filter(campaign=campaign, sheets__in=made).count()
        party = sum(1 for c in pcs if c)
        self.stdout.write(self.style.SUCCESS(
            f"{campaign.name}: {sum(1 for c in npcs if c)} PNJ y {party} {'personaje' if party == 1 else 'personajes'} "
            f"{'cargados o reescritos' if self.update else 'cargados'}; {links} enlaces con páginas del mundo."))
        if kept:
            self.stdout.write(f"{kept} ya estaban y se han dejado como están (usa --actualizar para reescribirlos).")
        if self.renamed_sheets:
            self.stdout.write("Renombradas y reescritas: " + ", ".join(self.renamed_sheets) + ".")
        if self.missing_pages:
            missing = ", ".join(sorted(set(self.missing_pages)))
            self.stdout.write(f"Páginas del mundo que no existen en la campaña (sin enlazar): {missing}.")
