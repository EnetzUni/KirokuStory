"""Logic shared by several screens (home, menu badges, turns)."""
from django.db.models import F, Max, OuterRef, Q, Subquery

from .models import ActiveEffect, Character, CharacterAbility, Combatant, Encounter, Post, Scene


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
    duration for the active effects of everyone in the fight (characters, and
    enemies without a sheet). Effects that reach 0 are removed (spec 5.6)."""
    character_ids = [pk for pk in encounter.combatants.values_list("character_id", flat=True) if pk]
    CharacterAbility.objects.filter(character_id__in=character_ids, cooldown_left__gt=0).update(cooldown_left=F("cooldown_left") - 1)
    effects = ActiveEffect.objects.filter(Q(character_id__in=character_ids) | Q(combatant__encounter=encounter))
    effects.update(remaining_turns=F("remaining_turns") - 1)
    effects.filter(remaining_turns__lte=0).delete()


def add_condition(effect, rounds, character=None, combatant=None):
    """Put a state on a character (or on an enemy without a sheet) for some rounds. A state it already
    suffers is not doubled: it lasts the longer of the two."""
    owner = {"character": character} if character is not None else {"combatant": combatant}
    row = ActiveEffect.objects.filter(effect=effect, **owner).first()
    if row is None:
        return ActiveEffect.objects.create(effect=effect, remaining_turns=rounds, **owner)
    if rounds > row.remaining_turns:
        row.remaining_turns = rounds
        row.save(update_fields=["remaining_turns"])
    return row


# ---- Pairs that act at once (Shayne and Aurox) ----

def acts_together(a, b):
    """Whether two characters take one activation: one of them acts with the other's template (or
    with the other itself, when it is the template)."""
    if a is None or b is None or a.pk == b.pk:
        return False
    return a.acts_with_id == (b.template_id or b.pk) or b.acts_with_id == (a.template_id or a.pk)


def activation_groups(combatants):
    """The fight's order in turns: one combatant each, or two partners side by side in the order
    (a pair of the bestiary acts at once, as if it were one)."""
    groups, i = [], 0
    while i < len(combatants):
        size = 2 if i + 1 < len(combatants) and acts_together(combatants[i].character, combatants[i + 1].character) else 1
        groups.append(combatants[i:i + size])
        i += size
    return groups


def group_names(group):
    return " y ".join(c.display_name for c in group)


def partner_for(character, campaign, inside=()):
    """Who comes into a fight with this character: its partner template, or a copy of it, from the
    campaign and not in the fight yet. None if it has no partner (or none is free)."""
    if character is None or not character.acts_with_id:
        return None
    found = Character.objects.filter(campaigns=campaign).exclude(pk__in=inside).exclude(pk=character.pk)
    if character.template_id is None:
        return found.filter(pk=character.acts_with_id).first()
    return found.filter(template_id=character.acts_with_id).order_by("pk").first()


def copy_into_fight(template, campaign, encounter, initiative=None):
    """A copy of an NPC template, last in the order of the fight; a template that acts with another
    brings a copy of its partner right behind it. Returns the copies (ValueError: unique ability)."""
    order = (encounter.combatants.aggregate(m=Max("order"))["m"] or 0) + 1 if encounter.combatants.exists() else 0
    copies = [template.make_copy(campaigns=[campaign])]
    partner = template.acts_with
    if partner is not None and partner.is_npc_template and partner.pk != template.pk:
        try:
            copies.append(partner.make_copy(campaigns=[campaign]))
        except ValueError:  # a partner with a unique ability stays out
            pass
    for i, copy in enumerate(copies):
        Combatant.objects.create(encounter=encounter, character=copy, order=order + i, initiative=initiative)
    return copies
