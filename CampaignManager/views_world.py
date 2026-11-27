"""Mundo: the campaign's wiki of places, NPCs, factions, objects and stories."""
from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from django.utils.translation import gettext, gettext_noop
from django.views.generic import CreateView, DetailView, TemplateView, UpdateView

from .ajax import respond
from .forms import PageForm
from .models import Character, Page, Post, Scene, Session, TimelineEvent
from .permissions import CampaignMemberMixin, MasterRequiredMixin, campaign_member, master_only, npc_templates_for
from .wiki import WikiResolver, first_sentence, mentions

CATEGORIES = [
    ("lugar", gettext_noop("Lugares"), gettext_noop("Los sitios del mundo. Los que tienen punto en el mapa se abren desde allí.")),
    ("pnj", gettext_noop("Personajes no jugadores"), gettext_noop("Gente que el grupo conoce. El máster ve también sus secretos.")),
    ("faccion", gettext_noop("Facciones"), gettext_noop("Grupos, órdenes y bandas.")),
    ("objeto", gettext_noop("Objetos"), gettext_noop("Objetos con historia.")),
    ("historia", gettext_noop("Historias"), gettext_noop("Leyendas, crónicas antiguas y lo que se cuenta en las tabernas.")),
    ("pj", gettext_noop("Personajes jugadores"), gettext_noop("El grupo. Abre la ficha de cada uno.")),
]
CATEGORY_INFO = {key: (label, intro) for key, label, intro in CATEGORIES}


def link_index(campaign, page=None):
    """Everything [[Name]] can open, for the link finder of the page editor: the pages (but the one
    being edited), the party's sheets and the NPC templates (which only the master gets as links).
    A sheet named like a page is left out: the page is what the link opens."""
    kinds = dict(Page.Category.choices)
    pages = Page.objects.filter(campaign=campaign).only("title", "category", "revealed").order_by("title")
    if page is not None and page.pk:
        pages = pages.exclude(pk=page.pk)
    out = [{"t": p.title, "c": p.category, "k": gettext(kinds.get(p.category, "")), "h": not p.revealed} for p in pages]
    taken = {p.title.lower() for p in Page.objects.filter(campaign=campaign).only("title")}
    sheets = [(c, "pj", gettext("Personaje jugador")) for c in campaign.party]
    sheets += [(c, "ficha", gettext("Ficha de PNJ · solo tú")) for c in npc_templates_for(campaign).order_by("name")]
    for character, kind, label in sheets:
        if character.name.lower() not in taken:
            taken.add(character.name.lower())
            out.append({"t": character.name, "c": kind, "k": label, "h": False})
    return out


def deletion_summary(page):
    """What deleting a page leaves behind, for the master to read before saying yes."""
    others = Page.objects.filter(campaign=page.campaign).exclude(pk=page.pk).only("body", "secret")
    linked = sum(1 for other in others if mentions(other.body, page.title) or mentions(other.secret, page.title))
    linked += sum(1 for session in Session.objects.filter(campaign=page.campaign).only("body", "master_notes")
                  if mentions(session.body, page.title) or mentions(session.master_notes, page.title))
    return {"children": page.children.count(), "linked": linked, "sheets": page.sheets.count(),
            "on_map": bool(page.location_id)}


def tree(pages, category, current=None):
    """Pages of a category as nested branches: each page with the pages inside it, so the
    menu can fold every branch. Pages whose parent belongs to another category go under a
    branch named after that parent («Academia de Makep» › Laplace, Satela...).
    A branch is `here` when the page being read is in it: it opens by itself."""
    by_id = {p.pk: p for p in pages}
    in_cat = [p for p in pages if p.category == category]
    children = {}
    for p in in_cat:
        children.setdefault(p.parent_id, []).append(p)
    line = {current.pk, *(a.pk for a in current.ancestors())} if current is not None else set()
    seen = set()

    def branch(page):
        if page.pk in seen:
            return None
        seen.add(page.pk)
        kids = [node for node in (branch(child) for child in children.get(page.pk, [])) if node]
        return {"page": page, "children": kids, "here": page.pk in line, "count": len(kids)}

    nodes = [node for node in (branch(p) for p in in_cat if not p.parent_id or p.parent_id not in by_id) if node]
    outside = []
    for p in in_cat:
        if p.parent_id in by_id and by_id[p.parent_id].category != category and p.parent_id not in outside:
            outside.append(p.parent_id)
    for parent_id in outside:
        kids = [node for node in (branch(p) for p in in_cat if p.parent_id == parent_id) if node]
        if kids:
            # Opens only when the page being read is one of its pages (not merely below the same place).
            nodes.append({"header": by_id[parent_id], "children": kids, "count": len(kids),
                          "here": any(k["here"] for k in kids)})
    return nodes


class WorldMixin(CampaignMemberMixin):
    def sidebar(self, current=None, category=None):
        """The left menu, one foldable section per category. Open: the section of the page
        or category being read, or every section with results while searching."""
        pages = list(Page.objects.visible_for(self.request.user, self.campaign).order_by("title"))
        query = self.request.GET.get("q", "").strip().lower()
        party = list(self.campaign.party)
        groups = []
        for key, label, _ in CATEGORIES:
            nodes = None  # a tree of foldable branches; flat `items` for the party and for a search
            if key == "pj":
                items = [{"character": c} for c in party]
                if query:
                    items = [i for i in items if query in i["character"].name.lower()]
                count = len(items)
            elif query:
                items = [{"page": p} for p in pages if p.category == key and query in p.title.lower()]
                count = len(items)
            else:
                items, nodes = [], tree(pages, key, current)
                count = sum(1 for p in pages if p.category == key)
            if query and not items:
                continue
            here = (current is not None and current.category == key) or category == key
            groups.append({"key": key, "label": label, "items": items, "nodes": nodes, "here": here,
                           "open": here or bool(query), "count": count})
        return {"world_groups": groups, "q": self.request.GET.get("q", ""), "current_page": current,
                "no_results": bool(query) and not groups}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["wiki"] = WikiResolver(self.campaign, self.request.user, self.is_master)
        return context


class WorldIndexView(WorldMixin, TemplateView):
    template_name = "world_index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        category = self.request.GET.get("cat", "lugar")
        if category not in CATEGORY_INFO:
            category = "lugar"
        pages = Page.objects.visible_for(self.request.user, self.campaign).select_related("parent")
        if category == "pj":
            cards = [{"character": c} for c in self.campaign.party.select_related("race", "char_class")]
        else:
            cards = [{"page": p, "summary": p.summary or first_sentence(p.body) or "Página vacía."}
                     for p in pages.filter(category=category).order_by("title")]
        label, intro = CATEGORY_INFO[category]
        context.update(self.sidebar(category=category), category=category, category_label=label, category_intro=intro,
                       cards=cards)
        return context


class PageDetailView(WorldMixin, DetailView):
    template_name = "world_page.html"
    context_object_name = "page"

    def get_queryset(self):
        return Page.objects.visible_for(self.request.user, self.campaign).select_related("parent", "location__map")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        page, user, is_master, campaign = self.object, self.request.user, self.is_master, self.campaign
        visible = Page.objects.visible_for(user, campaign)
        visible_ids = set(visible.values_list("pk", flat=True))
        refs = []
        for session in Session.objects.visible_for(user, campaign).order_by("number"):
            if mentions(session.body, page.title):
                refs.append({"label": f"Sesión {session.number} · {session.title}", "url": reverse("diary", args=[campaign.pk])})
        for other in visible.exclude(pk=page.pk).order_by("title"):
            if mentions(other.body, page.title) or (is_master and mentions(other.secret, page.title)):
                refs.append({"label": other.title, "url": reverse("world_page", args=[campaign.pk, other.slug])})
        posts = Post.objects.visible_for(user, campaign)
        for scene in Scene.objects.of(campaign):
            if scene.location_id == page.pk or any(mentions(p.text, page.title) for p in posts.filter(scene=scene)):
                refs.append({"label": f"Escena: {scene.title}", "url": reverse("diary_scene", args=[campaign.pk, scene.pk])})
        if page.location_id:
            for event in TimelineEvent.objects.visible_for(user, campaign).filter(location=page.location):
                refs.append({"label": f"Suceso: {event.title}", "url": reverse("diary_timeline", args=[campaign.pk]) + f"?evento={event.pk}"})
        # Its sheets, with their numbers. NPC sheets are the master's: players only see the party's.
        sheets = page.sheets.select_related("race", "char_class").order_by("is_npc", "name")
        if not is_master:
            sheets = sheets.filter(is_npc=False, status=Character.Status.APROBADO, campaigns=campaign)
        context.update(
            self.sidebar(current=page),
            deletion=deletion_summary(page) if is_master else None,
            category_label=CATEGORY_INFO.get(page.category, ("", ""))[0],
            ancestors=[a for a in page.ancestors() if a.pk in visible_ids],
            children=[c for c in page.children.order_by("title") if c.pk in visible_ids],
            refs=refs,
            location_visible=bool(page.location_id) and (is_master or page.location.status != "oculto"),
            sheets=[{"character": c, "max_ap": c.max_ap, "url": reverse("sheet", args=[campaign.pk, c.pk])} for c in sheets],
        )
        return context


class PageFormMixin:
    """The page editor: title and category, the two texts with a finder to link other pages, and the rest
    (visibility, image, where it is, its sheets) to one side. world_edit.html lays it out."""
    model = Page
    form_class = PageForm
    template_name = "world_edit.html"
    saved_msg = ""

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "campaign": self.campaign}

    def get_queryset(self):
        return Page.objects.of(self.campaign)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        page = getattr(self, "object", None)
        form = context["form"]
        context.update(
            self.sidebar(current=page),
            # Each category as a choice to click, with what goes in it.
            category_radios=[{"radio": radio, "intro": CATEGORY_INFO[radio.data["value"]][1]} for radio in form["category"]],
            link_index=link_index(self.campaign, page),
            deletion=deletion_summary(page) if page is not None else None,
        )
        return context

    def form_valid(self, form):
        was_revealed = bool(form.initial.get("revealed", False))
        response = super().form_valid(form)
        page = self.object
        if page.location_id and page.revealed != was_revealed:
            page.set_revealed(page.revealed)  # its map location shows as known, or hides, as with «Revelar al grupo»
        messages.success(self.request, gettext(self.saved_msg))
        return response

    def get_success_url(self):
        return reverse("world_page", args=[self.campaign.pk, self.object.slug])


class PageCreateView(MasterRequiredMixin, PageFormMixin, WorldMixin, CreateView):
    saved_msg = gettext_noop("Página creada.")

    def get_initial(self):
        category = self.request.GET.get("cat", "lugar")
        return {"title": self.request.GET.get("titulo", ""),
                "category": category if category in dict(Page.Category.choices) else "lugar"}


class PageUpdateView(MasterRequiredMixin, PageFormMixin, WorldMixin, UpdateView):
    saved_msg = gettext_noop("Cambios guardados.")


@require_POST
@campaign_member
def page_toggle(request, campaign, slug):
    master_only(request)
    page = get_object_or_404(Page, campaign=campaign, slug=slug)
    page.set_revealed(not page.revealed)
    msg = "Página revelada al grupo." if page.revealed else "Página oculta al grupo."
    if page.location_id:
        msg += f" Su lugar del mapa está ahora «{page.location.get_status_display().lower()}»."
    return respond(request, redirect_to=reverse("world_page", args=[campaign.pk, page.slug]), msg=msg)


@require_POST
@campaign_member
def page_delete(request, campaign, slug):
    """The page goes; what was inside it stays, loose, and its links show as a page to create."""
    master_only(request)
    page = get_object_or_404(Page, campaign=campaign, slug=slug)
    title, category = page.title, page.category
    page.delete()
    return respond(request, redirect_to=reverse("world", args=[campaign.pk]) + f"?cat={category}",
                   msg=f"Página «{title}» borrada.")


@require_GET
@campaign_member
def page_preview(request, campaign, slug):
    """Summary for the floating preview of a [[link]]. Hidden pages do not exist for players."""
    page = Page.objects.visible_for(request.user, campaign).filter(slug=slug).first()
    if page is None:
        raise Http404
    return JsonResponse({"title": page.title, "summary": page.summary or first_sentence(page.body),
                         "hidden": not page.revealed, "url": reverse("world_page", args=[campaign.pk, page.slug])})
