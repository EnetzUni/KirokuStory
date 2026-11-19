"""Inicio: the campaign's front page."""
from django.contrib.auth.decorators import login_required
from django.db.models import Case, IntegerField, When
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from .mapping import map_data, party_location
from .ajax import respond
from .models import Campaign, Character, Quest, Session, TimelineEvent
from .permissions import CampaignMemberMixin, campaign_member, master_only, user_campaigns
from .services import active_encounter, current_combatant, scenes_awaiting
from .wiki import WikiResolver


@login_required
def start(request):
    """Open the user's first campaign (active ones first)."""
    campaigns = user_campaigns(request.user).annotate(
        rank=Case(When(status="activa", then=0), When(status="pausada", then=1), default=2, output_field=IntegerField())
    ).order_by("rank", "name")
    first = campaigns.first()
    if first is None:
        return render(request, "no_campaigns.html")
    return redirect("home", campaign_id=first.pk)


class HomeView(CampaignMemberMixin, TemplateView):
    template_name = "home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        campaign, user, is_master = self.campaign, self.request.user, self.is_master
        last = Session.objects.visible_for(user, campaign).filter(published=True).order_by("-number").first()
        encounter = active_encounter(campaign)
        actor = current_combatant(encounter)
        quests = []
        for quest in Quest.objects.of(campaign).filter(status=Quest.Status.ACTIVA).prefetch_related("objectives"):
            # Only the objectives this reader can see count.
            objectives = [o for o in quest.objectives.all() if is_master or not o.hidden]
            done = sum(1 for o in objectives if o.done)
            quests.append({"quest": quest, "done": done, "total": len(objectives)})
        first_map = next((m for m in campaign.maps.all() if m.moves.exists()), campaign.maps.first())
        context.update(
            wiki=WikiResolver(campaign, user, is_master),
            last_session=last,
            awaiting=scenes_awaiting(user, campaign, is_master),
            events=list(TimelineEvent.objects.of(campaign).filter(hidden=False)
                        .select_related("session").order_by("-session__number", "-id")[:4]),
            encounter=encounter,
            actor=actor,
            party=campaign.party.select_related("race", "char_class"),
            quests=quests,
            where=party_location(campaign),
            md=map_data(first_map, is_master) if first_map else None,
        )
        return context


@require_POST
@campaign_member
def campaign_ruleset(request, campaign):
    """The master chooses the campaign's rules: Aegis (ours) or the standard SRD 5.2.1.
    It applies from the next level up, character or unique skill on."""
    master_only(request)
    ruleset = request.POST.get("reglamento")
    back = reverse("home", args=[campaign.pk])
    if ruleset not in Campaign.Ruleset.values:
        return respond(request, redirect_to=back, error=True, msg="Elige Aegis o SRD 5.2.1.")
    campaign.ruleset = ruleset
    campaign.save(update_fields=["ruleset"])
    return respond(request, redirect_to=back, msg=f"{campaign.name} juega ahora con {campaign.get_ruleset_display()}.")

