"""Mapa: the campaign's maps, their locations and the party's route."""
from django.db.models import Max
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from .ajax import is_ajax, respond
from .forms import LocationForm, MapForm
from .mapping import map_data
from .models import Map, MapLocation, Page, PartyMove, Session, TimelineEvent
from .permissions import CampaignMemberMixin, campaign_member, master_only
from .wiki import first_sentence


def map_context(request, campaign, is_master, map_id=None, location_id=None, draft=None):
    user = request.user
    maps = list(campaign.maps.all())
    if not maps:
        return {"maps": [], "current_map": None}
    map_id = map_id or request.GET.get("mapa")
    current = next((m for m in maps if str(m.pk) == str(map_id)), maps[0])
    sessions = list(Session.objects.visible_for(user, campaign).order_by("number"))
    max_session = sessions[-1].number if sessions else 1
    until = request.GET.get("hasta")
    until = max(1, min(int(until), max_session)) if until and until.isdigit() else max_session
    md = map_data(current, is_master, until_session=until)
    visible = {loc.pk: loc for loc in md["locations"]}
    location_id = location_id or request.GET.get("lugar")
    selected = visible.get(int(location_id)) if str(location_id or "").isdigit() else None
    if selected is None and draft is None:
        selected = md["party"] if md["party"] and md["party"].pk in visible else None
    detail = None
    if selected is not None:
        page = Page.objects.visible_for(user, campaign).filter(location=selected).first()
        visits = sorted({m.session.number for m in current.moves.filter(location=selected).select_related("session") if m.session})
        events = TimelineEvent.objects.visible_for(user, campaign).filter(location=selected).select_related("session")
        last = Session.objects.filter(campaign=campaign).order_by("-number").first()
        actions = []
        if is_master:
            if selected.status == MapLocation.Status.OCULTO:
                actions.append(("estado", "conocido", "Revelar a los jugadores"))
            if selected.status == MapLocation.Status.CONOCIDO:
                actions += [("estado", "visitado", "Marcar como visitado"), ("estado", "oculto", "Volver a ocultar")]
            if selected.status == MapLocation.Status.VISITADO:
                actions.append(("estado", "conocido", "Marcar como no visitado"))
            if md["party"] is None or md["party"].pk != selected.pk:
                actions.append(("grupo", "1", f"Mover al grupo aquí (sesión {last.number})" if last else "Mover al grupo aquí"))
        detail = {"location": selected, "page": page,
                  "description": (page.summary or first_sentence(page.body)) if page else "",
                  "visits": visits, "events": events, "actions": actions}
    query_session = request.GET.get("hasta", "")
    return {
        "maps": maps, "current_map": current, "md": md, "detail": detail, "selected": selected,
        "max_session": max_session, "until": until,
        "until_session": next((s for s in sessions if s.number == until), None),
        "placing": is_master and (request.GET.get("colocar") == "1" or draft is not None), "draft": draft,
        "map_form": MapForm(instance=current, campaign=campaign) if is_master else None,
        "location_form": LocationForm(initial={"status": "conocido", "x": 50, "y": 50}),
        "places": md["locations"], "query_session": query_session,
    }


class MapView(CampaignMemberMixin, TemplateView):
    template_name = "map.html"

    def get(self, request, *args, **kwargs):
        if is_ajax(request):
            context = {"campaign": self.campaign, "is_master": self.is_master,
                       **map_context(request, self.campaign, self.is_master)}
            return JsonResponse({"html": render_to_string("partials/map_section.html", context, request=request), "target": "#mapa-seccion"})
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(map_context(self.request, self.campaign, self.is_master))
        return context


def _map_url(campaign, map_id, location_id=None):
    url = reverse("map", args=[campaign.pk]) + f"?mapa={map_id}"
    return url + (f"&lugar={location_id}" if location_id else "")


def _map_response(request, campaign, map_id, location_id=None, msg="", error=False):
    context = {"campaign": campaign, "is_master": request.is_master,
               **map_context(request, campaign, request.is_master, map_id=map_id, location_id=location_id)}
    return respond(request, redirect_to=_map_url(campaign, map_id, location_id), template="partials/map_section.html",
                   context=context, target="#mapa-seccion", msg=msg, error=error)


@require_POST
@campaign_member
def map_new(request, campaign):
    master_only(request)
    count = campaign.maps.count()
    new = Map.objects.create(campaign=campaign, name=f"Mapa {count + 1}", order=count)
    return respond(request, redirect_to=_map_url(campaign, new.pk), msg="Mapa creado. Ponle nombre y sube su imagen.")


@require_POST
@campaign_member
def map_edit(request, campaign, map_id):
    master_only(request)
    map_ = get_object_or_404(Map, pk=map_id, campaign=campaign)
    form = MapForm(request.POST, request.FILES, instance=map_, campaign=campaign)
    if form.is_valid():
        form.save()
        return respond(request, redirect_to=_map_url(campaign, map_.pk), msg="Mapa guardado.")
    return respond(request, redirect_to=_map_url(campaign, map_.pk), msg="La imagen o el nombre no son válidos.", error=True)


@require_POST
@campaign_member
def location_add(request, campaign, map_id):
    """A new location where the master clicked; its page in the World is created too."""
    master_only(request)
    map_ = get_object_or_404(Map, pk=map_id, campaign=campaign)
    form = LocationForm(request.POST)
    if not form.is_valid():
        return _map_response(request, campaign, map_.pk, msg="Ponle nombre al lugar y una posición entre 0 y 100.", error=True)
    location = form.save(commit=False)
    location.map = map_
    location.save()
    Page.objects.create(campaign=campaign, category=Page.Category.LUGAR, title=location.name, location=location,
                        revealed=location.status != MapLocation.Status.OCULTO, summary=f"Lugar de {map_.name}.")
    return _map_response(request, campaign, map_.pk, location.pk, msg="Lugar guardado, con su página en el Mundo.")


@require_POST
@campaign_member
def location_action(request, campaign, pk):
    master_only(request)
    location = get_object_or_404(MapLocation, pk=pk, map__campaign=campaign)
    action = request.POST.get("accion")
    if action == "estado":
        status = request.POST.get("valor")
        if status not in MapLocation.Status.values:
            raise Http404
        location.status = status
        location.save(update_fields=["status"])
        # Its page follows: hidden locations have hidden pages.
        Page.objects.filter(location=location).update(revealed=status != MapLocation.Status.OCULTO)
        msg = f"{location.name}: {location.get_status_display().lower()}."
    elif action == "grupo":
        last = Session.objects.filter(campaign=campaign).order_by("-number").first()
        order = (location.map.moves.aggregate(m=Max("order"))["m"] or 0) + 1
        PartyMove.objects.create(map=location.map, location=location, session=last, order=order)
        location.status = MapLocation.Status.VISITADO
        location.save(update_fields=["status"])
        Page.objects.filter(location=location).update(revealed=True)
        msg = f"El grupo está ahora en {location.name}."
    else:
        raise Http404
    return _map_response(request, campaign, location.map_id, location.pk, msg=msg)
