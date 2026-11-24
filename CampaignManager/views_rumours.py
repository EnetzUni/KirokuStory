"""Rumores de <campaña>: what NPCs say, official notices, news, legends... The master
writes them, sorted into categories with a colour that each campaign defines. Players see
the published ones; who said them and where, only if that page of the world is revealed to
them; and never how much truth they hold."""
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, TemplateView, UpdateView

from .ajax import is_ajax, respond
from .forms import RumourCategoryForm, RumourForm
from .models import Page, Rumour, RumourCategory, Session
from .permissions import CampaignMemberMixin, MasterRequiredMixin, campaign_member, master_only
from .wiki import WikiResolver

TRUTH_PILL = {"verdad": "p-verde", "medias": "p-ambar", "mentira": "p-coral", "sin_decidir": ""}


def rumours_context(request, campaign, is_master, category=None):
    """The section of the page that AJAX replaces: the category filter, the rumours and,
    for masters, the categories to edit."""
    user = request.user
    category = category if category is not None else request.GET.get("cat", "")
    visible = Rumour.objects.visible_for(user, campaign).select_related("category", "source_page", "place", "session")
    categories = list(RumourCategory.objects.of(campaign))
    counts = {}
    for rumour in visible:
        counts[rumour.category_id] = counts.get(rumour.category_id, 0) + 1
    if category == "sin":
        rumours = [r for r in visible if r.category_id is None]
    elif category.isdigit():
        rumours = [r for r in visible if str(r.category_id) == category]
    else:
        category, rumours = "", list(visible)
    # Players only see who said it and where if that page of the world is revealed to them.
    pages = set(Page.objects.visible_for(user, campaign).values_list("pk", flat=True))
    sessions = set(Session.objects.visible_for(user, campaign).values_list("pk", flat=True))
    items = [{
        "rumour": r, "category": r.category,
        "source_page": r.source_page if r.source_page_id in pages else None,
        "place": r.place if r.place_id in pages else None,
        "session": r.session if r.session_id in sessions else None,
        "truth_pill": TRUTH_PILL.get(r.truth, ""),
    } for r in rumours]
    return {
        "campaign": campaign, "is_master": is_master, "items": items, "current_category": category,
        "filters": [{"category": c, "count": counts.get(c.pk, 0)} for c in categories if is_master or counts.get(c.pk)],
        "uncategorized": counts.get(None, 0), "total": sum(counts.values()),
        "category_forms": [(c, RumourCategoryForm(instance=c, prefix=f"cat{c.pk}", campaign=campaign)) for c in categories] if is_master else [],
        "new_category_form": RumourCategoryForm(prefix="nueva", campaign=campaign) if is_master else None,
        "wiki": WikiResolver(campaign, user, is_master),
    }


def _section_response(request, campaign, msg="", error=False):
    category = request.POST.get("cat", "")
    context = rumours_context(request, campaign, request.is_master, category=category)
    url = reverse("rumours", args=[campaign.pk]) + (f"?cat={category}" if category else "")
    return respond(request, redirect_to=url, template="partials/rumours_section.html", context=context,
                   target="#rumores", msg=msg, error=error)


class RumourListView(CampaignMemberMixin, TemplateView):
    template_name = "rumours.html"

    def get(self, request, *args, **kwargs):
        if is_ajax(request):  # the category filter
            html = render_to_string("partials/rumours_section.html",
                                    rumours_context(request, self.campaign, self.is_master), request=request)
            return JsonResponse({"html": html, "target": "#rumores"})
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(rumours_context(self.request, self.campaign, self.is_master))
        return context


class RumourFormMixin:
    model = Rumour
    form_class = RumourForm
    template_name = "rumour_form.html"

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "campaign": self.campaign}

    def get_queryset(self):
        return Rumour.objects.of(self.campaign)

    def get_success_url(self):
        return reverse("rumours", args=[self.campaign.pk]) + f"#rumor-{self.object.pk}"


class RumourCreateView(MasterRequiredMixin, RumourFormMixin, CreateView):
    def get_initial(self):
        # «+ Nuevo rumor» from a category's filter: that category; the session, the last one.
        initial = {"session": Session.objects.filter(campaign=self.campaign).order_by("-number").first()}
        category = self.request.GET.get("cat", "")
        if category.isdigit():
            initial["category"] = category
        return initial


class RumourUpdateView(MasterRequiredMixin, RumourFormMixin, UpdateView):
    pass


@require_POST
@campaign_member
def rumour_action(request, campaign, pk):
    """Publish or hide a rumour, or delete it."""
    master_only(request)
    rumour = get_object_or_404(Rumour, pk=pk, campaign=campaign)
    if request.POST.get("accion") == "borrar":
        rumour.delete()
        return _section_response(request, campaign, "Rumor borrado.")
    rumour.published = not rumour.published
    rumour.save(update_fields=["published"])
    return _section_response(request, campaign, "Rumor publicado: los jugadores lo ven." if rumour.published
                             else "Rumor oculto para los jugadores.")


@require_POST
@campaign_member
def rumour_category(request, campaign):
    """The master's categories: create one, change its name or colour, or delete it
    (its rumours stay, without a category)."""
    master_only(request)
    action, _, pk = request.POST.get("accion", "").partition(":")
    if action == "crear":
        form = RumourCategoryForm(request.POST, prefix="nueva", campaign=campaign)
        if not form.is_valid():
            return _section_response(request, campaign, " ".join(m for e in form.errors.values() for m in e), error=True)
        form.instance.order = RumourCategory.objects.of(campaign).count()
        category = form.save()
        return _section_response(request, campaign, f"Categoría «{category.name}» creada.")
    category = get_object_or_404(RumourCategory, pk=pk, campaign=campaign)
    if action == "borrar":
        category.delete()
        return _section_response(request, campaign, f"Categoría «{category.name}» borrada; sus rumores quedan sin categoría.")
    form = RumourCategoryForm(request.POST, instance=category, prefix=f"cat{category.pk}", campaign=campaign)
    if not form.is_valid():
        return _section_response(request, campaign, " ".join(m for e in form.errors.values() for m in e), error=True)
    form.save()
    return _section_response(request, campaign, f"Categoría «{category.name}» guardada.")
