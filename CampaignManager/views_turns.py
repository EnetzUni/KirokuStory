"""Turnos: the combat order, whose turn it is and the end of each round.

The master runs the fight. Players see the order and whose turn it is (the page
refreshes itself by AJAX every few seconds) and can change the hit points and
AP of their own characters; the hit points of enemies and NPCs are not sent to them.
"""
from django.db import IntegrityError, transaction
from django.db.models import F, Max, Prefetch
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.utils.translation import gettext_noop
from django.views.generic import TemplateView

from . import rules
from .ajax import is_ajax, respond
from .models import ActiveEffect, Armor, Character, Combatant, Encounter
from .permissions import CampaignMemberMixin, campaign_member, master_only, npc_templates_for
from .services import active_encounter, end_round
from .views_characters import HP_FIELDS, SheetError, armor_damage_amount, change_hp, hp_event_text, take_damage

HP_ACTIONS = ("vida_menos", "vida_mas", "dano", "curar", "ap_menos", "ap_mas")


def _max_ap(armor):
    return armor.ap + armor.armor_type.ap if armor else 0


def turns_context(request, campaign, is_master):
    user = request.user
    encounter = active_encounter(campaign)
    context = {"campaign": campaign, "is_master": is_master, "encounter": encounter}
    if encounter is None:
        return context
    combatants = list(encounter.combatants.select_related("character__player").prefetch_related(
        Prefetch("character__activeeffect_set", queryset=ActiveEffect.objects.select_related("effect").order_by("effect__name"))))
    # Equipped armor of every character in the fight, in one query (max AP, game rule 2).
    armors = {a.carrier: a for a in Armor.objects.filter(
        equipped=True, item__inventory__character_id__in=[c.character_id for c in combatants if c.character_id]
    ).select_related("armor_type").annotate(carrier=F("item__inventory__character_id"))}
    index = min(encounter.turn_index, len(combatants) - 1) if combatants else None
    rows = []
    for i, combatant in enumerate(combatants):
        character = combatant.character
        pc = character is not None and not character.is_npc
        mine = pc and character.player_id == user.pk
        shown = is_master or pc  # players never receive the HP of enemies and NPCs
        rows.append({
            "c": combatant, "current": i == index, "pc": pc, "mine": mine, "shown": shown,
            "can_touch": is_master or mine,
            "kind": gettext_noop("PJ") if pc else (gettext_noop("PNJ") if character else gettext_noop("Enemigo")),
            "hp": combatant.current_hp if shown else None, "hp_max": combatant.max_hp if shown else None,
            "ap": character.temp_ap if character and shown else None,
            "ap_max": _max_ap(armors.get(character.pk)) if character and shown else None,
            "dead": bool(character and character.dead), "down": bool(character and character.knocked_down),
            "effects": [f"{e.effect.name} {e.remaining_turns}" for e in character.activeeffect_set.all()]
            if character and shown else [],
            "hint": rules.ability_modifier(character.dexterity) if character and is_master else None,
            "sheet": shown and character is not None,
        })
    actor = combatants[index] if index is not None else None
    context.update(rows=rows, actor=actor,
                   my_turn=bool(actor and actor.character and not actor.character.is_npc and actor.character.player_id == user.pk))
    if is_master:
        inside = {c.character_id for c in combatants if c.character_id}
        in_campaign = Character.objects.filter(campaigns=campaign).exclude(pk__in=inside).order_by("name")
        context.update(
            add_pcs=in_campaign.filter(is_npc=False, status=Character.Status.APROBADO),
            add_npcs=in_campaign.filter(is_npc=True),
            add_templates=[t for t in npc_templates_for(campaign).order_by("name")
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


def _renumber(combatants):
    for order, combatant in enumerate(combatants):
        if combatant.order != order:
            combatant.order = order
            combatant.save(update_fields=["order"])


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
    if action == "siguiente":
        if not combatants:
            raise TurnError("Añade combatientes antes de pasar el turno.")
        encounter.turn_index += 1
        msg = ""
        if encounter.turn_index >= len(combatants):
            encounter.turn_index = 0
            encounter.round += 1
            end_round(encounter)
            msg = f"Ronda {encounter.round}. "
        encounter.save(update_fields=["turn_index", "round"])
        return msg + f"Turno de {combatants[encounter.turn_index].display_name}."
    if action == "ordenar":
        actor = combatants[min(encounter.turn_index, len(combatants) - 1)] if combatants else None
        combatants.sort(key=lambda c: (c.initiative is None, -(c.initiative or 0), c.display_name))
        _renumber(combatants)
        if actor is not None:
            encounter.turn_index = combatants.index(actor)
            encounter.save(update_fields=["turn_index"])
        return "Orden por iniciativa."
    if action == "terminar":
        encounter.active = False
        encounter.save(update_fields=["active"])
        return f"Combate terminado en la ronda {encounter.round}."
    if action == "anadir":
        order = (encounter.combatants.aggregate(m=Max("order"))["m"] or 0) + 1 if combatants else 0
        if post.get("personaje"):
            character = get_object_or_404(Character, pk=post["personaje"], campaigns=campaign)
            if any(c.character_id == character.pk for c in combatants):
                raise TurnError(f"{character.name} ya está en el combate.")
        elif post.get("plantilla"):
            template = get_object_or_404(npc_templates_for(campaign), pk=post["plantilla"])
            try:
                character = template.make_copy(campaigns=[campaign])
            except ValueError:
                raise TurnError(f"{template.name} tiene una habilidad única: no se puede copiar.")
        else:
            name = post.get("nombre", "").strip()[:100]
            hp = _number(post.get("vida"))
            if not name or hp is None or hp <= 0:
                raise TurnError("Pon nombre y vida al enemigo.")
            Combatant.objects.create(encounter=encounter, name=name, hp=hp, hp_max=hp, order=order,
                                     initiative=_number(post.get("iniciativa")))
            return f"{name} entra en el combate."
        Combatant.objects.create(encounter=encounter, character=character, order=order,
                                 initiative=_number(post.get("iniciativa")))
        return f"{character.name} entra en el combate."
    combatant = get_object_or_404(Combatant, pk=post.get("combatiente"), encounter=encounter)
    if action == "iniciativa":
        combatant.initiative = _number(post.get("valor"))
        combatant.save(update_fields=["initiative"])
        return ""
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
            if action in HP_ACTIONS:
                if encounter is None:
                    raise TurnError("No hay ningún combate en marcha.")
                combatant = get_object_or_404(Combatant.objects.select_related("character"),
                                              pk=request.POST.get("combatiente"), encounter=encounter)
                character = combatant.character
                # Players only touch their own characters; the master, everyone.
                if not request.is_master and not (character and not character.is_npc and character.player_id == request.user.pk):
                    master_only(request)
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
