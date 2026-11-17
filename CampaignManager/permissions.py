"""Who can see and change what (spec, section 6).

Everything needs a logged-in user. A user only sees the campaigns where they
are a master or a player; any other campaign is a 404. Hidden content is
filtered on the server, in the `visible_for()` querysets and in these checks.
"""
from functools import wraps

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404

from .models import Campaign, Character


def get_campaign_for(user, campaign_id):
    """The campaign if the user is one of its masters or players, else 404."""
    campaign = get_object_or_404(Campaign, pk=campaign_id)
    if not campaign.is_member(user):
        raise Http404("No es una de tus campañas.")
    return campaign


def user_campaigns(user):
    if not user.is_authenticated:
        return Campaign.objects.none()
    return Campaign.objects.filter(Q(masters=user) | Q(players=user)).distinct()


def can_edit_character(user, character, campaign):
    """Masters edit every character of their campaign; players only their own."""
    if campaign.is_master(user):
        return True
    return character.player_id == user.pk


def visible_characters(user):
    """Characters a user may see outside a campaign's pages (the rules catalog):
    every character of the campaigns they direct and the general NPC templates,
    the approved player characters of the campaigns they play, and their own."""
    if not user.is_authenticated:
        return Character.objects.none()
    visible = (Q(campaigns__masters=user)
               | Q(campaigns__players=user, is_npc=False, status=Character.Status.APROBADO)
               | Q(player=user))
    if user.dm_campaigns.exists():
        visible |= Q(is_npc=True, template__isnull=True, campaigns__isnull=True)
    return Character.objects.filter(visible).distinct()


def npc_templates_for(campaign):
    """NPC templates a master of this campaign can open and copy: the ones in the
    campaign, the ones with copies in it, and the general ones (no campaign)."""
    return Character.objects.filter(is_npc=True, template__isnull=True).filter(
        Q(campaigns=campaign) | Q(copies__campaigns=campaign) | Q(campaigns__isnull=True)
    ).distinct()


def character_in_campaign(campaign, character_id, user):
    """A character the user may open from this campaign, else 404.

    Characters of the campaign, plus (for masters) its NPC templates. Players
    cannot open NPC sheets, nor another player's character still pending approval.
    """
    character = get_object_or_404(Character, pk=character_id)
    is_master = campaign.is_master(user)
    if is_master:
        if not (character.campaigns.filter(pk=campaign.pk).exists()
                or npc_templates_for(campaign).filter(pk=character.pk).exists()):
            raise Http404("Ese personaje no está en esta campaña.")
        return character
    if not character.campaigns.filter(pk=campaign.pk).exists() or character.is_npc:
        raise Http404("Ese personaje no está en esta campaña.")
    if character.status == Character.Status.PENDIENTE and character.player_id != user.pk:
        raise Http404("Ese personaje todavía no está aprobado.")
    return character


class CampaignMemberMixin(LoginRequiredMixin):
    """For views under /c/<campaign_id>/: loads the campaign or raises 404."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.campaign = get_campaign_for(request.user, kwargs["campaign_id"])
        self.is_master = self.campaign.is_master(request.user)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(campaign=self.campaign, is_master=self.is_master)
        return context


class MasterRequiredMixin(CampaignMemberMixin):
    """Only the campaign's masters (403 for its players)."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            campaign = get_campaign_for(request.user, kwargs["campaign_id"])
            if not campaign.is_master(request.user):
                raise PermissionDenied("Solo el máster puede hacer esto.")
        return super().dispatch(request, *args, **kwargs)


def campaign_member(view):
    """Decorator for small function views (AJAX endpoints): adds `campaign` and `is_master`."""

    @wraps(view)
    def wrapper(request, campaign_id, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.contrib.auth.views import redirect_to_login
            return redirect_to_login(request.get_full_path())
        campaign = get_campaign_for(request.user, campaign_id)
        request.campaign = campaign
        request.is_master = campaign.is_master(request.user)
        return view(request, campaign, *args, **kwargs)

    return wrapper


def master_only(request):
    if not request.is_master:
        raise PermissionDenied("Solo el máster puede hacer esto.")
