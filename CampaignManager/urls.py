from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),

    path("campaigns/", views.CampaignListView.as_view(), name="campaign_list"),
    path("campaigns/<int:pk>/", views.CampaignDetailView.as_view(), name="campaign_detail"),

    path("characters/", views.CharacterListView.as_view(), name="character_list"),
    path("characters/<int:pk>/", views.CharacterDetailView.as_view(), name="character_detail"),

    path("races/", views.RaceListView.as_view(), name="race_list"),
    path("races/<int:pk>/", views.RaceDetailView.as_view(), name="race_detail"),

    path("classes/", views.CharacterClassListView.as_view(), name="character_class_list"),
    path("classes/<int:pk>/", views.CharacterClassDetailView.as_view(), name="character_class_detail"),

    path("spells/", views.SpellListView.as_view(), name="spell_list"),
    path("spells/<int:pk>/", views.SpellDetailView.as_view(), name="spell_detail"),

    path("items/", views.ItemListView.as_view(), name="item_list"),
    path("items/<int:pk>/", views.ItemDetailView.as_view(), name="item_detail"),
]
