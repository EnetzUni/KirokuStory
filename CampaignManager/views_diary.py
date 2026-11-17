"""Diario: crónicas, línea del tiempo, pizarra, escenas de rol y misiones."""
import math
import re
import secrets

from django.core.exceptions import PermissionDenied
from django.db.models import Prefetch
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, ListView, TemplateView, UpdateView
from django.utils.translation import gettext_noop

from .ajax import is_ajax, respond
from .forms import BoardCardForm, EventForm, ObjectiveFormSet, PostForm, QuestForm, SceneForm, SessionForm
from .models import (
    BoardCard, BoardLink, Character, Page, Post, Quest, QuestObjective, Scene, Session, TimelineEvent,
)
from .campaign_models import BOARD_HEIGHT, BOARD_WIDTH, CARD_HEIGHT, CARD_WIDTH
from .permissions import CampaignMemberMixin, MasterRequiredMixin, campaign_member, master_only
from .services import scenes_awaiting
from .wiki import WikiResolver

TABS = [("diary", gettext_noop("Crónicas")), ("diary_timeline", gettext_noop("Línea del tiempo")), ("diary_board", gettext_noop("Pizarra")),
        ("diary_scenes", gettext_noop("Escenas de rol")), ("diary_quests", gettext_noop("Misiones"))]


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
# Crónicas
# ---------------------------------------------------------------------------

class ChronicleListView(DiaryMixin, ListView):
    template_name = "diary_chronicles.html"
    context_object_name = "sessions"

    def get_queryset(self):
        return Session.objects.visible_for(self.request.user, self.campaign).order_by("-number")


class SessionFormMixin:
    model = Session
    form_class = SessionForm
    template_name = "diary_session_form.html"

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "campaign": self.campaign}

    def get_queryset(self):
        return Session.objects.filter(campaign=self.campaign)

    def get_success_url(self):
        return reverse("diary", args=[self.campaign.pk])


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
    visible_ids = {s.pk for s in sessions}
    by_session = {}
    for event in events:
        key = event.session_id if event.session_id in visible_ids else None
        by_session.setdefault(key, []).append(event)

    segments, marks, x = [], [], 0
    groups = ([(None, "Sin sesión", "Sucesos sin sesión")] if by_session.get(None) else []) + [
        (s.pk, f"S{s.number} · {s.title}", f"Sesión {s.number} · {s.title} · {s.played_on:%d/%m/%Y}") for s in sessions]
    for index, (key, label, full) in enumerate(groups):
        group_events = by_session.get(key, [])
        width = max(1, len(group_events)) * SLOT
        segments.append({"left": x, "width": width, "label": label, "full": full, "even": index % 2 == 1})
        for j, event in enumerate(group_events):
            up = len(marks) % 2 == 0
            mx = x + SLOT // 2 + j * SLOT
            marks.append({"event": event, "x": mx, "card_left": mx - 72,
                          "card_top": 36 if up else 180, "stem_top": 130 if up else 160})
        x += width
    selected_id = selected or request.GET.get("evento")
    chosen = next((m for m in marks if str(m["event"].pk) == str(selected_id)), marks[-1] if marks else None)
    last = last_session(campaign)
    query = request.GET.copy()
    return {
        "segments": segments, "marks": marks, "chosen": chosen, "past_width": x,
        "future_left": x, "future_label": f"Próxima · S{(last.number + 1) if last else 1}", "width": x + SLOT + 24,
        "party": party, "pc_filter": pc_filter or "", "show_hidden": show_hidden,
        "event_form": EventForm(campaign=campaign) if is_master else None, "add_session": last,
        "query_hidden": "0" if show_hidden else "1", "query": query.urlencode(),
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
        return _timeline_response(request, campaign, "Escribe antes la crónica de una sesión.", error=True)
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
    form = BoardCardForm(request.POST, campaign=campaign, is_master=request.is_master, visible_pages=visible_pages)
    if not form.is_valid():
        return _board_response(request, campaign, "La nota necesita un título.", error=True)
    card = form.save(commit=False)
    n = BoardCard.objects.of(campaign).count()
    card.author, card.x, card.y = request.user, 260 + (n % 3) * 40, 150 + (n % 4) * 30
    card.save()
    return _board_response(request, campaign, "Nota clavada. Pulsa «Mover» para colocarla donde quieras.", selected=card.pk)


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
        card.y = max(0, min(BOARD_HEIGHT - CARD_HEIGHT, y))
        card.save(update_fields=["x", "y"])
        return _board_response(request, campaign, "Nota movida.", selected=card.pk)
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

STATUS_GROUPS = [(Quest.Status.ACTIVA, gettext_noop("Activas")), (Quest.Status.COMPLETADA, gettext_noop("Completadas")), (Quest.Status.FALLIDA, gettext_noop("Fallidas"))]


def quest_card(quest, is_master):
    objectives = []
    for objective in quest.objectives.all():
        locked = objective.hidden and not is_master
        # A hidden objective's text never reaches players.
        objectives.append({"objective": objective, "locked": locked, "text": "" if locked else objective.text,
                           "secret": objective.hidden and is_master})
    done = sum(1 for o in quest.objectives.all() if o.done)
    return {"quest": quest, "objectives": objectives, "done": done, "total": len(objectives)}


class QuestListView(DiaryMixin, TemplateView):
    template_name = "diary_quests.html"
    tab = "diary_quests"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        quests = Quest.objects.of(self.campaign).select_related("giver").prefetch_related("objectives")
        groups = []
        for status, label in STATUS_GROUPS:
            items = [quest_card(q, self.is_master) for q in quests if q.status == status]
            if items:
                groups.append({"label": label, "items": items})
        context.update(groups=groups)
        return context


class QuestFormMixin:
    model = Quest
    form_class = QuestForm
    template_name = "diary_quest_form.html"
    tab = "diary_quests"

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "campaign": self.campaign}

    def get_queryset(self):
        return Quest.objects.of(self.campaign)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["objectives"] = kwargs.get("objectives") or ObjectiveFormSet(instance=getattr(self, "object", None) or Quest())
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object() if "pk" in kwargs else None
        form = self.get_form()
        objectives = ObjectiveFormSet(request.POST, instance=self.object or Quest(campaign=self.campaign))
        if form.is_valid() and objectives.is_valid():
            self.object = form.save()
            objectives.instance = self.object
            objectives.save()
            return respond(request, redirect_to=reverse("diary_quests", args=[self.campaign.pk]), msg="Misión guardada.")
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
    quest = Quest.objects.prefetch_related("objectives").get(pk=objective.quest_id)
    context = {"campaign": campaign, "is_master": True, "q": quest_card(quest, True)}
    return respond(request, redirect_to=reverse("diary_quests", args=[campaign.pk]),
                   template="partials/quest_card.html", context=context, target=f"#mision-{quest.pk}")
