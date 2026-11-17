"""Logic shared by several screens (home, menu badges, turns)."""
from django.db.models import F, OuterRef, Subquery

from .models import ActiveEffect, CharacterAbility, Encounter, Post, Scene


def scenes_awaiting(user, campaign, is_master):
    """Open scenes where it is the reader's turn to write: the last message they
    can see is not theirs. Masters see every open scene; players, the ones with
    one of their characters. Two queries, whatever the number of scenes."""
    visible = Post.objects.visible_for(user, campaign)
    last_id = visible.filter(scene=OuterRef("pk")).order_by("-created_at", "-id").values("id")[:1]
    scenes = Scene.objects.filter(campaign=campaign, is_open=True).annotate(last_post_id=Subquery(last_id))
    if not is_master:
        scenes = scenes.filter(participants__player=user).distinct()
    scenes = [scene for scene in scenes if scene.last_post_id]
    posts = Post.objects.select_related("author", "as_character").in_bulk([scene.last_post_id for scene in scenes])
    return [(scene, posts[scene.last_post_id]) for scene in scenes if posts[scene.last_post_id].author_id != user.pk]


def active_encounter(campaign):
    return Encounter.objects.filter(campaign=campaign, active=True).first()


def current_combatant(encounter):
    if encounter is None:
        return None
    combatants = list(encounter.combatants.select_related("character"))
    if not combatants:
        return None
    return combatants[min(encounter.turn_index, len(combatants) - 1)]


def end_round(encounter):
    """When a round closes: 1 round less of cooldown for the abilities and of
    duration for the active effects of the characters in the fight. Effects
    that reach 0 are removed (spec 5.6)."""
    character_ids = [pk for pk in encounter.combatants.values_list("character_id", flat=True) if pk]
    CharacterAbility.objects.filter(character_id__in=character_ids, cooldown_left__gt=0).update(cooldown_left=F("cooldown_left") - 1)
    effects = ActiveEffect.objects.filter(character_id__in=character_ids)
    effects.update(remaining_turns=F("remaining_turns") - 1)
    effects.filter(remaining_turns__lte=0).delete()
