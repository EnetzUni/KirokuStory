from django.contrib.auth import views as auth_views
from django.urls import path

from . import views, views_characters, views_diary, views_home, views_map, views_rumours, views_setup, views_turns, views_world

campaign = "c/<int:campaign_id>/"

urlpatterns = [
    path("", views_home.start, name="start"),
    path("entrar/", auth_views.LoginView.as_view(template_name="registration/login.html", redirect_authenticated_user=True), name="login"),
    path("salir/", auth_views.LogoutView.as_view(), name="logout"),

    # Asistente de campaña: create a campaign and fill it tab by tab
    path("campanas/nueva/", views_setup.campaign_new, name="campaign_new"),
    path(campaign + "asistente/", views_setup.campaign_setup, name="campaign_setup"),

    # Inicio
    path(campaign, views_home.HomeView.as_view(), name="home"),
    path(campaign + "reglamento/", views_home.campaign_ruleset, name="campaign_ruleset"),

    # Diario
    path(campaign + "diario/", views_diary.AdventureListView.as_view(), name="diary"),
    path(campaign + "diario/sesion/nueva/", views_diary.SessionCreateView.as_view(), name="diary_session_new"),
    path(campaign + "diario/sesion/<int:pk>/editar/", views_diary.SessionUpdateView.as_view(), name="diary_session_edit"),
    path(campaign + "diario/linea/", views_diary.TimelineView.as_view(), name="diary_timeline"),
    path(campaign + "diario/linea/suceso/", views_diary.event_add, name="diary_event_add"),
    path(campaign + "diario/linea/suceso/<int:pk>/visibilidad/", views_diary.event_toggle, name="diary_event_toggle"),
    path(campaign + "diario/pizarra/", views_diary.BoardView.as_view(), name="diary_board"),
    path(campaign + "diario/pizarra/nota/", views_diary.card_add, name="diary_card_add"),
    path(campaign + "diario/pizarra/nota/<int:pk>/", views_diary.card_action, name="diary_card_action"),
    path(campaign + "diario/escenas/", views_diary.SceneView.as_view(), name="diary_scenes"),
    path(campaign + "diario/escenas/nueva/", views_diary.SceneCreateView.as_view(), name="diary_scene_new"),
    path(campaign + "diario/escenas/<int:pk>/", views_diary.SceneView.as_view(), name="diary_scene"),
    path(campaign + "diario/escenas/<int:pk>/publicar/", views_diary.post_add, name="diary_post"),
    path(campaign + "diario/escenas/<int:pk>/estado/", views_diary.scene_toggle, name="diary_scene_toggle"),
    # «Misiones» are now the adventures; the old address takes you there.
    path(campaign + "diario/misiones/", views_diary.OldQuestsRedirect.as_view(), name="diary_quests"),
    path(campaign + "diario/aventuras/nueva/", views_diary.QuestCreateView.as_view(), name="diary_quest_new"),
    path(campaign + "diario/aventuras/<int:pk>/editar/", views_diary.QuestUpdateView.as_view(), name="diary_quest_edit"),
    path(campaign + "diario/aventuras/objetivo/<int:pk>/", views_diary.objective_toggle, name="diary_objective_toggle"),

    # Mundo
    path(campaign + "mundo/", views_world.WorldIndexView.as_view(), name="world"),
    path(campaign + "mundo/nueva/", views_world.PageCreateView.as_view(), name="world_new"),
    path(campaign + "mundo/<slug:slug>/", views_world.PageDetailView.as_view(), name="world_page"),
    path(campaign + "mundo/<slug:slug>/editar/", views_world.PageUpdateView.as_view(), name="world_edit"),
    path(campaign + "mundo/<slug:slug>/visibilidad/", views_world.page_toggle, name="world_toggle"),
    path(campaign + "mundo/<slug:slug>/resumen/", views_world.page_preview, name="world_preview"),

    # Rumores
    path(campaign + "rumores/", views_rumours.RumourListView.as_view(), name="rumours"),
    path(campaign + "rumores/nuevo/", views_rumours.RumourCreateView.as_view(), name="rumours_new"),
    path(campaign + "rumores/<int:pk>/editar/", views_rumours.RumourUpdateView.as_view(), name="rumours_edit"),
    path(campaign + "rumores/<int:pk>/accion/", views_rumours.rumour_action, name="rumours_action"),
    path(campaign + "rumores/categorias/", views_rumours.rumour_category, name="rumours_category"),

    # Mapa
    path(campaign + "mapa/", views_map.MapView.as_view(), name="map"),
    path(campaign + "mapa/nuevo/", views_map.map_new, name="map_new"),
    path(campaign + "mapa/<int:map_id>/editar/", views_map.map_edit, name="map_edit"),
    path(campaign + "mapa/<int:map_id>/lugar/", views_map.location_add, name="map_location_add"),
    path(campaign + "mapa/lugar/<int:pk>/", views_map.location_action, name="map_location_action"),
    path(campaign + "mapa/tramo/<int:pk>/", views_map.move_path, name="map_move_path"),
    path(campaign + "mapa/tramo/<int:pk>/quitar/", views_map.move_delete, name="map_move_delete"),
    path(campaign + "mapa/<int:map_id>/tiempo/", views_map.map_weather, name="map_weather"),
    path(campaign + "mapa/<int:map_id>/tiempo/region/", views_map.weather_region_add, name="map_weather_add"),
    path(campaign + "mapa/tiempo/region/<int:pk>/quitar/", views_map.weather_region_delete, name="map_weather_delete"),
    path(campaign + "mapa/tiempo/buscar/", views_map.weather_place_search, name="map_weather_search"),

    # Personajes
    path(campaign + "personajes/", views_characters.CharacterListView.as_view(), name="characters"),
    path(campaign + "personajes/crear/", views_characters.creator, name="creator"),
    path(campaign + "personajes/crear-pnj/", views_characters.npc_creator, name="npc_creator"),
    path(campaign + "personajes/xp/", views_characters.party_xp, name="party_xp"),
    path(campaign + "personajes/habilidad-unica/<int:pk>/", views_characters.unique_skill_review, name="unique_skill_review"),
    path(campaign + "personajes/<int:pk>/", views_characters.SheetView.as_view(), name="sheet"),
    path(campaign + "personajes/<int:pk>/accion/", views_characters.sheet_action, name="sheet_action"),
    path(campaign + "personajes/<int:pk>/vista/", views_characters.sheet_view_choice, name="sheet_view"),
    path(campaign + "personajes/<int:pk>/subir-nivel/", views_characters.level_up, name="level_up"),
    path(campaign + "personajes/<int:pk>/editar/", views_characters.character_edit, name="character_edit"),
    path(campaign + "personajes/<int:pk>/revision/", views_characters.character_review, name="character_review"),
    path(campaign + "personajes/<int:pk>/copiar/", views_characters.character_copy, name="character_copy"),

    # Turnos
    path(campaign + "turnos/", views_turns.TurnsView.as_view(), name="turns"),
    path(campaign + "turnos/accion/", views_turns.turns_action, name="turns_action"),

    # Reglas: the SRD catalog (pages from E3)
    path("reglas/", views.index, name="index"),
    path("reglas/campanas/", views.CampaignListView.as_view(), name="campaign_list"),
    path("reglas/campanas/<int:pk>/", views.CampaignDetailView.as_view(), name="campaign_detail"),
    path("reglas/personajes/", views.CharacterListView.as_view(), name="character_list"),
    path("reglas/personajes/<int:pk>/", views.CharacterDetailView.as_view(), name="character_detail"),
    path("reglas/razas/", views.RaceListView.as_view(), name="race_list"),
    path("reglas/razas/<int:pk>/", views.RaceDetailView.as_view(), name="race_detail"),
    path("reglas/subrazas/", views.SubraceListView.as_view(), name="subrace_list"),
    path("reglas/subrazas/<int:pk>/", views.SubraceDetailView.as_view(), name="subrace_detail"),
    path("reglas/clases/", views.CharacterClassListView.as_view(), name="character_class_list"),
    path("reglas/clases/<int:pk>/", views.CharacterClassDetailView.as_view(), name="character_class_detail"),
    path("reglas/subclases/", views.SubclassListView.as_view(), name="subclass_list"),
    path("reglas/subclases/<int:pk>/", views.SubclassDetailView.as_view(), name="subclass_detail"),
    path("reglas/conjuros/", views.SpellListView.as_view(), name="spell_list"),
    path("reglas/conjuros/<int:pk>/", views.SpellDetailView.as_view(), name="spell_detail"),
    path("reglas/objetos/", views.ItemListView.as_view(), name="item_list"),
    path("reglas/objetos/<int:pk>/", views.ItemDetailView.as_view(), name="item_detail"),
]
