"""Tests of the house rules (data-model.md, «Game rules»: 2, 3, 5, 6 and 16) and of
what players may see (spec, section 6).

    python manage.py test CampaignManager
"""
import datetime
import json
import re

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from . import rules
from .models import (
    Ability, Armor, ArmorDamage, ArmorType, BoardCard, Campaign, Character, CharacterAbility, CharacterClass,
    ClassAbility, Combatant, Encounter, Inventory, Item, MapLocation, Page, Post, QuestObjective, Race, Session,
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

class LevelUpTests(RulesData):
    """The master gives XP; the level waits until the player levels up from the
    sheet, one level at a time, with that level's choices (game rule 16)."""

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
        urls = [self.url(name) for name in ("home", "diary", "diary_timeline", "diary_board", "diary_scenes", "diary_quests",
                                            "world", "map", "characters", "turns")]
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
        response = self.player().get(self.url("diary_quests"))
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
    def test_hit_points_stay_between_zero_and_maximum(self):
        aria = Character.objects.get(name="Aria")
        client = self.player()
        url = self.url("sheet_action", aria.pk)
        client.post(url, {"accion": "dano", "cantidad": "999"}, **AJAX)
        aria.refresh_from_db()
        self.assertEqual(aria.temp_hp, 0)
        client.post(url, {"accion": "curar", "cantidad": "999"}, **AJAX)
        aria.refresh_from_db()
        self.assertEqual(aria.temp_hp, aria.hp)

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
