"""Turnos: the combat order, whose turn it is and the end of each round.

The master runs the fight and decides who acts: the order (by initiative or by hand)
and whose turn it is. Players see the order and whose turn it is (the page refreshes
itself by AJAX every few seconds) and can change the hit points, AP and states of their
own characters; the numbers of the enemy side are not sent to them, only how wounded each
one looks (a red veil over its picture, in four steps) and the states it suffers (a tag,
and an animation over its picture: it burns, it bleeds, it is frozen...).

A pair of the bestiary (Shayne and Aurox, `Character.acts_with`) side by side in the order
takes one turn: both act at once, as if they were one.
"""
import math

from django.db import IntegrityError, transaction
from django.db.models import F, Prefetch
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.utils.translation import gettext_noop
from django.views.generic import TemplateView

from . import rules
from .ajax import is_ajax, respond
from .models import ActiveEffect, Armor, Character, Combatant, Effect, Encounter
from .permissions import CampaignMemberMixin, campaign_member, master_only, npc_templates_for
from .services import (
    active_encounter, activation_groups, acts_together, add_condition, copy_into_fight, end_round, group_names,
    partner_for,
)
from .templatetags.mesa import avatar_color
from .views_characters import HP_FIELDS, SheetError, armor_damage_amount, change_hp, hp_event_text, take_damage

HP_ACTIONS = ("vida_menos", "vida_mas", "dano", "curar", "ap_menos", "ap_mas")
STATE_ACTIONS = ("estado_anadir", "estado_quitar")
MAX_STATE_ROUNDS = 99
# How wounded a fighter looks, for everyone: a red veil over its picture that thickens in four steps.
WOUNDS = ["", gettext_noop("Rasguños"), gettext_noop("Herido"), gettext_noop("Malherido"), gettext_noop("Al límite")]
# The animation over a card's picture for each kind of state (mesa.css, .fx-*), found by the words of
# its name: the catalog's states and the master's own («Quemado», «Sangrando», «Congelado»...).
FX = [("sangr", "sangrado"), ("quem", "quemado"), ("ardiend", "quemado"), ("congel", "helado"), ("helad", "helado"),
      ("envenen", "envenenado"), ("aturd", "aturdido"), ("paraliz", "paralizado"), ("cegad", "cegado"),
      ("aterr", "aterrado"), ("asust", "aterrado"), ("atad", "atado"), ("apresad", "atado"), ("agarrad", "atado"),
      ("suspend", "suspendido"), ("hechiz", "hechizado"), ("inconscien", "dormido"), ("invisib", "invisible"),
      ("petrific", "petrificado")]


def wound_level(hp, hp_max):
    """0 unhurt; 1 to 4 for each quarter of its hit points lost (4 at 0 or below). Only the step reaches
    players: enough to judge how a fight goes, not to count hit points."""
    if hp is None or not hp_max or hp_max <= 0:
        return 0
    lost = 1 - max(hp, 0) / hp_max
    return 0 if lost <= 0 else min(len(WOUNDS) - 1, math.ceil(lost * (len(WOUNDS) - 1)))


def effect_kind(name):
    """The animation of a state, by its name ("" for those with none: Cansancio, Ensordecido...)."""
    low = name.lower()
    return next((kind for word, kind in FX if word in low), "")


def _max_ap(armor):
    return armor.ap + armor.armor_type.ap if armor else 0


def _group_at(groups, index):
    """The group of the order that holds the combatant at `index`, and where that group starts."""
    start = 0
    for g, group in enumerate(groups):
        if index < start + len(group):
            return g, start
        start += len(group)
    return len(groups) - 1, start - len(groups[-1])


def turns_context(request, campaign, is_master):
    user = request.user
    encounter = active_encounter(campaign)
    context = {"campaign": campaign, "is_master": is_master, "encounter": encounter}
    if encounter is None:
        return context
    states = ActiveEffect.objects.select_related("effect").order_by("effect__name")
    combatants = list(encounter.combatants.select_related("character__player").prefetch_related(
        Prefetch("character__activeeffect_set", queryset=states), Prefetch("effects", queryset=states)))
    # Equipped armor of every character in the fight, in one query (max AP, game rule 2).
    armors = {a.carrier: a for a in Armor.objects.filter(
        equipped=True, item__inventory__character_id__in=[c.character_id for c in combatants if c.character_id]
    ).select_related("armor_type").annotate(carrier=F("item__inventory__character_id"))}
    # Whose turn it is: one combatant, or a pair that acts at once; and who comes after.
    groups = activation_groups(combatants)
    current = after = None
    if combatants:
        current, _ = _group_at(groups, min(encounter.turn_index, len(combatants) - 1))
        after = (current + 1) % len(groups) if len(groups) > 1 else None
    rows, group_of = [], {}
    for g, group in enumerate(groups):
        for combatant in group:
            group_of[combatant.pk] = g
    for i, combatant in enumerate(combatants):
        character = combatant.character
        pc = character is not None and not character.is_npc
        mine = pc and character.player_id == user.pk
        # The enemy side's numbers are the master's alone: players judge how it goes from what the
        # master tells them, not from hit points. They see the party's characters, and their own
        # wherever it stands (a character turned against the group); never an NPC's.
        shown = is_master or mine or (pc and combatant.with_party)
        wound = wound_level(combatant.current_hp, combatant.max_hp)
        g = group_of[combatant.pk]
        # States show on every card (a fighter on fire is there for all to see); how many rounds
        # they last, only where the numbers do.
        effects = [{"pk": e.pk, "name": e.effect.name, "rounds": e.remaining_turns if shown else None,
                    "kind": effect_kind(e.effect.name), "description": e.effect.description}
                   for e in (character.activeeffect_set.all() if character else combatant.effects.all())]
        rows.append({
            "c": combatant, "current": g == current, "pc": pc, "mine": mine, "shown": shown,
            # The card: its side of the table, its place in the order, its picture.
            "party": combatant.with_party, "position": i + 1, "next": g == after,
            "portrait": character.portrait.url if character is not None and character.portrait else "",
            "color": avatar_color(character if character is not None else combatant),
            "can_touch": is_master or mine,
            "kind": gettext_noop("PJ") if pc else (gettext_noop("PNJ") if character else gettext_noop("Enemigo")),
            "hp": combatant.current_hp if shown else None, "hp_max": combatant.max_hp if shown else None,
            "ap": character.temp_ap if character and shown else None,
            "ap_max": _max_ap(armors.get(character.pk)) if character and shown else None,
            "dead": bool(character and character.dead), "down": bool(character and character.knocked_down),
            "effects": effects, "fx": list(dict.fromkeys(e["kind"] for e in effects if e["kind"])),
            "hint": rules.ability_modifier(character.dexterity) if character and is_master else None,
            "wound": wound, "wound_label": WOUNDS[wound],
            # ‹ › move its turn (itself, or the pair it is in) one place in the order.
            "first": g == 0, "last": g == len(groups) - 1,
            "partner": next((o.display_name for o in groups[g] if o.pk != combatant.pk), ""),
            "sheet": shown and character is not None,
        })
    actor_rows = [r for r in rows if r["current"]]
    next_rows = [r for r in rows if r["next"]]
    context.update(rows=rows, actor=actor_rows[0]["c"] if actor_rows else None,
                   actor_row=actor_rows[0] if actor_rows else None, actor_rows=actor_rows,
                   actor_names=group_names(r["c"] for r in actor_rows),
                   next_row=next_rows[0] if next_rows else None, next_names=group_names(r["c"] for r in next_rows),
                   # The two sides of the table, each in the order of the fight.
                   party_rows=[r for r in rows if r["party"]], enemy_rows=[r for r in rows if not r["party"]],
                   my_turn=any(r["mine"] for r in actor_rows),
                   # The states to put on a card, for whoever can touch one.
                   conditions=list(Effect.objects.conditions()) if is_master or any(r["mine"] for r in rows) else [])
    if is_master:
        inside = {c.character_id for c in combatants if c.character_id}
        in_campaign = Character.objects.filter(campaigns=campaign).exclude(pk__in=inside).order_by("name")
        context.update(
            add_pcs=in_campaign.filter(is_npc=False, status=Character.Status.APROBADO),
            add_npcs=in_campaign.filter(is_npc=True),
            add_templates=[t for t in npc_templates_for(campaign).select_related("acts_with").order_by("name")
                           if not t.characterability_set.filter(ability__unique=True).exists()],
        )
    return context


class TurnsView(CampaignMemberMixin, TemplateView):
    template_name = "turns.html"

    def get(self, request, *args, **kwargs):
        if is_ajax(request):
            html = render_to_string("partials/turns_list.html", turns_context(request, self.campaign, self.is_master), request=request)
            return JsonResponse({"html": html, "target": "#turnos"})
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(turns_context(self.request, self.campaign, self.is_master))
        return context


class TurnError(Exception):
    pass


def _number(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _touch(request, combatant, action, encounter):
    """Hit points and AP from the turns list: on the sheet for characters, on the combatant for enemies.
    For characters, damage goes through their AP and they can be knocked down or die (house rule)."""
    amount = _number(request.POST.get("cantidad"))
    if action in ("dano", "curar"):
        if amount is None or amount <= 0:
            raise TurnError("Escribe una cantidad mayor que 0.")
        delta = -amount if action == "dano" else amount
    else:
        delta = -1 if action.endswith("menos") else 1
    character = combatant.character
    name = combatant.display_name
    if action.startswith("ap"):
        if character is None:
            raise TurnError(f"{name} no lleva armadura de ficha.")
        character.temp_ap = rules.clamp(character.temp_ap + delta, 0, character.max_ap)
        character.save(update_fields=["temp_ap"])
        return ""
    if character is not None:
        try:
            if action == "dano":
                msg = take_damage(character, amount, armor_damage_amount(request.POST.get("penetrante")),
                                  armor_damage_amount(request.POST.get("triturante")), encounter)
            else:
                before, event = change_hp(character, delta, encounter)
                msg = (f"{name}: vida {before} → {character.temp_hp}." if action == "curar" else "")
                msg = (msg + hp_event_text(character, event, True)).strip()
        except SheetError as error:
            raise TurnError(str(error))
        character.save(update_fields=HP_FIELDS)
        return msg
    # Enemies without a sheet: their own hit points, no AP.
    if combatant.hp is None:
        raise TurnError(f"{name} no tiene vida apuntada.")
    before = combatant.hp
    combatant.hp = max(0, before + delta) if combatant.hp_max is None else rules.clamp(before + delta, 0, combatant.hp_max)
    combatant.save(update_fields=["hp"])
    after = combatant.hp
    if action in ("dano", "curar"):
        return f"{name}: vida {before} → {after}." + (" ¡Fuera de combate!" if after == 0 else "")
    return ""


def _state(post, combatant, action):
    """A state put on a card (from its list, for some rounds) or taken off it (the × of its tag): on the
    character's sheet, or on the combatant for an enemy without one."""
    character = combatant.character
    owner = {"character": character} if character is not None else {"combatant": combatant}
    name = combatant.display_name
    if action == "estado_anadir":
        if not post.get("efecto"):
            raise TurnError("Elige un estado.")
        effect = get_object_or_404(Effect.objects.conditions(), pk=_number(post.get("efecto")))
        rounds = _number(post.get("rondas"))
        rounds = 1 if rounds is None or rounds < 1 else min(rounds, MAX_STATE_ROUNDS)
        add_condition(effect, rounds, **owner)
        return f"{name}: {effect.name} durante {rounds} {'ronda' if rounds == 1 else 'rondas'}."
    row = get_object_or_404(ActiveEffect.objects.select_related("effect"), pk=_number(post.get("estado")), **owner)
    row.delete()
    return f"{name}: sin {row.effect.name}."


def _renumber(combatants):
    for order, combatant in enumerate(combatants):
        if combatant.order != order:
            combatant.order = order
            combatant.save(update_fields=["order"])


def _keep_pairs_together(combatants):
    """After sorting: each partner goes right behind the first of its pair (they act at once, with the
    best initiative of the two)."""
    ordered = []
    for combatant in combatants:
        if combatant in ordered:
            continue
        ordered.append(combatant)
        mate = next((o for o in combatants if o not in ordered and acts_together(combatant.character, o.character)), None)
        if mate is not None:
            ordered.append(mate)
    return ordered


def _master_action(request, campaign, encounter, action):
    post = request.POST
    if action == "empezar":
        if encounter is not None:
            raise TurnError("Ya hay un combate en marcha.")
        try:
            encounter = Encounter.objects.create(campaign=campaign)
        except IntegrityError:
            raise TurnError("Ya hay un combate en marcha.")
        for order, character in enumerate(campaign.party.order_by("name")):
            Combatant.objects.create(encounter=encounter, character=character, order=order)
        return "Empieza el combate con el grupo. Añade a los enemigos y apunta la iniciativa."
    if encounter is None:
        raise TurnError("No hay ningún combate en marcha.")
    combatants = list(encounter.combatants.select_related("character"))
    groups = activation_groups(combatants)
    if action == "ronda":
        # A new round when the master says so (fights by sides): cooldowns and effects tick, and the
        # first of the order acts, unless the master gives the turn to another.
        encounter.round += 1
        encounter.turn_index = 0
        end_round(encounter)
        encounter.save(update_fields=["turn_index", "round"])
        return f"Ronda {encounter.round}." + (f" Turno de {group_names(groups[0])}." if groups else "")
    if action == "siguiente":
        if not combatants:
            raise TurnError("Añade combatientes antes de pasar el turno.")
        # The turn passes to the next of the order; a pair takes one turn, so it passes both.
        current, start = _group_at(groups, min(encounter.turn_index, len(combatants) - 1))
        msg = ""
        if current + 1 >= len(groups):
            encounter.turn_index, coming = 0, groups[0]
            encounter.round += 1
            end_round(encounter)
            msg = f"Ronda {encounter.round}. "
        else:
            encounter.turn_index, coming = start + len(groups[current]), groups[current + 1]
        encounter.save(update_fields=["turn_index", "round"])
        return msg + f"Turno de {group_names(coming)}."
    if action == "ordenar":
        actor = combatants[min(encounter.turn_index, len(combatants) - 1)] if combatants else None
        combatants.sort(key=lambda c: (c.initiative is None, -(c.initiative or 0), c.display_name))
        combatants = _keep_pairs_together(combatants)
        _renumber(combatants)
        if actor is not None:
            encounter.turn_index = _group_at(activation_groups(combatants), combatants.index(actor))[1]
            encounter.save(update_fields=["turn_index"])
        return "Orden por iniciativa."
    if action == "terminar":
        encounter.active = False
        encounter.save(update_fields=["active"])
        return f"Combate terminado en la ronda {encounter.round}."
    if action == "anadir":
        initiative = _number(post.get("iniciativa"))
        if post.get("plantilla"):
            template = get_object_or_404(npc_templates_for(campaign).select_related("acts_with"), pk=post["plantilla"])
            try:
                copies = copy_into_fight(template, campaign, encounter, initiative)
            except ValueError:
                raise TurnError(f"{template.name} tiene una habilidad única: no se puede copiar.")
            names = " y ".join(c.name for c in copies)
            return f"{names} {'entra' if len(copies) == 1 else 'entran'} en el combate" + (
                ": actúan a la vez." if len(copies) > 1 else ".")
        order = max((c.order for c in combatants), default=-1) + 1
        if post.get("personaje"):
            character = get_object_or_404(Character, pk=post["personaje"], campaigns=campaign)
            inside = [c.character_id for c in combatants if c.character_id]
            if character.pk in inside:
                raise TurnError(f"{character.name} ya está en el combate.")
            # Its partner, if it has one in the campaign, comes in right behind it.
            joining = [character] + [p for p in [partner_for(character, campaign, inside)] if p is not None]
            for i, one in enumerate(joining):
                Combatant.objects.create(encounter=encounter, character=one, order=order + i, initiative=initiative)
            names = " y ".join(c.name for c in joining)
            return f"{names} {'entra' if len(joining) == 1 else 'entran'} en el combate" + (
                ": actúan a la vez." if len(joining) > 1 else ".")
        name = post.get("nombre", "").strip()[:100]
        hp = _number(post.get("vida"))
        if not name or hp is None or hp <= 0:
            raise TurnError("Pon nombre y vida al enemigo.")
        Combatant.objects.create(encounter=encounter, name=name, hp=hp, hp_max=hp, order=order, initiative=initiative)
        return f"{name} entra en el combate."
    combatant = get_object_or_404(Combatant, pk=post.get("combatiente"), encounter=encounter)
    if action == "iniciativa":
        combatant.initiative = _number(post.get("valor"))
        combatant.save(update_fields=["initiative"])
        return ""
    if action == "turno":
        # The master gives the turn to whoever they want, whatever the order says (to a pair, both).
        g, start = _group_at(groups, combatants.index(combatant))
        encounter.turn_index = start
        encounter.save(update_fields=["turn_index"])
        return f"Turno de {group_names(groups[g])}."
    if action in ("antes", "despues"):
        # One place earlier or later in the order (a pair moves as one); whoever has the turn keeps it.
        actor = combatants[min(encounter.turn_index, len(combatants) - 1)]
        g, _ = _group_at(groups, combatants.index(combatant))
        h = g - 1 if action == "antes" else g + 1
        if 0 <= h < len(groups):
            groups[g], groups[h] = groups[h], groups[g]
            combatants = [c for group in groups for c in group]
            _renumber(combatants)
            encounter.turn_index = _group_at(activation_groups(combatants), combatants.index(actor))[1]
            encounter.save(update_fields=["turn_index"])
        return ""
    if action == "lado":
        # The card goes to the other side of the table: an NPC who fights with the party, a traitor...
        combatant.side = Combatant.Side.ENEMIGOS if combatant.with_party else Combatant.Side.GRUPO
        combatant.save(update_fields=["side"])
        return f"{combatant.display_name} lucha ahora {'con' if combatant.with_party else 'contra'} el grupo."
    if action == "quitar":
        index = combatants.index(combatant)
        combatant.delete()
        if index < encounter.turn_index:
            encounter.turn_index -= 1
        encounter.turn_index = max(0, min(encounter.turn_index, len(combatants) - 2))
        encounter.save(update_fields=["turn_index"])
        return f"{combatant.display_name} sale del combate."
    raise Http404("Acción desconocida.")


@require_POST
@campaign_member
def turns_action(request, campaign):
    action = request.POST.get("accion", "")
    try:
        with transaction.atomic():
            encounter = Encounter.objects.select_for_update().filter(campaign=campaign, active=True).first()
            if action in HP_ACTIONS + STATE_ACTIONS:
                if encounter is None:
                    raise TurnError("No hay ningún combate en marcha.")
                combatant = get_object_or_404(Combatant.objects.select_related("character"),
                                              pk=request.POST.get("combatiente"), encounter=encounter)
                character = combatant.character
                # Players only touch their own characters; the master, everyone.
                if not request.is_master and not (character and not character.is_npc and character.player_id == request.user.pk):
                    master_only(request)
                if action in STATE_ACTIONS:
                    msg = _state(request.POST, combatant, action)
                else:
                    msg = _touch(request, combatant, action, encounter)
            else:
                master_only(request)
                msg = _master_action(request, campaign, encounter, action)
    except TurnError as error:
        msg, failed = str(error), True
    else:
        failed = False
    return respond(request, redirect_to=reverse("turns", args=[campaign.pk]), template="partials/turns_list.html",
                   context=turns_context(request, campaign, request.is_master), target="#turnos", msg=msg, error=failed)
