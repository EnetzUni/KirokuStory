"""Tests of the house rules (data-model.md, «Game rules»: 2, 3, 5, 6 and 16) and of
what players may see (spec, section 6).

    python manage.py test CampaignManager
"""
import datetime
import importlib
import io
import json
import re
import tempfile
from pathlib import Path
from unittest import mock

from django.apps import apps as django_apps
from django.core.cache import cache
from django.utils import timezone

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from . import rules, weather
from .models import (
    Rumour, RumourCategory, WeatherRegion,
    Ability, ActiveEffect, Armor, ArmorDamage, ArmorType, BoardCard, Campaign, Character, CharacterAbility, CharacterClass,
    ClassAbility, ClassAddon, Combatant, Effect, Encounter, Inventory, Item, Map, MapLocation, Page, PartyMove, Post, Quest, QuestObjective,
    Race, Session, Spell, Subclass, SubclassAbility, Subrace, TimelineEvent, UniqueSkill, User, Weapon,
)
from .views_characters import hit_die, level_up_plan

AJAX = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


class RulesData(TestCase):
    """A small world built by hand: one race, one class, two campaigns, armor and weapons."""

    @classmethod
    def setUpTestData(cls):
        cls.master = User.objects.create_user("master", password="x")
        cls.player = User.objects.create_user("player", password="x")
        cls.race = Race.objects.create(name="Humano", speed=10)
        cls.char_class = CharacterClass.objects.create(name="Guerrero", primary_ability="Fuerza")
        cls.campaign = Campaign.objects.create(name="Una", start_date=datetime.date(2026, 1, 1))
        cls.other_campaign = Campaign.objects.create(name="Otra", start_date=datetime.date(2026, 1, 1))
        for campaign in (cls.campaign, cls.other_campaign):
            campaign.masters.add(cls.master)
            campaign.players.add(cls.player)
        cls.heavy = ArmorType.objects.create(type="Pesada", ap=3, magic_ap=0)
        cls.light = ArmorType.objects.create(type="Ligera", ap=1, magic_ap=1)
        cls.mail = cls.armor_template("Cota de mallas", cls.heavy, ap=2, magic_ap=1)
        cls.robe = cls.armor_template("Túnica", cls.light, ap=0, magic_ap=3)
        cls.sword = Item.objects.create(name="Espada")
        Weapon.objects.create(item=cls.sword, to_hit="2 + str")
        cls.axe = Item.objects.create(name="Hacha")
        Weapon.objects.create(item=cls.axe, to_hit="str")

    @classmethod
    def armor_template(cls, name, armor_type, ap, magic_ap):
        item = Item.objects.create(name=name)
        Armor.objects.create(item=item, armor_type=armor_type, ap=ap, magic_ap=magic_ap)
        return item

    def character(self, name="Brom", campaigns=(), **fields):
        values = dict(name=name, race=self.race, char_class=self.char_class, player=self.player, hp=20, temp_hp=20,
                      ac=12, strength=16, dexterity=12, constitution=14, intelligence=10, wisdom=10, charisma=8)
        values.update(fields)
        character = Character.objects.create(**values)
        character.campaigns.set(campaigns)
        return character

    def carry(self, character, template, equipped=False):
        """Give the character its own copy of a weapon or armor (game rule 12)."""
        copy = template.make_copy()
        Inventory.objects.create(character=character, item=copy, quantity=1)
        if equipped:
            equipment = getattr(copy, "weapon", None) or copy.armor
            equipment.equipped = True
            equipment.full_clean()
            equipment.save()
        return copy


class ArmorPointsTests(RulesData):
    """Rule 2: maximum AP is calculated from the equipped armor; only the current AP is stored."""

    def test_max_ap_is_armor_plus_armor_type(self):
        brom = self.character()
        self.carry(brom, self.mail, equipped=True)
        self.assertEqual(brom.max_ap, 2 + 3)
        self.assertEqual(brom.max_magic_ap, 1 + 0)

    def test_no_armor_means_no_ap(self):
        brom = self.character()
        self.carry(brom, self.mail)  # carried, not worn
        self.assertEqual(brom.max_ap, 0)
        self.assertEqual(brom.max_magic_ap, 0)

    def test_ap_and_armor_class_are_different(self):
        brom = self.character(ac=17, temp_ap=1)
        self.carry(brom, self.robe, equipped=True)
        self.assertEqual((brom.ac, brom.temp_ap, brom.max_ap), (17, 1, 1))

    def test_max_ap_is_not_a_field(self):
        field_names = {field.name for field in Character._meta.get_fields()}
        self.assertIn("temp_ap", field_names)
        self.assertNotIn("max_ap", field_names)
        self.assertNotIn("ap", field_names)

    def test_sheet_restores_and_caps_ap_at_the_calculated_maximum(self):
        brom = self.character(campaigns=[self.campaign], temp_ap=0)
        self.carry(brom, self.mail, equipped=True)
        self.client.force_login(self.player)
        url = reverse("sheet_action", args=[self.campaign.pk, brom.pk])
        self.client.post(url, {"accion": "ap_restaurar"}, **AJAX)
        brom.refresh_from_db()
        self.assertEqual(brom.temp_ap, 5)
        self.client.post(url, {"accion": "ap_mas"}, **AJAX)
        brom.refresh_from_db()
        self.assertEqual(brom.temp_ap, 5)


class ArmorDamageTests(TestCase):
    """Rule 3: penetrante ignores AP without changing it; triturante subtracts AP after the damage."""

    def test_penetrating_ignores_ap_and_leaves_it_unchanged(self):
        damages = [(ArmorDamage.Type.PENETRATING, 2)]
        self.assertEqual(rules.ap_against_attack(5, damages), 3)
        self.assertEqual(rules.ap_after_attack(5, damages), 5)

    def test_shredding_subtracts_after_the_damage(self):
        damages = [(ArmorDamage.Type.SHREDDING, 3)]
        self.assertEqual(rules.ap_against_attack(5, damages), 5)  # the damage meets the full AP
        self.assertEqual(rules.ap_after_attack(5, damages), 2)

    def test_both_types_deal_damage_against_unchanged_ap_then_shred(self):
        damages = [(ArmorDamage.Type.PENETRATING, 2), (ArmorDamage.Type.SHREDDING, 3)]
        self.assertEqual(rules.ap_against_attack(5, damages), 3)
        self.assertEqual(rules.ap_after_attack(5, damages), 2)

    def test_ap_never_goes_below_zero(self):
        self.assertEqual(rules.ap_against_attack(1, [(ArmorDamage.Type.PENETRATING, 4)]), 0)
        self.assertEqual(rules.ap_after_attack(1, [(ArmorDamage.Type.SHREDDING, 4)]), 0)

    def test_armor_damage_text(self):
        self.assertEqual(str(ArmorDamage(type=ArmorDamage.Type.SHREDDING, amount=3)), "Triturante 3")
        self.assertEqual(str(ArmorDamage(type=ArmorDamage.Type.PENETRATING, amount=2)), "Penetrante 2")


class EquipmentTests(RulesData):
    """Rule 5: several weapons, one armor; only what is in the character's inventory."""

    def test_several_weapons_can_be_equipped(self):
        brom = self.character()
        self.carry(brom, self.sword, equipped=True)
        self.carry(brom, self.axe, equipped=True)
        self.assertEqual(Weapon.objects.filter(equipped=True, item__inventory__character=brom).count(), 2)

    def test_only_one_armor_can_be_equipped(self):
        brom = self.character()
        self.carry(brom, self.mail, equipped=True)
        robe = self.carry(brom, self.robe)
        robe.armor.equipped = True
        with self.assertRaises(ValidationError):
            robe.armor.full_clean()

    def test_an_item_nobody_carries_cannot_be_equipped(self):
        loose = self.sword.make_copy()
        loose.weapon.equipped = True
        with self.assertRaises(ValidationError):
            loose.weapon.full_clean()

    def test_a_template_cannot_be_equipped(self):
        self.mail.armor.equipped = True
        with self.assertRaises(ValidationError):
            self.mail.armor.full_clean()

    def test_dropping_an_item_unequips_it(self):
        brom = self.character()
        mail = self.carry(brom, self.mail, equipped=True)
        Inventory.objects.filter(character=brom, item=mail).delete()
        mail.armor.refresh_from_db()
        self.assertFalse(mail.armor.equipped)
        self.assertEqual(brom.max_ap, 0)

    def test_sheet_swaps_armor_instead_of_wearing_two(self):
        brom = self.character(campaigns=[self.campaign])
        self.carry(brom, self.mail, equipped=True)
        robe = self.carry(brom, self.robe)
        self.client.force_login(self.player)
        row = Inventory.objects.get(character=brom, item=robe)
        self.client.post(reverse("sheet_action", args=[self.campaign.pk, brom.pk]), {"accion": "equipar", "valor": row.pk}, **AJAX)
        worn = Armor.objects.filter(equipped=True, item__inventory__character=brom)
        self.assertEqual([armor.item_id for armor in worn], [robe.pk])
        brom.refresh_from_db()
        self.assertEqual(brom.temp_ap, 1)

    def test_catalog_weapons_are_added_as_own_copies(self):
        brom = self.character(campaigns=[self.campaign])
        self.client.force_login(self.player)
        self.client.post(reverse("sheet_action", args=[self.campaign.pk, brom.pk]), {"accion": "anadir_objeto", "objeto": self.sword.pk}, **AJAX)
        carried = Inventory.objects.get(character=brom)
        self.assertEqual(carried.item.template_id, self.sword.pk)


class UniqueAbilityTests(RulesData):
    """Rule 6: a unique ability belongs to only one character per campaign."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.pact = Ability.objects.create(name="Pacto de la Ceniza", unique=True)

    def test_second_holder_in_the_same_campaign_is_rejected(self):
        self.character("Morwen", campaigns=[self.campaign]).abilities.add(self.pact)
        aria = self.character("Aria", campaigns=[self.campaign])
        row = CharacterAbility(character=aria, ability=self.pact)
        with self.assertRaises(ValidationError):
            row.full_clean()
        with self.assertRaises(ValidationError):
            aria.abilities.add(self.pact)

    def test_holders_in_different_campaigns_are_allowed(self):
        self.character("Morwen", campaigns=[self.campaign]).abilities.add(self.pact)
        aria = self.character("Aria", campaigns=[self.other_campaign])
        aria.abilities.add(self.pact)
        self.assertTrue(aria.abilities.filter(pk=self.pact.pk).exists())

    def test_joining_a_campaign_where_the_ability_is_taken_is_rejected(self):
        self.character("Morwen", campaigns=[self.campaign]).abilities.add(self.pact)
        aria = self.character("Aria", campaigns=[self.other_campaign])
        aria.abilities.add(self.pact)
        with self.assertRaises(ValidationError):
            aria.campaigns.add(self.campaign)

    def test_granted_unique_ability_counts_too(self):
        from .models import RaceAbility
        tiefling = Race.objects.create(name="Tiefling", speed=10)
        RaceAbility.objects.create(race=tiefling, ability=self.pact, level_required=1)
        self.character("Morwen", campaigns=[self.campaign], race=tiefling)
        aria = self.character("Aria", campaigns=[self.campaign])
        aria.race = tiefling
        with self.assertRaises(ValidationError):
            aria.full_clean()

    def test_npc_template_with_a_unique_ability_cannot_be_copied(self):
        villain = self.character("Morwen", is_npc=True, player=None)
        villain.abilities.add(self.pact)
        with self.assertRaises(ValueError):
            villain.make_copy()


# ---------------------------------------------------------------------------
# Visibility (spec, section 6): with the demo campaign
# ---------------------------------------------------------------------------

class LevelUpData(RulesData):
    """A wizard class with a subclass, a class ability at level 2 and spells, and helpers."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.wizard = CharacterClass.objects.create(name="Mago", primary_ability="Inteligencia", magic="completa",
                                                   description="Dado de puntos de golpe: 1d6 por nivel de mago.")
        cls.evoker = Subclass.objects.create(name="Evocador", char_class=cls.wizard)
        Subclass.objects.create(name="Adivino", char_class=cls.wizard)
        ClassAbility.objects.create(char_class=cls.wizard, ability=Ability.objects.create(name="Académico"), level_required=2)
        spell = dict(casting_time="1 acción", duration="Instantáneo")
        cls.cantrip = Spell.objects.create(name="Rayo de escarcha", level=0, **spell)
        cls.missile = Spell.objects.create(name="Proyectil mágico", level=1, **spell)
        cls.shield = Spell.objects.create(name="Escudo", level=1, **spell)
        cls.misty_step = Spell.objects.create(name="Paso brumoso", level=2, **spell)
        cls.wizard.spells.set([cls.cantrip, cls.missile, cls.shield, cls.misty_step])

    def mage(self, name="Aria", **fields):
        # Constitution 14: +2.
        return self.character(name, campaigns=[self.campaign], char_class=self.wizard, **fields)

    def level_up(self, character, user=None, **data):
        self.client.force_login(user or self.player)
        data = {"nivel": character.level + 1, "hp_roll": "3", **data}
        return self.client.post(reverse("level_up", args=[self.campaign.pk, character.pk]), data)


class LevelUpTests(LevelUpData):
    """The master gives XP; the level waits until the player levels up from the
    sheet, one level at a time, with that level's choices (game rule 16, Aegis)."""

    def test_master_xp_does_not_change_the_level(self):
        aria = self.mage()
        self.client.force_login(self.master)
        self.client.post(reverse("sheet_action", args=[self.campaign.pk, aria.pk]), {"accion": "xp_dar", "cantidad": "300"}, **AJAX)
        aria.refresh_from_db()
        self.assertEqual((aria.xp, aria.level, aria.can_level_up), (300, 1, True))
        self.client.force_login(self.player)
        response = self.client.get(reverse("sheet", args=[self.campaign.pk, aria.pk]))
        self.assertContains(response, reverse("level_up", args=[self.campaign.pk, aria.pk]))

    def test_players_cannot_give_xp(self):
        aria = self.mage()
        self.client.force_login(self.player)
        response = self.client.post(reverse("sheet_action", args=[self.campaign.pk, aria.pk]),
                                    {"accion": "xp_dar", "cantidad": "300"}, **AJAX)
        self.assertEqual(response.status_code, 403)
        aria.refresh_from_db()
        self.assertEqual(aria.xp, 0)

    def test_party_xp_goes_to_the_ticked_characters(self):
        aria, brom = self.mage(), self.character("Brom", campaigns=[self.campaign])
        url = reverse("party_xp", args=[self.campaign.pk])
        self.client.force_login(self.player)
        self.assertEqual(self.client.post(url, {"cantidad": "300", "personajes": [aria.pk]}).status_code, 403)
        self.client.force_login(self.master)
        self.client.post(url, {"cantidad": "300", "personajes": [aria.pk]})
        aria.refresh_from_db()
        brom.refresh_from_db()
        self.assertEqual((aria.xp, brom.xp), (300, 0))

    def test_level_up_needs_the_xp(self):
        aria = self.mage(xp=299)
        self.client.force_login(self.player)
        response = self.client.get(reverse("level_up", args=[self.campaign.pk, aria.pk]))
        self.assertRedirects(response, reverse("sheet", args=[self.campaign.pk, aria.pk]))
        self.level_up(aria)
        aria.refresh_from_db()
        self.assertEqual(aria.level, 1)

    def test_players_cannot_level_up_other_characters(self):
        other = User.objects.create_user("other", password="x")
        self.campaign.players.add(other)
        aria = self.mage(xp=300, player=other)
        self.assertEqual(self.level_up(aria).status_code, 403)
        aria.refresh_from_db()
        self.assertEqual(aria.level, 1)

    def test_hit_points_are_the_1d4_rolled_at_the_table_plus_constitution(self):
        aria = self.mage(xp=300, hp=20, temp_hp=15)
        self.client.force_login(self.player)
        self.assertContains(self.client.get(reverse("level_up", args=[self.campaign.pk, aria.pk])), "Académico")
        self.level_up(aria, hp_roll="3")
        aria.refresh_from_db()
        # 3 on the 1d4 + Constitution 2, whatever the class's hit die (d6). Current hit points go up the same.
        self.assertEqual((aria.level, aria.hp, aria.temp_hp), (2, 25, 20))

    def test_the_1d4_result_goes_from_1_to_4(self):
        aria = self.mage(xp=300, hp=20, temp_hp=20, constitution=6)  # Constitution -2
        for wrong in ("", "0", "5"):
            self.assertContains(self.level_up(aria, hp_roll=wrong), "1d4")
            aria.refresh_from_db()
            self.assertEqual(aria.level, 1)
        self.level_up(aria, hp_roll="1")
        aria.refresh_from_db()
        # 1 - 2 is below 1: a new level always adds at least 1 hit point.
        self.assertEqual((aria.level, aria.hp), (2, 21))

    def test_the_subclass_is_chosen_at_level_3(self):
        aria = self.mage(xp=900, level=2)
        response = self.level_up(aria)
        self.assertContains(response, "Elige una subclase.")
        aria.refresh_from_db()
        self.assertEqual(aria.level, 2)
        self.level_up(aria, subclass=self.evoker.pk)
        aria.refresh_from_db()
        self.assertEqual((aria.level, aria.subclass), (3, self.evoker))

    def test_ability_score_improvement_and_constitution_for_every_level(self):
        aria = self.mage(xp=2700, level=3, subclass=self.evoker, hp=20, temp_hp=20, intelligence=19)
        self.assertContains(self.level_up(aria, asi_first="intelligence", asi_second="intelligence"), "no puede pasar de 20")
        aria.refresh_from_db()
        self.assertEqual((aria.level, aria.intelligence), (3, 19))
        self.level_up(aria, asi_first="constitution", asi_second="constitution")
        aria.refresh_from_db()
        # Constitution 14 -> 16 (+3): 3 on the 1d4 + 3 for level 4, and +1 for each of the 3 levels already had.
        self.assertEqual((aria.level, aria.constitution, aria.hp), (4, 16, 20 + 6 + 3))

    def test_feats_or_the_improvement_every_4_levels(self):
        self.assertEqual([level for level in range(1, 21) if rules.is_feat_level(level)], [4, 8, 12, 16, 20])
        tough = Ability.objects.create(name="Duro", feat=True, description="+2 puntos de golpe por nivel.")
        Ability.objects.create(name="Don del destino", feat=True, feat_min_level=19)
        aria = self.mage(xp=300)
        self.client.force_login(self.player)
        page = self.client.get(reverse("level_up", args=[self.campaign.pk, aria.pk]))
        self.assertNotContains(page, 'name="choice"')  # level 2: nothing to choose
        self.assertNotContains(page, 'name="asi_first"')
        aria = self.mage(xp=2700, level=3, subclass=self.evoker, name="Lira")
        page = self.client.get(reverse("level_up", args=[self.campaign.pk, aria.pk]))
        self.assertContains(page, "Duro")
        self.assertNotContains(page, "Don del destino")  # epic boons from level 20
        self.assertContains(self.level_up(aria), "Elige la mejora de característica o una dote.")
        self.assertContains(self.level_up(aria, choice="caracteristica"), "Elige a qué característica va este punto.")
        aria.refresh_from_db()
        self.assertEqual(aria.level, 3)
        self.level_up(aria, choice=str(tough.pk), asi_first="strength", asi_second="strength")
        aria.refresh_from_db()
        # The feat instead of the improvement: the scores do not change.
        self.assertEqual((aria.level, aria.strength), (4, 16))
        self.assertTrue(aria.characterability_set.filter(ability=tough).exists())

    def test_spells_are_given_by_the_master(self):
        aria = self.mage(xp=300)
        self.client.force_login(self.player)
        self.assertNotContains(self.client.get(reverse("level_up", args=[self.campaign.pk, aria.pk])), self.missile.name)
        url = reverse("sheet_action", args=[self.campaign.pk, aria.pk])
        self.assertEqual(self.client.post(url, {"accion": "conjuro_ensenar", "conjuro": self.missile.pk}, **AJAX).status_code, 403)
        self.client.force_login(self.master)
        self.client.post(url, {"accion": "conjuro_ensenar", "conjuro": self.missile.pk}, **AJAX)
        self.assertEqual(list(aria.spells.all()), [self.missile])
        self.assertTrue(self.client.post(url, {"accion": "conjuro_ensenar", "conjuro": self.missile.pk}, **AJAX).json()["error"])
        self.client.post(url, {"accion": "conjuro_olvidar", "conjuro": self.missile.pk}, **AJAX)
        self.assertFalse(aria.spells.exists())

    def test_magic_points_come_from_the_class_and_the_level(self):
        self.assertEqual([rules.class_magic_points(kind, 5) for kind in ("completa", "media", "pacto", "ninguna")], [16, 8, 6, 0])
        aria = self.mage(xp=300, magic_points=2)
        self.assertEqual((aria.magic_points_max, aria.has_magic), (2, True))
        self.assertFalse(self.character("Brom").has_magic)  # this Guerrero has no magic
        self.level_up(aria)
        aria.refresh_from_db()
        # Level 2: 3. The current points go up the same as the maximum.
        self.assertEqual((aria.level, aria.magic_points, aria.magic_points_max), (2, 3, 3))

    def test_the_master_adjusts_the_magic_points(self):
        aria, brom = self.mage(magic_points=2), self.character("Brom", campaigns=[self.campaign])
        url = reverse("sheet_action", args=[self.campaign.pk, aria.pk])
        self.client.force_login(self.player)
        self.assertEqual(self.client.post(url, {"accion": "magia_ajuste", "ajuste": "3"}, **AJAX).status_code, 403)
        self.client.force_login(self.master)
        self.client.post(url, {"accion": "magia_ajuste", "ajuste": "3"}, **AJAX)
        aria.refresh_from_db()
        self.assertEqual((aria.magic_points_bonus, aria.magic_points_max, aria.magic_points), (3, 5, 5))
        self.client.post(url, {"accion": "magia_ajuste", "ajuste": "-10"}, **AJAX)
        aria.refresh_from_db()
        self.assertEqual((aria.magic_points_max, aria.magic_points, aria.has_magic), (0, 0, False))
        # A class without magic can get some from the master.
        self.client.post(reverse("sheet_action", args=[self.campaign.pk, brom.pk]), {"accion": "magia_ajuste", "ajuste": "4"}, **AJAX)
        brom.refresh_from_db()
        self.assertEqual((brom.magic_points_max, brom.magic_points), (4, 4))

    def test_sending_the_form_twice_levels_up_once(self):
        aria = self.mage(xp=900, hp=20, temp_hp=20)
        data = {"nivel": 2, "hp_roll": "3"}
        url = reverse("level_up", args=[self.campaign.pk, aria.pk])
        self.client.force_login(self.player)
        self.client.post(url, data)
        self.client.post(url, data)
        aria.refresh_from_db()
        self.assertEqual((aria.level, aria.hp, aria.can_level_up), (2, 25, True))

    def test_a_unique_ability_of_the_new_level_is_checked(self):
        ward = Ability.objects.create(name="Guardián único", unique=True)
        rival = self.character("Rival", campaigns=[self.campaign])
        CharacterAbility.objects.create(character=rival, ability=ward)
        aria = self.mage(xp=300)
        ClassAbility.objects.create(char_class=self.wizard, ability=ward, level_required=2)
        self.assertContains(self.level_up(aria), "already has the unique ability")
        aria.refresh_from_db()
        self.assertEqual(aria.level, 1)


class UniqueSkillTests(RulesData):
    """The player proposes the unique skill, in the creator or later from the sheet;
    a master approves it (changing it if needed) or rejects it (game rule 17)."""

    def create(self, user, **draft):
        from django.test import RequestFactory
        from . import views_characters
        request = RequestFactory().post("/")
        request.user = user
        scores = dict(strength=15, dexterity=14, constitution=13, intelligence=12, wisdom=10, charisma=8)
        draft = {"name": "Lira", "race": self.race.pk, "char_class": self.char_class.pk, "scores": scores, **draft}
        return views_characters._create_character(request, self.campaign, draft)

    def act(self, user, character, **data):
        self.client.force_login(user)
        return self.client.post(reverse("sheet_action", args=[self.campaign.pk, character.pk]), data, **AJAX)

    def test_proposed_in_the_creator(self):
        lira = self.create(self.player, skill_name="Ojo de halcón", skill_description="Ve en la oscuridad total.")
        self.assertEqual((lira.unique_skill.status, lira.unique_skill.ability), (UniqueSkill.Status.PENDIENTE, None))
        self.assertFalse(lira.abilities.exists())
        # Without one, the character is created all the same.
        self.assertFalse(UniqueSkill.objects.filter(character=self.create(self.player, name="Sin")).exists())

    def test_a_master_creating_the_character_approves_it(self):
        lira = self.create(self.master, skill_name="Ojo de halcón", skill_description="Ve en la oscuridad total.")
        skill = lira.unique_skill
        self.assertEqual(skill.status, UniqueSkill.Status.APROBADA)
        self.assertTrue(skill.ability.unique)
        self.assertEqual(list(lira.abilities.all()), [skill.ability])

    def test_proposed_later_from_the_sheet_and_approved_with_changes(self):
        brom = self.character(campaigns=[self.campaign])
        response = self.act(self.player, brom, accion="unica_proponer", name="", description="Algo")
        self.assertTrue(response.json()["error"])
        self.act(self.player, brom, accion="unica_proponer", name="Puño de piedra", description="Rompe muros de un golpe.")
        skill = UniqueSkill.objects.get(character=brom)
        self.assertEqual(skill.status, UniqueSkill.Status.PENDIENTE)
        self.assertEqual(self.act(self.player, brom, accion="unica_aprobar", name="X", description="Y").status_code, 403)
        # The master sees it waiting in «Personajes», with the menu badge.
        self.client.force_login(self.master)
        page = self.client.get(reverse("characters", args=[self.campaign.pk]))
        self.assertContains(page, "Puño de piedra")
        self.assertContains(page, 'class="insignia">1<')
        self.client.post(reverse("unique_skill_review", args=[self.campaign.pk, skill.pk]),
                         {"accion": "aprobar", "name": "Puño de roca", "description": "Rompe una puerta de un golpe.", "nota": "Solo puertas."})
        skill.refresh_from_db()
        self.assertEqual((skill.status, skill.ability.name, skill.ability.unique, skill.master_note),
                         (UniqueSkill.Status.APROBADA, "Puño de roca", True, "Solo puertas."))
        self.assertTrue(brom.characterability_set.filter(ability=skill.ability).exists())
        self.client.force_login(self.player)
        sheet = self.client.get(reverse("sheet", args=[self.campaign.pk, brom.pk]) + "?tab=feats")
        self.assertContains(sheet, "habilidad-unica")
        self.assertContains(sheet, "Puño de roca")
        # One per character.
        self.assertTrue(self.act(self.player, brom, accion="unica_proponer", name="Otra", description="Otra.").json()["error"])

    def test_rejected_with_a_note_and_proposed_again(self):
        brom = self.character(campaigns=[self.campaign])
        self.act(self.player, brom, accion="unica_proponer", name="Inmortal", description="No puede morir.")
        self.act(self.master, brom, accion="unica_rechazar", nota="Demasiado fuerte.")
        skill = UniqueSkill.objects.get(character=brom)
        self.assertEqual((skill.status, skill.master_note), (UniqueSkill.Status.RECHAZADA, "Demasiado fuerte."))
        self.assertFalse(brom.abilities.exists())
        self.client.force_login(self.player)
        self.assertContains(self.client.get(reverse("sheet", args=[self.campaign.pk, brom.pk]) + "?tab=feats"), "Demasiado fuerte.")
        self.act(self.player, brom, accion="unica_proponer", name="Duro de pelar", description="Una vez al día, queda a 1 de vida.")
        skill.refresh_from_db()
        self.assertEqual((skill.status, skill.name, skill.master_note), (UniqueSkill.Status.PENDIENTE, "Duro de pelar", ""))

    def test_a_master_gives_one_directly(self):
        brom = self.character(campaigns=[self.campaign])
        self.act(self.master, brom, accion="unica_proponer", name="Puño de piedra", description="Rompe muros.")
        self.assertEqual(UniqueSkill.objects.get(character=brom).status, UniqueSkill.Status.APROBADA)


class RulesetTests(LevelUpData):
    """Each campaign plays with Aegis (ours, the default) or the standard SRD 5.2.1."""

    def setUp(self):
        self.campaign.ruleset = Campaign.Ruleset.SRD
        self.campaign.save()

    def test_aegis_is_the_default_and_only_masters_change_it(self):
        self.assertEqual(Campaign.objects.create(name="Nueva", start_date=datetime.date(2026, 1, 1)).ruleset, "aegis")
        url = reverse("campaign_ruleset", args=[self.campaign.pk])
        self.client.force_login(self.player)
        self.assertEqual(self.client.post(url, {"reglamento": "aegis"}).status_code, 403)
        self.client.force_login(self.master)
        self.client.post(url, {"reglamento": "aegis"})
        self.campaign.refresh_from_db()
        self.assertTrue(self.campaign.is_aegis)

    def test_srd_hit_points_use_the_class_hit_die(self):
        aria = self.mage(xp=300, hp=20, temp_hp=20)
        self.level_up(aria, hp_method="fijo", hp_roll="")  # no 1d4 in the SRD
        aria.refresh_from_db()
        # d6: fixed value 4, + Constitution 2.
        self.assertEqual((aria.level, aria.hp), (2, 26))
        lira = self.mage("Lira", xp=300, hp=20, temp_hp=20)
        self.level_up(lira, hp_method="tirar")
        lira.refresh_from_db()
        self.assertIn(lira.hp - 20, range(1 + 2, 6 + 2 + 1))

    def test_srd_feat_levels(self):
        self.assertEqual([lv for lv in range(1, 21) if rules.is_feat_level(lv, rules.SRD, "Guerrero")], [4, 6, 8, 12, 14, 16, 19])
        self.assertEqual([lv for lv in range(1, 21) if rules.is_feat_level(lv, rules.SRD, "Mago")], [4, 8, 12, 16, 19])

    def test_srd_new_spells_follow_the_class_table(self):
        # Mago 1 -> 2: one more prepared spell, no cantrip, highest slot 1.
        self.assertEqual(rules.srd_new_spells("Mago", 2), (0, 1))
        aria = self.mage(xp=300)
        self.client.force_login(self.player)
        page = self.client.get(reverse("level_up", args=[self.campaign.pk, aria.pk])).content.decode()
        self.assertIn(f'name="spells" value="{self.missile.pk}"', page)
        self.assertNotIn(f'name="spells" value="{self.cantrip.pk}"', page)
        self.assertNotIn(f'name="spells" value="{self.misty_step.pk}"', page)
        self.assertContains(self.level_up(aria, hp_method="fijo", spells=[self.missile.pk, self.shield.pk]), "como mucho 1")
        self.level_up(aria, hp_method="fijo", spells=[self.missile.pk])
        aria.refresh_from_db()
        self.assertEqual((aria.level, list(aria.spells.all())), (2, [self.missile]))

    def test_unique_skills_are_aegis_only(self):
        aria = self.mage()
        self.client.force_login(self.player)
        sheet = self.client.get(reverse("sheet", args=[self.campaign.pk, aria.pk]) + "?tab=feats")
        self.assertNotContains(sheet, "habilidad-unica")
        response = self.client.post(reverse("sheet_action", args=[self.campaign.pk, aria.pk]),
                                    {"accion": "unica_proponer", "name": "X", "description": "Y"}, **AJAX)
        self.assertTrue(response.json()["error"])
        self.assertFalse(UniqueSkill.objects.filter(character=aria).exists())
        # The creator skips its step.
        creator = reverse("creator", args=[self.campaign.pk])
        self.assertRedirects(self.client.get(creator + "?paso=5"), creator + "?paso=6", fetch_redirect_response=False)


class DemoData(TestCase):
    fixtures = ["datos_iniciales_srd", "armas_caseras", "clases_aegis", "demo_campana"]

    @classmethod
    def setUpTestData(cls):
        cls.corona = Campaign.objects.get(name="La Corona de Ceniza")
        cls.lucia = User.objects.get(username="lucia")
        cls.carlos = User.objects.get(username="carlos")

    def player(self):
        self.client.force_login(self.lucia)
        return self.client

    def url(self, name, *args):
        return reverse(name, args=[self.corona.pk, *args])


class HiddenContentTests(DemoData):
    """Nothing hidden reaches a player: not in the HTML, not in the JSON."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # The demo has no hidden adventures nor secrets on adventures and events: add some.
        first = Session.objects.filter(campaign=cls.corona).order_by("number").first()
        hidden = Quest.objects.create(campaign=cls.corona, title="El pacto que nadie firmó", start_session=first,
                                      description="Morwen compra a los guardias del faro.", hidden=True)
        QuestObjective.objects.create(quest=hidden, text="Descubrir quién paga a los guardias")
        Quest.objects.filter(campaign=cls.corona, hidden=False).update(secret="La corona entera es una réplica de cobre.")
        TimelineEvent.objects.filter(campaign=cls.corona, hidden=False).update(secret="El orco del faro era un espía de la torre.")

    def secrets(self):
        """Texts only the masters may read (whispers to others included)."""
        corona = self.corona
        texts = []
        texts += QuestObjective.objects.filter(quest__campaign=corona, hidden=True, done=False).values_list("text", flat=True)
        texts += BoardCard.objects.filter(campaign=corona, classified=True, revealed=False).values_list("text", flat=True)
        texts += Page.objects.filter(campaign=corona).exclude(secret="").values_list("secret", flat=True)
        texts += Page.objects.filter(campaign=corona, revealed=False).exclude(body="").values_list("body", flat=True)
        texts += TimelineEvent.objects.filter(campaign=corona, hidden=True).exclude(description="").values_list("description", flat=True)
        texts += TimelineEvent.objects.filter(campaign=corona).exclude(secret="").values_list("secret", flat=True)
        texts += Quest.objects.filter(campaign=corona).exclude(secret="").values_list("secret", flat=True)
        for quest in Quest.objects.filter(campaign=corona, hidden=True):
            texts += [quest.title, quest.description, *quest.objectives.values_list("text", flat=True)]
        texts += Session.objects.filter(campaign=corona).exclude(master_notes="").values_list("master_notes", flat=True)
        texts += Session.objects.filter(campaign=corona, published=False).exclude(body="").values_list("body", flat=True)
        texts += Rumour.objects.filter(campaign=corona, published=False).values_list("text", flat=True)
        texts += (Post.objects.filter(scene__campaign=corona, whisper_to__isnull=False)
                  .exclude(author=self.lucia).exclude(whisper_to__player=self.lucia).values_list("text", flat=True))
        # Unique skills not approved yet of other players' characters, and the master's notes on them.
        others = UniqueSkill.objects.filter(character__campaigns=corona).exclude(character__player=self.lucia)
        texts += others.exclude(status=UniqueSkill.Status.APROBADA).values_list("description", flat=True)
        texts += others.exclude(master_note="").values_list("master_note", flat=True)
        # The longest plain piece of each: it survives HTML escaping, line breaks and [[links]].
        return {max(re.split(r"[\n<>&\"'\[\]]", text), key=len).strip()[:60] for text in texts if text.strip()}

    def pages(self):
        corona = self.corona
        urls = [self.url(name) for name in ("home", "diary", "diary_timeline", "diary_board", "diary_scenes",
                                            "world", "rumours", "map", "characters", "turns")]
        urls += [self.url("diary_timeline") + f"?aventura={quest.pk}" for quest in corona.quests.all()]
        urls += [self.url("world") + f"?cat={category}" for category in ("lugar", "pnj", "faccion", "objeto", "historia")]
        urls += [self.url("diary_scene", scene.pk) for scene in corona.scenes.all()]
        urls += [self.url("world_page", page.slug) for page in Page.objects.filter(campaign=corona, revealed=True)]
        urls += [self.url("diary_board") + f"?nota={card.pk}" for card in corona.board_cards.all()]
        for map_ in corona.maps.all():
            urls += [self.url("map") + f"?mapa={map_.pk}&lugar={location.pk}" for location in map_.locations.all()]
        aria = Character.objects.get(name="Aria")
        urls += [self.url("sheet", aria.pk) + f"?tab={tab}" for tab in ("res", "act", "spells", "inv", "feats", "bio")]
        urls += [self.url("sheet", Character.objects.get(name="Kaida").pk) + "?tab=feats"]  # her unique skill is pending
        return urls

    def test_demo_has_secrets_to_hide(self):
        self.assertGreaterEqual(len(self.secrets()), 8)

    def test_no_secret_in_any_page_or_json(self):
        client = self.player()
        secrets = self.secrets()
        for url in self.pages():
            for headers in ({}, AJAX):
                response = client.get(url, **headers)
                self.assertEqual(response.status_code, 200, url)
                body = response.content.decode()
                if response.get("Content-Type", "").startswith("application/json"):
                    body = json.dumps(response.json(), ensure_ascii=False)
                for secret in secrets:
                    self.assertFalse(secret in body, f"{secret!r} leaked in {url}")

    def test_the_master_does_see_them(self):
        self.client.force_login(self.carlos)
        hidden_pages = [self.url("world_page", page.slug) for page in Page.objects.filter(campaign=self.corona, revealed=False)]
        body = "".join(self.client.get(url).content.decode() for url in self.pages() + hidden_pages)
        for secret in self.secrets():
            self.assertTrue(secret in body, f"the master does not see {secret!r}")

    def test_hidden_adventures_do_not_exist_for_players(self):
        quest = Quest.objects.get(campaign=self.corona, hidden=True)
        client = self.player()
        self.assertNotContains(client.get(self.url("diary")), quest.title)
        self.assertNotContains(client.get(self.url("diary_timeline") + f"?aventura={quest.pk}"), quest.title)
        self.assertNotContains(client.get(self.url("home")), quest.title)
        self.assertEqual(client.get(self.url("diary_quest_edit", quest.pk)).status_code, 403)
        self.client.force_login(self.carlos)
        self.assertContains(self.client.get(self.url("diary")), "OCULTA · solo la ves tú")

    def test_sessions_without_a_date(self):
        session = Session.objects.filter(campaign=self.corona, published=True).order_by("number").first()
        Session.objects.filter(pk=session.pk).update(played_on=None)
        for name in ("diary", "diary_timeline", "home"):
            self.assertEqual(self.player().get(self.url(name)).status_code, 200, name)

    def test_hidden_objective_text_is_not_sent(self):
        objective = QuestObjective.objects.filter(quest__campaign=self.corona, hidden=True, done=False).first()
        response = self.player().get(self.url("diary"))
        self.assertNotContains(response, objective.text)
        self.assertContains(response, "Objetivo oculto")

    def test_classified_note_content_is_not_sent(self):
        card = BoardCard.objects.filter(campaign=self.corona, classified=True, revealed=False).first()
        response = self.player().get(self.url("diary_board") + f"?nota={card.pk}", **AJAX)
        self.assertNotIn(card.text, json.dumps(response.json(), ensure_ascii=False))

    def test_hidden_pages_do_not_exist_for_players(self):
        page = Page.objects.filter(campaign=self.corona, revealed=False).first()
        client = self.player()
        self.assertEqual(client.get(self.url("world_page", page.slug)).status_code, 404)
        self.assertEqual(client.get(self.url("world_preview", page.slug)).status_code, 404)

    def test_hidden_map_locations_are_not_sent(self):
        response = self.player().get(self.url("map"))
        statuses = {location.status for location in response.context["md"]["locations"]}
        self.assertNotIn(MapLocation.Status.OCULTO, statuses)
        self.assertTrue(MapLocation.objects.filter(map__campaign=self.corona, status=MapLocation.Status.OCULTO).exists())

    def test_enemy_hit_points_are_not_sent_to_players(self):
        encounter = Encounter.objects.get(campaign=self.corona, active=True)
        enemy = Combatant.objects.filter(encounter=encounter, character__is_npc=True).first()
        html = self.player().get(self.url("turns"), **AJAX).json()["html"]
        self.assertNotIn(f'data-valor="vida-{enemy.pk}"', html)
        self.assertIn(enemy.display_name, html)


class AccessTests(DemoData):
    def test_admin_roles_have_different_permissions(self):
        """«Reglas» (nerea) edits the rules catalog; «Campañas» (mikel) edits the campaigns
        and only views the catalog (migration 0018)."""
        rules_add, campaign_add = reverse("admin:CampaignManager_ability_add"), reverse("admin:CampaignManager_campaign_add")
        rules_list = reverse("admin:CampaignManager_ability_changelist")
        self.client.force_login(User.objects.get(username="nerea"))
        self.assertEqual(self.client.get(rules_add).status_code, 200)
        self.assertEqual(self.client.get(campaign_add).status_code, 403)
        self.client.force_login(User.objects.get(username="mikel"))
        self.assertEqual(self.client.get(campaign_add).status_code, 200)
        self.assertEqual(self.client.get(rules_list).status_code, 200)
        self.assertEqual(self.client.get(rules_add).status_code, 403)

    def test_everything_needs_a_login(self):
        for url in (self.url("home"), self.url("characters"), reverse("index"), reverse("character_list")):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertIn(reverse("login"), response["Location"])

    def test_other_campaigns_are_404(self):
        outsider = Campaign.objects.exclude(masters=self.lucia).exclude(players=self.lucia).first()
        self.assertEqual(self.player().get(reverse("home", args=[outsider.pk])).status_code, 404)

    def test_players_only_change_their_own_characters(self):
        brom = Character.objects.get(name="Brom")
        before = brom.temp_hp
        response = self.player().post(self.url("sheet_action", brom.pk), {"accion": "vida_menos"}, **AJAX)
        self.assertEqual(response.status_code, 403)
        brom.refresh_from_db()
        self.assertEqual(brom.temp_hp, before)

    def test_players_cannot_open_npc_sheets(self):
        npc = Character.objects.filter(campaigns=self.corona, is_npc=True).first()
        self.assertEqual(self.player().get(self.url("sheet", npc.pk)).status_code, 404)

    def test_master_only_actions_are_403_for_players(self):
        client = self.player()
        self.assertEqual(client.post(self.url("turns_action"), {"accion": "siguiente"}, **AJAX).status_code, 403)
        self.assertEqual(client.get(self.url("diary_quest_new")).status_code, 403)


class SheetTests(DemoData):
    def test_hit_points_stay_between_death_and_maximum(self):
        aria = Character.objects.get(name="Aria")
        client = self.player()
        url = self.url("sheet_action", aria.pk)
        client.post(url, {"accion": "curar", "cantidad": "999"}, **AJAX)
        aria.refresh_from_db()
        self.assertEqual(aria.temp_hp, aria.hp)
        client.post(url, {"accion": "dano", "cantidad": "999"}, **AJAX)
        aria.refresh_from_db()
        self.assertEqual((aria.temp_hp, aria.dead), (rules.death_hp(aria.hp), True))

    def test_casting_with_a_level_n_slot_costs_n_magic_points(self):
        aria = Character.objects.get(name="Aria")
        spell = aria.spells.filter(level=3).first()
        before = aria.magic_points
        self.player().post(self.url("sheet_action", aria.pk), {"accion": "lanzar", "conjuro": spell.pk, "espacio": "3"}, **AJAX)
        aria.refresh_from_db()
        self.assertEqual(aria.magic_points, before - 3)

    def test_without_javascript_the_form_redirects_back(self):
        aria = Character.objects.get(name="Aria")
        response = self.player().post(self.url("sheet_action", aria.pk), {"accion": "vida_menos", "tab": "inv"})
        self.assertRedirects(response, self.url("sheet", aria.pk) + "?tab=inv")


class TurnsTests(DemoData):
    def test_closing_a_round_ticks_cooldowns_and_effects(self):
        from .models import ActiveEffect, Effect
        encounter = Encounter.objects.get(campaign=self.corona, active=True)
        brom = Character.objects.get(name="Brom")
        effect = ActiveEffect.objects.create(character=brom, effect=Effect.objects.first(), remaining_turns=1)
        ability = brom.characterability_set.filter(cooldown_rounds__isnull=False).first()
        ability.cooldown_left = 2
        ability.save()
        self.client.force_login(self.carlos)
        for _ in range(encounter.combatants.count() - encounter.turn_index):
            self.client.post(self.url("turns_action"), {"accion": "siguiente"}, **AJAX)
        encounter.refresh_from_db()
        self.assertEqual((encounter.round, encounter.turn_index), (3, 0))
        self.assertFalse(ActiveEffect.objects.filter(pk=effect.pk).exists())
        ability.refresh_from_db()
        self.assertEqual(ability.cooldown_left, 1)


class RouteTests(DemoData):
    """The way followed between two stops: waypoints the master draws, and an arrow to each stop."""

    def setUp(self):
        self.moves = list(PartyMove.objects.filter(map__campaign=self.corona).select_related("location", "map"))
        self.move = self.moves[1]  # the stretch from the first stop to the second
        self.start, self.end = self.moves[0].location, self.move.location

    def save_path(self, user, puntos):
        self.client.force_login(user)
        return self.client.post(self.url("map_move_path", self.move.pk), {"puntos": puntos}, **AJAX)

    def test_every_stretch_has_a_line_and_an_arrow_to_its_stop(self):
        page = self.player().get(self.url("map") + f"?mapa={self.move.map_id}").content.decode()
        self.assertEqual(page.count("<polyline"), len(self.moves) - 1)
        self.assertIn(f'data-flecha="{self.move.pk}" style="left:{self.end.x:g}%;top:{self.end.y:g}%', page)

    def test_the_master_draws_the_way_between_two_stops(self):
        response = self.save_path(self.carlos, "10,20 30.556,40")
        self.assertEqual(response.json()["error"], False)
        self.move.refresh_from_db()
        self.assertEqual(self.move.path, [[10, 20], [30.56, 40]])
        page = self.player().get(self.url("map") + f"?mapa={self.move.map_id}").content.decode()
        self.assertIn(f'points="{self.start.x:g},{self.start.y:g} 10,20 30.56,40 {self.end.x:g},{self.end.y:g}"', page)
        # The arrow points along the last part: from the last waypoint to the stop.
        self.assertIn(f"--dx:{round(self.end.x - 30.56, 2):g};--dy:{round(self.end.y - 40, 2):g}", page)

    def test_an_empty_way_is_a_straight_line_again(self):
        self.move.path = [[10, 20]]
        self.move.save()
        self.save_path(self.carlos, "")
        self.move.refresh_from_db()
        self.assertEqual(self.move.path, [])

    def test_players_cannot_draw_the_way(self):
        self.assertEqual(self.save_path(self.lucia, "10,20").status_code, 403)
        self.move.refresh_from_db()
        self.assertEqual(self.move.path, [])

    def test_points_outside_the_map_are_refused(self):
        for bad in ("10,abc", "150,20", "10", "nan,5"):
            self.assertEqual(self.save_path(self.carlos, bad).status_code, 400, bad)
        self.move.refresh_from_db()
        self.assertEqual(self.move.path, [])

    def test_only_the_master_gets_the_route_editor(self):
        url = self.url("map") + f"?mapa={self.move.map_id}&recorrido=1"
        self.assertNotContains(self.player().get(url), "data-guardar")
        self.client.force_login(self.carlos)
        self.assertContains(self.client.get(url), self.url("map_move_path", self.move.pk))


class BigBoardTests(DemoData):
    """The board is three times bigger each way and is seen through a zoomable window."""

    def add_note(self, **position):
        self.player().post(self.url("diary_card_add"), {"title": "Pista nueva", **position}, **AJAX)
        return BoardCard.objects.get(campaign=self.corona, title="Pista nueva")

    def test_a_new_note_goes_where_the_window_is_looking(self):
        card = self.add_note(x="2600", y="1500")
        self.assertEqual((card.x, card.y), (2600, 1500))

    def test_a_new_note_stays_on_the_board(self):
        card = self.add_note(x="99999", y="-50")
        self.assertEqual((card.x, card.y), (3300 - 200, 0))

    def test_without_a_position_the_note_goes_near_the_middle(self):
        card = self.add_note()
        self.assertTrue(1300 < card.x < 1800 and 700 < card.y < 1100, (card.x, card.y))

    def test_notes_can_be_moved_anywhere_on_the_big_board(self):
        card = self.add_note()
        self.client.post(self.url("diary_card_action", card.pk), {"accion": "mover", "x": "3000", "y": "1750"}, **AJAX)
        card.refresh_from_db()
        self.assertEqual((card.x, card.y), (3000, 1750))

    def test_the_board_window_is_zoomable(self):
        page = self.player().get(self.url("diary_board")).content.decode()
        self.assertIn(f'data-zoom="pizarra-{self.corona.pk}"', page)
        self.assertIn('data-ancho="3300" data-alto="1920"', page)


class UniqueSkillChangeTests(RulesData):
    """After approval the master can rewrite the unique skill or take it away; before it,
    the player can withdraw the proposal."""

    def act(self, user, character, **data):
        self.client.force_login(user)
        return self.client.post(reverse("sheet_action", args=[self.campaign.pk, character.pk]), data, **AJAX)

    def approved(self):
        brom = self.character(campaigns=[self.campaign])
        self.act(self.master, brom, accion="unica_proponer", name="Puño de piedra", description="Rompe muros.")
        return brom, UniqueSkill.objects.get(character=brom)

    def test_the_master_rewrites_it_and_it_stays_the_same_ability(self):
        brom, skill = self.approved()
        self.act(self.master, brom, accion="unica_editar", name="Puño de hierro", description="Rompe puertas.", nota="Más corta.")
        skill.refresh_from_db()
        self.assertEqual((skill.name, skill.ability.name, skill.ability.description, skill.master_note),
                         ("Puño de hierro", "Puño de hierro", "Rompe puertas.", "Más corta."))
        self.assertEqual(list(brom.abilities.all()), [skill.ability])

    def test_the_master_takes_it_away_and_the_player_can_propose_another(self):
        brom, skill = self.approved()
        self.act(self.master, brom, accion="unica_quitar")
        self.assertFalse(UniqueSkill.objects.filter(character=brom).exists())
        self.assertFalse(Ability.objects.filter(pk=skill.ability_id).exists())
        self.assertFalse(brom.abilities.exists())
        self.act(self.player, brom, accion="unica_proponer", name="Otra", description="Otra cosa.")
        self.assertEqual(UniqueSkill.objects.get(character=brom).status, UniqueSkill.Status.PENDIENTE)

    def test_players_cannot_change_an_approved_one(self):
        brom, skill = self.approved()
        self.assertEqual(self.act(self.player, brom, accion="unica_editar", name="Dios", description="Todo.").status_code, 403)
        self.assertEqual(self.act(self.player, brom, accion="unica_quitar").status_code, 403)
        self.assertTrue(self.act(self.player, brom, accion="unica_retirar").json()["error"])
        skill.refresh_from_db()
        self.assertEqual(skill.ability.name, "Puño de piedra")

    def test_the_player_withdraws_a_proposal(self):
        brom = self.character(campaigns=[self.campaign])
        self.act(self.player, brom, accion="unica_proponer", name="Inmortal", description="No puede morir.")
        self.act(self.player, brom, accion="unica_retirar")
        self.assertFalse(UniqueSkill.objects.filter(character=brom).exists())


class KnockdownAndDeathTests(RulesData):
    """House rule: at 0 hit points a character is knocked down; it dies at minus half its
    maximum, or when it is knocked down a second time in the same fight."""

    def setUp(self):
        self.brom = self.character(campaigns=[self.campaign], hp=20, temp_hp=20)  # dies at -10

    def act(self, user=None, **data):
        self.client.force_login(user or self.player)
        response = self.client.post(reverse("sheet_action", args=[self.campaign.pk, self.brom.pk]), data, **AJAX)
        self.brom.refresh_from_db()
        return response

    def hit(self, amount, user=None):
        return self.act(user, accion="dano", cantidad=str(amount))

    def test_death_hp_is_minus_half_the_maximum(self):
        self.assertEqual((rules.death_hp(90), rules.death_hp(45), rules.death_hp(20)), (-45, -23, -10))

    def test_at_zero_it_is_knocked_down_not_dead(self):
        msg = self.hit(20).json()["msg"]
        self.assertEqual((self.brom.temp_hp, self.brom.knocked_down, self.brom.dead), (0, True, False))
        self.assertIn("derribado", msg)
        self.assertContains(self.client.get(reverse("sheet", args=[self.campaign.pk, self.brom.pk])), "Muere si llega a -10 de vida")

    def test_it_dies_at_minus_half_its_maximum(self):
        self.hit(29)
        self.assertEqual((self.brom.temp_hp, self.brom.dead), (-9, False))
        self.hit(1)
        self.assertEqual((self.brom.temp_hp, self.brom.dead), (-10, True))
        self.hit(50)  # dead: nothing changes any more
        self.assertEqual(self.brom.temp_hp, -10)

    def test_a_second_knockdown_in_the_same_fight_kills(self):
        Encounter.objects.create(campaign=self.campaign)
        self.hit(20)
        self.act(accion="curar", cantidad="5")
        self.assertEqual((self.brom.temp_hp, self.brom.dead), (5, False))
        msg = self.hit(5).json()["msg"]
        self.assertTrue(self.brom.dead)
        self.assertIn("segunda vez", msg)

    def test_falls_in_different_fights_do_not_add_up(self):
        first = Encounter.objects.create(campaign=self.campaign)
        self.hit(20)
        self.act(accion="curar", cantidad="5")
        first.active = False
        first.save()
        Encounter.objects.create(campaign=self.campaign)
        self.hit(5)
        self.assertEqual((self.brom.knocked_down, self.brom.dead), (True, False))

    def test_out_of_a_fight_falls_do_not_add_up(self):
        self.hit(20)
        self.act(accion="curar", cantidad="5")
        self.hit(5)
        self.assertEqual((self.brom.knocked_down, self.brom.dead), (True, False))

    def test_only_the_master_revives(self):
        self.hit(30)
        self.assertTrue(self.brom.dead)
        self.assertTrue(self.act(accion="curar", cantidad="10").json()["error"])
        self.assertTrue(self.act(accion="descanso_largo").json()["error"])
        self.assertEqual(self.act(accion="revivir").status_code, 403)
        self.act(self.master, accion="revivir")
        self.assertEqual((self.brom.dead, self.brom.temp_hp), (False, 1))

    def test_the_turns_list_follows_the_same_rule(self):
        encounter = Encounter.objects.create(campaign=self.campaign)
        combatant = Combatant.objects.create(encounter=encounter, character=self.brom)
        self.client.force_login(self.master)
        url = reverse("turns_action", args=[self.campaign.pk])
        self.client.post(url, {"accion": "dano", "cantidad": "20", "combatiente": combatant.pk}, **AJAX)
        self.client.post(url, {"accion": "curar", "cantidad": "3", "combatiente": combatant.pk}, **AJAX)
        self.client.post(url, {"accion": "dano", "cantidad": "3", "combatiente": combatant.pk}, **AJAX)
        self.brom.refresh_from_db()
        self.assertTrue(self.brom.dead)


class DamageThroughArmorTests(RulesData):
    """House rule with game rule 3: True Damage = Damage - AP. The AP stay, unless the hit
    is triturante (AP lost after the damage); penetrante ignores that many AP for the hit."""

    def setUp(self):
        self.brom = self.character(campaigns=[self.campaign], hp=30, temp_hp=30)
        self.carry(self.brom, self.mail, equipped=True)  # max AP 5
        self.brom.temp_ap = 5
        self.brom.save()

    def act(self, **data):
        self.client.force_login(self.player)
        response = self.client.post(reverse("sheet_action", args=[self.campaign.pk, self.brom.pk]), data, **AJAX)
        self.brom.refresh_from_db()
        return response

    def test_the_ap_take_off_part_of_the_damage_and_stay(self):
        msg = self.act(accion="dano", cantidad="12").json()["msg"]
        self.assertEqual((self.brom.temp_hp, self.brom.temp_ap), (23, 5))
        self.assertIn("12 − 5 AP = 7", msg)

    def test_a_hit_smaller_than_the_ap_does_nothing(self):
        self.act(accion="dano", cantidad="4")
        self.assertEqual((self.brom.temp_hp, self.brom.temp_ap), (30, 5))

    def test_penetrante_ignores_ap_for_that_hit(self):
        self.act(accion="dano", cantidad="12", penetrante="2")
        self.assertEqual((self.brom.temp_hp, self.brom.temp_ap), (21, 5))

    def test_triturante_takes_ap_after_the_damage(self):
        self.act(accion="dano", cantidad="12", triturante="3")
        self.assertEqual((self.brom.temp_hp, self.brom.temp_ap), (23, 2))  # the hit met the full 5 AP

    def test_healing_and_the_one_point_buttons_do_not_go_through_ap(self):
        self.act(accion="vida_menos")
        self.assertEqual(self.brom.temp_hp, 29)

    def test_ap_are_added_and_taken_by_amounts_up_to_the_armor_maximum(self):
        self.act(accion="ap_quitar", cantidad="4")
        self.assertEqual(self.brom.temp_ap, 1)
        self.act(accion="ap_sumar", cantidad="10")
        self.assertEqual(self.brom.temp_ap, 5)
        self.act(accion="ap_quitar", cantidad="99")
        self.assertEqual(self.brom.temp_ap, 0)
        self.assertTrue(self.act(accion="ap_sumar", cantidad="").json()["error"])


class MapDeletionTests(DemoData):
    """The master removes a stretch of the route added by mistake, or a whole place,
    which takes with it the stretches that lead to it."""

    def setUp(self):
        self.moves = list(PartyMove.objects.filter(map__campaign=self.corona).select_related("location"))

    def test_removing_a_stretch_joins_the_stops_around_it(self):
        middle = self.moves[1]
        self.client.force_login(self.carlos)
        response = self.client.post(self.url("map_move_delete", middle.pk), **AJAX)
        self.assertIn("quitado del camino", response.json()["msg"])
        self.assertFalse(PartyMove.objects.filter(pk=middle.pk).exists())
        self.assertTrue(MapLocation.objects.filter(pk=middle.location_id).exists())  # the place stays
        page = self.client.get(self.url("map") + f"?mapa={middle.map_id}").content.decode()
        self.assertEqual(page.count("<polyline"), len(self.moves) - 2)

    def test_removing_a_place_removes_the_stretches_that_lead_to_it(self):
        place = self.moves[1].location
        stops = PartyMove.objects.filter(location=place).count()
        page = Page.objects.filter(location=place).first() or Page.objects.create(
            campaign=self.corona, category=Page.Category.LUGAR, title=place.name, location=place, revealed=True)
        self.client.force_login(self.carlos)
        self.client.post(self.url("map_location_action", place.pk), {"accion": "borrar"}, **AJAX)
        self.assertFalse(MapLocation.objects.filter(pk=place.pk).exists())
        self.assertFalse(PartyMove.objects.filter(location_id=place.pk).exists())
        self.assertEqual(PartyMove.objects.filter(map__campaign=self.corona).count(), len(self.moves) - stops)
        page.refresh_from_db()  # its page in the World stays, no longer linked to the map
        self.assertIsNone(page.location_id)

    def test_players_cannot_remove_anything(self):
        client = self.player()
        self.assertEqual(client.post(self.url("map_move_delete", self.moves[1].pk), **AJAX).status_code, 403)
        self.assertEqual(client.post(self.url("map_location_action", self.moves[1].location_id), {"accion": "borrar"}, **AJAX).status_code, 403)
        self.assertEqual(PartyMove.objects.filter(map__campaign=self.corona).count(), len(self.moves))


class AdventureTests(DemoData):
    """«Aventuras» (once «Crónicas» and «Misiones»): an adventure is in progress, completed or
    failed, spans from one session to another, has the master's checklist (hidden objectives
    show as «Objetivo oculto» until checked) and appears as a bar on the timeline."""

    def quest(self, title):
        return Quest.objects.get(campaign=self.corona, title=title)

    def master(self):
        self.client.force_login(self.carlos)
        return self.client

    def save(self, quest=None, **fields):
        data = {"title": "La torre", "description": "", "status": "activa", "start_session": "", "end_session": "", "giver": "",
                "objectives-TOTAL_FORMS": "0", "objectives-INITIAL_FORMS": "0", **fields}
        url = self.url("diary_quest_edit", quest.pk) if quest else self.url("diary_quest_new")
        return self.master().post(url, data)

    def session(self, number):
        return Session.objects.get(campaign=self.corona, number=number)

    def test_the_tab_is_called_aventuras_and_misiones_is_part_of_it(self):
        page = self.player().get(self.url("diary")).content.decode()
        self.assertIn(">Aventuras<", page)
        self.assertNotIn("Crónicas", page)
        self.assertNotIn(">Misiones<", page)
        self.assertRedirects(self.player().get(self.url("diary_quests")), self.url("diary"))

    def test_each_adventure_shows_its_span_and_its_sessions(self):
        page = self.player().get(self.url("diary")).content.decode()
        # From this adventure to the next one (finished adventures are folded <details>, not <section>).
        start = page.index('id="aventura-%d"' % self.quest("Saqueadores en el Camino del Faro").pk)
        end = page.find('id="aventura-', start + 1)
        raiders = page[start:end if end != -1 else None]
        self.assertIn("Sesión 2 → 2", raiders)
        self.assertIn('id="sesion-%d"' % self.session(2).pk, raiders)
        self.assertNotIn('id="sesion-%d"' % self.session(3).pk, raiders)
        self.assertIn("Sesión 1 → en curso", page)

    def test_an_adventure_can_start_in_one_session_and_end_in_another(self):
        self.save(title="La torre", status="completada", start_session=self.session(3).pk, end_session=self.session(5).pk)
        tower = self.quest("La torre")
        self.assertEqual((tower.start_session.number, tower.end_session.number, tower.status), (3, 5, "completada"))
        self.assertEqual([n for n in range(1, 7) if tower.covers(n, 6)], [3, 4, 5])

    def test_the_span_must_make_sense(self):
        in_progress = self.save(status="activa", start_session=self.session(3).pk, end_session=self.session(5).pk)
        self.assertContains(in_progress, "Una aventura en curso todavía no ha terminado")
        backwards = self.save(status="fallida", start_session=self.session(5).pk, end_session=self.session(3).pk)
        self.assertContains(backwards, "No puede terminar antes de empezar")
        self.assertFalse(Quest.objects.filter(title="La torre").exists())

    def test_a_hidden_objective_is_revealed_when_checked(self):
        objective = QuestObjective.objects.filter(quest__campaign=self.corona, hidden=True, done=False).first()
        self.assertNotContains(self.player().get(self.url("diary")), objective.text)
        self.master().post(self.url("diary_objective_toggle", objective.pk), **AJAX)
        page = self.player().get(self.url("diary"))
        self.assertContains(page, objective.text)

    def test_the_timeline_shows_the_path_of_adventures(self):
        page = self.player().get(self.url("diary_timeline")).content.decode()
        for quest in self.corona.quests.all():
            self.assertIn(f"#aventura-{quest.pk}", page)
        self.assertIn("tl-aventura activa abierta", page)
        self.assertIn("tl-aventura fallida", page)

    def test_the_timeline_of_one_adventure(self):
        raiders = self.quest("Saqueadores en el Camino del Faro")
        page = self.player().get(self.url("diary_timeline") + f"?aventura={raiders.pk}").content.decode()
        self.assertIn("S2 · ", page)
        self.assertNotIn("S5 · ", page)
        self.assertIn(f"#aventura-{raiders.pk}", page)
        self.assertNotIn(f"#aventura-{self.quest('La Corona de Ceniza').pk}", page)

    def test_players_do_not_see_unpublished_sessions_of_an_adventure(self):
        last = self.session(6)
        last.published = False
        last.save()
        page = self.player().get(self.url("diary")).content.decode()
        self.assertNotIn(f'id="sesion-{last.pk}"', page)
        self.assertNotIn("S6 · ", self.player().get(self.url("diary_timeline")).content.decode())


def png_upload(name="foto.png"):
    """A small real PNG, as a browser would upload it."""
    import io
    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (40, 50), "#884422").save(buffer, "PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


class ObjectiveEditorTests(DemoData):
    """The adventure form has one «new objective» box that the page turns into as many
    objectives as the master adds; new ones go at the end and the card shows the progress."""

    def post(self, url, objectives, initial=0, **fields):
        data = {"title": "La torre", "description": "", "status": "activa", "start_session": "", "end_session": "",
                "giver": "", "objectives-TOTAL_FORMS": str(len(objectives)), "objectives-INITIAL_FORMS": str(initial)}
        for i, row in enumerate(objectives):
            for key, value in row.items():
                data[f"objectives-{i}-{key}"] = value
        data.update(fields)
        self.client.force_login(self.carlos)
        return self.client.post(url, data)

    def test_the_form_has_a_single_new_objective_box(self):
        self.client.force_login(self.carlos)
        page = self.client.get(self.url("diary_quest_new")).content.decode()
        self.assertIn('name="objectives-TOTAL_FORMS" value="1"', page)
        self.assertIn("data-anadir-objetivo", page)
        self.assertIn('id="objetivo-plantilla"', page)

    def test_many_objectives_are_saved_at_once_in_the_order_they_were_added(self):
        rows = [{"text": f"Paso {n}"} for n in range(1, 8)] + [{"text": ""}]  # 7 added, then the empty box
        rows[2]["hidden"] = "on"
        self.post(self.url("diary_quest_new"), rows)
        quest = Quest.objects.get(campaign=self.corona, title="La torre")
        self.assertEqual([o.text for o in quest.objectives.all()], [f"Paso {n}" for n in range(1, 8)])
        self.assertEqual([o.order for o in quest.objectives.all()], list(range(7)))
        self.assertTrue(quest.objectives.get(text="Paso 3").hidden)

    def test_editing_adds_at_the_end_and_removes(self):
        self.post(self.url("diary_quest_new"), [{"text": "Uno"}, {"text": "Dos"}, {"text": ""}])
        quest = Quest.objects.get(campaign=self.corona, title="La torre")
        one, two = quest.objectives.all()
        rows = [{"id": one.pk, "text": "Uno", "done": "on"}, {"id": two.pk, "text": "Dos", "DELETE": "on"},
                {"text": "Tres"}, {"text": "Cuatro"}, {"text": ""}]
        self.post(self.url("diary_quest_edit", quest.pk), rows, initial=2)
        self.assertEqual([(o.text, o.done) for o in quest.objectives.all()], [("Uno", True), ("Tres", False), ("Cuatro", False)])
        self.assertEqual(quest.objectives.get(text="Cuatro").order, quest.objectives.get(text="Tres").order + 1)

    def test_the_card_shows_the_progress(self):
        quest = Quest.objects.get(campaign=self.corona, title="La Corona de Ceniza")
        done, total = quest.objectives.filter(done=True).count(), quest.objectives.count()
        page = self.player().get(self.url("diary")).content.decode()
        card = page[page.index(f'id="mision-{quest.pk}"'):]
        self.assertIn(f"<b>{done} / {total}</b>", card[:card.index("</article>")])
        self.assertIn(f"width:{round(100 * done / total)}%", card[:card.index("</article>")])


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="kiroku-test-media-"))
class PersonCardTests(DemoData):
    """The board also has person cards: with a photo, and a red cross once dealt with."""

    def add(self, user=None, **data):
        self.client.force_login(user or self.lucia)
        self.client.post(self.url("diary_card_add"), {"title": "El encapuchado", "kind": "persona", **data})
        return BoardCard.objects.get(campaign=self.corona, title="El encapuchado")

    def test_a_person_card_with_a_photo(self):
        card = self.add(image=png_upload())
        self.assertTrue(card.is_person)
        self.assertTrue(card.image.name.startswith("pizarra/"))
        page = self.player().get(self.url("diary_board")).content.decode()
        self.assertIn("nota persona", page)
        self.assertIn(card.image.url, page)

    def test_clues_do_not_keep_a_photo(self):
        card = self.add(kind="pista", image=png_upload())
        self.assertEqual((card.kind, card.image.name), ("pista", ""))

    def test_crossing_out_a_person_and_back(self):
        card = self.add()
        url = self.url("diary_card_action", card.pk)
        self.client.post(url, {"accion": "tachar"}, **AJAX)
        card.refresh_from_db()
        self.assertTrue(card.crossed)
        self.assertIn('class="tachon"', self.player().get(self.url("diary_board")).content.decode())
        self.client.post(url, {"accion": "tachar"}, **AJAX)
        card.refresh_from_db()
        self.assertFalse(card.crossed)

    def test_changing_and_removing_the_photo(self):
        card = self.add()
        url = self.url("diary_card_action", card.pk)
        self.client.post(url, {"accion": "foto", "foto": png_upload("otra.png")})
        card.refresh_from_db()
        self.assertTrue(card.image.name.startswith("pizarra/otra"))
        self.client.post(url, {"accion": "foto_quitar"}, **AJAX)
        card.refresh_from_db()
        self.assertEqual(card.image.name, "")

    def test_clues_cannot_be_crossed_out(self):
        self.client.force_login(self.lucia)
        clue = BoardCard.objects.filter(campaign=self.corona, kind="pista", classified=False).first()
        response = self.client.post(self.url("diary_card_action", clue.pk), {"accion": "tachar"}, **AJAX)
        self.assertTrue(response.json()["error"])

    def test_a_classified_person_shows_nothing_to_players(self):
        card = self.add(user=self.carlos, image=png_upload(), classified="on")
        card.crossed = True
        card.save()
        page = self.player().get(self.url("diary_board")).content.decode()
        self.assertNotIn(card.image.url, page)
        self.assertNotIn("El encapuchado", page)
        self.assertNotIn('class="tachon"', page)
        self.assertEqual(self.client.post(self.url("diary_card_action", card.pk), {"accion": "tachar"}, **AJAX).status_code, 403)


class FullScreenTests(DemoData):
    """The map and the board windows can go full screen, with a clear way out."""

    def test_both_windows_offer_full_screen_and_a_way_out(self):
        board = self.player().get(self.url("diary_board")).content.decode()
        self.assertIn('data-zoom-accion="pantalla"', board)
        self.assertIn('data-zoom-accion="salir"', board)
        corona_map = self.corona.maps.exclude(image="").first()
        if corona_map is not None:  # the zoom (and full screen) needs the map's image
            page = self.player().get(self.url("map") + f"?mapa={corona_map.pk}").content.decode()
            self.assertIn('data-zoom-accion="pantalla"', page)


class SheetViewTests(DemoData):
    """«Vista web» / «Vista ficha»: the choice is kept on the user, both views share the same
    actions and permissions, and the sheet's fingerprint lets tablets refresh it."""

    def choose(self, view, character=None, user=None):
        self.client.force_login(user or self.lucia)
        character = character or Character.objects.get(name="Aria")
        return self.client.post(self.url("sheet_view", character.pk), {"vista": view, "tab": "res"}, **AJAX)

    def test_the_choice_is_kept_on_the_user_for_every_sheet(self):
        response = self.choose("ficha")
        self.assertIn('class="hoja-vista"', response.json()["html"])
        self.lucia.refresh_from_db()
        self.assertEqual(self.lucia.sheet_view, "ficha")
        # Another sheet (and a full page load) opens in the same view.
        brom = Character.objects.get(name="Brom")
        self.assertContains(self.client.get(self.url("sheet", brom.pk)), 'class="hoja-vista"')
        self.choose("web")
        self.assertNotContains(self.client.get(self.url("sheet", brom.pk)), 'class="hoja-vista"')

    def test_an_unknown_view_is_404(self):
        self.assertEqual(self.choose("pdf").status_code, 404)

    def test_actions_answer_in_the_chosen_view_and_both_show_the_same_values(self):
        aria = Character.objects.get(name="Aria")
        self.choose("ficha")
        response = self.client.post(self.url("sheet_action", aria.pk), {"accion": "vida_menos", "tab": "res"}, **AJAX).json()
        aria.refresh_from_db()
        self.assertIn('class="hoja-vista"', response["html"])
        self.assertIn(f'data-valor="vida">{aria.temp_hp}<', response["html"])
        self.assertTrue(response["version"])
        self.choose("web")
        self.assertContains(self.client.get(self.url("sheet", aria.pk)), f'data-valor="vida">{aria.temp_hp}<')

    def test_the_paper_sheet_has_its_sections(self):
        self.choose("ficha")
        page = self.client.get(self.url("sheet", Character.objects.get(name="Aria").pk)).content.decode()
        for text in ("Ficha de personaje", "Juego de manos", "Trato con animales", "Tiradas de salvación", "Inventario",
                     "Ataques", "Afinidad NPC", "Apuntes", "Habilidades", "Habilidad única", "hoja-magia"):
            self.assertIn(text, page)

    def test_someone_elses_sheet_is_read_only_in_the_paper_view_too(self):
        brom = Character.objects.get(name="Brom")
        self.assertEqual(self.choose("ficha", brom).status_code, 200)  # choosing how to see it is allowed
        page = self.client.get(self.url("sheet", brom.pk)).content.decode()
        self.assertIn('class="hoja-fs hoja-columna" disabled', page)
        self.assertNotIn("descanso_largo", page)
        for action in ("vida_menos", "ap_mas", "insp_dar", "magia_gastar", "notas"):
            response = self.client.post(self.url("sheet_action", brom.pk), {"accion": action}, **AJAX)
            self.assertEqual(response.status_code, 403, action)

    def test_players_cannot_open_npc_sheets_through_the_view_switch(self):
        npc = Character.objects.filter(campaigns=self.corona, is_npc=True).first()
        self.assertEqual(self.choose("ficha", npc).status_code, 404)

    def test_the_fingerprint_changes_only_when_the_sheet_changes(self):
        aria = Character.objects.get(name="Aria")
        self.choose("ficha")
        url = self.url("sheet", aria.pk)
        first = self.client.get(url, **AJAX).json()["version"]
        self.assertEqual(self.client.get(url, **AJAX).json()["version"], first)
        # The master changes her hit points from Turns: the tablet's next refresh sees it.
        Character.objects.filter(pk=aria.pk).update(temp_hp=aria.temp_hp - 1)
        self.assertNotEqual(self.client.get(url, **AJAX).json()["version"], first)


class NpcCreatorTests(DemoData):
    """A master makes an NPC with the same creator: no player, free scores, own hit points.
    It lands in the campaign as a template that can be copied."""

    def walk(self, client, name, steps):
        url = self.url(name)
        for step, data in steps:
            response = client.post(url, {"paso": step, "ir": "siguiente", **data})
            self.assertEqual(response.status_code, 302, (step, getattr(response, "context", None) and response.context["form"].errors))
        return client.post(url, {"paso": rules_last_step(), "ir": "enviar"})

    def steps(self, scores, **identity):
        race = Race.objects.filter(subraces__isnull=True).order_by("name").first()
        char_class = CharacterClass.objects.order_by("name").first()
        return [(0, {"race": race.pk}), (1, {"char_class": char_class.pk}), (2, scores),
                (3, {"name": "Guardia de la torre", **identity}), (4, {}), (5, {})]

    def test_the_master_creates_an_npc(self):
        scores = {"method": "libre", "strength": 18, "dexterity": 12, "constitution": 16,
                  "intelligence": 6, "wisdom": 9, "charisma": 4}
        self.client.force_login(self.carlos)
        response = self.walk(self.client, "npc_creator", self.steps(scores, hp="120"))
        self.assertRedirects(response, self.url("characters"), fetch_redirect_response=False)
        npc = Character.objects.get(name="Guardia de la torre")
        self.assertEqual((npc.is_npc, npc.player, npc.status, npc.hp, npc.temp_hp, npc.strength, npc.charisma),
                         (True, None, Character.Status.APROBADO, 120, 120, 18, 4))
        self.assertTrue(npc.campaigns.filter(pk=self.corona.pk).exists())
        self.assertIsNone(npc.template)
        page = self.client.get(self.url("characters")).content.decode()
        self.assertIn("Guardia de la torre", page)
        self.assertIn(reverse("character_copy", args=[self.corona.pk, npc.pk]), page)  # it can be copied

    def test_players_cannot_create_npcs_nor_use_free_scores(self):
        client = self.player()
        self.assertEqual(client.get(self.url("npc_creator")).status_code, 403)
        self.assertNotContains(client.get(self.url("characters")), self.url("npc_creator"))
        scores = {"method": "libre", "strength": 18, "dexterity": 18, "constitution": 18,
                  "intelligence": 18, "wisdom": 18, "charisma": 18}
        client.post(self.url("creator"), {"paso": 0, "ir": "siguiente", "race": Race.objects.filter(subraces__isnull=True).first().pk})
        response = client.post(self.url("creator"), {"paso": 2, "ir": "siguiente", **scores})
        self.assertEqual(response.status_code, 200)  # refused: «libre» is not a choice for players
        self.assertTrue(response.context["form"].errors)


def rules_last_step():
    from .views_characters import LAST_STEP
    return LAST_STEP


class RouteBetweenSessionsTests(DemoData):
    """The map's slider has two ends: the route from session «desde» to session «hasta»,
    starting where the party was when «desde» began."""

    def md(self, query):
        the_map = Map.objects.filter(campaign=self.corona).order_by("order", "id").first()
        return self.player().get(self.url("map") + f"?mapa={the_map.pk}&{query}").context

    def test_only_the_route_between_two_sessions(self):
        moves = list(PartyMove.objects.filter(map__campaign=self.corona).select_related("session", "location"))
        last = max(m.session.number for m in moves if m.session)
        context = self.md(f"desde={last}&hasta={last}")
        shown = context["md"]["moves"]
        before = [m for m in moves if m.session and m.session.number < last]
        # The last stop before that session, then the stops of that session.
        self.assertEqual(shown[0], before[-1])
        self.assertTrue(all(m.session.number == last for m in shown[1:]))
        self.assertEqual(len(context["md"]["segments"]), len(shown) - 1)
        self.assertEqual((context["since"], context["until"]), (last, last))

    def test_the_whole_route_by_default_and_ends_kept_in_order(self):
        moves = PartyMove.objects.filter(map__campaign=self.corona)
        self.assertEqual(len(self.md("")["md"]["moves"]), moves.count())
        context = self.md("desde=5&hasta=2")
        self.assertEqual((context["since"], context["until"]), (2, 2))
        self.assertIn('name="desde"', self.player().get(self.url("map")).content.decode())


class FoldingTests(DemoData):
    """Adventures that are over are folded two by two; the world menu folds by category."""

    def test_finished_adventures_are_folded(self):
        page = self.player().get(self.url("diary")).content.decode()
        for quest in Quest.objects.filter(campaign=self.corona).exclude(status=Quest.Status.ACTIVA):
            self.assertRegex(page, rf'<details id="aventura-{quest.pk}" class="aventura-plegada[^"]*"')
        for quest in Quest.objects.filter(campaign=self.corona, status=Quest.Status.ACTIVA):
            self.assertIn(f'<section id="aventura-{quest.pk}"', page)
        self.assertIn('class="rejilla-aventuras"', page)

    def test_the_world_menu_opens_the_section_being_read(self):
        page = self.player().get(self.url("world") + "?cat=pnj").content.decode()
        sections = re.findall(r'<details class="seccion-arbol"([^>]*)>', page)
        self.assertGreater(len(sections), 1)
        open_ones = [s for s in sections if re.search(r"\bopen\b", s)]
        self.assertEqual(len(open_ones), 1)
        self.assertIn("mundo-{}-pnj".format(self.corona.pk), open_ones[0])
        # While searching, every section with results opens.
        page = self.player().get(self.url("world") + "?q=a").content.decode()
        sections = re.findall(r'<details class="seccion-arbol"([^>]*)>', page)
        self.assertTrue(sections and all(re.search(r"\bopen\b", s) for s in sections))


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="kiroku-test-media-"))
class FreeNpcTests(DemoData):
    """The NPC creator lets the master choose freely: a new race and class, any level, hit points,
    armor class, a unique weapon and an armor of their own. Players never see those pieces."""

    def post(self, step, **data):
        return self.client.post(self.url("npc_creator"), {"paso": step, "ir": "siguiente", **data})

    def test_an_npc_with_everything_written_by_hand(self):
        self.client.force_login(self.carlos)
        self.post(0, race_name="Inmortal", race_speed=12)
        self.post(1, class_name="Guerrero inmortal", class_magic="ninguna")
        self.post(2, method="libre", strength=24, dexterity=14, constitution=22, intelligence=10, wisdom=12, charisma=8)
        self.post(3, name="Ealoseum", level=19, hp=520, ac=21)
        self.post(4, weapon_name="Martillo del Inquisidor", weapon_damage="2d10 + 1d6", weapon_extra=4,
                  weapon_stat_bonus="str", weapon_hit_bonus=3, weapon_hit_stat="str", weapon_range=2, weapon_shred=3,
                  armor_name="Coraza de cazamagos", armor_type="Pesada", armor_ap=11, armor_magic_ap=2)
        self.post(5)
        self.client.post(self.url("npc_creator"), {"paso": 6, "ir": "enviar"})
        npc = Character.objects.get(name="Ealoseum")
        self.assertEqual((npc.race.name, npc.race.speed, npc.race.bespoke), ("Inmortal", 12, True))
        self.assertEqual((npc.char_class.name, npc.char_class.bespoke), ("Guerrero inmortal", True))
        self.assertEqual((npc.level, npc.hp, npc.temp_hp, npc.ac, npc.strength, npc.constitution, npc.can_level_up),
                         (19, 520, 520, 21, 24, 22, False))
        self.assertEqual((npc.max_ap, npc.max_magic_ap, npc.temp_ap), (11, 2, 11))
        weapon = Weapon.objects.get(item__inventory__character=npc)
        attack = weapon.attacks.get()
        self.assertTrue(weapon.equipped and weapon.item.template.bespoke)
        self.assertEqual((weapon.to_hit, attack.damage_text(npc), attack.range), ("3 + str", "2d10 + 1d6 + 11", 2))
        self.assertEqual([(d.type, d.amount) for d in attack.armor_damages.all()], [("shredding", 3)])
        # Players do not see the master's pieces in their creator.
        page = self.player().get(self.url("creator") + "?paso=4").content.decode()
        self.assertNotIn("Martillo del Inquisidor", page)
        self.assertNotIn("Inmortal", self.player().get(self.url("creator")).content.decode())
        # Nor in the rules catalog; masters do see them there.
        self.assertNotContains(self.player().get(reverse("race_list")), "Inmortal")
        self.assertNotContains(self.player().get(reverse("item_list")), "Martillo del Inquisidor")
        template = weapon.item.template
        self.assertEqual(self.player().get(reverse("item_detail", args=[template.pk])).status_code, 404)
        self.client.force_login(self.carlos)
        self.assertContains(self.client.get(reverse("race_list")), "Inmortal")

    def test_wrong_dice_are_refused(self):
        self.client.force_login(self.carlos)
        response = self.post(4, weapon_name="Hacha", weapon_damage="dos dados")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "no son dados")

    def test_jumping_to_another_step_keeps_the_portrait(self):
        self.client.force_login(self.carlos)
        url = self.url("npc_creator")
        # A step link of the list on top: what is written is saved, the image too.
        self.client.post(url, {"paso": 3, "ir": "paso-6", "name": "Kaledric", "portrait": png_upload("kaledric.png")})
        draft = self.client.session[f"creador-pnj:{self.corona.pk}"]
        self.assertEqual(draft["name"], "Kaledric")
        self.assertTrue(draft["portrait"].startswith("retratos/kaledric"))
        # With another field wrong, the image is kept anyway (the browser empties the file box).
        self.client.post(url, {"paso": 3, "ir": "siguiente", "name": "", "portrait": png_upload("otra.png")})
        self.assertTrue(self.client.session[f"creador-pnj:{self.corona.pk}"]["portrait"].startswith("retratos/otra"))


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="kiroku-test-media-"))
class MasterEditTests(DemoData):
    """The master changes anything on a sheet, with or without the rules."""

    def edit(self, character, **changes):
        self.client.force_login(self.carlos)
        url = self.url("character_edit", character.pk)
        context = self.client.get(url).context
        form = context["form"]
        data = {name: ("" if value is None else value) for name, value in form.initial.items()
                if name in form.fields and name != "portrait"}
        if context["worn_form"] is not None:  # what the browser sends already filled in
            data.update(context["worn_form"].initial)
        data = {k: (v.pk if hasattr(v, "pk") else v) for k, v in data.items()}
        data.update(changes)
        data = {k: v for k, v in data.items() if v is not False}
        return self.client.post(url, data)

    def test_anything_beyond_the_rules(self):
        aria = Character.objects.get(name="Aria")
        response = self.edit(aria, level=17, hp=300, temp_hp=250, strength=28, ac=25, new_race="Owling",
                             new_race_speed=12, weapon_name="Bastón de Sócrates", weapon_damage="3d8",
                             armor_name="Túnica de la academia", armor_type="Ligera", armor_ap=5, armor_magic_ap=6)
        self.assertRedirects(response, self.url("sheet", aria.pk), fetch_redirect_response=False)
        aria.refresh_from_db()
        self.assertEqual((aria.level, aria.hp, aria.temp_hp, aria.strength, aria.ac, aria.race.name, aria.subrace),
                         (17, 300, 250, 28, 25, "Owling", None))
        self.assertEqual((aria.max_ap, aria.max_magic_ap), (5, 6))
        self.assertTrue(Weapon.objects.filter(item__inventory__character=aria, item__name="Bastón de Sócrates").exists())

    def test_the_armor_worn_changes_its_ap(self):
        brom = Character.objects.get(name="Brom")
        self.assertIsNotNone(brom.equipped_armor)
        self.edit(brom, worn_type="Pesada", worn_ap=12, worn_magic_ap=1)
        self.assertEqual((brom.max_ap, brom.max_magic_ap), (12, 1))

    def test_a_pair_that_acts_at_once_goes_both_ways(self):
        first, second = Character.objects.filter(campaigns=self.corona, is_npc=True, template__isnull=True)[:2]
        self.edit(first, acts_with=second.pk)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.acts_with, second.acts_with), (second, first))
        self.assertContains(self.client.get(self.url("sheet", second.pk)), "Actúa a la vez con")
        # Alone again: the partner stops acting with it too.
        self.edit(first, acts_with="")
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.acts_with, second.acts_with), (None, None))
        # Player characters have no partner to choose.
        self.client.force_login(self.carlos)
        aria = Character.objects.get(name="Aria")
        self.assertNotIn("acts_with", self.client.get(self.url("character_edit", aria.pk)).context["form"].fields)

    def test_data_that_would_break_is_refused(self):
        aria = Character.objects.get(name="Aria")
        other = Subrace.objects.exclude(race=aria.race).first()
        response = self.edit(aria, subrace=other.pk)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "es un linaje de")

    def test_only_masters(self):
        aria = Character.objects.get(name="Aria")
        self.assertEqual(self.player().get(self.url("character_edit", aria.pk)).status_code, 403)
        self.client.force_login(self.carlos)
        self.assertContains(self.client.get(self.url("sheet", aria.pk)), self.url("character_edit", aria.pk))

    def test_the_paper_sheet_shows_the_portrait(self):
        aria = Character.objects.get(name="Aria")
        aria.portrait = png_upload("aria.png")
        aria.save()
        self.client.force_login(self.lucia)
        self.client.post(self.url("sheet_view", aria.pk), {"vista": "ficha"})
        self.assertContains(self.client.get(self.url("sheet", aria.pk)), 'class="avatar grande hoja-retrato"')
        self.assertContains(self.client.get(self.url("sheet", aria.pk)), aria.portrait.url)


class WorldBranchesTests(DemoData):
    """Pages inside other pages fold in the world menu; the branch being read opens."""

    def test_branches_fold_and_the_one_being_read_opens(self):
        child = next(p for p in Page.objects.filter(campaign=self.corona, revealed=True, parent__revealed=True)
                     if p.category == p.parent.category)
        parent = child.parent
        page = self.player().get(self.url("world_page", child.slug)).content.decode()
        branch = re.search(r'<details class="rama"([^>]*)>\s*<summary>(.*?)</summary>', page, re.S)
        self.assertIsNotNone(branch)
        opened = re.findall(r'<details class="rama" open data-aqui[^>]*data-recordar="mundo-\d+-(\d+)"', page)
        self.assertIn(str(parent.pk), opened)


class WorldEditorTests(DemoData):
    """The page editor: a link can show words of its own ([[Page|words]]), the link finder gets every page
    and sheet but the page itself, visibility is saved with the page (its map place follows), a category's
    cards start a new page or edit one, and a page can be deleted, leaving what was inside it loose."""

    def page(self, title):
        return Page.objects.get(campaign=self.corona, title=title)

    def test_a_link_can_show_words_of_its_own(self):
        from .wiki import WikiResolver, mentions, plain
        port = self.page("Puerto Gris")
        text = "Volvieron a [[Puerto Gris|la ciudad]], soñando con [[Santuario hundido|el templo del mar]] y [[Faro|el faro]]."
        master = WikiResolver(self.corona, self.carlos, True).inline(text)
        self.assertIn(self.url("world_page", port.slug), master)
        self.assertIn(">la ciudad</a>", master)
        self.assertIn(">el templo del mar</a>", master)
        self.assertIn("?titulo=Faro", master)  # a page to create, under the name it links to
        self.assertIn(">el faro</a>", master)
        player = WikiResolver(self.corona, self.lucia, False).inline(text)
        self.assertIn(">la ciudad</a>", player)
        self.assertNotIn("Santuario", player)  # a hidden page: its words, no link and no name
        self.assertIn("el templo del mar", player)
        self.assertEqual(plain(text), "Volvieron a la ciudad, soñando con el templo del mar y el faro.")
        self.assertTrue(mentions(text, "puerto gris"))
        self.assertFalse(mentions(text, "la ciudad"))
        # «Aparece en» finds it too.
        Page.objects.filter(pk=self.page("Robleda").pk).update(body="Vende su madera en [[Puerto Gris|la capital]].")
        refs = self.player().get(self.url("world_page", port.slug)).context["refs"]
        self.assertIn("Robleda", [ref["label"] for ref in refs])

    def test_the_editor_offers_what_can_be_linked(self):
        self.assertEqual(self.player().get(self.url("world_new")).status_code, 403)
        self.client.force_login(self.carlos)
        response = self.client.get(self.url("world_edit", self.page("Robleda").slug))
        found = {item["t"]: item for item in response.context["link_index"]}
        self.assertNotIn("Robleda", found)  # not itself
        self.assertEqual((found["Santuario hundido"]["c"], found["Santuario hundido"]["h"]), ("lugar", True))
        party = [c.name for c in self.corona.party]
        self.assertTrue(party)
        self.assertTrue(all(found[name]["c"] == "pj" for name in party))
        self.assertContains(response, 'id="indice-enlaces"')
        self.assertContains(response, "data-enlazar ", count=2)  # the body and the secret
        self.assertEqual([c["radio"].data["value"] for c in response.context["category_radios"]],
                         ["lugar", "pnj", "faccion", "objeto", "historia"])

    def test_visibility_is_saved_with_the_page_and_its_map_place_follows(self):
        sanctuary = self.page("Santuario hundido")
        self.client.force_login(self.carlos)
        fields = {"title": sanctuary.title, "category": "lugar", "parent": sanctuary.parent_id,
                  "location": sanctuary.location_id, "body": sanctuary.body, "secret": sanctuary.secret}
        response = self.client.post(self.url("world_edit", sanctuary.slug), {**fields, "revealed": "on"}, follow=True)
        self.assertContains(response, "Cambios guardados.")
        sanctuary.refresh_from_db()
        self.assertTrue(sanctuary.revealed)
        self.assertEqual(sanctuary.location.status, MapLocation.Status.CONOCIDO)
        self.client.post(self.url("world_edit", sanctuary.slug), fields)  # hidden again
        sanctuary.refresh_from_db()
        self.assertFalse(sanctuary.revealed)
        self.assertEqual(sanctuary.location.status, MapLocation.Status.OCULTO)

    def test_a_new_page_starts_from_its_category(self):
        crown = self.page("La Corona de Ceniza")
        self.client.force_login(self.carlos)
        cards = self.client.get(self.url("world") + "?cat=objeto")
        self.assertContains(cards, self.url("world_new") + "?cat=objeto")
        self.assertContains(cards, self.url("world_edit", crown.slug))
        self.assertEqual(self.client.get(self.url("world_new") + "?cat=objeto").context["form"]["category"].value(), "objeto")
        response = self.client.post(self.url("world_new"), {
            "title": "Llave de hueso", "category": "objeto",
            "body": "Abre la primera puerta de [[Ruinas de Vel-Ithar|las ruinas]]."}, follow=True)
        key = self.page("Llave de hueso")
        self.assertRedirects(response, self.url("world_page", key.slug))
        self.assertContains(response, "Página creada.")
        self.assertContains(response, ">las ruinas</a>")
        self.assertFalse(key.revealed)  # a new page starts hidden from the group
        cards = self.player().get(self.url("world") + "?cat=objeto")
        self.assertNotContains(cards, self.url("world_new"))
        self.assertNotContains(cards, self.url("world_edit", crown.slug))

    def test_deleting_a_page_leaves_what_was_inside_it_loose(self):
        from .wiki import WikiResolver
        sanctuary, ithra = self.page("Santuario hundido"), self.page("Ithra")
        place = sanctuary.location_id
        self.assertEqual(self.player().post(self.url("world_delete", sanctuary.slug)).status_code, 403)
        self.client.force_login(self.carlos)
        shown = self.client.get(self.url("world_page", sanctuary.slug))
        deletion = shown.context["deletion"]
        self.assertEqual((deletion["children"], deletion["on_map"]), (1, True))
        self.assertGreaterEqual(deletion["linked"], 2)  # the ruins' secret and Ithra
        self.assertContains(shown, "Las páginas que tiene dentro (1) quedarán sueltas.")
        self.assertContains(self.client.get(self.url("world_edit", sanctuary.slug)), 'form="borrar-pagina"')
        response = self.client.post(self.url("world_delete", sanctuary.slug), follow=True)
        self.assertRedirects(response, self.url("world") + "?cat=lugar")
        self.assertContains(response, "Página «Santuario hundido» borrada.")
        self.assertFalse(Page.objects.filter(pk=sanctuary.pk).exists())
        ithra.refresh_from_db()
        self.assertIsNone(ithra.parent)
        self.assertTrue(MapLocation.objects.filter(pk=place).exists())
        # Its links keep their words: for the master, a page to create again.
        self.assertIn("?titulo=Santuario+hundido", WikiResolver(self.corona, self.carlos, True).inline(ithra.body))


class MissionTests(DemoData):
    """An adventure is an arc that tells what it is about and holds its missions, folded inside."""

    def setUp(self):
        self.arc = Quest.objects.get(campaign=self.corona, title="La Corona de Ceniza")
        self.mission = Quest.objects.get(campaign=self.corona, title="Saqueadores en el Camino del Faro")
        self.mission.parent = self.arc
        self.mission.save()

    def test_missions_are_told_inside_their_adventure(self):
        page = self.player().get(self.url("diary")).content.decode()
        start = page.index(f'id="aventura-{self.arc.pk}"')
        end = page.index("</section>", start)
        self.assertIn(f'<details id="aventura-{self.mission.pk}" class="mision', page[start:end])
        # Not on its own any more, nor as a bar of the timeline.
        self.assertEqual(page.count(f'id="aventura-{self.mission.pk}"'), 1)
        timeline = self.player().get(self.url("diary_timeline")).content.decode()
        self.assertNotIn(f"#aventura-{self.mission.pk}", timeline)

    def test_a_hidden_adventure_hides_its_missions(self):
        Quest.objects.filter(pk=self.arc.pk).update(hidden=True)
        self.assertNotContains(self.player().get(self.url("diary")), self.mission.title)

    def test_only_one_level(self):
        other = Quest.objects.get(campaign=self.corona, title="Una deuda en los muelles")
        other.parent = self.mission
        with self.assertRaises(ValidationError):
            other.full_clean()
        self.arc.parent = other
        with self.assertRaises(ValidationError):
            self.arc.full_clean()


def as_browser(*forms):
    """What a browser sends for these forms (and formsets) as the page shows them, untouched."""
    data = {}
    for form in forms:
        if form is None:
            continue
        subforms = [form]
        if hasattr(form, "management_form"):
            management = form.management_form
            data.update({management.add_prefix(name): value for name, value in management.initial.items()})
            subforms = form.forms
        for sub in subforms:
            for name in sub.fields:
                value = sub[name].value()
                if value is None or value is False or value == "":  # 0 is a value («XP repartida: 0»)
                    continue
                if value is True:
                    value = "on"
                elif isinstance(value, (list, tuple)) or hasattr(value, "all"):
                    value = [getattr(v, "pk", v) for v in value]
                else:
                    value = getattr(value, "pk", value)
                data[sub.add_prefix(name)] = value
    return data


class CampaignSetupTests(DemoData):
    """A campaign made from the web: the first tab creates it, the others fill it little by little."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.unai = User.objects.get(username="unai")

    def create(self):
        self.client.force_login(self.unai)
        response = self.client.post(reverse("campaign_new"), {
            "name": "Las Islas Rotas", "description": "Un archipiélago tras el fin del mundo.", "start_date": "2026-11-23",
            "ruleset": "aegis", "status": "activa"})
        campaign = Campaign.objects.get(name="Las Islas Rotas")
        self.assertRedirects(response, reverse("campaign_setup", args=[campaign.pk]) + "?paso=1", fetch_redirect_response=False)
        return campaign

    def tab(self, campaign, step, ir="siguiente", **changes):
        """Open a tab, change some fields and press a button, as in the browser."""
        url = reverse("campaign_setup", args=[campaign.pk])
        context = self.client.get(url + f"?paso={step}").context
        data = as_browser(*(context.get(name) for name in ("form", "page_form", "formset")))
        data.update({"paso": step, "ir": ir})
        data.update(changes)
        return self.client.post(url, data)

    def test_anonymous_users_log_in_first(self):
        response = self.client.get(reverse("campaign_new"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_create_it_and_fill_it_tab_by_tab(self):
        campaign = self.create()
        self.assertTrue(campaign.is_master(self.unai))
        self.assertEqual((campaign.ruleset, campaign.status), ("aegis", "activa"))
        # La mesa
        self.tab(campaign, 1, masters=[self.unai.pk], players=[self.lucia.pk], notice="Traed dados.")
        campaign.refresh_from_db()
        self.assertEqual((list(campaign.players.all()), campaign.notice), ([self.lucia], "Traed dados."))
        # El mundo
        self.tab(campaign, 2, world_year="año 12 tras la Caída", **{"mundo-title": "El Archipiélago",
                 "mundo-body": "Islas y mar.", "mundo-secret": "El mar está vivo."})
        root = Page.objects.get(campaign=campaign, title="El Archipiélago")
        self.assertEqual((root.category, root.revealed, root.secret), ("lugar", True, "El mar está vivo."))
        # Lugares: one written, two left empty; inside the world by default and hidden until revealed.
        self.tab(campaign, 4, **{"paginas-0-title": "Puerto Sal", "paginas-0-body": "El único puerto libre.",
                                 "paginas-0-secret": "Lo controla el culto."})
        place = Page.objects.get(campaign=campaign, category="lugar", title="Puerto Sal")
        self.assertEqual((place.parent, place.revealed), (root, False))
        self.assertEqual(Page.objects.filter(campaign=campaign, category="lugar").count(), 2)
        # Facciones and PNJ, inside the place.
        self.tab(campaign, 5, **{"paginas-0-title": "El culto de la marea", "paginas-0-parent": place.pk,
                                 "paginas-0-revealed": "on"})
        self.tab(campaign, 6, **{"paginas-0-title": "La capitana Ivo", "paginas-0-secret": "Es del culto."})
        self.assertTrue(Page.objects.filter(campaign=campaign, category="faccion", revealed=True, parent=place).exists())
        self.assertTrue(Page.objects.filter(campaign=campaign, category="pnj", title="La capitana Ivo").exists())
        # Objetos e historias: each entry chooses which.
        self.tab(campaign, 7, **{"paginas-0-title": "El ancla de hueso", "paginas-0-category": "objeto",
                                 "paginas-1-title": "La Caída", "paginas-1-category": "historia"})
        self.assertEqual(set(Page.objects.filter(campaign=campaign, category__in=["objeto", "historia"])
                             .values_list("title", "category")), {("El ancla de hueso", "objeto"), ("La Caída", "historia")})
        # Tramas: an adventure with its objectives, then a mission inside it.
        self.tab(campaign, 8, ir="mas", **{"tramas-0-title": "La marea negra", "tramas-0-description": "El mar sube.",
                                           "tramas-0-objectives": "Llegar a Puerto Sal\nHablar con Ivo",
                                           "tramas-0-hidden_objectives": "Descubrir el culto"})
        adventure = Quest.objects.get(campaign=campaign, title="La marea negra")
        self.assertEqual([(o.text, o.hidden) for o in adventure.objectives.all()],
                         [("Llegar a Puerto Sal", False), ("Hablar con Ivo", False), ("Descubrir el culto", True)])
        self.tab(campaign, 8, **{"tramas-0-title": "El faro apagado", "tramas-0-parent": adventure.pk})
        self.assertEqual(adventure.missions.get().title, "El faro apagado")
        # Primera sesión
        self.tab(campaign, 9, **{"sesion-title": "Naufragio", "sesion-body": "Llegan a la costa.", "sesion-published": "on"})
        self.assertEqual(campaign.sessions.get().number, 1)
        summary = self.client.get(reverse("campaign_setup", args=[campaign.pk]) + "?paso=10").content.decode()
        self.assertIn("1 aventura(s), 1 misión(es)", summary)
        self.assertIn("El Archipiélago", summary)
        # The player sees the campaign, but not the assistant.
        self.client.force_login(self.lucia)
        self.assertEqual(self.client.get(reverse("home", args=[campaign.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("campaign_setup", args=[campaign.pk])).status_code, 403)

    def test_an_empty_tab_saves_nothing_and_errors_keep_what_was_written(self):
        campaign = self.create()
        response = self.tab(campaign, 5)
        self.assertRedirects(response, reverse("campaign_setup", args=[campaign.pk]) + "?paso=6", fetch_redirect_response=False)
        self.assertFalse(Page.objects.filter(campaign=campaign).exists())
        # A faction without a name: the tab comes back with the text and the error.
        response = self.tab(campaign, 5, **{"paginas-0-body": "Sin nombre todavía."})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sin nombre todavía.")
        self.assertFalse(Page.objects.filter(campaign=campaign).exists())

    def test_the_master_stays_and_nobody_is_master_and_player(self):
        campaign = self.create()
        response = self.tab(campaign, 1, masters=[self.carlos.pk])
        self.assertContains(response, "Tú tienes que seguir siendo máster")
        response = self.tab(campaign, 1, masters=[self.unai.pk], players=[self.unai.pk])
        self.assertContains(response, "no puede ser máster y jugador a la vez")
        self.assertTrue(campaign.is_master(self.unai))

    def test_other_campaigns_are_not_reachable(self):
        self.client.force_login(self.unai)
        other = Campaign.objects.exclude(masters=self.unai).exclude(players=self.unai).first()
        self.assertEqual(self.client.get(reverse("campaign_setup", args=[other.pk])).status_code, 404)


class RumourTests(DemoData):
    """«Rumores»: the master writes them in categories with a colour; players see the
    published ones, never their truth, nor who said them if that page is hidden."""

    def rumour(self, text):
        return Rumour.objects.get(campaign=self.corona, text__startswith=text)

    def test_players_see_the_published_ones_without_their_truth(self):
        page = self.player().get(self.url("rumours")).content.decode()
        self.assertIn("Dicen que Lady Morwen paga en oro antiguo", page)
        self.assertNotIn("otra mitad de la corona", page)  # hidden
        self.assertNotIn("Verdad a medias", page)  # the truth is only for masters
        self.assertIn("Comunicado oficial", page)
        self.client.force_login(self.carlos)
        page = self.client.get(self.url("rumours")).content.decode()
        self.assertIn("otra mitad de la corona", page)
        self.assertIn("Verdad a medias", page)

    def test_the_category_filter(self):
        official = RumourCategory.objects.get(campaign=self.corona, name="Comunicado oficial")
        html = self.player().get(self.url("rumours") + f"?cat={official.pk}", **AJAX).json()["html"]
        self.assertIn("Puerto Gris por el Camino del Faro", html)
        self.assertNotIn("Lady Morwen paga", html)

    def test_who_said_it_is_not_linked_if_that_page_is_hidden(self):
        ithra = Page.objects.get(campaign=self.corona, title="Ithra")
        rumour = self.rumour("El santuario hundido")
        rumour.published = True
        rumour.save()
        page = self.player().get(self.url("rumours")).content.decode()
        self.assertIn("El santuario hundido guarda", page)
        self.assertNotIn(reverse("world_page", args=[self.corona.pk, ithra.slug]), page)

    def test_the_master_writes_publishes_and_deletes(self):
        rumour_category = RumourCategory.objects.get(campaign=self.corona, name="Noticia")
        self.client.force_login(self.carlos)
        self.client.post(self.url("rumours_new"), {"category": rumour_category.pk, "text": "El faro lleva tres noches apagado.",
                                                   "source": "Un pescador", "truth": "verdad"})
        rumour = self.rumour("El faro lleva")
        self.assertFalse(rumour.published)
        self.assertNotContains(self.player().get(self.url("rumours")), "El faro lleva")
        self.client.force_login(self.carlos)
        self.client.post(self.url("rumours_action", rumour.pk), {"accion": "publicar"}, **AJAX)
        self.assertContains(self.player().get(self.url("rumours")), "El faro lleva")
        self.client.force_login(self.carlos)
        self.client.post(self.url("rumours_action", rumour.pk), {"accion": "borrar"}, **AJAX)
        self.assertFalse(Rumour.objects.filter(pk=rumour.pk).exists())

    def test_only_masters_manage_rumours(self):
        rumour = self.rumour("Dicen que Lady Morwen")
        client = self.player()
        self.assertEqual(client.post(self.url("rumours_action", rumour.pk), {"accion": "borrar"}, **AJAX).status_code, 403)
        self.assertEqual(client.get(self.url("rumours_new")).status_code, 403)
        self.assertEqual(client.post(self.url("rumours_category"), {"accion": "crear", "nueva-name": "X", "nueva-color": "#000000"}).status_code, 403)

    def test_the_master_defines_the_categories(self):
        self.client.force_login(self.carlos)
        url = self.url("rumours_category")
        self.client.post(url, {"accion": "crear", "nueva-name": "Bando del rey", "nueva-color": "#B22222"}, **AJAX)
        royal = RumourCategory.objects.get(campaign=self.corona, name="Bando del rey")
        self.assertEqual(royal.color, "#B22222")
        response = self.client.post(url, {"accion": "crear", "nueva-name": "bando del rey", "nueva-color": "#000000"}, **AJAX)
        self.assertTrue(response.json()["error"])  # the same name
        self.client.post(url, {"accion": f"guardar:{royal.pk}", f"cat{royal.pk}-name": "Edicto real", f"cat{royal.pk}-color": "#DAA520"}, **AJAX)
        royal.refresh_from_db()
        self.assertEqual((royal.name, royal.color), ("Edicto real", "#DAA520"))
        legend = RumourCategory.objects.get(campaign=self.corona, name="Leyenda")
        self.client.post(url, {"accion": f"borrar:{legend.pk}"}, **AJAX)
        self.assertIsNone(self.rumour("En el Bosque de Ceniza").category)  # the rumour stays

    def test_a_new_campaign_gets_the_default_categories(self):
        campaign = Campaign.objects.create(name="Nueva", start_date=datetime.date(2026, 1, 1))
        self.assertEqual(list(campaign.rumour_categories.values_list("name", flat=True)),
                         ["Rumor", "Comunicado oficial", "Noticia", "Leyenda"])


def fake_open_meteo(day_and_night=True):
    """Stands in for Open-Meteo: one answer per place, day and night alternating."""
    calls = []

    def answer(url, params):
        calls.append(params)
        if "geocoding" in url:
            return {"results": [
                {"name": "Bilbao", "admin1": "País Vasco", "country": "España", "latitude": 43.26, "longitude": -2.93},
                {"name": "Bilbao", "admin1": "Madrid", "country": "España", "latitude": 40.43, "longitude": -3.70},
                {"name": "Bilbao", "admin1": "Tolima", "country": "Colombia", "latitude": 4.15, "longitude": -74.9},
            ]}
        count = len(params["latitude"].split(","))
        return [{"current": {"time": "2026-11-24T15:00", "temperature_2m": 14.6 + i, "is_day": (i + 1) % 2 if day_and_night else 1,
                             "weather_code": 61, "wind_speed_10m": 31.0, "precipitation": 1.2}} for i in range(count)]
    return answer, calls


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class WeatherTests(DemoData):
    """The map's weather layer: live weather of real places (weather.py, mocked here)."""

    def setUp(self):
        cache.clear()
        self.valcendra = self.corona.maps.get(name="Valcendra")

    def layer(self, user):
        self.client.force_login(user)
        return self.client.get(self.url("map_weather", self.valcendra.pk), **AJAX).json()

    def test_one_request_for_the_map_then_the_cache(self):
        answer, calls = fake_open_meteo()
        with mock.patch("CampaignManager.weather._get_json", side_effect=answer):
            data = self.layer(self.carlos)
            self.layer(self.carlos)
        self.assertEqual(len(calls), 1)  # four regions, one request; the second page uses the stored values
        self.assertEqual(len(calls[0]["latitude"].split(",")), 4)
        self.assertIn("tiempo-insignia", data["layer"])
        self.assertIn("15°", data["layer"])
        self.assertIn("💨31", data["layer"])  # windy: shown
        self.assertIn("💧1.2", data["layer"])  # raining: shown

    def test_day_and_night_follow_each_real_place(self):
        answer, _ = fake_open_meteo()
        with mock.patch("CampaignManager.weather._get_json", side_effect=answer):
            data = self.layer(self.carlos)
        self.assertIn('class="region-tiempo noche"', data["layer"])
        self.assertIn('class="region-tiempo"', data["layer"])
        self.assertIn("🌙", data["layer"])
        self.assertIn("☀️", data["layer"])
        self.assertIn("de noche", data["caption"])
        self.assertIn("de día", data["caption"])

    def test_one_clock_the_campaigns(self):
        self.corona.time_zone = "Asia/Tokyo"
        self.corona.save()
        answer, _ = fake_open_meteo()
        with mock.patch("CampaignManager.weather._get_json", side_effect=answer):
            caption = self.layer(self.lucia)["caption"]
        tokyo = timezone.localtime(timezone.now(), timezone.get_fixed_timezone(9 * 60)).strftime("%H:%M")
        self.assertIn("Asia/Tokyo", caption)
        self.assertIn(tokyo, caption)

    def test_api_down_shows_the_last_values_or_nothing(self):
        broken = mock.patch("CampaignManager.weather._get_json", side_effect=weather.WeatherUnavailable("down"))
        with broken:
            data = self.layer(self.carlos)  # never fetched: nothing to show, but the page works
        self.assertNotIn("tiempo-insignia", data["layer"])
        self.assertIn("no responde", data["caption"])
        cache.clear()
        answer, _ = fake_open_meteo()
        with mock.patch("CampaignManager.weather._get_json", side_effect=answer):
            self.layer(self.carlos)
        WeatherRegion.objects.update(weather_at=timezone.now() - datetime.timedelta(hours=2))
        with broken as down:
            data = self.layer(self.carlos)
            self.layer(self.carlos)
        self.assertEqual(down.call_count, 1)  # after failing, it waits before calling again
        self.assertIn("tiempo-insignia", data["layer"])  # the last values
        self.assertIn("datos de hace 120 min", data["caption"])

    def test_players_see_the_layer_but_not_hidden_regions_nor_real_places(self):
        answer, _ = fake_open_meteo()
        with mock.patch("CampaignManager.weather._get_json", side_effect=answer):
            data = self.layer(self.lucia)
        self.assertIn("Costa de Puerto Gris", data["caption"])
        self.assertNotIn("Santuario hundido", data["caption"] + data["layer"])  # its location is hidden
        self.assertNotIn("Bilbao", data["caption"])  # the real place is the master's business
        self.assertEqual(data["layer"].count("<ellipse") + data["layer"].count("<polygon"), 3)

    def test_only_the_master_links_and_unlinks(self):
        add, region = self.url("map_weather_add", self.valcendra.pk), WeatherRegion.objects.first()
        client = self.player()
        self.assertEqual(client.post(add, {"name": "X", "place": "43, -2", "location": ""}).status_code, 403)
        self.assertEqual(client.post(self.url("map_weather_delete", region.pk)).status_code, 403)
        self.assertEqual(client.get(self.url("map_weather_search") + "?q=Bilbao").status_code, 403)
        self.assertEqual(WeatherRegion.objects.count(), 4)

    def test_the_master_links_a_location_or_a_drawn_region(self):
        add = self.url("map_weather_add", self.valcendra.pk)
        robleda = self.valcendra.locations.get(name="Robleda")
        answer, _ = fake_open_meteo()
        self.client.force_login(self.carlos)
        with mock.patch("CampaignManager.weather._get_json", side_effect=answer):
            # A location, with the place written as «City, Country»: the country narrows the search.
            self.client.post(add, {"name": "Robleda", "location": robleda.pk, "place": "Bilbao, Colombia"}, **AJAX)
            # A drawn region, with coordinates.
            self.client.post(add, {"name": "Las Marismas", "location": "", "points": "10,80 30,80 20,95", "place": "52.37, 4.89"}, **AJAX)
            # Neither a location nor 3 points.
            response = self.client.post(add, {"name": "Nada", "location": "", "points": "10,10 20,20", "place": "1, 1"}, **AJAX)
            found = self.client.get(self.url("map_weather_search") + "?q=Bilbao, Madrid").json()["results"]
        self.assertTrue(response.json()["error"])
        linked = WeatherRegion.objects.get(name="Robleda")
        self.assertEqual((linked.location, linked.place_name, linked.latitude), (robleda, "Bilbao, Tolima, Colombia", 4.15))
        drawn = WeatherRegion.objects.get(name="Las Marismas")
        self.assertEqual((drawn.points, drawn.latitude, drawn.longitude), ([[10, 80], [30, 80], [20, 95]], 52.37, 4.89))
        self.assertEqual([p["name"] for p in found], ["Bilbao, Madrid, España"])
        self.client.post(self.url("map_weather_delete", drawn.pk), **AJAX)
        self.assertFalse(WeatherRegion.objects.filter(pk=drawn.pk).exists())



class MapNamesTests(DemoData):
    """The map can hide the places' names (to see the weather zones): a «Nombres» switch, and
    each pin keeps its name on hover."""

    def test_the_switch_and_the_names_on_hover(self):
        the_map = Map.objects.filter(campaign=self.corona).exclude(image="").first()
        page = self.player().get(self.url("map") + f"?mapa={the_map.pk}").content.decode()
        self.assertIn("data-nombres", page)
        location = the_map.locations.exclude(status="oculto").first()
        self.assertIn(f'title="{location.name}"', page)


class MapImportanceTests(DemoData):
    """Places are main (capitals, key places) or secondary. The map can hide the secondary ones,
    or all (mesa.js, «Lugares»); with the weather layer on, it does not draw the party's route."""

    def setUp(self):
        self.valcendra = Map.objects.get(campaign=self.corona, name="Valcendra")
        self.minor = self.valcendra.locations.get(name="Paso del Cuervo")
        self.main = self.valcendra.locations.get(name="Puerto Gris")

    def map_page(self, client, query=""):
        return client.get(self.url("map") + f"?mapa={self.valcendra.pk}{query}").content.decode()

    def test_pins_carry_their_importance_and_the_main_ones_are_drawn_on_top(self):
        self.assertEqual(self.minor.importance, MapLocation.Importance.SECUNDARIO)
        self.assertEqual(self.main.importance, MapLocation.Importance.PRINCIPAL)
        page = self.map_page(self.player())
        self.assertIn('class="pin conocido lugar-secundario"', page)
        self.assertIn('class="pin visitado lugar-principal"', page)
        self.assertIn("data-lugares", page)  # the «Lugares» switch
        # Secondary pins come first in the page, so the main ones overlap them.
        self.assertLess(page.index('pin conocido lugar-secundario'), page.index('pin visitado lugar-principal'))
        # The list beside the map: the secondary places, in a part that folds.
        folded = page[page.index("data-secundarios"):]
        self.assertIn(self.minor.name, folded)
        self.assertNotIn(f">{self.main.name}<", folded)

    def test_the_home_page_map_has_the_switch_too(self):
        page = self.player().get(self.url("home")).content.decode()
        self.assertIn("data-lugares", page)
        self.assertIn("lugar-secundario", page)

    def test_the_master_changes_a_places_importance(self):
        self.client.force_login(self.carlos)
        page = self.map_page(self.client, f"&lugar={self.minor.pk}")
        self.assertIn('name="valor" value="principal"', page)
        response = self.client.post(self.url("map_location_action", self.minor.pk),
                                    {"accion": "importancia", "valor": "principal"}, **AJAX)
        self.assertIn("principal", response.json()["msg"])
        self.minor.refresh_from_db()
        self.assertEqual(self.minor.importance, MapLocation.Importance.PRINCIPAL)
        bad = self.client.post(self.url("map_location_action", self.minor.pk), {"accion": "importancia", "valor": "capital"}, **AJAX)
        self.assertEqual(bad.status_code, 404)

    def test_players_cannot_change_it(self):
        response = self.player().post(self.url("map_location_action", self.main.pk),
                                      {"accion": "importancia", "valor": "secundario"}, **AJAX)
        self.assertEqual(response.status_code, 403)
        self.main.refresh_from_db()
        self.assertEqual(self.main.importance, MapLocation.Importance.PRINCIPAL)

    def test_a_new_place_can_be_secondary(self):
        self.client.force_login(self.carlos)
        self.client.post(self.url("map_location_add", self.valcendra.pk),
                         {"name": "Molino viejo", "x": "40", "y": "40", "status": "conocido", "importance": "secundario"}, **AJAX)
        self.assertEqual(self.valcendra.locations.get(name="Molino viejo").importance, MapLocation.Importance.SECUNDARIO)

    def test_the_weather_view_hides_the_route(self):
        self.client.force_login(self.carlos)
        plain = self.map_page(self.client)
        self.assertNotIn("con-tiempo", plain)
        self.assertIn("<polyline", plain)
        with mock.patch("CampaignManager.weather.refresh", return_value=True):
            page = self.map_page(self.client, "&tiempo=1")
        self.assertIn('id="mapa-seccion" class="fila con-tiempo"', page)
        # The slider and «Editar el camino» are marked to hide with it (mesa.css)...
        self.assertEqual(page.count("ruta-controles"), 2)
        # ...but not while the master is shaping the route.
        with mock.patch("CampaignManager.weather.refresh", return_value=True):
            editing = self.map_page(self.client, "&tiempo=1&recorrido=1")
        self.assertNotIn("ruta-controles", editing)


class ArcaneWarriorTests(DemoData):
    """Aegis: the Arcane Warrior is the Explorador with combat magic instead of spells: magic
    points («a») 10 + 2 per level, spent on the add-ons of its class (Leah's sheet)."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.warrior = CharacterClass.objects.get(name="Arcane Warrior")
        cls.explorer = CharacterClass.objects.get(name="Explorador")
        # Leah at level 9: Dexterity 17 (+3), so 28 «a» and «salvación a superar» 15.
        cls.leah = Character.objects.create(
            name="Leah", race=Race.objects.get(name="Humano"), char_class=cls.warrior, player=cls.lucia, level=9, xp=48000,
            hp=63, temp_hp=63, ac=15, strength=12, dexterity=17, constitution=18, intelligence=10, wisdom=10, charisma=14,
            magic_points=28)
        cls.leah.campaigns.add(cls.corona)

    def addon(self, name):
        return ClassAddon.objects.get(char_class=self.warrior, name=name)

    def use(self, addon, client=None, **data):
        client = client or self.player()
        return client.post(self.url("sheet_action", self.leah.pk), {"accion": "anadido", "anadido": addon.pk, **data}, **AJAX)

    def test_it_is_the_explorador_without_spellcasting(self):
        def links(char_class):
            return {(row.ability.name, row.level_required) for row in char_class.classability_set.select_related("ability")}
        self.assertEqual(links(self.warrior), links(self.explorer) - {("Lanzamiento de conjuros", 1)} | {("Magia de combate", 1)})
        self.assertEqual([s.name for s in self.warrior.subclasses.all()], [s.name for s in self.explorer.subclasses.all()])
        self.assertEqual(
            {(r.ability_id, r.level_required) for r in self.warrior.subclasses.get().subclassability_set.all()},
            {(r.ability_id, r.level_required) for r in self.explorer.subclasses.get().subclassability_set.all()})
        self.assertEqual((hit_die(self.warrior), self.warrior.primary_ability), (10, self.explorer.primary_ability))
        self.assertEqual((self.warrior.magic, self.warrior.aegis_only, self.warrior.spells.count()), ("combate", True, 0))
        self.assertEqual([g["kind"] for g in self.warrior.addon_groups()], ["espada", "arco", "buff", "defensa"])
        self.assertEqual(self.warrior.addons.count(), 20)

    def test_magic_points_and_saving_throw_as_on_leahs_sheet(self):
        self.assertEqual([rules.class_magic_points("combate", level) for level in (1, 9, 20)], [12, 28, 50])
        self.assertEqual((self.leah.magic_points_max, self.leah.addon_save_dc, self.leah.has_magic), (28, 15, True))
        self.assertEqual(level_up_plan(self.leah, self.corona)["magic_after"], 30)

    def test_the_sheet_shows_the_addons_and_using_one_spends_its_cost(self):
        page = self.player().get(self.url("sheet", self.leah.pk) + "?tab=spells").content.decode()
        for text in ("Añadidos de la clase", "Flecha Trueno", "1a-5a", "Salvación a superar 15", "Puntos de magia («a»)"):
            self.assertIn(text, page)
        self.assertIn(">Añadidos</a>", page)  # the tab, instead of «Conjuros»
        self.assertNotIn("No conoce conjuros", page)
        response = self.use(self.addon("Flecha Trueno"))
        self.assertEqual(response.json()["msg"], "Leah usa Flecha Trueno (5a): le quedan 23 de 28 puntos de magia.")
        self.use(self.addon("Escudo"), coste="3")  # the player chooses how many: 4 × 3 temporary hit points
        self.leah.refresh_from_db()
        self.assertEqual(self.leah.magic_points, 20)

    def test_an_addon_cannot_cost_more_than_its_range_nor_more_than_what_is_left(self):
        self.assertTrue(self.use(self.addon("Escudo"), coste="6").json()["error"])
        Character.objects.filter(pk=self.leah.pk).update(magic_points=4)
        response = self.use(self.addon("Parálisis"))
        self.assertTrue(response.json()["error"])
        self.assertIn("cuesta 5a y le quedan 4", response.json()["msg"])
        self.leah.refresh_from_db()
        self.assertEqual(self.leah.magic_points, 4)

    def test_only_the_addons_of_its_own_class(self):
        aria = Character.objects.get(name="Aria")
        response = self.player().post(self.url("sheet_action", aria.pk), {"accion": "anadido", "anadido": self.addon("Flecha").pk}, **AJAX)
        self.assertEqual(response.status_code, 404)
        brom = Character.objects.get(name="Brom")  # another player's character
        response = self.player().post(self.url("sheet_action", brom.pk), {"accion": "anadido", "anadido": self.addon("Flecha").pk}, **AJAX)
        self.assertEqual(response.status_code, 403)

    def test_the_paper_sheet_has_the_addons_table(self):
        self.lucia.sheet_view = User.SheetView.FICHA
        self.lucia.save(update_fields=["sheet_view"])
        page = self.player().get(self.url("sheet", self.leah.pk)).content.decode()
        for text in ("hoja-anadidos", "Bombardeo Perfecto", "¿Efectos Secundarios?", "Buffs", '<b class="hoja-num">15</b>'):
            self.assertIn(text, page)
        self.assertNotIn("hoja-magia", page)  # no spells table: it knows none

    def test_only_aegis_campaigns_offer_it(self):
        url = self.url("creator") + "?paso=1"
        self.client.force_login(User.objects.get(username="unai"))
        self.assertIn("Arcane Warrior", self.client.get(url).content.decode())
        self.corona.ruleset = Campaign.Ruleset.SRD
        self.corona.save(update_fields=["ruleset"])
        self.assertNotIn("Arcane Warrior", self.client.get(url).content.decode())
        response = self.client.post(url, {"paso": "1", "ir": "siguiente", "char_class": self.warrior.pk})
        self.assertEqual(response.status_code, 200)  # not a valid choice: the step shows the error
        self.assertIn("char_class", response.context["form"].errors)

    def test_the_catalog_shows_its_addons(self):
        page = self.player().get(reverse("character_class_detail", args=[self.warrior.pk])).content.decode()
        for text in ("Aegis", "Añadidos de la clase", "Empuje y Derribo", "1a-5a", "Magia de combate"):
            self.assertIn(text, page)


class ArcaneWarriorMigrationTests(TestCase):
    """A database that already had the SRD catalog gets the Arcane Warrior from migration 0032;
    an empty one, from fixtures/clases_aegis.json. Both must make the same class."""
    fixtures = ["datos_iniciales_srd"]

    def test_the_migration_makes_the_class_of_the_fixture(self):
        migration = importlib.import_module("CampaignManager.migrations.0032_arcane_warrior")
        migration.add_arcane_warrior(django_apps)
        migration.add_arcane_warrior(django_apps)  # a second time changes nothing
        warrior = CharacterClass.objects.get(name="Arcane Warrior")
        rows = json.loads((Path(__file__).parent / "fixtures" / "clases_aegis.json").read_text(encoding="utf-8"))
        by_model = {}
        for row in rows:
            by_model.setdefault(row["model"].split(".")[1], []).append(row)
        fixture_class = by_model["characterclass"][0]["fields"]
        for field in ("name", "primary_ability", "description", "magic", "aegis_only"):
            self.assertEqual(getattr(warrior, field), fixture_class[field], field)
        names = dict(Ability.objects.values_list("pk", "name"))
        names.update({row["pk"]: row["fields"]["name"] for row in by_model["ability"]})
        self.assertEqual(
            sorted((names[r["fields"]["ability"]], r["fields"]["level_required"]) for r in by_model["classability"]),
            sorted((r.ability.name, r.level_required) for r in warrior.classability_set.select_related("ability")))
        self.assertEqual(Ability.objects.get(name="Magia de combate").description, by_model["ability"][0]["fields"]["description"])
        self.assertEqual(
            sorted((names[r["fields"]["ability"]], r["fields"]["level_required"]) for r in by_model["subclassability"]),
            sorted((r.ability.name, r.level_required) for r in SubclassAbility.objects.filter(subclass__char_class=warrior)))
        self.assertEqual(
            [tuple(r["fields"][k] for k in ("kind", "order", "name", "cost", "max_cost", "effect")) for r in by_model["classaddon"]],
            [(a.kind, a.order, a.name, a.cost, a.max_cost, a.effect) for a in warrior.addons.all()])


class PortraitCardTests(DemoData):
    """«Personajes»: each party member's card opens with its portrait (or its initial) and its name."""

    def test_portraits_and_names_on_the_cards(self):
        aria = Character.objects.get(name="Aria")
        aria.portrait = "retratos/aria.png"
        aria.save(update_fields=["portrait"])
        page = self.player().get(self.url("characters")).content.decode()
        self.assertIn(f'<img src="{aria.portrait.url}"', page)
        self.assertIn('<h3 class="retrato-nombre">Aria</h3>', page)
        self.assertIn('<h3 class="retrato-nombre">Brom</h3>', page)  # without a portrait: its initial
        self.assertIn('class="retrato-inicial" aria-hidden="true">B<', page)
        # «Añadir retrato» only on the cards one can edit that have no portrait yet.
        self.assertNotIn("Añadir retrato", page)
        Character.objects.filter(pk=aria.pk).update(portrait="")
        self.assertIn("Añadir retrato", self.player().get(self.url("characters")).content.decode())


class RouteColourTests(DemoData):
    """Each stretch of the party's route has its own colour, chosen by the master (mesa.js draws it as small arrows)."""

    def setUp(self):
        self.moves = list(PartyMove.objects.filter(map__campaign=self.corona).select_related("location", "map"))
        self.move = self.moves[1]

    def paint(self, user, color):
        self.client.force_login(user)
        return self.client.post(self.url("map_move_color", self.move.pk), {"color": color}, **AJAX)

    def test_the_master_gives_a_stretch_its_colour(self):
        response = self.paint(self.carlos, "#2b6cb0")
        self.assertFalse(response.json()["error"])
        self.move.refresh_from_db()
        self.assertEqual(self.move.color, "#2B6CB0")
        page = self.player().get(self.url("map") + f"?mapa={self.move.map_id}").content.decode()
        self.assertIn(f'data-tramo="{self.move.pk}" fill="none" stroke="#2B6CB0"', page)
        self.assertIn("--color:#2B6CB0", page)  # its arrow head too
        # The others keep the copper of before.
        self.assertIn(f'data-tramo="{self.moves[2].pk}" fill="none" stroke="#BC6C25"', page)

    def test_only_a_colour_and_only_the_master(self):
        self.assertTrue(self.paint(self.carlos, "azul").json()["error"])
        self.assertEqual(self.paint(self.lucia, "#000000").status_code, 403)
        self.move.refresh_from_db()
        self.assertEqual(self.move.color, "#BC6C25")

    def test_the_route_editor_offers_a_colour_for_each_stretch(self):
        self.client.force_login(self.carlos)
        page = self.client.get(self.url("map") + f"?mapa={self.move.map_id}&recorrido=1").content.decode()
        self.assertIn(self.url("map_move_color", self.move.pk), page)
        self.assertIn('type="color" name="color" value="#bc6c25" list="colores-ruta"', page)

    def test_a_new_stretch_keeps_the_colour_of_the_one_before(self):
        last = self.moves[-1]
        PartyMove.objects.filter(pk=last.pk).update(color="#6B46C1")
        place = MapLocation.objects.filter(map=last.map).exclude(pk=last.location_id).first()
        self.client.force_login(self.carlos)
        self.client.post(self.url("map_location_action", place.pk), {"accion": "grupo"}, **AJAX)
        self.assertEqual(PartyMove.objects.filter(map=last.map).last().color, "#6B46C1")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="kiroku-test-media-"))
class AdventureLookTests(DemoData):
    """Adventures and missions can have a colour and a small header image; a mission without
    a colour takes its adventure's."""

    def setUp(self):
        self.adventure = Quest.objects.filter(campaign=self.corona, parent__isnull=True, status="activa").first()

    def save(self, quest, **fields):
        data = {"title": quest.title, "description": quest.description, "status": quest.status,
                "start_session": quest.start_session_id or "", "end_session": quest.end_session_id or "", "giver": quest.giver_id or "",
                "parent": quest.parent_id or "", "objectives-TOTAL_FORMS": "0", "objectives-INITIAL_FORMS": "0", **fields}
        self.client.force_login(self.carlos)
        return self.client.post(self.url("diary_quest_edit", quest.pk), data)

    def test_a_colour_from_the_palette_another_or_none(self):
        self.save(self.adventure, color="#2B6CB0")
        self.adventure.refresh_from_db()
        self.assertEqual(self.adventure.color, "#2B6CB0")
        self.save(self.adventure, color="otro", color_otro="#12ab9f")  # «Otro»: the browser's colour picker
        self.adventure.refresh_from_db()
        self.assertEqual(self.adventure.color, "#12AB9F")
        response = self.save(self.adventure, color="otro", color_otro="verde")
        self.assertEqual(response.status_code, 200)  # the form again, with the error
        self.save(self.adventure, color="")
        self.adventure.refresh_from_db()
        self.assertEqual(self.adventure.color, "")

    def test_a_mission_takes_its_adventures_colour(self):
        Quest.objects.filter(pk=self.adventure.pk).update(color="#9E2A2B")
        mission = Quest.objects.create(campaign=self.corona, title="Buscar al traductor", parent=self.adventure)
        own = Quest.objects.create(campaign=self.corona, title="Pagar la deuda", parent=self.adventure, color="#2F855A")
        self.assertEqual(Quest.objects.get(pk=mission.pk).shown_color, "#9E2A2B")
        page = self.player().get(self.url("diary")).content.decode()
        self.assertIn(f'id="aventura-{mission.pk}" class="mision con-color" style="--aventura:#9E2A2B"', page)
        self.assertIn(f'id="aventura-{own.pk}" class="mision con-color" style="--aventura:#2F855A"', page)
        self.assertIn('class="punto-color" style="background:#9E2A2B"', self.player().get(self.url("home")).content.decode())

    def test_a_cover_shows_as_a_header_and_can_be_removed(self):
        self.save(self.adventure, cover=png_upload("portada.png"))
        self.adventure.refresh_from_db()
        self.assertTrue(self.adventure.cover.name.startswith("aventuras/"))
        page = self.player().get(self.url("diary")).content.decode()
        self.assertIn(f'<img class="aventura-portada" src="{self.adventure.cover.url}"', page)
        self.client.force_login(self.carlos)
        form = self.client.get(self.url("diary_quest_edit", self.adventure.pk)).content.decode()
        self.assertIn('enctype="multipart/form-data"', form)
        self.assertIn("Quitar la portada", form)
        self.save(self.adventure, **{"cover-clear": "on"})
        self.adventure.refresh_from_db()
        self.assertEqual(self.adventure.cover.name, "")


class BattleTableTests(DemoData):
    """«Turnos» as a table of cards: the party on one side, those who fight it on the other,
    with whose turn it is and who comes next; the master can move a card to the other side."""

    def setUp(self):
        self.encounter = Encounter.objects.get(campaign=self.corona, active=True)
        self.orc = Combatant.objects.filter(encounter=self.encounter, character__is_npc=True).first()

    def test_two_sides_of_cards(self):
        self.client.force_login(self.carlos)
        response = self.client.get(self.url("turns"))
        party = {r["c"].pk for r in response.context["party_rows"]}
        enemies = {r["c"].pk for r in response.context["enemy_rows"]}
        combatants = list(self.encounter.combatants.select_related("character"))
        self.assertEqual(party, {c.pk for c in combatants if c.character and not c.character.is_npc})
        self.assertEqual(enemies, {c.pk for c in combatants} - party)
        page = response.content.decode()
        actor = combatants[self.encounter.turn_index]
        after = combatants[(self.encounter.turn_index + 1) % len(combatants)]
        self.assertIn(f'id="combatiente-{actor.pk}" class="carta actual"', page)
        self.assertIn(f'id="combatiente-{after.pk}" class="carta siguiente', page)
        self.assertIn('class="campo-batalla"', page)
        # Writing a number and pressing Enter deals damage: «Daño» is the form's first button.
        card = page[page.index(f'id="combatiente-{actor.pk}"'):]
        self.assertLess(card.index('value="dano"'), card.index('value="vida_menos"'))

    def test_the_master_moves_a_card_to_the_other_side(self):
        self.client.force_login(self.carlos)
        response = self.client.post(self.url("turns_action"), {"accion": "lado", "combatiente": self.orc.pk}, **AJAX)
        self.assertIn("lucha ahora con el grupo", response.json()["msg"])
        self.orc.refresh_from_db()
        self.assertEqual(self.orc.side, Combatant.Side.GRUPO)
        self.assertIn(self.orc.pk, {r["c"].pk for r in self.client.get(self.url("turns")).context["party_rows"]})
        self.assertEqual(self.player().post(self.url("turns_action"), {"accion": "lado", "combatiente": self.orc.pk}, **AJAX).status_code, 403)

    def test_players_see_the_enemy_cards_without_their_numbers(self):
        html = self.player().get(self.url("turns"), **AJAX).json()["html"]
        self.assertIn(f'id="combatiente-{self.orc.pk}"', html)
        self.assertNotIn(f'data-valor="vida-{self.orc.pk}"', html)
        card = html[html.index(f'id="combatiente-{self.orc.pk}"'):html.index("</article>", html.index(f'id="combatiente-{self.orc.pk}"'))]
        self.assertNotIn('value="dano"', card)

    def test_the_master_decides_who_acts(self):
        self.client.force_login(self.carlos)
        combatants = list(self.encounter.combatants.all())
        actor = combatants[self.encounter.turn_index]
        chosen = next(c for c in combatants if c != actor)
        response = self.client.post(self.url("turns_action"), {"accion": "turno", "combatiente": chosen.pk}, **AJAX)
        self.assertEqual(response.json()["msg"], f"Turno de {chosen.display_name}.")
        self.encounter.refresh_from_db()
        self.assertEqual(list(self.encounter.combatants.all())[self.encounter.turn_index], chosen)
        # Moving a card in the order keeps the turn with whoever has it.
        first = combatants[0]
        self.client.post(self.url("turns_action"), {"accion": "despues", "combatiente": first.pk}, **AJAX)
        order = list(self.encounter.combatants.all())
        self.assertEqual(order[1], first)
        self.encounter.refresh_from_db()
        self.assertEqual(order[self.encounter.turn_index], chosen)
        # A new round when the master says so: the first of the order acts.
        round_before = self.encounter.round
        self.client.post(self.url("turns_action"), {"accion": "ronda"}, **AJAX)
        self.encounter.refresh_from_db()
        self.assertEqual((self.encounter.round, self.encounter.turn_index), (round_before + 1, 0))
        for action in ("turno", "antes", "ronda"):
            self.assertEqual(self.player().post(self.url("turns_action"), {"accion": action, "combatiente": first.pk},
                                                **AJAX).status_code, 403)

    def test_a_red_veil_tells_how_wounded_a_fighter_looks(self):
        from .views_turns import wound_level
        self.assertEqual([wound_level(hp, 100) for hp in (100, 90, 75, 60, 30, 10, 0, -5)], [0, 1, 1, 2, 3, 4, 4, 4])
        self.assertEqual(wound_level(None, None), 0)
        character = self.orc.character
        Character.objects.filter(pk=character.pk).update(temp_hp=max(1, character.hp // 3))
        html = self.player().get(self.url("turns"), **AJAX).json()["html"]
        card = html[html.index(f'id="combatiente-{self.orc.pk}"'):]
        self.assertIn('class="carta-retrato herida-3"', card)  # the player sees how it looks...
        self.assertNotIn(f'data-valor="vida-{self.orc.pk}"', html)  # ...not its hit points

    def template(self, name):
        """An NPC template of the campaign, made from the orc's sheet."""
        sheet = Character.objects.get(pk=self.orc.character_id)
        sheet.pk, sheet.template, sheet.name = None, None, name
        sheet._state.adding = True
        sheet.save()
        sheet.campaigns.set([self.corona])
        return sheet

    def test_a_pair_takes_one_turn(self):
        shayne, aurox = self.template("Shayne"), self.template("Aurox")
        shayne.acts_with = aurox
        shayne.save()
        aurox.acts_with = shayne
        aurox.save()
        self.client.force_login(self.carlos)
        act = self.url("turns_action")
        response = self.client.post(act, {"accion": "anadir", "plantilla": shayne.pk}, **AJAX)
        self.assertEqual(response.json()["msg"], "Shayne 1 y Aurox 1 entran en el combate: actúan a la vez.")
        order = list(self.encounter.combatants.select_related("character"))
        self.assertEqual([c.display_name for c in order[-2:]], ["Shayne 1", "Aurox 1"])
        pair = order[-2:]
        # The turn to one of them is the turn to both.
        response = self.client.post(act, {"accion": "turno", "combatiente": pair[1].pk}, **AJAX)
        self.assertEqual(response.json()["msg"], "Turno de Shayne 1 y Aurox 1.")
        context = self.client.get(self.url("turns")).context
        self.assertEqual([r["c"] for r in context["actor_rows"]], pair)
        self.assertEqual(context["actor_names"], "Shayne 1 y Aurox 1")
        self.assertContains(self.client.get(self.url("turns")), "A la vez con Aurox 1")
        # «Siguiente» passes both: a new round, since they were last.
        round_before = Encounter.objects.get(pk=self.encounter.pk).round
        self.client.post(act, {"accion": "siguiente"}, **AJAX)
        self.encounter.refresh_from_db()
        self.assertEqual((self.encounter.round, self.encounter.turn_index), (round_before + 1, 0))
        # ‹ moves the pair as one; by initiative, the partner goes right behind the first of the two.
        self.client.post(act, {"accion": "antes", "combatiente": pair[0].pk}, **AJAX)
        names = [c.display_name for c in self.encounter.combatants.all()]
        self.assertEqual(names[-3:][:2], ["Shayne 1", "Aurox 1"])
        Combatant.objects.filter(pk=pair[0].pk).update(initiative=30)
        Combatant.objects.filter(pk=pair[1].pk).update(initiative=-5)
        self.client.post(act, {"accion": "ordenar"}, **AJAX)
        names = [c.display_name for c in self.encounter.combatants.all()]
        self.assertEqual(names[:2], ["Shayne 1", "Aurox 1"])

    def test_states_go_on_the_cards(self):
        from .views_turns import effect_kind
        self.assertEqual([effect_kind(n) for n in ("Quemado", "Sangrando", "Congelado", "Envenenado", "Cansancio")],
                         ["quemado", "sangrado", "helado", "envenenado", ""])
        burning = Effect.objects.get(name="Quemado")
        act = self.url("turns_action")
        self.client.force_login(self.carlos)
        response = self.client.post(act, {"accion": "estado_anadir", "combatiente": self.orc.pk, "efecto": burning.pk,
                                          "rondas": 2}, **AJAX)
        self.assertEqual(response.json()["msg"], f"{self.orc.display_name}: Quemado durante 2 rondas.")
        self.client.post(act, {"accion": "estado_anadir", "combatiente": self.orc.pk, "efecto": burning.pk, "rondas": 1}, **AJAX)
        row = ActiveEffect.objects.get(character=self.orc.character, effect=burning)  # not doubled; the longer stays
        self.assertEqual(row.remaining_turns, 2)
        self.assertIn("Elige un estado", self.client.post(act, {"accion": "estado_anadir", "combatiente": self.orc.pk,
                                                                "efecto": ""}, **AJAX).json()["msg"])
        # The list offers states, not the traits of weapons and armor.
        conditions = self.client.get(self.url("turns")).context["conditions"]
        self.assertIn(burning, conditions)
        self.assertFalse([e for e in conditions if e.name.isupper()])
        # Everyone sees it burn; only the master, how long, and the × that puts it out.
        html = self.player().get(self.url("turns"), **AJAX).json()["html"]
        card = html[html.index(f'id="combatiente-{self.orc.pk}"'):html.index("</article>", html.index(f'id="combatiente-{self.orc.pk}"'))]
        self.assertIn('class="fx fx-quemado"', card)
        self.assertIn("Quemado", card)
        self.assertNotIn("estado_quitar", card)
        self.assertNotIn('name="efecto"', card)
        self.assertEqual(self.player().post(act, {"accion": "estado_anadir", "combatiente": self.orc.pk,
                                                  "efecto": burning.pk}, **AJAX).status_code, 403)
        self.client.force_login(self.carlos)
        html = self.client.get(self.url("turns"), **AJAX).json()["html"]
        self.assertIn(f'name="estado" value="{row.pk}"', html)
        self.client.post(act, {"accion": "estado_quitar", "combatiente": self.orc.pk, "estado": row.pk}, **AJAX)
        self.assertFalse(ActiveEffect.objects.filter(pk=row.pk).exists())
        # A player puts states on their own character.
        mine = Combatant.objects.get(encounter=self.encounter, character__player=self.lucia)
        self.player().post(act, {"accion": "estado_anadir", "combatiente": mine.pk, "efecto": burning.pk}, **AJAX)
        self.assertTrue(ActiveEffect.objects.filter(character=mine.character, effect=burning).exists())

    def test_an_enemy_without_a_sheet_suffers_states_too(self):
        self.client.force_login(self.carlos)
        act = self.url("turns_action")
        self.client.post(act, {"accion": "anadir", "nombre": "Bandido", "vida": 12}, **AJAX)
        bandit = Combatant.objects.get(encounter=self.encounter, name="Bandido")
        frozen = Effect.objects.get(name="Congelado")
        self.client.post(act, {"accion": "estado_anadir", "combatiente": bandit.pk, "efecto": frozen.pk, "rondas": 1}, **AJAX)
        self.assertEqual(bandit.effects.get().effect, frozen)
        self.assertIn('class="fx fx-helado"', self.client.get(self.url("turns"), **AJAX).json()["html"])
        self.client.post(act, {"accion": "ronda"}, **AJAX)  # its round is over: it thaws
        self.assertFalse(bandit.effects.exists())

    def test_only_the_master_sees_the_numbers_of_the_enemy_side(self):
        # Two party members turned against the group: hers and another player's.
        mine = Combatant.objects.get(encounter=self.encounter, character__player=self.lucia)
        other = (Combatant.objects.filter(encounter=self.encounter, character__is_npc=False)
                 .exclude(character__player=self.lucia).first())
        Combatant.objects.filter(pk__in=[mine.pk, other.pk]).update(side=Combatant.Side.ENEMIGOS)
        html = self.player().get(self.url("turns"), **AJAX).json()["html"]
        for value in ("vida", "ap"):
            self.assertNotIn(f'data-valor="{value}-{other.pk}"', html)
        self.assertIn(f'data-valor="vida-{mine.pk}"', html)  # her own character, wherever it stands
        self.client.force_login(self.carlos)
        self.assertIn(f'data-valor="vida-{other.pk}"', self.client.get(self.url("turns"), **AJAX).json()["html"])


class WorldSheetsTests(RulesData):
    """World pages and character sheets point to each other (Page.sheets): the page shows its sheets
    with their numbers (players only the party's), each sheet its pages under «En el mundo», and in
    the diary [[Name]] opens an NPC's sheet for masters."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.page = Page.objects.create(campaign=cls.campaign, title="Shogunato Takeda", category="faccion", revealed=True)
        cls.secret = Page.objects.create(campaign=cls.campaign, title="La red", category="faccion")

    def setUp(self):
        self.hero = self.character("Thalion", campaigns=[self.campaign])
        self.samurai = self.character("Samurái Takeda", campaigns=[self.campaign], player=None, is_npc=True,
                                      hp=45, temp_hp=45, ac=13)
        self.page.sheets.add(self.hero, self.samurai)
        self.secret.sheets.add(self.hero)

    def get(self, user, name, *args):
        self.client.force_login(user)
        return self.client.get(reverse(name, args=[self.campaign.pk, *args]))

    def test_the_page_shows_its_sheets_and_npcs_only_to_masters(self):
        npc_sheet = reverse("sheet", args=[self.campaign.pk, self.samurai.pk])
        page = self.get(self.master, "world_page", self.page.slug).content.decode()
        self.assertIn(npc_sheet, page)
        self.assertIn("PV 45 · CA 13 · AP 0", page)
        page = self.get(self.player, "world_page", self.page.slug).content.decode()
        self.assertNotIn(npc_sheet, page)
        self.assertIn(reverse("sheet", args=[self.campaign.pk, self.hero.pk]), page)

    def test_the_sheet_shows_its_pages_of_the_world(self):
        response = self.get(self.player, "sheet", self.hero.pk)
        self.assertEqual(response.context["world_pages"], [self.page])  # not the hidden one
        self.assertContains(response, reverse("world_page", args=[self.campaign.pk, self.page.slug]))
        response = self.get(self.master, "sheet", self.hero.pk)
        self.assertEqual(set(response.context["world_pages"]), {self.page, self.secret})

    def test_the_master_links_sheets_from_the_page_form(self):
        self.samurai.make_copy(campaigns=[self.campaign])
        url = reverse("world_edit", args=[self.campaign.pk, self.secret.slug])
        response = self.get(self.master, "world_edit", self.secret.slug)
        # The party and the NPC templates; not the copies made for a fight.
        self.assertEqual(set(response.context["form"].fields["sheets"].queryset), {self.hero, self.samurai})
        self.client.post(url, {"title": self.secret.title, "category": "faccion", "sheets": [self.samurai.pk]})
        self.assertEqual(set(self.secret.sheets.all()), {self.samurai})

    def test_wiki_links_open_npc_sheets_only_for_masters(self):
        from .wiki import WikiResolver
        text = "Les cortó el paso un [[Samurái Takeda]]."
        self.assertIn(reverse("sheet", args=[self.campaign.pk, self.samurai.pk]),
                      WikiResolver(self.campaign, self.master, True).inline(text))
        self.assertNotIn("href", WikiResolver(self.campaign, self.player, False).inline(text))

    def test_an_npc_runs_at_its_own_speed(self):
        self.assertEqual(self.samurai.speed, 10)  # its race's
        self.samurai.own_speed = 12
        self.assertEqual(self.samurai.speed, 12)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="kiroku-test-media-"))
class LoadSokokuTests(TestCase):
    """«cargar_sokoku»: the 82 NPCs of the bestiary and the sheets of Zarek, Leah, Thalion and Balasar,
    with their numbers and their pages of the world. A second run keeps them; --actualizar rewrites them."""
    fixtures = ["datos_iniciales_srd", "armas_caseras", "clases_aegis"]

    @classmethod
    def setUpTestData(cls):
        cls.campaign = Campaign.objects.create(name="Infinity Blade", start_date=datetime.date(2026, 1, 1))
        for title, category in (("Shogunato Takeda", "faccion"), ("Thane", "pnj"), ("Eldran", "lugar")):
            Page.objects.create(campaign=cls.campaign, title=title, category=category)

    def load(self, *args):
        out = io.StringIO()
        call_command("cargar_sokoku", *args, stdout=out)
        return out.getvalue()

    def sheet(self, name, npc=True):
        return Character.objects.get(campaigns=self.campaign, name=name, is_npc=npc, template__isnull=True)

    def test_the_bestiary_and_the_party(self):
        out = self.load()
        self.assertIn("82 PNJ y 4 personajes", out)
        self.assertIn("Páginas del mundo que no existen", out)  # this campaign has only three
        self.assertEqual(Character.objects.filter(campaigns=self.campaign, is_npc=True).count(), 82)
        samurai = self.sheet("Samurái Takeda")
        self.assertEqual((samurai.level, samurai.hp, samurai.ac, samurai.speed, samurai.max_ap), (5, 45, 13, 10, 5))
        katana = Weapon.objects.get(item__inventory__character=samurai, item__name="Katana a dos manos")
        self.assertEqual(katana.item.template.name, "Katana")  # a copy of the house weapon, with its numbers
        self.assertEqual(katana.to_hit_bonus(samurai), 5)
        self.assertEqual(katana.attacks.get().damage_text(samurai), "2d10 + 8")
        self.assertEqual({(e.effect.name, e.value) for e in katana.item.itemeffect_set.select_related("effect")},
                         {("IAIDO", None), ("SANGRADO", 3), ("ESGRIMA", None)})
        self.assertTrue(CharacterAbility.objects.filter(character=samurai, ability__name="Iaido", max_uses=1).exists())
        self.assertEqual(list(samurai.world_pages.values_list("title", flat=True)), ["Shogunato Takeda"])
        self.assertIn("Thane", self.sheet("Thane, señor de la Casa Ix").world_pages.values_list("title", flat=True))

        thalion = self.sheet("Thalion", npc=False)
        self.assertEqual((thalion.char_class.name, thalion.subclass.name, thalion.temp_hp, thalion.hp),
                         ("Paladín", "Juramento de entrega", 107, 110))
        self.assertEqual((thalion.temp_ap, thalion.max_ap, thalion.max_magic_ap, thalion.magic_points), (7, 11, 5, 16))
        self.assertEqual(thalion.spells.count(), 23)
        self.assertTrue(thalion.portrait.name.endswith("thalion.jpg"))
        zarek = self.sheet("Zarek", npc=False)
        self.assertEqual((zarek.char_class.name, zarek.subclass.name, zarek.subrace.name),
                         ("Artífice", "Artillero", "Legado infernal: infernal"))
        self.assertEqual(zarek.unique_skill.status, UniqueSkill.Status.APROBADA)
        leah = self.sheet("Leah", npc=False)
        self.assertEqual(leah.magic_points_max, 28)  # the Arcane Warrior's «a»: 10 + 2 × level
        bow = Weapon.objects.get(item__inventory__character=leah, item__name="Arco largo")
        self.assertEqual(bow.to_hit_bonus(leah), 8)
        balasar = self.sheet("Balasar", npc=False)
        self.assertEqual((balasar.subclass.name, balasar.subrace.name, balasar.speed, balasar.magic_points),
                         ("Círculo de la Luna", "Linaje élfico: elfo de los bosques", 12, 41))
        self.assertTrue(balasar.spells.filter(name="Curación dañina").exists())  # his house spell
        self.assertTrue(CharacterAbility.objects.filter(character=balasar, ability__name="Forma salvaje", max_uses=2).exists())

    def test_a_second_run_keeps_the_sheets_and_actualizar_rewrites_them(self):
        self.load()
        samurai = self.sheet("Samurái Takeda")
        carried = samurai.inventory_set.count()
        Character.objects.filter(pk=samurai.pk).update(temp_hp=3)
        self.assertIn("86 ya estaban", self.load())
        self.assertEqual(Character.objects.filter(campaigns=self.campaign).count(), 86)
        self.assertEqual(self.sheet("Samurái Takeda").temp_hp, 3)
        self.load("--actualizar")
        samurai = self.sheet("Samurái Takeda")
        self.assertEqual((samurai.temp_hp, samurai.inventory_set.count()), (45, carried))
        self.assertEqual(Character.objects.filter(campaigns=self.campaign).count(), 86)
        self.assertEqual(UniqueSkill.objects.filter(character__name="Zarek").count(), 1)

    def test_shayne_and_aurox_act_as_one(self):
        self.load()
        shayne, aurox = self.sheet("Shayne"), self.sheet("Aurox")
        self.assertEqual((shayne.acts_with, aurox.acts_with), (aurox, shayne))
        self.assertEqual((aurox.race.name, aurox.char_class.name, aurox.hp, aurox.ac, aurox.max_ap), ("Constructo", "Jefe", 360, 13, 8))
        self.assertEqual((shayne.level, shayne.hp, shayne.ac, shayne.speed), (12, 90, 17, 12))
        both = {"Cómo pelean juntos"}
        for one in (shayne, aurox):
            self.assertTrue(both <= set(one.abilities.values_list("name", flat=True)))
            self.assertIn("Lo que se cuenta", one.background)
        # Shayne's orders, each with its limit; Aurox's grab is a roll, not a weapon.
        held = {row.ability.name: row for row in shayne.characterability_set.select_related("ability")}
        self.assertTrue({"Órdenes", "¡Ahí!", "¡Trae!", "¡Cúbreme!", "¡Ruge!", "Lánzame", "Juntos"} <= set(held))
        self.assertEqual((held["¡Trae!"].cooldown_rounds, held["Juntos"].max_uses), (2, 1))
        self.assertTrue(aurox.abilities.filter(name="Agarrar").exists())
        self.assertFalse(Weapon.objects.filter(item__inventory__character=aurox, item__name="Agarrar").exists())
        claws = Weapon.objects.get(item__inventory__character=aurox, item__name="Garras")
        self.assertEqual((claws.to_hit_bonus(aurox), claws.attacks.get().damage_text(aurox)), (12, "2d10 + 16"))

    def test_a_sheet_loaded_under_an_older_name_is_renamed_everywhere(self):
        old_name = "Tokiasa Yarikawa (otro nombre)"
        template = Character.objects.create(
            name=old_name, is_npc=True, race=Race.objects.first(), char_class=CharacterClass.objects.first(),
            hp=10, temp_hp=10, ac=10, strength=10, dexterity=10, constitution=10, intelligence=10, wisdom=10, charisma=10)
        template.campaigns.add(self.campaign)
        copy = template.make_copy(campaigns=[self.campaign])
        stale = Ability.objects.create(name="Rasgo viejo", description="Lo que ya no está en el bestiario.")
        CharacterAbility.objects.create(character=template, ability=stale)
        self.assertIn("Renombradas y reescritas: Tokiasa Yarikawa.", self.load())
        tokiasa = self.sheet("Tokiasa Yarikawa")
        self.assertEqual(tokiasa.pk, template.pk)
        self.assertEqual(Character.objects.get(pk=copy.pk).name, "Tokiasa Yarikawa 1")
        self.assertEqual((tokiasa.level, tokiasa.hp), (16, 290))
        self.assertFalse(Ability.objects.filter(pk=stale.pk).exists())  # nobody holds it any more
        self.assertFalse(Character.objects.filter(campaigns=self.campaign, name__startswith=old_name).exists())
        self.assertNotIn("Hattori", tokiasa.background)
        self.assertFalse(tokiasa.abilities.filter(name="Nombre verdadero").exists())
        self.assertTrue(tokiasa.abilities.filter(name="Resistencia legendaria").exists())
