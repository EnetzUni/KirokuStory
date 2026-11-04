from django.shortcuts import render
from django.views.generic import DetailView, ListView

from .models import Campaign, Character, CharacterClass, Item, Race, Spell


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


# Races

class RaceListView(ListView):
    model = Race
    context_object_name = "races"
    template_name = "race_list.html"


class RaceDetailView(DetailView):
    model = Race
    context_object_name = "race"
    template_name = "race_detail.html"


# Character classes

class CharacterClassListView(ListView):
    model = CharacterClass
    context_object_name = "character_classes"
    template_name = "character_class_list.html"


class CharacterClassDetailView(DetailView):
    model = CharacterClass
    context_object_name = "character_class"
    template_name = "character_class_detail.html"


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
