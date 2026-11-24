"""Asistente de campaña: a campaign made from the web, one tab after another.

The first tab creates the campaign with the least it needs, and whoever creates it becomes
its master. The next tabs fill it little by little: the table, the world, its map, places,
factions, NPCs, objects and stories, its plots and the first session. Every tab saves what
it has as soon as you move on; a tab left empty saves nothing. Masters can come back to any
tab later from the campaign's home page.
"""
import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext_noop

from . import rules
from .forms import (
    CampaignBasicsForm, CampaignMapForm, CampaignTableForm, CampaignWorldForm, PlotFormSet, SessionForm,
    WorldEntryFormSet, WorldPageForm,
)
from .models import Campaign, Page, Quest, QuestObjective, Session
from .permissions import campaign_member, master_only

STEPS = [
    gettext_noop("Lo básico"), gettext_noop("La mesa"), gettext_noop("El mundo"), gettext_noop("El mapa"),
    gettext_noop("Lugares"), gettext_noop("Facciones"), gettext_noop("Personajes no jugadores"),
    gettext_noop("Objetos e historias"), gettext_noop("Tramas"), gettext_noop("Primera sesión"), gettext_noop("Resumen"),
]
LAST_STEP = len(STEPS) - 1
INTROS = [
    gettext_noop("Lo mínimo para empezar: cómo se llama, de qué va, cuándo empezó y con qué reglas se juega. Al guardarla serás su máster."),
    gettext_noop("Quién se sienta a la mesa. Los jugadores ven lo que reveles; los másters lo ven y lo cambian todo."),
    gettext_noop("El mundo donde pasa la campaña: su página en «Mundo», que contendrá los lugares, y en qué momento está."),
    gettext_noop("Una imagen del mapa. Los lugares se ponen encima después, desde «Mapa», pulsando donde están."),
    gettext_noop("Regiones, ciudades, mazmorras... Cada lugar puede estar dentro de otro: así se ordena el menú del Mundo."),
    gettext_noop("Gremios, cultos, casas nobles, bandas: quién mueve los hilos. Lo que de verdad quieren va en su secreto."),
    gettext_noop("La gente que el grupo conocerá. Lo que sabe el grupo de cada uno y, aparte, lo que solo sabes tú."),
    gettext_noop("Objetos con historia y leyendas, crónicas antiguas o rumores de taberna."),
    gettext_noop("Las aventuras de la campaña (sus arcos) y las misiones que hay dentro de cada una, con sus objetivos."),
    gettext_noop("Si ya habéis jugado, la crónica de la primera sesión. Puedes dejarla para luego."),
    gettext_noop("Lo que tiene ya la campaña. Puedes volver a cualquier pestaña desde aquí o desde la portada."),
]
# List tabs: the kinds of world page each one adds.
LIST_STEPS = {4: ("lugar",), 5: ("faccion",), 6: ("pnj",), 7: ("objeto", "historia")}
WORLD_STEP, MAP_STEP, PLOTS_STEP, SESSION_STEP = 2, 3, 8, 9


def world_root(campaign):
    """The world's page: the first place made that is not inside another."""
    return Page.objects.filter(campaign=campaign, category=Page.Category.LUGAR, parent__isnull=True).order_by("id").first()


def step_url(campaign, step):
    return reverse("campaign_setup", args=[campaign.pk]) + f"?paso={rules.clamp(step, 0, LAST_STEP)}"


def step_forms(step, campaign, user, data=None, files=None):
    """The forms of a tab, by name. Unbound without data."""
    if step == 0:
        return {"form": CampaignBasicsForm(data, instance=campaign)}
    if step == 1:
        return {"form": CampaignTableForm(data, instance=campaign, user=user)}
    if step == WORLD_STEP:
        root = world_root(campaign)
        initial = ({"title": root.title, "summary": root.summary, "body": root.body, "secret": root.secret,
                    "revealed": root.revealed} if root else {"revealed": True})
        return {"form": CampaignWorldForm(data, files, instance=campaign),
                "page_form": WorldPageForm(data, initial=initial, prefix="mundo")}
    if step == MAP_STEP:
        return {"form": CampaignMapForm(data, files, prefix="mapa")}
    if step in LIST_STEPS:
        categories = LIST_STEPS[step]
        root = world_root(campaign) if categories == ("lugar",) else None
        return {"formset": WorldEntryFormSet(data, prefix="paginas", form_kwargs={
            "campaign": campaign, "categories": categories, "default_parent": root.pk if root else None})}
    if step == PLOTS_STEP:
        return {"formset": PlotFormSet(data, prefix="tramas", form_kwargs={"campaign": campaign})}
    if step == SESSION_STEP:
        last = campaign.sessions.order_by("-number").first()
        return {"form": SessionForm(data, campaign=campaign, prefix="sesion",
                                    initial={"number": (last.number + 1) if last else 1, "world_day": campaign.world_day})}
    return {}


def _lines(text):
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


@transaction.atomic
def save_step(step, forms, campaign):
    """Save what the tab has. Returns a short text of what was saved, or ""."""
    form, formset = forms.get("form"), forms.get("formset")
    if step in (0, 1):
        if form.has_changed():
            form.save()
            return "Datos de la campaña guardados."
        return ""
    if step == WORLD_STEP:
        saved = []
        if form.has_changed():
            form.save()
            saved.append("la campaña")
        page_form = forms["page_form"]
        if page_form.has_changed():
            data = page_form.cleaned_data
            root = world_root(campaign) or Page(campaign=campaign, category=Page.Category.LUGAR)
            for name in ("title", "summary", "body", "secret", "revealed"):
                setattr(root, name, data[name])
            root.save()
            saved.append(f"la página de {root.title}")
        return ("Guardado: " + " y ".join(saved) + ".") if saved else ""
    if step == MAP_STEP:
        if form.has_changed():
            new = form.save(commit=False)
            new.campaign, new.order = campaign, campaign.maps.count()
            new.save()
            return f"Mapa «{new.name}» guardado."
        return ""
    if step in LIST_STEPS:
        made = []
        for entry in formset.forms:
            if not entry.has_changed():
                continue
            data = entry.cleaned_data
            page = Page(campaign=campaign, title=data["title"].strip(), category=data["category"], parent=data.get("parent"),
                        summary=data["summary"], body=data["body"], secret=data["secret"], revealed=data["revealed"])
            page.full_clean()
            page.save()
            made.append(page.title)
        return ("Añadido: " + ", ".join(made) + ".") if made else ""
    if step == PLOTS_STEP:
        made = []
        for entry in formset.forms:
            if not entry.has_changed():
                continue
            data = entry.cleaned_data
            quest = Quest(campaign=campaign, title=data["title"].strip(), parent=data.get("parent"), status=data["status"],
                          description=data["description"], secret=data["secret"], hidden=data["hidden"])
            quest.full_clean()
            quest.save()
            objectives = [(text, False) for text in _lines(data["objectives"])]
            objectives += [(text, True) for text in _lines(data["hidden_objectives"])]
            for order, (text, hidden) in enumerate(objectives):
                QuestObjective.objects.create(quest=quest, text=text[:300], hidden=hidden, order=order)
            made.append(quest.title)
        return ("Añadido: " + ", ".join(made) + ".") if made else ""
    if step == SESSION_STEP:
        if form.has_changed():
            session = form.save()
            return f"Sesión {session.number} guardada."
        return ""
    return ""


def _target(going, step):
    """Where a button sends: «siguiente», «atras», «saltar», «mas» (this tab again) or a tab («paso-3»)."""
    if going and going.startswith("paso-") and going[5:].isdigit():
        return rules.clamp(int(going[5:]), 0, LAST_STEP)
    if going == "atras":
        return max(0, step - 1)
    if going == "mas":
        return step
    return min(LAST_STEP, step + 1)


def _context(request, campaign, step, forms):
    context = {
        "campaign": campaign, "is_master": True, "step": step, "last_step": LAST_STEP, "intro": INTROS[step],
        "steps": [{"n": i, "num": i + 1, "label": label, "current": i == step} for i, label in enumerate(STEPS)],
        "list_step": step in LIST_STEPS, **forms,
    }
    if campaign is None:
        return context
    if step in LIST_STEPS:
        context["existing"] = list(Page.objects.filter(campaign=campaign, category__in=LIST_STEPS[step])
                                   .select_related("parent").order_by("title"))
    elif step == MAP_STEP:
        context["existing_maps"] = list(campaign.maps.all())
    elif step == PLOTS_STEP:
        context["existing_quests"] = list(Quest.objects.filter(campaign=campaign, parent__isnull=True)
                                          .prefetch_related("missions").order_by("id"))
    elif step == SESSION_STEP:
        context["existing_sessions"] = list(campaign.sessions.order_by("number"))
    elif step == LAST_STEP:
        pages = Page.objects.filter(campaign=campaign)
        context["summary"] = [
            (STEPS[1], f"{campaign.masters.count()} máster(s), {campaign.players.count()} jugador(es)", 1),
            (STEPS[2], world_root(campaign).title if world_root(campaign) else "sin escribir", 2),
            (STEPS[3], f"{campaign.maps.count()} mapa(s)", 3),
            (STEPS[4], f"{pages.filter(category='lugar').count()} lugar(es)", 4),
            (STEPS[5], f"{pages.filter(category='faccion').count()} facción(es)", 5),
            (STEPS[6], f"{pages.filter(category='pnj').count()} PNJ", 6),
            (STEPS[7], f"{pages.filter(category__in=['objeto', 'historia']).count()} objeto(s) e historia(s)", 7),
            (STEPS[8], f"{campaign.quests.filter(parent__isnull=True).count()} aventura(s), "
                       f"{campaign.quests.filter(parent__isnull=False).count()} misión(es)", 8),
            (STEPS[9], f"{campaign.sessions.count()} sesión(es)", 9),
        ]
    return context


@login_required
def campaign_new(request):
    """Tab 1 of a campaign that does not exist yet: saving it creates it."""
    form = CampaignBasicsForm(request.POST or None, initial={
        "start_date": datetime.date.today(), "ruleset": Campaign.Ruleset.AEGIS, "status": Campaign.Status.ACTIVA})
    if request.method == "POST" and form.is_valid():
        campaign = form.save()
        campaign.masters.add(request.user)
        messages.success(request, f"Campaña «{campaign.name}» creada: eres su máster. Sigue rellenándola pestaña a pestaña.")
        return redirect(step_url(campaign, 1))
    return render(request, "campaign_setup.html", _context(request, None, 0, {"form": form}))


@campaign_member
def campaign_setup(request, campaign):
    """Any tab of the assistant, for the campaign's masters."""
    master_only(request)
    try:
        step = rules.clamp(int(request.POST.get("paso", request.GET.get("paso", 0))), 0, LAST_STEP)
    except ValueError:
        step = 0
    if request.method == "POST":
        going = request.POST.get("ir", "siguiente")
        if going == "terminar":
            return redirect("home", campaign_id=campaign.pk)
        if going == "saltar":
            return redirect(step_url(campaign, step + 1))
        forms = step_forms(step, campaign, request.user, request.POST, request.FILES)
        changed = any(f.has_changed() for f in forms.values())
        if changed:
            if not all(f.is_valid() for f in forms.values()):
                # Nothing is lost: the tab comes back with what was written and its errors.
                return render(request, "campaign_setup.html", _context(request, campaign, step, forms))
            msg = save_step(step, forms, campaign)
            if msg:
                messages.success(request, msg)
        return redirect(step_url(campaign, _target(going, step)))
    return render(request, "campaign_setup.html",
                  _context(request, campaign, step, step_forms(step, campaign, request.user)))
