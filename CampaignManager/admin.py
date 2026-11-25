"""Admin. Two groups (created by migration 0018): «Reglas» edits the rules
catalog and «Campañas» edits the campaign models (and only views the catalog)."""
from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin

from .models import (
    Ability, ActiveEffect, Armor, ArmorDamage, ArmorType, Attack, BoardCard, BoardLink, Campaign, Character,
    CharacterAbility, CharacterClass, ClassAbility, Combatant, Component, DiceRoll, Effect, Encounter, Inventory,
    Item, ItemEffect, Map, MapLocation, Page, PartyMove, Post, Quest, QuestObjective, Race, RaceAbility, Rumour,
    RumourCategory, Scene, Session, Spell, Subclass, SubclassAbility, Subrace, SubraceAbility, TimelineEvent, UniqueSkill,
    User, Weapon, WeatherRegion,
    granted_ability_ids, unique_ability_conflicts,
)

admin.site.site_header = "Kiroku Story · administración"
admin.site.site_title = "Kiroku Story"

# UserAdmin hashes the password and shows the proper user forms.
admin.site.register(User, UserAdmin)


# ---------------------------------------------------------------------------
# Rules catalog («Reglas»)
# ---------------------------------------------------------------------------

class RaceAbilityInline(admin.TabularInline):
    model = RaceAbility
    extra = 0
    autocomplete_fields = ["ability"]


class SubraceAbilityInline(RaceAbilityInline):
    model = SubraceAbility


class ClassAbilityInline(RaceAbilityInline):
    model = ClassAbility


class SubclassAbilityInline(RaceAbilityInline):
    model = SubclassAbility


class SubraceInline(admin.TabularInline):
    model = Subrace
    extra = 0
    fields = ["name"]
    show_change_link = True


@admin.register(Race)
class RaceAdmin(admin.ModelAdmin):
    list_display = ("name", "speed")
    search_fields = ["name"]
    inlines = [SubraceInline, RaceAbilityInline]


@admin.register(Subrace)
class SubraceAdmin(admin.ModelAdmin):
    list_display = ("name", "race")
    list_filter = ("race",)
    search_fields = ["name"]
    inlines = [SubraceAbilityInline]


@admin.register(CharacterClass)
class CharacterClassAdmin(admin.ModelAdmin):
    list_display = ("name", "primary_ability", "magic")
    list_filter = ("magic",)
    search_fields = ["name"]
    filter_horizontal = ["spells"]
    inlines = [ClassAbilityInline]


@admin.register(Subclass)
class SubclassAdmin(admin.ModelAdmin):
    list_display = ("name", "char_class")
    list_filter = ("char_class",)
    search_fields = ["name"]
    inlines = [SubclassAbilityInline]


@admin.register(Ability)
class AbilityAdmin(admin.ModelAdmin):
    list_display = ("name", "unique", "feat", "feat_min_level")
    list_filter = ("feat", "unique")
    search_fields = ["name", "description"]
    filter_horizontal = ["effects"]


@admin.register(RaceAbility, SubraceAbility, ClassAbility, SubclassAbility)
class AbilityLinkAdmin(admin.ModelAdmin):
    list_display = ("__str__", "level_required")
    list_filter = ("level_required",)
    autocomplete_fields = ["ability"]


@admin.register(Spell)
class SpellAdmin(admin.ModelAdmin):
    list_display = ("name", "level", "casting_time", "range", "duration")
    list_filter = ("level", "components")
    search_fields = ["name", "description"]
    filter_horizontal = ["components", "attacks", "effects"]


@admin.register(Component)
class ComponentAdmin(admin.ModelAdmin):
    search_fields = ["type"]


class DiceInline(admin.TabularInline):
    """Dice rolls inside an attack (Attack.dice is many-to-many)."""
    model = Attack.dice.through
    extra = 1
    verbose_name = "dice roll"
    verbose_name_plural = "dice rolls"


class ArmorDamageInline(admin.TabularInline):
    model = Attack.armor_damages.through
    extra = 0
    verbose_name = "armor damage"
    verbose_name_plural = "armor damages"


@admin.register(Attack)
class AttackAdmin(admin.ModelAdmin):
    list_display = ("name", "extra", "stat_bonus", "range", "area", "cooldown")
    list_filter = ("stat_bonus",)
    search_fields = ["name"]
    exclude = ["dice", "armor_damages"]
    filter_horizontal = ["effects"]
    inlines = [DiceInline, ArmorDamageInline]


@admin.register(DiceRoll)
class DiceRollAdmin(admin.ModelAdmin):
    list_display = ("__str__", "amount", "sides", "per_extra_level")
    list_filter = ("sides", "per_extra_level")


@admin.register(ArmorDamage)
class ArmorDamageAdmin(admin.ModelAdmin):
    list_display = ("__str__", "type", "amount")
    list_filter = ("type",)


@admin.register(Effect)
class EffectAdmin(admin.ModelAdmin):
    list_display = ("name", "duration")
    search_fields = ["name", "description"]


class IsTemplateFilter(admin.SimpleListFilter):
    title = "kind"
    parameter_name = "kind"

    def lookups(self, request, model_admin):
        return [("template", "Templates"), ("copy", "Copies")]

    def queryset(self, request, queryset):
        if self.value() == "template":
            return queryset.filter(template__isnull=True)
        if self.value() == "copy":
            return queryset.filter(template__isnull=False)
        return queryset


class ItemEffectInline(admin.TabularInline):
    model = ItemEffect
    extra = 0
    autocomplete_fields = ["effect"]


class WeaponInline(admin.StackedInline):
    model = Weapon
    extra = 0
    filter_horizontal = ["attacks"]


class ArmorInline(admin.StackedInline):
    model = Armor
    extra = 0


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ("name", "template", "price", "consumable")
    list_filter = (IsTemplateFilter, "consumable")
    search_fields = ["name", "description"]
    list_select_related = ["template"]
    autocomplete_fields = ["template"]
    inlines = [ItemEffectInline, WeaponInline, ArmorInline]
    actions = ["make_item_copy"]

    @admin.action(description="Make a copy of the selected items")
    def make_item_copy(self, request, queryset):
        for item in queryset:
            item.make_copy()
        self.message_user(
            request,
            f"{len(queryset)} item copies made. Add them to a character's inventory from Inventories.",
        )


@admin.register(ItemEffect)
class ItemEffectAdmin(admin.ModelAdmin):
    list_display = ("item", "effect", "value")
    list_select_related = ["item", "effect"]
    autocomplete_fields = ["item", "effect"]


@admin.register(Weapon)
class WeaponAdmin(admin.ModelAdmin):
    list_display = ("item", "to_hit", "equipped")
    list_filter = ("equipped",)
    search_fields = ["item__name"]
    list_select_related = ["item"]
    filter_horizontal = ["attacks"]


@admin.register(Armor)
class ArmorAdmin(admin.ModelAdmin):
    list_display = ("item", "armor_type", "ap", "magic_ap", "equipped")
    list_filter = ("armor_type", "equipped")
    search_fields = ["item__name"]
    list_select_related = ["item", "armor_type"]


@admin.register(ArmorType)
class ArmorTypeAdmin(admin.ModelAdmin):
    list_display = ("type", "ap", "magic_ap")
    filter_horizontal = ["effects"]


# ---------------------------------------------------------------------------
# Campaigns («Campañas»)
# ---------------------------------------------------------------------------

@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ("name", "ruleset", "status", "start_date", "world_day")
    list_filter = ("ruleset", "status")
    search_fields = ["name", "description"]
    filter_horizontal = ["masters", "players"]


class CharacterAdminForm(forms.ModelForm):
    class Meta:
        model = Character
        fields = "__all__"

    def clean(self):
        cleaned_data = super().clean()
        # Unique abilities (game rule 6), on the campaigns, race and class as they
        # will be saved. The abilities held directly are edited in the inline,
        # where CharacterAbility.clean() checks each new one. Django saves the
        # links one by one after this, so the flag skips the in-between checks.
        if "race" in cleaned_data and "char_class" in cleaned_data:
            held = set(self.instance.characterability_set.values_list("ability_id", flat=True)) if self.instance.pk else set()
            ability_ids = held | granted_ability_ids(
                cleaned_data["race"],
                cleaned_data.get("subrace"),
                cleaned_data["char_class"],
                cleaned_data.get("subclass"),
                level=cleaned_data.get("level") or 1,
            )
            campaigns = cleaned_data.get("campaigns") or []
            for message in unique_ability_conflicts(self.instance, campaigns, ability_ids):
                self.add_error(None, message)
            self.instance._unique_abilities_checked = True
        return cleaned_data


class CharacterAbilityInline(admin.TabularInline):
    model = CharacterAbility
    extra = 0
    autocomplete_fields = ["ability"]


class InventoryInline(admin.TabularInline):
    model = Inventory
    extra = 0
    autocomplete_fields = ["item"]


class UniqueSkillInline(admin.StackedInline):
    model = UniqueSkill
    fk_name = "character"
    extra = 0
    max_num = 1
    autocomplete_fields = ["ability"]


class ActiveEffectInline(admin.TabularInline):
    model = ActiveEffect
    extra = 0
    autocomplete_fields = ["effect"]


@admin.register(Character)
class CharacterAdmin(admin.ModelAdmin):
    form = CharacterAdminForm
    inlines = [CharacterAbilityInline, InventoryInline, ActiveEffectInline, UniqueSkillInline]
    list_display = ("name", "is_npc", "status", "template", "player", "race", "char_class", "level", "xp")
    list_filter = ("is_npc", "status", "campaigns", "char_class")
    search_fields = ["name", "background"]
    list_select_related = ["template", "player", "race", "char_class"]
    filter_horizontal = ["campaigns", "spells"]
    autocomplete_fields = ["template"]
    actions = ["make_npc_copy"]

    @admin.action(description="Make a copy of the selected NPC templates")
    def make_npc_copy(self, request, queryset):
        made = 0
        for character in queryset:
            try:
                character.make_copy()
                made += 1
            except ValueError as error:
                self.message_user(request, f"{character} skipped: {error}", level=messages.WARNING)
        self.message_user(request, f"{made} NPC copies made.")


@admin.register(UniqueSkill)
class UniqueSkillAdmin(admin.ModelAdmin):
    list_display = ("character", "name", "status", "updated_at")
    list_filter = ("status",)
    search_fields = ["character__name", "name", "description"]
    list_select_related = ["character"]
    autocomplete_fields = ["character", "ability"]


@admin.register(CharacterAbility)
class CharacterAbilityAdmin(admin.ModelAdmin):
    list_display = ("character", "ability", "uses_left", "max_uses", "recharge", "cooldown_left", "cooldown_rounds")
    list_filter = ("recharge",)
    search_fields = ["character__name", "ability__name"]
    list_select_related = ["character", "ability"]
    autocomplete_fields = ["character", "ability"]


@admin.register(Inventory)
class InventoryAdmin(admin.ModelAdmin):
    list_display = ("character", "item", "quantity")
    search_fields = ["character__name", "item__name"]
    list_select_related = ["character", "item"]
    autocomplete_fields = ["character", "item"]


@admin.register(ActiveEffect)
class ActiveEffectAdmin(admin.ModelAdmin):
    list_display = ("character", "effect", "remaining_turns")
    list_select_related = ["character", "effect"]
    autocomplete_fields = ["character", "effect"]


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ("number", "title", "campaign", "played_on", "xp_awarded", "published")
    list_filter = ("campaign", "published")
    search_fields = ["title", "body"]
    list_select_related = ["campaign"]
    date_hierarchy = "played_on"


@admin.register(TimelineEvent)
class TimelineEventAdmin(admin.ModelAdmin):
    list_display = ("title", "campaign", "kind", "session", "world_day", "hidden")
    list_filter = ("campaign", "kind", "hidden")
    search_fields = ["title", "description", "secret"]
    list_select_related = ["campaign", "session"]
    filter_horizontal = ["characters"]


@admin.register(Page)
class PageAdmin(admin.ModelAdmin):
    list_display = ("title", "campaign", "category", "parent", "revealed")
    list_filter = ("campaign", "category", "revealed")
    search_fields = ["title", "summary", "body", "secret"]
    list_select_related = ["campaign", "parent"]
    autocomplete_fields = ["parent"]


class QuestObjectiveInline(admin.TabularInline):
    """Objectives inside their quest."""
    model = QuestObjective
    extra = 1


@admin.register(Quest)
class QuestAdmin(admin.ModelAdmin):
    list_display = ("title", "campaign", "status", "giver", "hidden")
    list_filter = ("campaign", "status", "hidden")
    search_fields = ["title", "description", "secret", "objectives__text"]
    list_select_related = ["campaign", "giver"]
    inlines = [QuestObjectiveInline]


@admin.register(BoardCard)
class BoardCardAdmin(admin.ModelAdmin):
    list_display = ("title", "kind", "campaign", "author", "classified", "revealed", "crossed", "x", "y")
    list_filter = ("campaign", "classified", "revealed")
    search_fields = ["title", "text"]
    list_select_related = ["campaign", "author"]


@admin.register(BoardLink)
class BoardLinkAdmin(admin.ModelAdmin):
    list_display = ("card_a", "card_b")
    list_select_related = ["card_a", "card_b"]


class PostInline(admin.StackedInline):
    model = Post
    extra = 0
    fields = ["author", "as_character", "text", "roll", "whisper_to", "created_at"]


@admin.register(Scene)
class SceneAdmin(admin.ModelAdmin):
    list_display = ("title", "campaign", "location", "is_open")
    list_filter = ("campaign", "is_open")
    search_fields = ["title"]
    list_select_related = ["campaign", "location"]
    filter_horizontal = ["participants"]
    inlines = [PostInline]


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ("scene", "author", "as_character", "whisper_to", "created_at")
    list_filter = ("scene__campaign",)
    search_fields = ["text"]
    list_select_related = ["scene", "author", "as_character", "whisper_to"]


class MapLocationInline(admin.TabularInline):
    model = MapLocation
    extra = 0


class WeatherRegionInline(admin.StackedInline):
    model = WeatherRegion
    extra = 0
    fields = ["name", "location", "points", "place_name", "latitude", "longitude"]


@admin.register(Map)
class MapAdmin(admin.ModelAdmin):
    list_display = ("name", "campaign", "order")
    list_filter = ("campaign",)
    list_select_related = ["campaign"]
    inlines = [MapLocationInline, WeatherRegionInline]


@admin.register(WeatherRegion)
class WeatherRegionAdmin(admin.ModelAdmin):
    list_display = ("name", "map", "place_name", "latitude", "longitude", "weather_at")
    list_filter = ("map__campaign",)
    search_fields = ["name", "place_name"]
    list_select_related = ["map", "location"]
    readonly_fields = ["weather", "weather_at"]


class RumourInline(admin.TabularInline):
    model = Rumour
    extra = 0
    fields = ["text", "published", "truth"]
    show_change_link = True


@admin.register(RumourCategory)
class RumourCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "campaign", "color", "order")
    list_filter = ("campaign",)
    list_select_related = ["campaign"]
    inlines = [RumourInline]


@admin.register(Rumour)
class RumourAdmin(admin.ModelAdmin):
    list_display = ("__str__", "campaign", "category", "truth", "published", "session")
    list_filter = ("campaign", "category", "truth", "published")
    search_fields = ["text", "source"]
    list_select_related = ["campaign", "category", "session"]
    autocomplete_fields = ["source_page", "place"]


@admin.register(MapLocation)
class MapLocationAdmin(admin.ModelAdmin):
    list_display = ("name", "map", "status", "importance", "x", "y")
    list_filter = ("map__campaign", "status", "importance")
    search_fields = ["name"]
    list_select_related = ["map"]


@admin.register(PartyMove)
class PartyMoveAdmin(admin.ModelAdmin):
    list_display = ("map", "order", "location", "session")
    list_filter = ("map",)
    list_select_related = ["map", "location", "session"]


class CombatantInline(admin.TabularInline):
    model = Combatant
    extra = 0
    autocomplete_fields = ["character"]


@admin.register(Encounter)
class EncounterAdmin(admin.ModelAdmin):
    list_display = ("campaign", "active", "round", "turn_index")
    list_filter = ("campaign", "active")
    list_select_related = ["campaign"]
    inlines = [CombatantInline]
