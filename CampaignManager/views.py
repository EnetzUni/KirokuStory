"""Reglas: the SRD catalog pages (from E3).

Every page needs a logged-in user. Campaigns and characters only show what the
reader may see: their own campaigns and `visible_characters()`.
"""
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Prefetch
from django.http import Http404
from django.shortcuts import render
from django.views.generic import DetailView, ListView

from .models import CharacterClass, Inventory, Item, Race, Spell, Subclass, Subrace, Weapon
from .permissions import user_campaigns, visible_characters


@login_required
def index(request):
    return render(request, "index.html")


class HideBespokeMixin:
    """Races, classes and items a master made by hand for one character are not shown to
    players (they would give away an NPC); masters see them."""

    def get_queryset(self):
        queryset = super().get_queryset()
        if not self.request.user.dm_campaigns.exists():
            queryset = queryset.filter(bespoke=False)
        return queryset


class CatalogDetailView(LoginRequiredMixin, DetailView):
    """Detail page that lists the characters linked to the object, filtered to the visible ones."""
    character_filter = None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.character_filter:
            context["characters"] = visible_characters(self.request.user).filter(
                **{self.character_filter: self.object}).order_by("name")
        return context


# Campaigns

class CampaignListView(LoginRequiredMixin, ListView):
    context_object_name = "campaigns"
    template_name = "campaign_list.html"

    def get_queryset(self):
        return user_campaigns(self.request.user).order_by("name")


class CampaignDetailView(CatalogDetailView):
    context_object_name = "campaign"
    template_name = "campaign_detail.html"
    character_filter = "campaigns"

    def get_queryset(self):
        return user_campaigns(self.request.user).prefetch_related("masters", "players")


# Characters

class CharacterListView(LoginRequiredMixin, ListView):
    context_object_name = "characters"
    template_name = "character_list.html"

    def get_queryset(self):
        return visible_characters(self.request.user).select_related(
            "race", "subrace", "char_class", "subclass", "player").order_by("name")


class CharacterDetailView(LoginRequiredMixin, DetailView):
    context_object_name = "character"
    template_name = "character_detail.html"

    def get_queryset(self):
        return visible_characters(self.request.user).select_related("race", "subrace", "char_class", "subclass", "player", "template")

    def get_context_data(self, **kwargs):
        # The weapons the character carries, with to hit and damage worked out for them.
        context = super().get_context_data(**kwargs)
        character = self.object
        visible = visible_characters(self.request.user)
        context["weapons"] = [
            {
                "weapon": weapon,
                "to_hit": f"{weapon.to_hit_bonus(character):+d}",
                "attacks": [(attack, attack.damage_text(character)) for attack in weapon.attacks.prefetch_related("dice")],
            }
            for weapon in Weapon.objects.filter(item__inventory__character=character).select_related("item")
        ]
        context["campaigns"] = character.campaigns.filter(pk__in=user_campaigns(self.request.user))
        context["copies"] = visible.filter(template=character).order_by("name") if character.is_npc_template else []
        context["template_visible"] = bool(character.template_id) and visible.filter(pk=character.template_id).exists()
        return context


# Races

class RaceListView(LoginRequiredMixin, HideBespokeMixin, ListView):
    queryset = Race.objects.order_by("name")
    context_object_name = "races"
    template_name = "race_list.html"


class RaceDetailView(HideBespokeMixin, CatalogDetailView):
    model = Race
    context_object_name = "race"
    template_name = "race_detail.html"
    character_filter = "race"


# Subraces

class SubraceListView(LoginRequiredMixin, ListView):
    queryset = Subrace.objects.select_related("race").order_by("race__name", "name")
    context_object_name = "subraces"
    template_name = "subrace_list.html"


class SubraceDetailView(CatalogDetailView):
    model = Subrace
    context_object_name = "subrace"
    template_name = "subrace_detail.html"
    character_filter = "subrace"


# Character classes

class CharacterClassListView(LoginRequiredMixin, HideBespokeMixin, ListView):
    queryset = CharacterClass.objects.order_by("name")
    context_object_name = "character_classes"
    template_name = "character_class_list.html"


class CharacterClassDetailView(HideBespokeMixin, CatalogDetailView):
    model = CharacterClass
    context_object_name = "character_class"
    template_name = "character_class_detail.html"
    character_filter = "char_class"


# Subclasses

class SubclassListView(LoginRequiredMixin, ListView):
    queryset = Subclass.objects.select_related("char_class").order_by("char_class__name", "name")
    context_object_name = "subclasses"
    template_name = "subclass_list.html"


class SubclassDetailView(CatalogDetailView):
    model = Subclass
    context_object_name = "subclass"
    template_name = "subclass_detail.html"
    character_filter = "subclass"


# Spells

class SpellListView(LoginRequiredMixin, ListView):
    queryset = Spell.objects.order_by("level", "name")
    context_object_name = "spells"
    template_name = "spell_list.html"


class SpellDetailView(CatalogDetailView):
    model = Spell
    context_object_name = "spell"
    template_name = "spell_detail.html"
    character_filter = "spells"


# Items: the catalog lists the templates. A copy (a character's own weapon or
# armor) can be opened only when one of the visible characters carries it.

class ItemListView(LoginRequiredMixin, HideBespokeMixin, ListView):
    queryset = Item.objects.filter(template__isnull=True).select_related("weapon", "armor").order_by("name")
    context_object_name = "items"
    template_name = "item_list.html"


class ItemDetailView(LoginRequiredMixin, DetailView):
    model = Item
    context_object_name = "item"
    template_name = "item_detail.html"

    def get_object(self, queryset=None):
        item = super().get_object(queryset)
        if item.template_id and not Inventory.objects.filter(item=item, character__in=visible_characters(self.request.user)).exists():
            raise Http404("Ese objeto no es de ninguno de tus personajes.")
        # A master's own piece: only its copies carried by the reader's characters can be opened.
        if item.template_id is None and item.bespoke and not self.request.user.dm_campaigns.exists():
            raise Http404("Ese objeto no está en el catálogo.")
        return item

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        visible = visible_characters(self.request.user)
        carried = Inventory.objects.filter(character__in=visible).select_related("character")
        context["carriers"] = carried.filter(item=self.object)
        context["copies"] = (
            self.object.copies.filter(inventory__character__in=visible).distinct()
            .prefetch_related(Prefetch("inventory_set", queryset=carried, to_attr="visible_rows"))
        ) if not self.object.template_id else []
        return context

