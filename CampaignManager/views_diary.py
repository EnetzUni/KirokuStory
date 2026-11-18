"""Diario: aventuras (con sus sesiones y objetivos), línea del tiempo, pizarra y escenas de rol."""
import math
import re
import secrets
from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied, ValidationError
from django.forms import ImageField
from django.db import transaction
from django.db.models import Max, Prefetch
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, RedirectView, TemplateView, UpdateView
from django.utils.translation import gettext_noop

from .ajax import is_ajax, respond
from .forms import BoardCardForm, EventForm, ObjectiveFormSet, PostForm, QuestForm, SceneForm, SessionForm
from .models import (
    BoardCard, BoardLink, Character, Page, Post, Quest, QuestObjective, Scene, Session, TimelineEvent,
)
from .campaign_models import BOARD_CENTRE_X, BOARD_CENTRE_Y, BOARD_HEIGHT, BOARD_WIDTH, CARD_HEIGHT, CARD_WIDTH
from .permissions import CampaignMemberMixin, MasterRequiredMixin, campaign_member, master_only
from .services import scenes_awaiting
from .wiki import WikiResolver

TABS = [("diary", gettext_noop("Aventuras")), ("diary_timeline", gettext_noop("Línea del tiempo")), ("diary_board", gettext_noop("Pizarra")),
        ("diary_scenes", gettext_noop("Escenas de rol"))]


def diary_tabs(request, campaign, is_master, current):
    awaiting = len(scenes_awaiting(request.user, campaign, is_master))
    return [{"url": reverse(name, args=[campaign.pk]), "label": label, "current": name == current,
             "badge": awaiting if name == "diary_scenes" and awaiting else ""} for name, label in TABS]


class DiaryMixin(CampaignMemberMixin):
    tab = "diary"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["wiki"] = WikiResolver(self.campaign, self.request.user, self.is_master)
        context["diary_tabs"] = diary_tabs(self.request, self.campaign, self.is_master, self.tab)
        return context


def last_session(campaign):
    return Session.objects.filter(campaign=campaign).order_by("-number").first()


# ---------------------------------------------------------------------------
# Aventuras: each adventure with its objectives and the sessions it spans
# ---------------------------------------------------------------------------

def adventure_quests(campaign):
    return list(Quest.objects.of(campaign).select_related("giver", "start_session", "end_session")
                .prefetch_related("objectives"))


class AdventureListView(DiaryMixin, TemplateView):
    template_name = "diary_adventures.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        sessions = list(Session.objects.visible_for(self.request.user, self.campaign).order_by("number"))
        last_number = sessions[-1].number if sessions else 0
        quests = adventure_quests(self.campaign)
        # Newest first inside each group; adventures without a start session at the end.
        quests.sort(key=lambda q: (q.start_session is None, -(q.start_session.number if q.start_session else 0), -q.pk))
        covered, groups = set(), []
        for status, label in STATUS_GROUPS:
            items = []
            for quest in quests:
                if quest.status != status:
                    continue
                own = [s for s in sessions if quest.covers(s.number, last_number)]
                covered.update(s.pk for s in own)
                items.append({"q": quest_card(quest, self.is_master), "sessions": own})
            if items:
                groups.append({"label": label, "items": items})
        context.update(groups=groups, loose=[s for s in reversed(sessions) if s.pk not in covered])
        return context


class OldQuestsRedirect(RedirectView):
    """«Misiones» is now part of «Aventuras»."""

    def get_redirect_url(self, *args, **kwargs):
        return reverse("diary", args=[kwargs["campaign_id"]])


class SessionFormMixin:
    model = Session
    form_class = SessionForm
    template_name = "diary_session_form.html"

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "campaign": self.campaign}

    def get_queryset(self):
        return Session.objects.filter(campaign=self.campaign)

    def get_success_url(self):
        return reverse("diary", args=[self.campaign.pk]) + f"#sesion-{self.object.pk}"


class SessionCreateView(MasterRequiredMixin, SessionFormMixin, DiaryMixin, CreateView):
    def get_initial(self):
        last = last_session(self.campaign)
        return {"number": (last.number + 1) if last else 1, "world_day": self.campaign.world_day}


class SessionUpdateView(MasterRequiredMixin, SessionFormMixin, DiaryMixin, UpdateView):
    pass


# ---------------------------------------------------------------------------
# Línea del tiempo
# ---------------------------------------------------------------------------

SLOT = 160


EVENTS_HEIGHT = 296   # the events part of the timeline (cards above and below the axis)
LANE_ROW = 34         # one row of adventure bars under it


def _adventure_lane(quests, spans, sessions, last_number, future_right):
    """Bars for the adventures under the events: from the first to the last session each
    one covers (into «Próxima» while in progress), packed in rows so they do not overlap."""
    bars, row_ends = [], []
    for quest in sorted((q for q in quests if q.start_session), key=lambda q: (q.start_session.number, q.pk)):
        own = [s for s in sessions if s.pk in spans and quest.covers(s.number, last_number)]
        if not own:
            continue
        left = spans[own[0].pk][0] + 4
        open_ = quest.status == Quest.Status.ACTIVA
        right = future_right - 8 if open_ else spans[own[-1].pk][0] + spans[own[-1].pk][1] - 4
        row = next((i for i, end in enumerate(row_ends) if end < left), len(row_ends))
        if row == len(row_ends):
            row_ends.append(right)
        row_ends[row] = right
        first, last = own[0].number, (quest.end_session.number if quest.end_session else None)
        bars.append({"quest": quest, "left": left, "width": max(24, right - left), "top": EVENTS_HEIGHT + 14 + row * LANE_ROW,
                     "open": open_, "first": first, "last": last})
    return bars, len(row_ends)


def timeline_context(request, campaign, is_master, selected=None):
    user = request.user
    show_hidden = is_master and request.GET.get("ocultos") != "0"
    events = (TimelineEvent.objects.visible_for(user, campaign)
              .select_related("session", "location", "location__map").prefetch_related("characters"))
    if is_master and not show_hidden:
        events = events.filter(hidden=False)
    party = list(campaign.party)
    pc_filter = request.GET.get("pj")
    if pc_filter and pc_filter.isdigit():
        events = events.filter(characters__pk=int(pc_filter))
    sessions = list(Session.objects.visible_for(user, campaign).order_by("number"))
    last_number = sessions[-1].number if sessions else 0
    quests = [q for q in adventure_quests(campaign) if q.start_session]
    # One adventure: only its sessions and their events.
    adventure = next((q for q in quests if str(q.pk) == request.GET.get("aventura")), None)
    if adventure is not None:
        sessions = [s for s in sessions if adventure.covers(s.number, last_number)]
    visible_ids = {s.pk for s in sessions}
    by_session = {}
    for event in events:
        key = event.session_id if event.session_id in visible_ids else None
        if key is None and adventure is not None:
            continue
        by_session.setdefault(key, []).append(event)

    # Links keep the other filters.
    params = {"pj": pc_filter if pc_filter and pc_filter.isdigit() else "", "ocultos": "" if show_hidden or not is_master else "0",
              "aventura": str(adventure.pk) if adventure else ""}

    def link(**changes):
        query = {k: v for k, v in {**params, **changes}.items() if v}
        return "?" + urlencode(query) if query else "?"

    segments, marks, spans, x = [], [], {}, 0
    groups = ([(None, "Sin sesión", "Sucesos sin sesión")] if by_session.get(None) else []) + [
        (s.pk, f"S{s.number} · {s.title}", f"Sesión {s.number} · {s.title} · {s.played_on:%d/%m/%Y}") for s in sessions]
    for index, (key, label, full) in enumerate(groups):
        group_events = by_session.get(key, [])
        width = max(1, len(group_events)) * SLOT
        segments.append({"left": x, "width": width, "label": label, "full": full, "even": index % 2 == 1})
        if key is not None:
            spans[key] = (x, width)
        for j, event in enumerate(group_events):
            up = len(marks) % 2 == 0
            mx = x + SLOT // 2 + j * SLOT
            marks.append({"event": event, "x": mx, "card_left": mx - 72, "href": link(evento=event.pk),
                          "card_top": 36 if up else 180, "stem_top": 130 if up else 160})
        x += width
    bars, rows = _adventure_lane([adventure] if adventure else quests, spans, sessions, last_number, x + SLOT)
    height = EVENTS_HEIGHT + (rows * LANE_ROW + 24 if rows else 0)
    selected_id = selected or request.GET.get("evento")
    chosen = next((m for m in marks if str(m["event"].pk) == str(selected_id)), marks[-1] if marks else None)
    last = last_session(campaign)
    return {
        "segments": segments, "marks": marks, "chosen": chosen, "past_width": x, "height": height,
        "future_left": x, "future_label": f"Próxima · S{(last.number + 1) if last else 1}", "width": x + SLOT + 24,
        "party": party, "pc_filter": params["pj"], "show_hidden": show_hidden,
        "event_form": EventForm(campaign=campaign) if is_master else None, "add_session": last,
        "adventure": adventure, "bars": bars,
        "pc_links": [{"label": pc.name, "href": link(pj=pc.pk), "on": params["pj"] == str(pc.pk)} for pc in party],
        "all_pcs_link": link(pj=""), "hidden_link": link(ocultos="0" if show_hidden else ""),
        "adventure_links": [{"label": q.title, "href": link(aventura=q.pk, evento=""), "on": adventure == q, "status": q.status}
                            for q in quests],
        "all_adventures_link": link(aventura="", evento=""),
    }


class TimelineView(DiaryMixin, TemplateView):
    template_name = "diary_timeline.html"
    tab = "diary_timeline"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(timeline_context(self.request, self.campaign, self.is_master))
        return context


def _timeline_response(request, campaign, msg="", error=False, selected=None):
    context = {"campaign": campaign, "is_master": request.is_master,
               **timeline_context(request, campaign, request.is_master, selected=selected)}
    return respond(request, redirect_to=reverse("diary_timeline", args=[campaign.pk]),
                   template="partials/timeline.html", context=context, target="#linea", msg=msg, error=error)


@require_POST
@campaign_member
def event_add(request, campaign):
    master_only(request)
    form = EventForm(request.POST, campaign=campaign)
    session = last_session(campaign)
    if session is None:
        return _timeline_response(request, campaign, "Crea antes una sesión.", error=True)
    if not form.is_valid():
        return _timeline_response(request, campaign, "Falta el título del suceso.", error=True)
    event = form.save(commit=False)
    event.session, event.world_day = session, campaign.world_day
    event.save()
    return _timeline_response(request, campaign, f"Suceso añadido a la sesión {session.number}.", selected=event.pk)


@require_POST
@campaign_member
def event_toggle(request, campaign, pk):
    master_only(request)
    event = get_object_or_404(TimelineEvent, pk=pk, campaign=campaign)
    event.hidden = not event.hidden
    event.save(update_fields=["hidden"])
    msg = "Suceso oculto para los jugadores." if event.hidden else "Suceso revelado a los jugadores."
    return _timeline_response(request, campaign, msg, selected=event.pk)


# ---------------------------------------------------------------------------
# Pizarra
# ---------------------------------------------------------------------------

def board_context(request, campaign, is_master, selected=None, mode=None):
    user = request.user
    cards = list(BoardCard.objects.of(campaign).select_related("author", "page"))
    by_id = {c.pk: c for c in cards}
    links = [l for l in BoardLink.objects.filter(card_a__campaign=campaign) if l.card_a_id in by_id and l.card_b_id in by_id]
    selected_id = selected or request.GET.get("nota")
    chosen = by_id.get(int(selected_id)) if str(selected_id or "").isdigit() else None
    mode = mode or (request.GET.get("modo") if chosen else None)
    visible_pages = Page.objects.visible_for(user, campaign).order_by("title")
    visible_page_ids = set(visible_pages.values_list("pk", flat=True))

    def pin(card):
        return card.x + CARD_WIDTH // 2, card.y + 12

    items = []
    for index, card in enumerate(cards):
        locked = card.locked_for(is_master)
        stamp = ""
        if card.classified:
            stamp = "CLASIFICADO" if locked else ("DESCLASIFICADO" if card.revealed else "CLASIFICADO · SOLO TÚ")
        items.append({"card": card, "locked": locked, "rot": ((index * 37) % 7) - 3, "stamp": stamp,
                      # A person's photo, name and cross are never sent while the card is classified for this reader.
                      "person": card.is_person and not locked, "crossed": card.is_person and card.crossed and not locked,
                      "current": chosen is not None and card.pk == chosen.pk,
                      "can_target": mode == "unir" and chosen is not None and card.pk != chosen.pk})
    lines = []
    for link in links:
        (ax, ay), (bx, by) = pin(by_id[link.card_a_id]), pin(by_id[link.card_b_id])
        lines.append({"x": ax, "y": ay, "len": round(math.hypot(bx - ax, by - ay)),
                      "ang": round(math.degrees(math.atan2(by - ay, bx - ax)), 1)})
    detail = None
    if chosen is not None:
        locked = chosen.locked_for(is_master)
        n_links = sum(1 for l in links if chosen.pk in (l.card_a_id, l.card_b_id))
        detail = {"card": chosen, "locked": locked, "n_links": n_links,
                  "page": chosen.page if (not locked and chosen.page_id in visible_page_ids) else None,
                  "can_edit": not locked, "can_delete": is_master or chosen.author_id == user.pk,
                  "targets": [c for c in cards if c.pk != chosen.pk]}
    return {
        "board_items": items, "board_lines": lines, "board_pins": [pin(c) for c in cards], "detail": detail,
        "mode": mode if chosen is not None else None, "board_w": BOARD_WIDTH, "board_h": BOARD_HEIGHT,
        "card_max_x": BOARD_WIDTH - CARD_WIDTH,
        "card_max_y": BOARD_HEIGHT - (chosen.height if chosen is not None else CARD_HEIGHT),
        "card_form": BoardCardForm(campaign=campaign, is_master=is_master, visible_pages=visible_pages),
        "visible_pages": visible_pages,
        "hidden_count": sum(1 for c in cards if c.classified and not c.revealed),
    }


class BoardView(DiaryMixin, TemplateView):
    template_name = "diary_board.html"
    tab = "diary_board"

    def get(self, request, *args, **kwargs):
        if is_ajax(request):
            context = {"campaign": self.campaign, "is_master": self.is_master,
                       **board_context(request, self.campaign, self.is_master)}
            return JsonResponse({"html": render_to_string("partials/board.html", context, request=request), "target": "#pizarra"})
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(board_context(self.request, self.campaign, self.is_master))
        return context


def _board_response(request, campaign, msg="", error=False, selected=None, mode=None):
    context = {"campaign": campaign, "is_master": request.is_master,
               **board_context(request, campaign, request.is_master, selected=selected, mode=mode)}
    url = reverse("diary_board", args=[campaign.pk]) + (f"?nota={selected}" if selected else "")
    return respond(request, redirect_to=url, template="partials/board.html", context=context,
                   target="#pizarra", msg=msg, error=error)


@require_POST
@campaign_member
def card_add(request, campaign):
    visible_pages = Page.objects.visible_for(request.user, campaign)
    form = BoardCardForm(request.POST, request.FILES, campaign=campaign, is_master=request.is_master, visible_pages=visible_pages)
    if not form.is_valid():
        msg = "La foto tiene que ser una imagen (JPG, PNG o WebP)." if "image" in form.errors else "La nota necesita un título."
        return _board_response(request, campaign, msg, error=True)
    card = form.save(commit=False)
    # Where the board window was looking (sent by mesa.js); otherwise near the middle of the board.
    try:
        x, y = int(float(request.POST["x"])), int(float(request.POST["y"]))
    except (KeyError, ValueError):
        n = BoardCard.objects.of(campaign).count()
        x, y = BOARD_CENTRE_X - 40 + (n % 3) * 40, BOARD_CENTRE_Y - 45 + (n % 4) * 30
    card.author = request.user
    card.x = max(0, min(BOARD_WIDTH - CARD_WIDTH, x))
    card.y = max(0, min(BOARD_HEIGHT - card.height, y))
    card.save()
    what = "Persona clavada" if card.is_person else "Nota clavada"
    return _board_response(request, campaign, f"{what}. Pulsa «Mover» para colocarla donde quieras.", selected=card.pk)


@require_POST
@campaign_member
def card_action(request, campaign, pk):
    card = get_object_or_404(BoardCard, pk=pk, campaign=campaign)
    action = request.POST.get("accion")
    locked = card.locked_for(request.is_master)
    if action == "mover":
        if locked:
            raise PermissionDenied
        try:
            x, y = int(float(request.POST["x"])), int(float(request.POST["y"]))
        except (KeyError, ValueError):
            return _board_response(request, campaign, "Posición no válida.", error=True, selected=card.pk)
        card.x = max(0, min(BOARD_WIDTH - CARD_WIDTH, x))
        card.y = max(0, min(BOARD_HEIGHT - card.height, y))
        card.save(update_fields=["x", "y"])
        return _board_response(request, campaign, "Nota movida.", selected=card.pk)
    if action in ("tachar", "foto", "foto_quitar"):
        # People only: the red cross (dealt with) and their photo.
        if locked:
            raise PermissionDenied
        if not card.is_person:
            return _board_response(request, campaign, "Solo las personas tienen foto y cruz.", error=True, selected=card.pk)
        if action == "tachar":
            card.crossed = not card.crossed
            card.save(update_fields=["crossed"])
            msg = f"{card.title}: tachado con una cruz roja." if card.crossed else f"{card.title}: sin la cruz."
            return _board_response(request, campaign, msg, selected=card.pk)
        if action == "foto_quitar":
            card.image = ""
            card.save(update_fields=["image"])
            return _board_response(request, campaign, "Foto quitada.", selected=card.pk)
        try:
            card.image = ImageField().clean(request.FILES.get("foto"))
        except ValidationError:
            return _board_response(request, campaign, "Sube una imagen (JPG, PNG o WebP).", error=True, selected=card.pk)
        card.save(update_fields=["image"])
        return _board_response(request, campaign, "Foto cambiada.", selected=card.pk)
    if action == "unir":
        if locked:
            raise PermissionDenied
        other = get_object_or_404(BoardCard, pk=request.POST.get("otra"), campaign=campaign)
        if other.pk == card.pk:
            return _board_response(request, campaign, "Elige otra nota.", error=True, selected=card.pk)
        a, b = sorted([card.pk, other.pk])
        _, created = BoardLink.objects.get_or_create(card_a_id=a, card_b_id=b)
        return _board_response(request, campaign, "Hilo rojo añadido." if created else "Esas notas ya estaban unidas.", selected=other.pk)
    if action == "pagina":
        if locked:
            raise PermissionDenied
        page_id = request.POST.get("pagina")
        card.page = get_object_or_404(Page.objects.visible_for(request.user, campaign), pk=page_id) if page_id else None
        card.save(update_fields=["page"])
        return _board_response(request, campaign, "Página enlazada." if card.page else "Enlace quitado.", selected=card.pk)
    if action in ("revelar", "clasificar"):
        master_only(request)
        if action == "revelar":
            card.revealed = not card.revealed
            msg = "Pista revelada a los jugadores." if card.revealed else "Pista clasificada otra vez."
        else:
            card.classified, card.revealed = not card.classified, False
            msg = "Nota clasificada." if card.classified else "La nota ya no está clasificada."
        card.save(update_fields=["revealed", "classified"])
        return _board_response(request, campaign, msg, selected=card.pk)
    if action == "quitar":
        if not (request.is_master or card.author_id == request.user.pk):
            raise PermissionDenied("Solo puedes quitar tus notas.")
        card.delete()
        return _board_response(request, campaign, "Nota quitada de la pizarra.")
    raise Http404


# ---------------------------------------------------------------------------
# Escenas de rol
# ---------------------------------------------------------------------------

ROLL = re.compile(r"^/tirar\s+(\d*)d(\d+)\s*(?:([+\-−])\s*(\d+))?", re.IGNORECASE)


def roll_dice(text):
    """«/tirar 2d6+3 …» at the start: roll on the server. Returns (roll text, rest of the message)."""
    match = ROLL.match(text.strip())
    if not match:
        return "", text
    amount = int(match.group(1) or 1)
    sides = int(match.group(2))
    if not (1 <= amount <= 20 and 2 <= sides <= 100):
        return "", text
    bonus = int(match.group(4) or 0) * (-1 if match.group(3) in ("-", "−") else 1)
    results = [secrets.randbelow(sides) + 1 for _ in range(amount)]
    total = sum(results) + bonus
    formula = f"{amount}d{sides}" + (f"{'+' if bonus > 0 else '−'}{abs(bonus)}" if bonus else "")
    if amount > 1 or bonus:
        detail = "[" + ", ".join(map(str, results)) + "]" + (f" {'+' if bonus > 0 else '−'} {abs(bonus)}" if bonus else "")
        roll = f"{formula} → {detail} = {total}"
    else:
        roll = f"{formula} → {total}"
    return roll, text.strip()[match.end():].strip()


def post_options(campaign, scene, user, is_master):
    """Who the reader can write as: their characters in the scene, or (masters)
    the narrator and the campaign's NPCs."""
    if is_master:
        npcs = Character.objects.filter(campaigns=campaign, is_npc=True).order_by("name")
        return [("narrador", "Narrador")] + [(str(c.pk), c.name) for c in npcs]
    return [(str(c.pk), c.name) for c in scene.participants.all() if c.player_id == user.pk]


def post_card(post, masters):
    if post.as_character_id:
        character = post.as_character
        name = character.name
        who = post.author.get_short_name() if post.author else ""
        sub = f"PNJ · {who}" if character.is_npc else who
        kind = "pnj" if character.is_npc else "pj"
    else:
        name, kind = "Narrador", "narrador"
        sub = f"{post.author.get_short_name() if post.author else ''} · máster"
    whisper = ""
    if post.whisper_to_id:
        to_masters = post.author_id not in masters and post.whisper_to_id == post.as_character_id
        whisper = "SUSURRO AL MÁSTER" if to_masters else f"SUSURRO PARA {post.whisper_to.name.upper()}"
    return {"post": post, "name": name, "sub": sub, "kind": kind, "whisper": whisper,
            "color_key": post.as_character if post.as_character_id else post.author}


def scenes_context(request, campaign, is_master, scene=None):
    user = request.user
    scenes = Scene.objects.of(campaign).select_related("location").prefetch_related("participants")
    party = list(campaign.party)
    pc_filter = request.GET.get("pj")
    if pc_filter and pc_filter.isdigit():
        scenes = scenes.filter(participants__pk=int(pc_filter))
    scenes = list(scenes)
    if scene is None and scenes:
        scene = scenes[0]
    awaiting = {s.pk for s, _ in scenes_awaiting(user, campaign, is_master)}
    visible_posts = Post.objects.visible_for(user, campaign)
    counts = {}
    for row in visible_posts.filter(scene__in=scenes).values_list("scene_id", flat=True):
        counts[row] = counts.get(row, 0) + 1
    context = {"scenes": [{"scene": s, "turn": s.pk in awaiting, "count": counts.get(s.pk, 0)} for s in scenes],
               "scene": scene, "party": party, "pc_filter": pc_filter or ""}
    if scene is None:
        return context
    masters = set(campaign.masters.values_list("pk", flat=True))
    posts = visible_posts.filter(scene=scene).select_related("author", "as_character", "whisper_to")
    options = post_options(campaign, scene, user, is_master)
    targets = scene.participants.filter(is_npc=False, player__isnull=False)
    context.update(
        posts=[post_card(p, masters) for p in posts],
        can_post=scene.is_open and bool(options) and (is_master or any(o[0] != "narrador" for o in options)),
        post_form=PostForm(options=options, whisper_targets=targets, is_master=is_master),
    )
    return context


class SceneView(DiaryMixin, TemplateView):
    template_name = "diary_scenes.html"
    tab = "diary_scenes"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        scene = None
        if "pk" in self.kwargs:
            scene = get_object_or_404(Scene, pk=self.kwargs["pk"], campaign=self.campaign)
        context.update(scenes_context(self.request, self.campaign, self.is_master, scene))
        return context


class SceneCreateView(MasterRequiredMixin, DiaryMixin, CreateView):
    model = Scene
    form_class = SceneForm
    template_name = "diary_scene_form.html"
    tab = "diary_scenes"

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "campaign": self.campaign}

    def get_success_url(self):
        return reverse("diary_scene", args=[self.campaign.pk, self.object.pk])


def _scene_response(request, campaign, scene, msg="", error=False):
    context = {"campaign": campaign, "is_master": request.is_master,
               "wiki": WikiResolver(campaign, request.user, request.is_master),
               **scenes_context(request, campaign, request.is_master, scene)}
    return respond(request, redirect_to=reverse("diary_scene", args=[campaign.pk, scene.pk]),
                   template="partials/scene_thread.html", context=context, target="#escena", msg=msg, error=error)


@require_POST
@campaign_member
def post_add(request, campaign, pk):
    scene = get_object_or_404(Scene, pk=pk, campaign=campaign)
    if not scene.is_open:
        return _scene_response(request, campaign, scene, "La escena está cerrada.", error=True)
    options = post_options(campaign, scene, request.user, request.is_master)
    targets = scene.participants.filter(is_npc=False, player__isnull=False)
    form = PostForm(request.POST, options=options, whisper_targets=targets, is_master=request.is_master)
    if not form.is_valid():
        return _scene_response(request, campaign, scene, "Escribe un mensaje y elige con quién lo escribes.", error=True)
    as_who = form.cleaned_data["as_who"]
    character = None if as_who == "narrador" else get_object_or_404(Character, pk=as_who)
    roll, text = roll_dice(form.cleaned_data["text"])
    if request.is_master:
        whisper_to = form.cleaned_data.get("whisper_to")
    else:
        # A player's whisper goes to the masters: whispered to their own character.
        whisper_to = character if form.cleaned_data.get("whisper") else None
    Post.objects.create(scene=scene, author=request.user, as_character=character, text=text, roll=roll, whisper_to=whisper_to)
    return _scene_response(request, campaign, scene, f"Tirada: {roll}" if roll else "")


@require_POST
@campaign_member
def scene_toggle(request, campaign, pk):
    master_only(request)
    scene = get_object_or_404(Scene, pk=pk, campaign=campaign)
    if scene.is_open:
        scene.is_open = False
        session = last_session(campaign)
        event = TimelineEvent.objects.create(
            campaign=campaign, session=session, world_day=campaign.world_day, title=f"Escena: {scene.title}",
            kind=TimelineEvent.Kind.SOCIAL, location=scene.location.location if scene.location_id else None,
        )
        event.characters.set(scene.participants.all())
        msg = "Escena cerrada. Ya está en la línea del tiempo" + (f", en la sesión {session.number}." if session else ".")
    else:
        scene.is_open = True
        msg = "Escena abierta otra vez."
    scene.save(update_fields=["is_open"])
    return _scene_response(request, campaign, scene, msg)


# ---------------------------------------------------------------------------
# Misiones
# ---------------------------------------------------------------------------

STATUS_GROUPS = [(Quest.Status.ACTIVA, gettext_noop("En curso")), (Quest.Status.COMPLETADA, gettext_noop("Completadas")), (Quest.Status.FALLIDA, gettext_noop("Fallidas"))]


def quest_card(quest, is_master):
    objectives = []
    for objective in quest.objectives.all():
        locked = objective.hidden and not is_master
        # A hidden objective's text never reaches players.
        objectives.append({"objective": objective, "locked": locked, "text": "" if locked else objective.text,
                           "secret": objective.hidden and is_master})
    done = sum(1 for o in quest.objectives.all() if o.done)
    return {"quest": quest, "objectives": objectives, "done": done, "total": len(objectives)}


class QuestFormMixin:
    model = Quest
    form_class = QuestForm
    template_name = "diary_quest_form.html"

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "campaign": self.campaign}

    def get_queryset(self):
        return Quest.objects.of(self.campaign)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        quest = getattr(self, "object", None)
        context["objectives"] = kwargs.get("objectives") or ObjectiveFormSet(instance=quest or Quest())
        saved = list(quest.objectives.all()) if quest and quest.pk else []
        context.update(objectives_total=len(saved), objectives_done=sum(1 for o in saved if o.done))
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object() if "pk" in kwargs else None
        form = self.get_form()
        objectives = ObjectiveFormSet(request.POST, instance=self.object or Quest(campaign=self.campaign))
        if form.is_valid() and objectives.is_valid():
            with transaction.atomic():
                self.object = form.save()
                objectives.instance = self.object
                rows = objectives.save(commit=False)
                for removed in objectives.deleted_objects:
                    removed.delete()
                # New objectives go after the ones already there, in the order they were added.
                last = self.object.objectives.aggregate(m=Max("order"))["m"]
                next_order = 0 if last is None else last + 1
                for row in rows:
                    if row.pk is None:
                        row.order, next_order = next_order, next_order + 1
                    row.save()
            return respond(request, redirect_to=reverse("diary", args=[self.campaign.pk]) + f"#aventura-{self.object.pk}",
                           msg="Aventura guardada.")
        return self.render_to_response(self.get_context_data(form=form, objectives=objectives))


class QuestCreateView(MasterRequiredMixin, QuestFormMixin, DiaryMixin, CreateView):
    pass


class QuestUpdateView(MasterRequiredMixin, QuestFormMixin, DiaryMixin, UpdateView):
    pass


@require_POST
@campaign_member
def objective_toggle(request, campaign, pk):
    master_only(request)
    objective = get_object_or_404(QuestObjective, pk=pk, quest__campaign=campaign)
    objective.done = not objective.done
    objective.save()  # marking a hidden objective as done reveals it
    quest = Quest.objects.select_related("giver", "start_session", "end_session").prefetch_related("objectives").get(pk=objective.quest_id)
    context = {"campaign": campaign, "is_master": True, "q": quest_card(quest, True)}
    return respond(request, redirect_to=reverse("diary", args=[campaign.pk]),
                   template="partials/quest_card.html", context=context, target=f"#mision-{quest.pk}")
