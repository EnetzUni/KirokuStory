from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin

from .models import (
    Ability,
    ActiveEffect,
    Armor,
    ArmorDamage,
    ArmorType,
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

# UserAdmin hashes the password and shows the proper user forms.
admin.site.register(User, UserAdmin)

admin.site.register(Campaign)


class CharacterAdminForm(forms.ModelForm):
    class Meta:
        model = Character
        fields = "__all__"

    def clean(self):
        cleaned_data = super().clean()
        # An NPC with a unique ability cannot have copies. The abilities chosen in
        # the form are only saved after the model, so this is checked here.
        abilities = cleaned_data.get("abilities") or []
        if self.instance.pk and self.instance.copies.exists() and any(a.unique for a in abilities):
            self.add_error(
                "abilities",
                "This NPC template has copies, so it cannot have a unique ability.",
            )
        return cleaned_data


@admin.register(Character)
class CharacterAdmin(admin.ModelAdmin):
    form = CharacterAdminForm
    list_display = ("name", "is_npc", "template", "player")
    list_filter = ("is_npc",)
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


admin.site.register(Race)
admin.site.register(CharacterClass)
admin.site.register(Ability)
admin.site.register(RaceAbility)
admin.site.register(ClassAbility)
admin.site.register(Spell)
admin.site.register(Component)
admin.site.register(Attack)
admin.site.register(DiceRoll)
admin.site.register(ArmorDamage)
admin.site.register(Effect)
admin.site.register(ActiveEffect)


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


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ("name", "template", "price")
    list_filter = (IsTemplateFilter,)
    actions = ["make_item_copy"]

    @admin.action(description="Make a copy of the selected items")
    def make_item_copy(self, request, queryset):
        for item in queryset:
            item.make_copy()
        self.message_user(
            request,
            f"{len(queryset)} item copies made. Add them to a character's inventory from Inventories.",
        )


admin.site.register(Inventory)
admin.site.register(Weapon)
admin.site.register(Armor)
admin.site.register(ArmorType)
