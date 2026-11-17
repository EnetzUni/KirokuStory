"""Personajes: the party, NPCs and templates, the character sheet and the creator."""
import re

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Q
from django.forms import ImageField
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.text import Truncator, get_valid_filename
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView
from django.utils.translation import gettext_noop

from . import rules
from .ajax import is_ajax, respond
from .forms import ClassStepForm, EquipmentStepForm, IdentityStepForm, RaceStepForm, ScoresStepForm
from .models import (
    ActiveEffect, Armor, Character, CharacterAbility, CharacterClass, ClassAbility, Combatant, Effect, Inventory, Item,
    Race, RaceAbility, SubclassAbility, Subrace, SubraceAbility, User, Weapon,
)
from .permissions import (
    CampaignMemberMixin, campaign_member, can_edit_character, character_in_campaign, master_only, npc_templates_for,
)
from .services import active_encounter, current_combatant

GOLD = "Pieza de oro (po)"
SHEET_TABS = [("res", gettext_noop("Resumen")), ("act", gettext_noop("Acciones")), ("spells", gettext_noop("Conjuros")), ("inv", gettext_noop("Inventario")),
              ("feats", gettext_noop("Rasgos")), ("bio", gettext_noop("Biografía"))]


# ---------------------------------------------------------------------------
# Lista
# ---------------------------------------------------------------------------

class CharacterListView(CampaignMemberMixin, TemplateView):
    template_name = "characters.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        campaign, user = self.campaign, self.request.user
        in_campaign = Character.objects.filter(campaigns=campaign).select_related(
            "race", "subrace", "char_class", "subclass", "player")
        pcs = in_campaign.filter(is_npc=False)
        if not self.is_master:
            pcs = pcs.filter(Q(status=Character.Status.APROBADO) | Q(player=user))
        pcs = list(pcs.order_by("-status", "name"))  # «pendiente» first
        for character in pcs:
            character.can_edit = can_edit_character(user, character, campaign)
        context["party"] = pcs
        if self.is_master:
            with_character = {c.player_id for c in pcs}
            context["no_character"] = [u for u in campaign.players.order_by("username") if u.pk not in with_character]
            context["npcs"] = list(in_campaign.filter(is_npc=True).select_related("template").order_by("name"))
            context["templates"] = [{
                "character": t,
                "copies": t.copies.filter(campaigns=campaign).count(),
                "unique": t.characterability_set.filter(ability__unique=True).exists(),
                "general": not t.campaigns.exists(),
            } for t in npc_templates_for(campaign).select_related("race", "char_class").order_by("name")]
            context["encounter"] = active_encounter(campaign)
        return context


@require_POST
@campaign_member
def character_review(request, campaign, pk):
    """The master approves or rejects a character made by a player."""
    master_only(request)
    character = get_object_or_404(Character, pk=pk, campaigns=campaign, status=Character.Status.PENDIENTE)
    if request.POST.get("accion") == "aprobar":
        character.status = Character.Status.APROBADO
        character.save(update_fields=["status"])
        msg = f"{character.name} ya forma parte del grupo."
    else:
        msg = f"Has rechazado a {character.name}."
        character.delete()
    return respond(request, redirect_to=reverse("characters", args=[campaign.pk]), msg=msg)


@require_POST
@campaign_member
def character_copy(request, campaign, pk):
    """Copy an NPC template into the campaign, or straight into the turns."""
    master_only(request)
    template = get_object_or_404(npc_templates_for(campaign), pk=pk)
    to_turns = bool(request.POST.get("turnos"))
    back = reverse("turns" if to_turns else "characters", args=[campaign.pk])
    try:
        copy = template.make_copy(campaigns=[campaign])
    except ValueError:
        return respond(request, redirect_to=back, error=True,
                       msg=f"{template.name} tiene una habilidad única: no se puede copiar.")
    msg = f"Creado {copy.name}, con su propio equipo."
    if to_turns:
        encounter = active_encounter(campaign)
        if encounter is None:
            return respond(request, redirect_to=reverse("characters", args=[campaign.pk]),
                           msg=msg + " No hay combate en marcha: queda en la campaña.")
        Combatant.objects.create(encounter=encounter, character=copy, order=encounter.combatants.count())
        msg += " Ya está en los turnos."
    return respond(request, redirect_to=back, msg=msg)


# ---------------------------------------------------------------------------
# Ficha
# ---------------------------------------------------------------------------

def spell_info(spell, character, max_slot):
    """Damage of a spell for each slot it can be cast with, and its scaling rule.
    Cantrips scale with character levels 5, 11 and 17; spells, per slot level above theirs."""
    attack = next(iter(spell.attacks.all()), None)
    if attack is None:
        return [], ""
    per = " + ".join(f"{d.amount}d{d.sides}" for d in attack.dice.all() if d.per_extra_level)
    base = attack.damage_text(character, 0)
    if spell.level == 0:
        extra = sum(1 for threshold in (5, 11, 17) if character.level >= threshold)
        rule = f"Daño base {base}" + (f"; +{per} a los niveles 5, 11 y 17." if per else ".")
        return [(0, attack.damage_text(character, extra))], rule
    rule = f"Daño base {base}" + (f"; +{per} por cada nivel de espacio por encima del {spell.level}." if per else ".")
    return [(slot, attack.damage_text(character, slot - spell.level)) for slot in range(spell.level, max_slot + 1)], rule


def sheet_context(request, campaign, character):
    user = request.user
    is_master = campaign.is_master(user)
    can_edit = can_edit_character(user, character, campaign)
    level = character.level
    prev_xp, next_xp = rules.XP_THRESHOLDS[level - 1], rules.xp_for_next_level(level)
    armor = character.equipped_armor
    tab = request.POST.get("tab") or request.GET.get("tab") or "res"
    if tab not in dict(SHEET_TABS):
        tab = "res"

    inventory = list(character.inventory_set.select_related("item__weapon", "item__armor__armor_type").order_by("item__name"))
    gold = next((row for row in inventory if row.item.name == GOLD), None)
    rows, weapons = [], []
    for row in inventory:
        if row is gold:
            continue
        item = row.item
        weapon, armor_row = getattr(item, "weapon", None), getattr(item, "armor", None)
        if weapon:
            kind, desc = gettext_noop("Arma"), f"Golpe {weapon.to_hit}"
        elif armor_row:
            kind = gettext_noop("Armadura")
            desc = (f"{armor_row.armor_type.type} · AP {armor_row.ap + armor_row.armor_type.ap}"
                    f" · AP mágico {armor_row.magic_ap + armor_row.armor_type.magic_ap}")
        else:
            kind, desc = (gettext_noop("Consumible") if item.consumable else gettext_noop("Objeto")), Truncator(item.description).chars(90)
        equipment = weapon or armor_row
        rows.append({"row": row, "item": item, "kind": kind, "desc": desc,
                     "equipment": equipment, "equipped": bool(equipment and equipment.equipped)})
        if weapon and weapon.equipped:
            try:
                to_hit = weapon.to_hit_bonus(character)
            except ValueError:
                to_hit = None
            attacks = []
            for attack in weapon.attacks.prefetch_related("dice", "armor_damages"):
                meta = ["Alcance sin definir" if attack.range is None else f"Alcance {attack.range} m"]
                if attack.area:
                    meta.append(f"área {attack.area} m")
                if attack.cooldown:
                    meta.append(f"enfriamiento {attack.cooldown} rondas")
                attacks.append({"attack": attack, "damage": attack.damage_text(character), "meta": " · ".join(meta),
                                "armor": [str(ad) for ad in attack.armor_damages.all()]})
            traits = [{"label": e.effect.name + (f" {e.value}" if e.value is not None else ""), "title": e.effect.description}
                      for e in item.itemeffect_set.select_related("effect")]
            weapons.append({"item": item, "weapon": weapon, "to_hit": to_hit, "traits": traits, "attacks": attacks})

    max_slot = rules.max_spell_slot(level)
    spell_groups = {}
    for spell in character.spells.prefetch_related("components", "attacks__dice").order_by("level", "name"):
        damages, rule = spell_info(spell, character, max_slot)
        by_slot = dict(damages)
        slots = [{"level": s, "cost": rules.magic_point_cost(s), "damage": by_slot.get(s, "")}
                 for s in range(spell.level, max_slot + 1)] if spell.level > 0 else []
        spell_groups.setdefault(spell.level, []).append({
            "spell": spell, "slots": slots, "damage": damages[0][1] if damages else "", "rule": rule,
            "components": ", ".join(c.type for c in spell.components.all()),
            "range": "Personal o toque" if spell.range == 0 else ("Especial" if spell.range is None else f"{spell.range} m"),
            "castable": spell.level == 0 or (character.has_magic and spell.level <= max_slot),
        })

    granted = []
    for label, link_model, field, source in [
        ("Raza", RaceAbility, "race", character.race), ("Linaje", SubraceAbility, "subrace", character.subrace),
        ("Clase", ClassAbility, "char_class", character.char_class), ("Subclase", SubclassAbility, "subclass", character.subclass),
    ]:
        if source is None:
            continue
        links = link_model.objects.filter(**{field: source}).select_related("ability").order_by("level_required", "ability__name")
        granted.append({"title": f"{label} · {source.name}",
                        "items": [{"ability": link.ability, "level": link.level_required, "reached": level >= link.level_required}
                                  for link in links]})

    abilities = list(character.characterability_set.select_related("ability").order_by("ability__name"))
    encounter = active_encounter(campaign)
    actor = current_combatant(encounter)
    history = character.events.filter(campaign=campaign)
    if not is_master:
        history = history.filter(hidden=False)
    return {
        "campaign": campaign, "is_master": is_master,
        "character": character, "can_edit": can_edit, "tab": tab,
        "tabs": [{"key": k, "label": label, "current": k == tab} for k, label in SHEET_TABS],
        "level": level, "xp_next": next_xp,
        "xp_pct": 100 if next_xp is None else round(100 * (character.xp - prev_xp) / (next_xp - prev_xp)),
        "stats": [{"abbr": rules.ABILITY_ABBR[key], "score": getattr(character, key),
                   "mod": rules.ability_modifier(getattr(character, key))} for key in rules.ABILITY_KEYS],
        "armor": armor, "max_ap": character.max_ap, "max_magic_ap": character.max_magic_ap,
        "armor_source": f"{armor.item.name} {armor.ap} + {armor.armor_type.type} {armor.armor_type.ap}" if armor else "Sin armadura",
        "dying": character.temp_hp <= 0,
        "saves": [{"kind": kind, "label": label, "pips": [{"i": i, "on": i < getattr(character, field)} for i in range(rules.DEATH_SAVES)]}
                  for kind, label, field in [("exito", "Éxitos", "death_successes"), ("fallo", "Fallos", "death_failures")]],
        "mp_pips": [i < character.magic_points for i in range(character.magic_points_max or 0)],
        "effects": list(character.activeeffect_set.select_related("effect").order_by("effect__name")),
        # States to add: the effects that are not weapon or armor traits.
        "effect_catalog": Effect.objects.filter(itemeffect__isnull=True, armortype__isnull=True).distinct().order_by("name") if can_edit else [],
        "abilities": abilities, "tracked": [a for a in abilities if a.tracked],
        "weapons": weapons, "spell_groups": sorted(spell_groups.items()), "max_slot": max_slot,
        "inventory": rows, "gold": gold.quantity if gold else 0,
        "catalog": Item.objects.filter(template__isnull=True).order_by("name") if can_edit else [],
        "granted": granted,
        "history": history.select_related("session").order_by("session__number", "id"),
        "my_turn": bool(actor and actor.character_id == character.pk), "encounter": encounter,
    }


class SheetView(CampaignMemberMixin, TemplateView):
    template_name = "sheet.html"

    def get(self, request, *args, **kwargs):
        if is_ajax(request):
            character = character_in_campaign(self.campaign, kwargs["pk"], request.user)
            html = render_to_string("partials/sheet_panel.html", sheet_context(request, self.campaign, character), request=request)
            return JsonResponse({"html": html, "target": "#ficha"})
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        character = character_in_campaign(self.campaign, self.kwargs["pk"], self.request.user)
        context.update(sheet_context(self.request, self.campaign, character))
        return context


def _sheet_response(request, campaign, character, msg="", error=False):
    character = Character.objects.get(pk=character.pk)
    context = sheet_context(request, campaign, character)
    url = reverse("sheet", args=[campaign.pk, character.pk]) + f"?tab={context['tab']}"
    return respond(request, redirect_to=url, template="partials/sheet_panel.html", context=context,
                   target="#ficha", msg=msg, error=error)


def _positive(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


class SheetError(Exception):
    """An action that cannot be done: nothing is saved and the message is shown."""


def change_hp(character, delta):
    """Add `delta` to the current hit points, between 0 and the maximum. Above 0,
    the death saves are cleared. Returns the hit points before the change."""
    before = character.temp_hp
    character.temp_hp = rules.clamp(before + delta, 0, character.hp)
    if character.temp_hp > 0:
        character.death_successes = character.death_failures = 0
    return before


def _toggle_save(character, field, index):
    """Click on death save box `index`: fill up to it, or empty it if it was the last one filled."""
    index = rules.clamp(index, 0, rules.DEATH_SAVES - 1)
    setattr(character, field, index if getattr(character, field) == index + 1 else index + 1)


def _equip(character, row):
    weapon, armor = Weapon.objects.filter(item=row.item).first(), Armor.objects.filter(item=row.item).first()
    if weapon:
        weapon.equipped = not weapon.equipped
        weapon.full_clean()
        weapon.save(update_fields=["equipped"])
        return f"{row.item.name}: {'equipada' if weapon.equipped else 'guardada'}."
    if armor is None:
        raise SheetError("Ese objeto no se equipa.")
    if not armor.equipped:
        # Only one armor equipped (game rule 5): take off the one worn.
        Armor.objects.filter(equipped=True, item__inventory__character=character).update(equipped=False)
    armor.equipped = not armor.equipped
    armor.full_clean()
    armor.save(update_fields=["equipped"])
    character.temp_ap = character.max_ap
    return f"{row.item.name}: {'puesta' if armor.equipped else 'quitada'}. AP {character.temp_ap}."


def _apply(request, character, action, arg):
    """Run one sheet action on `character` (locked for update) and return the message."""
    post, name = request.POST, character.name
    if action in ("vida_menos", "vida_mas"):
        change_hp(character, -1 if action == "vida_menos" else 1)
        return ""
    if action in ("dano", "curar"):
        amount = _positive(post.get("cantidad"))
        if amount is None:
            raise SheetError("Escribe una cantidad mayor que 0.")
        before = change_hp(character, -amount if action == "dano" else amount)
        verb = "recibe" if action == "dano" else "se cura"
        return f"{name} {verb} {amount}: vida {before} → {character.temp_hp}."
    if action in ("exito", "fallo"):
        if character.temp_hp > 0:
            raise SheetError("Las salvaciones de muerte solo se marcan a 0 de vida.")
        _toggle_save(character, "death_successes" if action == "exito" else "death_failures", arg or 0)
        return ""
    if action in ("ap_menos", "ap_mas", "ap_restaurar"):
        if action == "ap_restaurar":
            character.temp_ap = character.max_ap
            return f"AP restaurado: {character.temp_ap}."
        character.temp_ap = rules.clamp(character.temp_ap + (1 if action == "ap_mas" else -1), 0, character.max_ap)
        return ""
    if action in ("insp_dar", "insp_gastar"):
        if action == "insp_gastar" and character.inspiration <= 0:
            raise SheetError(f"{name} no tiene inspiración.")
        character.inspiration += 1 if action == "insp_dar" else -1
        return ""
    if action in ("magia_gastar", "magia_recuperar"):
        if not character.has_magic:
            raise SheetError(f"{name} no usa puntos de magia.")
        character.magic_points = rules.clamp(character.magic_points + (1 if action == "magia_recuperar" else -1),
                                             0, character.magic_points_max)
        return ""
    if action == "usar_habilidad":
        row = get_object_or_404(CharacterAbility.objects.select_related("ability"), pk=post.get("valor"), character=character)
        try:
            row.use()
        except ValueError:
            raise SheetError(f"{row.ability.name} no está lista.")
        row.save()
        msg = f"{name} usa {row.ability.name}."
        if row.has_uses:
            msg += f" Le quedan {row.uses_left} de {row.max_uses}."
        if row.has_cooldown:
            msg += f" Vuelve a estar lista en {row.cooldown_rounds} rondas."
        return msg
    if action in ("descanso_corto", "descanso_largo"):
        rest = CharacterAbility.Recharge.LARGO if action == "descanso_largo" else CharacterAbility.Recharge.CORTO
        for row in character.characterability_set.all():
            row.recover(rest)
            row.save()
        if rest == CharacterAbility.Recharge.CORTO:
            return f"{name} hace un descanso corto: recupera las habilidades de descanso corto."
        character.temp_hp, character.temp_ap = character.hp, character.max_ap
        if character.has_magic:
            character.magic_points = character.magic_points_max
        character.death_successes = character.death_failures = 0
        return f"{name} hace un descanso largo: vida, AP, puntos de magia, usos y salvaciones al máximo."
    if action == "lanzar":
        spell = get_object_or_404(character.spells, pk=post.get("conjuro"))
        if spell.level == 0:
            return f"{name} lanza {spell.name}. Los trucos no gastan puntos de magia."
        slot = _positive(post.get("espacio")) or spell.level
        if not spell.level <= slot <= rules.max_spell_slot(character.level):
            raise SheetError(f"{name} no puede lanzar {spell.name} con un espacio de nivel {slot}.")
        cost = rules.magic_point_cost(slot)
        if not character.has_magic or character.magic_points < cost:
            raise SheetError(f"No quedan puntos de magia para un espacio de nivel {slot} (cuesta {cost}).")
        character.magic_points -= cost
        return (f"{name} lanza {spell.name} con un espacio de nivel {slot}: "
                f"−{cost} puntos de magia, le quedan {character.magic_points}.")
    if action in ("objeto_menos", "objeto_mas", "usar_objeto", "equipar"):
        row = get_object_or_404(Inventory.objects.select_related("item"), pk=post.get("valor"), character=character)
        if action == "equipar":
            return _equip(character, row)
        if action == "usar_objeto" and not row.item.consumable:
            raise SheetError(f"{row.item.name} no se gasta al usarlo.")
        if action == "objeto_mas" and row.item.is_equipment:
            raise SheetError("Cada arma o armadura es una copia propia: añádela desde el catálogo.")
        row.quantity += 1 if action == "objeto_mas" else -1
        if row.quantity <= 0:
            row.delete()  # the signal also unequips it
            character.temp_ap = min(character.temp_ap, character.max_ap)
        else:
            row.save(update_fields=["quantity"])
        return f"{name} usa {row.item.name}." if action == "usar_objeto" else ""
    if action == "anadir_objeto":
        template = get_object_or_404(Item, pk=post.get("objeto"), template__isnull=True)
        if template.is_equipment:
            # Weapons and armor: an individual copy (game rule 12).
            Inventory.objects.create(character=character, item=template.make_copy(), quantity=1)
        else:
            quantity = _positive(post.get("cantidad")) or 1
            row, created = Inventory.objects.get_or_create(character=character, item=template, defaults={"quantity": quantity})
            if not created:
                row.quantity += quantity
                row.save(update_fields=["quantity"])
        return f"{template.name}, al inventario."
    if action in ("pagar", "recibir"):
        amount = _positive(post.get("cantidad"))
        if amount is None:
            raise SheetError("Escribe cuánto oro.")
        gold = Item.objects.filter(name=GOLD, template__isnull=True).first()
        if gold is None:
            raise SheetError("El catálogo no tiene la pieza de oro.")
        row, _ = Inventory.objects.get_or_create(character=character, item=gold, defaults={"quantity": 0})
        if action == "pagar" and row.quantity < amount:
            raise SheetError(f"{name} solo tiene {row.quantity} po.")
        row.quantity += amount if action == "recibir" else -amount
        row.save(update_fields=["quantity"])
        return f"{name} {'recibe' if action == 'recibir' else 'paga'} {amount} po. Tiene {row.quantity} po."
    if action == "estado_anadir":
        effect = get_object_or_404(Effect, pk=post.get("efecto"))
        rounds = _positive(post.get("rondas")) or 1
        ActiveEffect.objects.create(character=character, effect=effect, remaining_turns=rounds)
        return f"{name}: {effect.name} durante {rounds} rondas."
    if action in ("estado_menos", "estado_mas", "estado_quitar"):
        row = get_object_or_404(ActiveEffect.objects.select_related("effect"), pk=post.get("valor"), character=character)
        if action == "estado_quitar" or (action == "estado_menos" and row.remaining_turns <= 1):
            row.delete()
            return f"{name} ya no tiene {row.effect.name}."
        row.remaining_turns += 1 if action == "estado_mas" else -1
        row.save(update_fields=["remaining_turns"])
        return ""
    if action == "notas":
        character.notes = post.get("notas", "")[:5000]
        character.background = post.get("trasfondo", character.background)[:5000]
        return "Notas guardadas."
    raise Http404("Acción desconocida.")


@require_POST
@campaign_member
def sheet_action(request, campaign, pk):
    """Every counter of the sheet, by AJAX or with a normal form. The action is
    `accion`, optionally with an argument after a colon («exito:2»).
    Players only change their own characters; masters, any in their campaign."""
    character = character_in_campaign(campaign, pk, request.user)
    if not can_edit_character(request.user, character, campaign):
        raise PermissionDenied("Esta ficha no es tuya.")
    action, _, arg = request.POST.get("accion", "").partition(":")
    arg = int(arg) if arg.isdigit() else None
    if action == "retrato":
        return _portrait(request, campaign, character)
    try:
        with transaction.atomic():
            character = Character.objects.select_for_update().get(pk=character.pk)
            msg = _apply(request, character, action, arg)
            character.full_clean(exclude=["portrait"])
            character.save()
    except SheetError as error:
        return _sheet_response(request, campaign, character, str(error), error=True)
    return _sheet_response(request, campaign, character, msg)


def _portrait(request, campaign, character):
    try:
        upload = ImageField().clean(request.FILES.get("retrato"))
    except ValidationError:
        return _sheet_response(request, campaign, character, "Sube una imagen (JPG, PNG o WebP).", error=True)
    character.portrait = upload
    character.save(update_fields=["portrait"])
    return _sheet_response(request, campaign, character, "Retrato cambiado.")


# ---------------------------------------------------------------------------
# Creador de personajes: 6 steps with a Django form each; the draft is kept in
# the session. A player's character is «pendiente» until the master approves it.
# ---------------------------------------------------------------------------

STEPS = [gettext_noop("Raza y linaje"), gettext_noop("Clase"), gettext_noop("Características"), gettext_noop("Nombre, retrato y trasfondo"), gettext_noop("Equipo inicial"), gettext_noop("Resumen")]
STEP_FORMS = [RaceStepForm, ClassStepForm, ScoresStepForm, IdentityStepForm, EquipmentStepForm]
LAST_STEP = len(STEPS) - 1


def hit_die(char_class):
    """Sides of the class hit die, read from its SRD description («Dado de puntos de golpe: 1d10…»)."""
    match = re.search(r"Dado de puntos de golpe: 1d(\d+)", (char_class.description if char_class else "") or "")
    return int(match.group(1)) if match else None


def first_level_hp(char_class, constitution):
    """At level 1: the maximum of the hit die plus the Constitution modifier (SRD), at least 1."""
    die = hit_die(char_class)
    return max(1, die + rules.ability_modifier(constitution)) if die else None


def _new_draft():
    return {"method": "puntos", "scores": {key: 8 for key in rules.ABILITY_KEYS}, "weapons": [], "armor": None}


def _initial(draft, step):
    if step == 0:
        return {"race": draft.get("race"), "subrace": draft.get("subrace")}
    if step == 1:
        return {"char_class": draft.get("char_class")}
    if step == 2:
        return {"method": draft.get("method", "puntos"), **draft.get("scores", {})}
    if step == 3:
        return {"name": draft.get("name", ""), "background": draft.get("background", ""), "player": draft.get("player")}
    return {"weapons": draft.get("weapons", []), "armor": draft.get("armor")}


def _store(draft, step, form):
    data = form.cleaned_data
    if step == 0:
        draft["race"] = data["race"].pk
        draft["subrace"] = data["subrace"].pk if data.get("subrace") else None
    elif step == 1:
        draft["char_class"] = data["char_class"].pk
    elif step == 2:
        draft["method"], draft["scores"] = data["method"], form.scores()
    elif step == 3:
        draft["name"], draft["background"] = data["name"].strip(), data["background"]
        if "player" in form.fields:
            draft["player"] = data["player"].pk if data.get("player") else None
        upload = data.get("portrait")
        if upload:
            draft["portrait"] = default_storage.save(f"retratos/{get_valid_filename(upload.name)}", upload)
    elif step == 4:
        draft["weapons"] = [item.pk for item in data["weapons"]]
        draft["armor"] = data["armor"].pk if data.get("armor") else None


def draft_problems(draft):
    """What is still missing before the character can be sent, as (step, message)."""
    problems = []
    for step, form in [(0, RaceStepForm(_initial(draft, 0))), (1, ClassStepForm(_initial(draft, 1))),
                       (2, ScoresStepForm(_initial(draft, 2)))]:
        if not form.is_valid():
            problems += [(step, message) for errors in form.errors.values() for message in errors]
    if not draft.get("name"):
        problems.append((3, "Falta el nombre."))
    return problems


def _step_url(campaign, step):
    return reverse("creator", args=[campaign.pk]) + f"?paso={rules.clamp(step, 0, LAST_STEP)}"


@campaign_member
def creator(request, campaign):
    key = f"creador:{campaign.pk}"
    draft = request.session.get(key) or _new_draft()
    try:
        step = rules.clamp(int(request.POST.get("paso", request.GET.get("paso", 0))), 0, LAST_STEP)
    except ValueError:
        step = 0
    is_master = campaign.is_master(request.user)
    going = request.POST.get("ir")
    form = None
    if request.method == "POST" and going == "empezar":
        request.session[key] = _new_draft()
        return redirect(_step_url(campaign, 0))
    if step < LAST_STEP:
        form_class = STEP_FORMS[step]
        kwargs = {"campaign": campaign, "is_master": is_master} if form_class is IdentityStepForm else {}
        if request.method == "POST":
            form = form_class(request.POST, request.FILES, **kwargs)
            if form.is_valid():
                _store(draft, step, form)
            if form.is_valid() or going == "atras":
                request.session[key] = draft
                return redirect(_step_url(campaign, step - 1 if going == "atras" else step + 1))
        else:
            form = form_class(initial=_initial(draft, step), **kwargs)
    elif request.method == "POST":
        if going == "atras":
            return redirect(_step_url(campaign, step - 1))
        if not draft_problems(draft):
            character = _create_character(request, campaign, draft)
            request.session.pop(key, None)
            if character.status == Character.Status.PENDIENTE:
                messages.success(request, f"{character.name} está pendiente: el máster lo aprobará o lo rechazará.")
            else:
                messages.success(request, f"{character.name} se ha unido al grupo.")
            return redirect("characters", campaign_id=campaign.pk)
    request.session[key] = draft
    return render(request, "creator.html", _creator_context(request, campaign, draft, step, form, is_master))


def _creator_context(request, campaign, draft, step, form, is_master):
    bound = form is not None and form.is_bound
    race = Race.objects.filter(pk=draft.get("race")).first()
    subrace = Subrace.objects.filter(pk=draft.get("subrace")).first()
    char_class = CharacterClass.objects.filter(pk=draft.get("char_class")).first()
    scores = {**{key: 8 for key in rules.ABILITY_KEYS}, **(draft.get("scores") or {})}
    method = draft.get("method", "puntos")
    if bound and step == 2:
        # Show what was typed, even if it was not valid.
        method = form.data.get("method", method)
        for key in rules.ABILITY_KEYS:
            if str(form.data.get(key, "")).isdigit():
                scores[key] = int(form.data[key])
    chosen_race = str(form.data.get("race", "")) if bound and step == 0 else str(draft.get("race") or "")
    chosen_subrace = str(form.data.get("subrace", "")) if bound and step == 0 else str(draft.get("subrace") or "")
    chosen_class = str(form.data.get("char_class", "")) if bound and step == 1 else str(draft.get("char_class") or "")
    chosen_weapons = [str(pk) for pk in (form.data.getlist("weapons") if bound and step == 4 else draft.get("weapons", []))]
    chosen_armor = str(form.data.get("armor", "")) if bound and step == 4 else str(draft.get("armor") or "")
    armor = Item.objects.filter(pk=draft.get("armor")).select_related("armor__armor_type").first()
    spent = rules.point_buy_spent(v for v in scores.values() if v in rules.POINT_BUY_COST)
    weapon_choices = []
    for item in (Item.objects.filter(weapon__isnull=False, template__isnull=True)
                 .select_related("weapon").prefetch_related("weapon__attacks__dice").order_by("name")):
        damage = ", ".join(" + ".join(f"{d.amount}d{d.sides}" for d in a.dice.all() if not d.per_extra_level) or str(a.extra)
                           for a in item.weapon.attacks.all())
        weapon_choices.append({"item": item, "damage": damage, "checked": str(item.pk) in chosen_weapons})
    return {
        "campaign": campaign, "is_master": is_master, "step": step, "form": form, "draft": draft,
        "steps": [{"n": i, "label": label, "current": i == step} for i, label in enumerate(STEPS)],
        "last_step": LAST_STEP,
        "races": [{"race": r, "checked": str(r.pk) == chosen_race, "subraces": [
            {"subrace": s, "checked": str(s.pk) == chosen_subrace} for s in r.subraces.all()]}
            for r in Race.objects.prefetch_related("subraces").order_by("name")],
        "race": race, "subrace": subrace,
        "race_traits": RaceAbility.objects.filter(race=race, level_required__lte=1).select_related("ability") if race else [],
        "subrace_traits": SubraceAbility.objects.filter(subrace=subrace, level_required__lte=1).select_related("ability") if subrace else [],
        "classes": [{"c": c, "die": hit_die(c), "checked": str(c.pk) == chosen_class}
                    for c in CharacterClass.objects.order_by("name")],
        "char_class": char_class, "hit_die": hit_die(char_class),
        "class_traits": ClassAbility.objects.filter(char_class=char_class, level_required__lte=1).select_related("ability") if char_class else [],
        "method": method,
        "stats": [{"key": key, "abbr": rules.ABILITY_ABBR[key], "label": ScoresStepForm.base_fields[key].label,
                   "value": scores[key], "mod": rules.ability_modifier(scores[key])} for key in rules.ABILITY_KEYS],
        "points_left": rules.POINT_BUY_TOTAL - spent, "points_total": rules.POINT_BUY_TOTAL,
        "standard": rules.STANDARD_ARRAY, "point_costs": sorted(rules.POINT_BUY_COST.items()),
        "weapon_choices": weapon_choices,
        "armor_choices": [{"item": i, "checked": str(i.pk) == chosen_armor}
                          for i in Item.objects.filter(armor__isnull=False, template__isnull=True)
                          .select_related("armor__armor_type").order_by("name")],
        "no_armor": not chosen_armor,
        "weapons": Item.objects.filter(pk__in=draft.get("weapons", [])).order_by("name"), "armor": armor,
        "max_ap": (armor.armor.ap + armor.armor.armor_type.ap) if armor else 0,
        "hp": first_level_hp(char_class, scores["constitution"]),
        "ac": 10 + rules.ability_modifier(scores["dexterity"]),
        "player": User.objects.filter(pk=draft.get("player")).first() if is_master else request.user,
        "problems": draft_problems(draft) if step == LAST_STEP else [],
        "portrait_url": default_storage.url(draft["portrait"]) if draft.get("portrait") else "",
    }


@transaction.atomic
def _create_character(request, campaign, draft):
    is_master = campaign.is_master(request.user)
    char_class = CharacterClass.objects.get(pk=draft["char_class"])
    scores = draft["scores"]
    hp = first_level_hp(char_class, scores["constitution"]) or 1
    character = Character.objects.create(
        name=draft["name"], race_id=draft["race"], subrace_id=draft.get("subrace"), char_class=char_class,
        player=User.objects.filter(pk=draft.get("player")).first() if is_master else request.user,
        status=Character.Status.APROBADO if is_master else Character.Status.PENDIENTE,
        xp=0, hp=hp, temp_hp=hp, ac=10 + rules.ability_modifier(scores["dexterity"]),
        background=draft.get("background", ""), portrait=draft.get("portrait", ""), **scores,
    )
    character.campaigns.add(campaign)
    for pk in draft.get("weapons", []) + ([draft["armor"]] if draft.get("armor") else []):
        template = Item.objects.filter(pk=pk, template__isnull=True).first()
        if template is None:
            continue
        copy = template.make_copy()
        Inventory.objects.create(character=character, item=copy, quantity=1)
        Weapon.objects.filter(item=copy).update(equipped=True)
        Armor.objects.filter(item=copy).update(equipped=True)
    character.temp_ap = character.max_ap
    character.save(update_fields=["temp_ap"])
    return character
