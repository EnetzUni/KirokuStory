"""Tests of the house rules (data-model.md, «Game rules»: 2, 3, 5, 6 and 16) and of
what players may see (spec, section 6).

    python manage.py test CampaignManager
"""
import datetime
import json
import re
import tempfile

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from . import rules
from .models import (
    Ability, Armor, ArmorDamage, ArmorType, BoardCard, Campaign, Character, CharacterAbility, CharacterClass,
    ClassAbility, Combatant, Encounter, Inventory, Item, MapLocation, Page, PartyMove, Post, Quest, QuestObjective, Race, Session,
    Spell, Subclass, TimelineEvent, UniqueSkill, User, Weapon,
)

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
    fixtures = ["datos_iniciales_srd", "armas_caseras", "demo_campana"]

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

    def secrets(self):
        """Texts only the masters may read (whispers to others included)."""
        corona = self.corona
        texts = []
        texts += QuestObjective.objects.filter(quest__campaign=corona, hidden=True, done=False).values_list("text", flat=True)
        texts += BoardCard.objects.filter(campaign=corona, classified=True, revealed=False).values_list("text", flat=True)
        texts += Page.objects.filter(campaign=corona).exclude(secret="").values_list("secret", flat=True)
        texts += Page.objects.filter(campaign=corona, revealed=False).exclude(body="").values_list("body", flat=True)
        texts += TimelineEvent.objects.filter(campaign=corona, hidden=True).exclude(description="").values_list("description", flat=True)
        texts += Session.objects.filter(campaign=corona).exclude(master_notes="").values_list("master_notes", flat=True)
        texts += Session.objects.filter(campaign=corona, published=False).exclude(body="").values_list("body", flat=True)
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
                                            "world", "map", "characters", "turns")]
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
        raiders = page[page.index('id="aventura-%d"' % self.quest("Saqueadores en el Camino del Faro").pk):]
        raiders = raiders[:raiders.index("</section>")]
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
