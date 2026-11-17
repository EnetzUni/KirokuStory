"""Header data: the user's campaigns, the current one, the role and the menu."""
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop

from .permissions import user_campaigns
from .services import active_encounter, current_combatant, scenes_awaiting

SECTIONS = [
    ("home", gettext_noop("Inicio"), "home"),
    ("diary", gettext_noop("Diario"), "diary"),
    ("world", gettext_noop("Mundo"), "world"),
    ("map", gettext_noop("Mapa"), "map"),
    ("characters", gettext_noop("Personajes"), "characters"),
    ("turns", gettext_noop("Turnos"), "turns"),
]
# url_name prefix -> section
SECTION_OF = {"home": "home", "diary": "diary", "world": "world", "map": "map",
              "characters": "characters", "sheet": "characters", "creator": "characters", "turns": "turns"}
STATUS_TEXT = {"activa": gettext_noop("Campaña activa"), "pausada": gettext_noop("Campaña en pausa"), "terminada": gettext_noop("Campaña terminada")}


def campaign_nav(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    campaigns = list(user_campaigns(user).prefetch_related("masters"))
    match = getattr(request, "resolver_match", None)
    campaign_id = match.kwargs.get("campaign_id") if match else None
    current = next((c for c in campaigns if c.pk == campaign_id), None)
    context = {"nav_campaigns": [
        {"campaign": c, "is_master": user in c.masters.all(), "status": _(STATUS_TEXT[c.status])} for c in campaigns
    ]}
    if current is None:
        return context
    masters = list(current.masters.all())
    is_master = user in masters
    if is_master:
        others = [m.get_short_name() or m.username for m in masters if m != user]
        role = _("Diriges") + (" " + _("con") + " " + ", ".join(others) if others else "")
    else:
        role = _("Máster") + ": " + ", ".join(m.get_short_name() or m.username for m in masters)
    url_name = (match.url_name or "").split("_")[0]
    section = SECTION_OF.get(url_name, "")
    awaiting = len(scenes_awaiting(user, current, is_master))
    encounter = active_encounter(current)
    actor = current_combatant(encounter)
    my_turn = bool(actor and actor.character_id and actor.character.player_id == user.pk)
    nav = []
    for key, label, url in SECTIONS:
        badge = ""
        if key == "diary" and awaiting:
            badge = str(awaiting)
        if key == "turns" and encounter:
            badge = _("Te toca") if my_turn else f"R{encounter.round}"
        nav.append({"key": key, "label": _(label), "url": reverse(url, args=[current.pk]), "current": key == section, "badge": badge})
    context.update(nav_campaign=current, nav_is_master=is_master, nav_role=role, nav_items=nav,
                   nav_status=_(STATUS_TEXT[current.status]))
    return context
