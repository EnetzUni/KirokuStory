"""Mapa: the campaign's maps, their locations, the party's route and the weather layer."""
import zoneinfo

from django.db.models import Max
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from . import weather
from .ajax import is_ajax, respond
from .forms import LocationForm, MapForm, WeatherRegionForm
from .mapping import clean_path, map_data, parse_path
from .models import Map, MapLocation, Page, PartyMove, Session, TimelineEvent, WeatherRegion
from .permissions import CampaignMemberMixin, campaign_member, master_only
from .wiki import first_sentence


def map_context(request, campaign, is_master, map_id=None, location_id=None, draft=None, editing=None, weather_mode=None):
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
    # The other end of the slider: the route between two sessions (from 1 by default).
    since = request.GET.get("desde")
    since = max(1, min(int(since), until)) if since and since.isdigit() else 1
    md = map_data(current, is_master, until_session=until, since_session=since)
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
    # Links on the map keep the chosen sessions.
    route_query = "".join(f"&{name}={request.GET[name]}" for name in ("desde", "hasta")
                          if request.GET.get(name, "").isdigit())
    # The weather layer: «1» shows it (without JavaScript; with it, mesa.js loads it), «editar»
    # is the master linking regions to real places (it shows the layer and draws regions).
    weather_mode = weather_mode if weather_mode is not None else request.GET.get("tiempo", "")
    editing_weather = is_master and weather_mode == "editar" and bool(current.image)
    placing = is_master and not editing_weather and (request.GET.get("colocar") == "1" or draft is not None)

    def pct(number):
        return round((number - 1) * 100 / (max_session - 1), 2) if max_session > 1 else 0
    return {
        "maps": maps, "current_map": current, "md": md, "detail": detail, "selected": selected,
        "max_session": max_session, "until": until, "since": since,
        "until_session": next((s for s in sessions if s.number == until), None),
        "since_session": next((s for s in sessions if s.number == since), None),
        "since_pct": pct(since), "until_pct": pct(until),
        "placing": placing, "draft": draft,
        # The master shapes the way followed between stops (not while placing a location).
        "editing_route": is_master and not placing and not editing_weather and bool(md["segments"])
                         and (editing if editing is not None else request.GET.get("recorrido") == "1"),
        "weather": weather_layer(campaign, current, is_master) if weather_mode in ("1", "editar") and current.image else None,
        "weather_on": weather_mode == "1", "editing_weather": editing_weather,
        "weather_form": WeatherRegionForm(map=current) if editing_weather else None,
        "map_form": MapForm(instance=current, campaign=campaign) if is_master else None,
        "location_form": LocationForm(initial={"status": "conocido", "x": 50, "y": 50}),
        "places": md["locations"], "route_query": route_query,
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


def _map_url(campaign, map_id, location_id=None, editing=False, weather_mode=""):
    url = reverse("map", args=[campaign.pk]) + f"?mapa={map_id}"
    return (url + (f"&lugar={location_id}" if location_id else "") + ("&recorrido=1" if editing else "")
            + (f"&tiempo={weather_mode}" if weather_mode else ""))


def _map_response(request, campaign, map_id, location_id=None, msg="", error=False, editing=None, weather_mode=None):
    context = {"campaign": campaign, "is_master": request.is_master,
               **map_context(request, campaign, request.is_master, map_id=map_id, location_id=location_id, editing=editing,
                             weather_mode=weather_mode)}
    return respond(request, redirect_to=_map_url(campaign, map_id, location_id, bool(editing), weather_mode or ""),
                   template="partials/map_section.html", context=context, target="#mapa-seccion", msg=msg, error=error)


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
    elif action == "borrar":
        # The party's stops at this place go with it, so do the stretches that lead to it.
        # Its page in the World stays (unlinked from the map): it may have notes worth keeping.
        name, map_id, stops = location.name, location.map_id, location.moves.count()
        location.delete()
        msg = f"{name} borrado del mapa."
        if stops:
            msg += f" También {'la parada' if stops == 1 else f'las {stops} paradas'} del camino que llegaban allí."
        return _map_response(request, campaign, map_id, msg=msg + " Su página del Mundo se conserva.")
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


@require_POST
@campaign_member
def move_path(request, campaign, pk):
    """The route editor saves the waypoints of one stretch (from the previous stop to this one)."""
    master_only(request)
    move = get_object_or_404(PartyMove, pk=pk, map__campaign=campaign)
    path = parse_path(request.POST.get("puntos", ""))
    if path is None:
        return respond(request, redirect_to=_map_url(campaign, move.map_id), msg="Ese camino no es válido.", error=True)
    move.path = path
    move.save(update_fields=["path"])
    return respond(request, redirect_to=_map_url(campaign, move.map_id), msg="Camino guardado.")


@require_POST
@campaign_member
def move_delete(request, campaign, pk):
    """Remove one stretch of the route (added by mistake): the stop it arrives at goes,
    and the route goes straight from the stop before to the one after."""
    master_only(request)
    move = get_object_or_404(PartyMove.objects.select_related("location", "map"), pk=pk, map__campaign=campaign)
    moves = list(move.map.moves.select_related("location"))
    index = next(i for i, m in enumerate(moves) if m.pk == move.pk)
    previous = moves[index - 1] if index else None
    move.delete()
    if previous is None:
        msg = f"Quitada la salida del camino: {move.location.name}."
    else:
        msg = f"Tramo {previous.location.name} → {move.location.name} quitado del camino."
    return _map_response(request, campaign, move.map_id, msg=msg, editing=True)


# ---------------------------------------------------------------------------
# Weather layer: regions of the map linked to real places (weather.py)
# ---------------------------------------------------------------------------

REGION_RADIUS = 6  # % of the map's width, for a region around a location's pin
MAP_RATIO = 1.6  # the map is drawn 1.6 times wider than tall (.mapa aspect-ratio)


def visible_regions(map_, is_master):
    """Players do not see the regions of locations still hidden from them."""
    regions = list(map_.weather_regions.select_related("location"))
    if is_master:
        return regions
    return [r for r in regions if r.location is None or r.location.status != MapLocation.Status.OCULTO]


def weather_layer(campaign, map_, is_master):
    """What the layer draws: each region's shape (shaded at night), a badge with its weather
    and the campaign's clock. Fetches the stale weather first (weather.refresh)."""
    regions = visible_regions(map_, is_master)
    api_ok = weather.refresh(regions)
    now = timezone.now()
    items = []
    for region in regions:
        info = weather.describe(region, now)
        if region.location_id:
            x, y, ry = region.location.x, region.location.y, round(REGION_RADIUS * MAP_RATIO, 2)
            shape = {"ellipse": True, "cx": x, "cy": y, "rx": REGION_RADIUS, "ry": ry}
            badge = (x, y - ry * 0.7)  # above the pin, inside the region
        else:
            points = clean_path(region.points)
            shape = {"ellipse": False, "points": " ".join(f"{x:g},{y:g}" for x, y in points)}
            # Near its top, centred: the middle of a region is often where its pins are.
            badge = (sum(x for x, _ in points) / len(points), min(y for _, y in points) + 4)
        items.append({"region": region, "shape": shape, "info": info, "night": bool(info and not info["is_day"]),
                      "x": round(min(96, max(4, badge[0])), 2), "y": round(min(96, max(4, badge[1])), 2)})
    fetched = [r.weather_at for r in regions if r.weather_at]
    return {"regions": items, "api_ok": api_ok, "updated": max(fetched) if fetched else None,
            # One clock for everybody: the campaign's time zone, not each real place's.
            # Formatted here: the template's |time filter would convert it to the site's time zone.
            "clock": timezone.localtime(now, zoneinfo.ZoneInfo(campaign.time_zone)).strftime("%H:%M"),
            "time_zone": campaign.time_zone}


@campaign_member
def map_weather(request, campaign, map_id):
    """The weather layer of a map, for mesa.js: the layer drawn on the map and its caption."""
    map_ = get_object_or_404(Map, pk=map_id, campaign=campaign)
    if not is_ajax(request):
        return redirect(_map_url(campaign, map_.pk, weather_mode="1"))
    context = {"campaign": campaign, "is_master": request.is_master, "weather": weather_layer(campaign, map_, request.is_master)}
    return JsonResponse({"layer": render_to_string("partials/weather_layer.html", context, request=request),
                         "caption": render_to_string("partials/weather_caption.html", context, request=request)})


@require_POST
@campaign_member
def weather_region_add(request, campaign, map_id):
    """The master links a location of the map, or a region drawn on it, to a real place."""
    master_only(request)
    map_ = get_object_or_404(Map, pk=map_id, campaign=campaign)
    form = WeatherRegionForm(request.POST, map=map_)
    if not form.is_valid():
        errors = " ".join(m for e in form.errors.values() for m in e)
        return _map_response(request, campaign, map_.pk, msg=errors, error=True, weather_mode="editar")
    data = form.cleaned_data
    region = WeatherRegion(map=map_, name=data["name"].strip(), location=data.get("location"),
                           points=[] if data.get("location") else data["points"], place_name=data["place_name"],
                           latitude=data["latitude"], longitude=data["longitude"])
    region.full_clean()
    region.save()
    return _map_response(request, campaign, map_.pk, weather_mode="editar",
                         msg=f"{region.name} enlazada con {region.place_name} ({region.latitude:.2f}, {region.longitude:.2f}).")


@require_POST
@campaign_member
def weather_region_delete(request, campaign, pk):
    master_only(request)
    region = get_object_or_404(WeatherRegion, pk=pk, map__campaign=campaign)
    region.delete()
    return _map_response(request, campaign, region.map_id, weather_mode="editar",
                         msg=f"{region.name} ya no está enlazada con {region.place_name}.")


@campaign_member
def weather_place_search(request, campaign):
    """Real places for the master's search box (Open-Meteo's geocoding), as JSON."""
    master_only(request)
    try:
        return JsonResponse({"results": weather.search_places(request.GET.get("q", ""))})
    except weather.WeatherUnavailable:
        return JsonResponse({"results": [], "error": "Ahora no se puede buscar. Escribe las coordenadas («43.26, -2.93»)."})

