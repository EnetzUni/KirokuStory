from django.shortcuts import render
from django.views.generic import DetailView, ListView

from .models import Campaign, Character, CharacterClass, Item, Race, Spell, Subclass, Subrace, Weapon


def index(request):
    return render(request, "index.html")


# Campaigns

class CampaignListView(ListView):
    model = Campaign
    context_object_name = "campaigns"
    template_name = "campaign_list.html"


class CampaignDetailView(DetailView):
    model = Campaign
    context_object_name = "campaign"
    template_name = "campaign_detail.html"


# Characters

class CharacterListView(ListView):
    model = Character
    context_object_name = "characters"
    template_name = "character_list.html"


class CharacterDetailView(DetailView):
    model = Character
    context_object_name = "character"
    template_name = "character_detail.html"

    def get_context_data(self, **kwargs):
        # The weapons the character carries, with to hit and damage worked out for them.
        context = super().get_context_data(**kwargs)
        character = self.object
        context["weapons"] = [
            {
                "weapon": weapon,
                "to_hit": f"{weapon.to_hit_bonus(character):+d}",
                "attacks": [(attack, attack.damage_text(character)) for attack in weapon.attacks.all()],
            }
            for weapon in Weapon.objects.filter(item__inventory__character=character).select_related("item")
        ]
        return context


# Races

class RaceListView(ListView):
    model = Race
    context_object_name = "races"
    template_name = "race_list.html"


class RaceDetailView(DetailView):
    model = Race
    context_object_name = "race"
    template_name = "race_detail.html"


# Subraces

class SubraceListView(ListView):
    model = Subrace
    context_object_name = "subraces"
    template_name = "subrace_list.html"


class SubraceDetailView(DetailView):
    model = Subrace
    context_object_name = "subrace"
    template_name = "subrace_detail.html"


# Character classes

class CharacterClassListView(ListView):
    model = CharacterClass
    context_object_name = "character_classes"
    template_name = "character_class_list.html"


class CharacterClassDetailView(DetailView):
    model = CharacterClass
    context_object_name = "character_class"
    template_name = "character_class_detail.html"


# Subclasses

class SubclassListView(ListView):
    model = Subclass
    context_object_name = "subclasses"
    template_name = "subclass_list.html"


class SubclassDetailView(DetailView):
    model = Subclass
    context_object_name = "subclass"
    template_name = "subclass_detail.html"


# Spells

class SpellListView(ListView):
    model = Spell
    context_object_name = "spells"
    template_name = "spell_list.html"


class SpellDetailView(DetailView):
    model = Spell
    context_object_name = "spell"
    template_name = "spell_detail.html"


# Items

class ItemListView(ListView):
    model = Item
    context_object_name = "items"
    template_name = "item_list.html"


class ItemDetailView(DetailView):
    model = Item
    context_object_name = "item"
    template_name = "item_detail.html"
