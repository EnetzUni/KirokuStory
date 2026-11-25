"""Personajes: the party, NPCs and templates, the character sheet and the creator."""
import hashlib
import re
import secrets

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import F, Q
from django.forms import ImageField
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.utils.text import Truncator, get_valid_filename
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView
from django.utils.translation import gettext_noop

from . import bespoke, rules
from .ajax import is_ajax, respond
from .forms import (
    BespokeArmorFields, BespokeWeaponFields, CharacterMasterForm, ClassStepForm, EquipmentStepForm, EquippedArmorForm,
    IdentityStepForm, LevelUpForm, RaceStepForm, ScoresStepForm, UniqueSkillForm, UniqueSkillStepForm,
)
from .models import (
    Ability, ActiveEffect, Armor, ArmorDamage, ArmorType, Character, CharacterAbility, CharacterClass, ClassAbility, ClassAddon,
    Combatant, Effect, Inventory,
    Item, Race, RaceAbility, Spell, SubclassAbility, Subrace, SubraceAbility, UniqueSkill, User, Weapon,
    unique_ability_conflicts,
)
from .permissions import (
    CampaignMemberMixin, campaign_member, can_edit_character, character_in_campaign, master_only, npc_templates_for,
)
from .services import active_encounter, current_combatant

GOLD = "Pieza de oro (po)"
SHEET_TABS = [("res", gettext_noop("Resumen")), ("act", gettext_noop("Acciones")), ("spells", gettext_noop("Conjuros")), ("inv", gettext_noop("Inventario")),
              ("feats", gettext_noop("Rasgos")), ("bio", gettext_noop("Biografía"))]
# Aegis combat magic: the «Conjuros» tab of a class with add-ons (the Arcane Warrior) shows them.
ADDONS_TAB = gettext_noop("Añadidos")


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
        if self.is_master and campaign.is_aegis:  # unique skills are an Aegis rule
            context["skill_requests"] = list(UniqueSkill.objects.filter(
                status=UniqueSkill.Status.PENDIENTE, character__campaigns=campaign).select_related("character__player")
                .distinct().order_by("updated_at"))
            context["xp_party"] = [c for c in pcs if c.status == Character.Status.APROBADO]
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


def names_text(names):
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " y " + names[-1]


@require_POST
@campaign_member
def party_xp(request, campaign):
    """The master gives the same XP to the party members they tick. Whoever gets
    enough for the next level sees «Subir de nivel» on their sheet."""
    master_only(request)
    back = reverse("characters", args=[campaign.pk])
    amount = _positive(request.POST.get("cantidad"))
    if amount is None:
        return respond(request, redirect_to=back, error=True, msg="Escribe cuánta XP das a cada uno.")
    ids = [value for value in request.POST.getlist("personajes") if value.isdigit()]
    chosen = campaign.party.filter(pk__in=ids)
    if not chosen.exists():
        return respond(request, redirect_to=back, error=True, msg="Marca al menos un personaje del grupo.")
    chosen.update(xp=F("xp") + amount)
    chosen = list(campaign.party.filter(pk__in=ids).order_by("name"))
    msg = f"{amount} XP para {names_text(c.name for c in chosen)}."
    ready = [c.name for c in chosen if c.can_level_up]
    if ready:
        msg += f" Ya pueden subir de nivel: {names_text(ready)}."
    return respond(request, redirect_to=back, msg=msg)


# ---------------------------------------------------------------------------
# Habilidad única (game rule 17): the player proposes it, a master approves it
# (changing it if needed) or rejects it. Used by the sheet and the list.
# ---------------------------------------------------------------------------

def unique_skill_of(character):
    return UniqueSkill.objects.filter(character=character).select_related("ability").first()


def _skill_text(data):
    form = UniqueSkillForm(data)
    if not form.is_valid():
        raise SheetError(next(iter(form.errors.values()))[0])
    return form.cleaned_data["name"].strip(), form.cleaned_data["description"].strip()


def propose_unique_skill(request, character, data):
    """A player proposes (or rewrites) their character's unique skill; when a master
    writes it, it is approved at once. Returns the message."""
    name, description = _skill_text(data)
    skill = unique_skill_of(character) or UniqueSkill(character=character)
    if skill.status == UniqueSkill.Status.APROBADA and skill.pk:
        raise SheetError(f"{character.name} ya tiene su habilidad única: {skill.name}.")
    if request.campaign.is_master(request.user):
        try:
            skill.approve(name, description)
        except ValidationError as error:
            raise SheetError(" ".join(error.messages))
        return f"{character.name} tiene la habilidad única {name}."
    skill.propose(name, description)
    skill.save()
    return f"Propuesta enviada: el máster aprobará, cambiará o rechazará {name}."


def review_unique_skill(skill, action, data):
    """A master approves the proposal (with their changes, if any) or rejects it. Returns the message."""
    note = (data.get("nota") or "").strip()[:2000]
    who = skill.character.name
    if action == "rechazar":
        skill.reject(note)
        return f"Has rechazado la habilidad única de {who}." + (" Verá tu nota." if note else "")
    name, description = _skill_text(data)
    changed = (name, description) != (skill.name, skill.description)
    try:
        skill.approve(name, description, note)
    except ValidationError as error:
        raise SheetError(" ".join(error.messages))
    return f"{who} tiene la habilidad única {name}" + (", con tus cambios." if changed else ".")


def change_unique_skill(request, character, action, data):
    """After approval a master can rewrite the unique skill (it stays the same ability,
    with the new name and text) or take it away, so the player can propose another.
    Before approval the player can withdraw the proposal. Returns the message."""
    skill = unique_skill_of(character)
    if skill is None:
        raise SheetError(f"{character.name} no tiene habilidad única.")
    if action == "unica_retirar":
        if skill.status == UniqueSkill.Status.APROBADA:
            raise SheetError("Ya está aprobada: solo el máster puede quitarla.")
        skill.delete()
        return f"Propuesta de habilidad única retirada: {skill.name}."
    if not request.is_master:
        raise PermissionDenied("Solo el máster cambia o quita una habilidad única aprobada.")
    if skill.status != UniqueSkill.Status.APROBADA:
        raise SheetError("Todavía no está aprobada: apruébala o recházala.")
    if action == "unica_quitar":
        # Deleting the ability deletes the skill row and the character's link to it.
        skill.ability.delete()
        return f"{character.name} ya no tiene la habilidad única {skill.name}. Puede proponer otra."
    name, description = _skill_text(data)
    note = (data.get("nota") or "").strip()[:2000]
    skill.ability.name, skill.ability.description = name, description
    skill.ability.save(update_fields=["name", "description"])
    skill.name, skill.description = name, description
    if note:
        skill.master_note = note
    skill.save()
    return f"Habilidad única de {character.name} cambiada: {name}."


@require_POST
@campaign_member
def unique_skill_review(request, campaign, pk):
    """Approve, change or reject a unique skill from the list of things to review."""
    master_only(request)
    if not campaign.is_aegis:
        raise Http404("Las habilidades únicas son una regla de Aegis.")
    skill = get_object_or_404(UniqueSkill.objects.select_related("character"), pk=pk,
                              character__campaigns=campaign, status=UniqueSkill.Status.PENDIENTE)
    back = reverse("characters", args=[campaign.pk])
    try:
        with transaction.atomic():
            msg = review_unique_skill(skill, request.POST.get("accion"), request.POST)
    except SheetError as error:
        return respond(request, redirect_to=back, error=True, msg=str(error))
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


def granted_sources(character):
    """(label, link model, field, source) for the race, subrace, class and subclass
    of the character that grant abilities; missing ones are left out."""
    sources = [
        ("Raza", RaceAbility, "race", character.race), ("Linaje", SubraceAbility, "subrace", character.subrace),
        ("Clase", ClassAbility, "char_class", character.char_class), ("Subclase", SubclassAbility, "subclass", character.subclass),
    ]
    return [source for source in sources if source[3] is not None]


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
        first_attack = next(iter(spell.attacks.all()), None)
        spell_groups.setdefault(spell.level, []).append({
            "spell": spell, "slots": slots, "damage": damages[0][1] if damages else "", "rule": rule,
            "area": first_attack.area if first_attack and first_attack.area else None,
            "components": ", ".join(c.type for c in spell.components.all()),
            "range": "Personal o toque" if spell.range == 0 else ("Especial" if spell.range is None else f"{spell.range} m"),
            "castable": spell.level == 0 or (character.has_magic and spell.level <= max_slot),
        })

    addons = character.char_class.addon_groups()  # Aegis combat magic

    granted = []
    for label, link_model, field, source in granted_sources(character):
        links = link_model.objects.filter(**{field: source}).select_related("ability").order_by("level_required", "ability__name")
        granted.append({"title": f"{label} · {source.name}",
                        "items": [{"ability": link.ability, "level": link.level_required, "reached": level >= link.level_required}
                                  for link in links]})

    abilities = list(character.characterability_set.select_related("ability").order_by("ability__name"))
    # The unique skill: everyone sees it once approved; the proposal, only its player and the masters.
    # Unique skills are an Aegis rule: in an SRD campaign an approved one is just a unique ability.
    skill = unique_skill_of(character) if campaign.is_aegis else None
    if skill and skill.status != UniqueSkill.Status.APROBADA and not can_edit:
        skill = None
    encounter = active_encounter(campaign)
    actor = current_combatant(encounter)
    history = character.events.filter(campaign=campaign)
    if not is_master:
        history = history.filter(hidden=False)
    sheet_view = user.sheet_view if user.sheet_view in User.SheetView.values else User.SheetView.WEB
    context = {
        "campaign": campaign, "is_master": is_master, "sheet_view": sheet_view,
        "character": character, "can_edit": can_edit, "tab": tab,
        "tabs": [{"key": k, "label": ADDONS_TAB if k == "spells" and addons else label, "current": k == tab}
                 for k, label in SHEET_TABS],
        "level": level, "xp_next": next_xp,
        # Past 100 % while the player has not levelled up yet; below 0 if the master took XP back.
        "xp_pct": 100 if next_xp is None else rules.clamp(round(100 * (character.xp - prev_xp) / (next_xp - prev_xp)), 0, 100),
        "stats": [{"abbr": rules.ABILITY_ABBR[key], "score": getattr(character, key),
                   "mod": rules.ability_modifier(getattr(character, key))} for key in rules.ABILITY_KEYS],
        "armor": armor, "max_ap": character.max_ap, "max_magic_ap": character.max_magic_ap,
        "armor_source": f"{armor.item.name} {armor.ap} + {armor.armor_type.type} {armor.armor_type.ap}" if armor else "Sin armadura",
        # Knocked down / dead (house rule): how far it is from dying, and whether a second fall in this fight kills it.
        "knocked_down": character.knocked_down, "death_hp": character.death_hp,
        "fell_this_fight": bool(encounter and character.knocked_down_in_id == encounter.pk),
        "mp_pips": [i < character.magic_points for i in range(character.magic_points_max)],
        "spell_catalog": _spell_catalog(character) if is_master and tab == "spells" else [],
        "effects": list(character.activeeffect_set.select_related("effect").order_by("effect__name")),
        # States to add: the effects that are not weapon or armor traits.
        "effect_catalog": Effect.objects.filter(itemeffect__isnull=True, armortype__isnull=True).distinct().order_by("name") if can_edit else [],
        "abilities": abilities, "tracked": [a for a in abilities if a.tracked],
        # «Habilidades propias» without the unique skill, which has its own card.
        "own_abilities": [a for a in abilities if not (skill and a.ability_id == skill.ability_id)],
        "unique_skill": skill,
        "can_propose_skill": campaign.is_aegis and can_edit and (skill is None or skill.status != UniqueSkill.Status.APROBADA),
        "weapons": weapons, "spell_groups": sorted(spell_groups.items()), "max_slot": max_slot,
        "addon_groups": addons, "addon_dc": character.addon_save_dc if addons else None,
        "inventory": rows, "gold": gold.quantity if gold else 0,
        # The master's own pieces (bespoke) are not offered to players.
        "catalog": (Item.objects.filter(template__isnull=True, **({} if is_master else {"bespoke": False})).order_by("name")
                    if can_edit else []),
        "granted": granted,
        "history": history.select_related("session").order_by("session__number", "id"),
        "my_turn": bool(actor and actor.character_id == character.pk), "encounter": encounter,
    }
    if sheet_view == User.SheetView.FICHA:
        context.update(paper_context(character, weapons, spell_groups, abilities, granted, skill, addons))
    return context


def paper_context(character, weapons, spell_groups, abilities, granted, skill, addons=()):
    """What only the «Vista ficha» (the paper sheet layout) needs, from the same data."""
    mods = {key: rules.ability_modifier(getattr(character, key)) for key in rules.ABILITY_KEYS}
    held = {row.ability_id for row in abilities}
    spells = [s for _, group in sorted(spell_groups.items()) for s in group]
    # Spell save DC and spell attack: 8 + modifier and the modifier of the class's primary
    # ability (proficiency is not modelled yet, as in the skills).
    key = rules.ability_from_name(character.char_class.primary_ability) if character.has_magic or spells else None
    spell_mod = mods[key] if key else None
    hp = character.temp_hp
    return {
        "paper_stats": [{"label": rules.ABILITY_SHEET_LABEL[k], "score": getattr(character, k), "mod": mods[k]} for k in rules.ABILITY_KEYS],
        "paper_skills": [{"name": name, "abbr": rules.ABILITY_ABBR[k], "bonus": mods[k]} for name, k in rules.SKILLS],
        "paper_saves": [{"name": rules.ABILITY_NAMES[k], "bonus": mods[k]} for k in rules.ABILITY_KEYS],
        # Attacks are often named after their weapon («Daga · slash · 1 mano»): no need to say it twice.
        "paper_attacks": [{"weapon": w, "attack": a,
                           "label": a["attack"].name if a["attack"].name.startswith(w["item"].name) else f'{w["item"].name} · {a["attack"].name}'}
                          for w in weapons for a in w["attacks"]],
        "paper_weapons_without_attacks": [w for w in weapons if not w["attacks"]],
        "paper_abilities": [row for row in abilities if not (skill and row.ability_id == skill.ability_id)],
        "paper_granted": [i["ability"] for g in granted for i in g["items"] if i["reached"] and i["ability"].pk not in held],
        "paper_spells": spells,
        "spell_mod": spell_mod, "spell_dc": None if spell_mod is None else 8 + spell_mod,
        "magic_page": character.has_magic or bool(spells) or bool(addons),
        # The current hit points are tinted when low (a quarter or less) and at 0 or below.
        "hp_state": "cero" if hp <= 0 else ("baja" if hp * 4 <= character.hp else ""),
    }


def _spell_catalog(character):
    """Spells a master can teach the character: those of its class list first, then the rest."""
    class_ids = set(character.char_class.spells.values_list("pk", flat=True))
    spells = list(Spell.objects.exclude(pk__in=character.spells.values("pk")).order_by("level", "name"))
    groups = [(f"Lista de {character.char_class.name}", [sp for sp in spells if sp.pk in class_ids]),
              ("Otros conjuros", [sp for sp in spells if sp.pk not in class_ids])]
    return [(label, group) for label, group in groups if group]


CSRF_FIELD = re.compile(r'name="csrfmiddlewaretoken" value="[^"]*"')


def sheet_panel(request, campaign, character):
    """The sheet's panel (#ficha) in the reader's view, and a fingerprint of what it shows.
    The page asks for it every few seconds and only replaces it when the fingerprint
    changes (e.g. the master took hit points away from the Turns screen). The CSRF
    tokens change on every render, so they are left out of the fingerprint."""
    context = sheet_context(request, campaign, character)
    html = render_to_string("partials/sheet_panel.html", context, request=request)
    version = hashlib.sha1(CSRF_FIELD.sub("", html).encode(), usedforsecurity=False).hexdigest()[:16]
    return context, html, version


class SheetView(CampaignMemberMixin, TemplateView):
    template_name = "sheet.html"

    def get(self, request, *args, **kwargs):
        if is_ajax(request):
            character = character_in_campaign(self.campaign, kwargs["pk"], request.user)
            _, html, version = sheet_panel(request, self.campaign, character)
            return JsonResponse({"html": html, "target": "#ficha", "version": version})
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        character = character_in_campaign(self.campaign, self.kwargs["pk"], self.request.user)
        sheet, html, version = sheet_panel(self.request, self.campaign, character)
        # The panel is the output of render_to_string, already escaped by the template
        # engine; it is rendered once to also fingerprint it, so it is marked safe here.
        context.update(sheet, panel=mark_safe(html), sheet_version=version)  # nosec B308 B703
        return context


def _sheet_tab(request):
    tab = request.POST.get("tab") or request.GET.get("tab") or "res"
    return tab if tab in dict(SHEET_TABS) else "res"


def _sheet_response(request, campaign, character, msg="", error=False):
    character = Character.objects.get(pk=character.pk)
    if is_ajax(request):
        _, html, version = sheet_panel(request, campaign, character)
        return JsonResponse({"msg": msg, "error": error, "html": html, "target": "#ficha", "version": version})
    url = reverse("sheet", args=[campaign.pk, character.pk]) + f"?tab={_sheet_tab(request)}"
    return respond(request, redirect_to=url, msg=msg, error=error)


@require_POST
@campaign_member
def sheet_view_choice(request, campaign, pk):
    """«Vista web» / «Vista ficha»: kept on the user, so every sheet opens in that view.
    Anyone who can read the sheet can choose how to see it."""
    character = character_in_campaign(campaign, pk, request.user)
    view = request.POST.get("vista")
    if view not in User.SheetView.values:
        raise Http404("Vista desconocida.")
    request.user.sheet_view = view
    request.user.save(update_fields=["sheet_view"])
    return _sheet_response(request, campaign, character)


def _positive(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


class SheetError(Exception):
    """An action that cannot be done: nothing is saved and the message is shown."""


HP_FIELDS = ["temp_hp", "temp_ap", "dead", "knocked_down_in"]


def change_hp(character, delta, encounter=None):
    """Add `delta` to the current hit points, up to the maximum (house rule):
    at 0 or fewer the character is knocked down; at minus half its maximum
    (rules.death_hp) it dies, and so it does if it is knocked down a second time
    in `encounter`, the fight going on. Dead characters do not change.
    Returns (hit points before, what happened: "", "derribado", "muerto" or "segunda")."""
    if character.dead:
        raise SheetError(f"{character.name} está muerto. Solo el máster puede revivirlo.")
    before, lowest = character.temp_hp, rules.death_hp(character.hp)
    character.temp_hp = rules.clamp(before + delta, lowest, character.hp)
    if character.temp_hp <= lowest:
        character.dead = True
        return before, "muerto"
    if before > 0 >= character.temp_hp:
        if encounter is not None and character.knocked_down_in_id == encounter.pk:
            character.dead = True
            return before, "segunda"
        character.knocked_down_in = encounter
        return before, "derribado"
    return before, ""


def hp_event_text(character, event, in_fight):
    """What to tell after a change of hit points, if anything happened."""
    name = character.name
    if event == "muerto":
        return f" {name} llega a {character.temp_hp} de vida y muere."
    if event == "segunda":
        return f" {name} cae por segunda vez en este combate y muere."
    if event == "derribado":
        again = " o si vuelve a caer en este combate" if in_fight else ""
        return f" ¡{name} cae derribado! Muere si llega a {character.death_hp} de vida{again}."
    return ""


def take_damage(character, amount, penetrating=0, shredding=0, encounter=None):
    """A hit (game rule 3 and house rule): True Damage = Damage - AP goes to the hit
    points. Penetrante ignores that many AP for this hit; triturante takes that many
    AP away after the damage. Otherwise the AP stay as they were. Returns the message."""
    damages = [(kind, n) for kind, n in ((ArmorDamage.Type.PENETRATING, penetrating),
                                         (ArmorDamage.Type.SHREDDING, shredding)) if n]
    ap = character.temp_ap
    met = rules.ap_against_attack(ap, damages)
    true = rules.true_damage(amount, ap, damages)
    before, event = change_hp(character, -true, encounter)
    character.temp_ap = rules.ap_after_attack(ap, damages)
    how = f"{amount} − {met} AP = {true}" if met else f"{amount}"
    if penetrating:
        how += f" (penetrante {penetrating})"
    msg = f"{character.name} recibe {how} de daño: vida {before} → {character.temp_hp}."
    if character.temp_ap != ap:
        msg += f" Triturante {shredding}: AP {ap} → {character.temp_ap}."
    return msg + hp_event_text(character, event, encounter is not None)


def armor_damage_amount(value):
    """Penetrante or triturante of a hit: empty is 0; otherwise a whole number from 0."""
    if value in (None, ""):
        return 0
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise SheetError("Penetrante y triturante son números enteros (o se dejan vacíos).")
    if value < 0:
        raise SheetError("Penetrante y triturante no pueden ser negativos.")
    return value


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
    encounter = active_encounter(request.campaign)
    if action in ("vida_menos", "vida_mas"):
        # The −1 / +1 buttons fix the counter by hand: no AP in between.
        _, event = change_hp(character, -1 if action == "vida_menos" else 1, encounter)
        return hp_event_text(character, event, encounter is not None).strip()
    if action in ("dano", "curar"):
        amount = _positive(post.get("cantidad"))
        if amount is None:
            raise SheetError("Escribe una cantidad mayor que 0.")
        if action == "dano":
            return take_damage(character, amount, armor_damage_amount(post.get("penetrante")),
                               armor_damage_amount(post.get("triturante")), encounter)
        before, _ = change_hp(character, amount, encounter)
        return f"{name} se cura {amount}: vida {before} → {character.temp_hp}."
    if action == "revivir":
        if not request.is_master:
            raise PermissionDenied("Solo el máster revive a un personaje.")
        if not character.dead:
            raise SheetError(f"{name} no está muerto.")
        character.dead, character.knocked_down_in = False, None
        character.temp_hp = max(1, character.temp_hp)
        return f"{name} vuelve a la vida con {character.temp_hp} de vida."
    if action in ("ap_menos", "ap_mas", "ap_quitar", "ap_sumar", "ap_restaurar"):
        # Current AP, between 0 and the maximum the equipped armor gives (game rule 2).
        if action == "ap_restaurar":
            character.temp_ap = character.max_ap
            return f"AP restaurado: {character.temp_ap}."
        amount = 1
        if action in ("ap_quitar", "ap_sumar"):
            amount = _positive(post.get("cantidad"))
            if amount is None:
                raise SheetError("Escribe cuántos AP.")
        before = character.temp_ap
        sign = 1 if action in ("ap_mas", "ap_sumar") else -1
        character.temp_ap = rules.clamp(before + sign * amount, 0, character.max_ap)
        return f"{name}: AP {before} → {character.temp_ap}." if action in ("ap_quitar", "ap_sumar") else ""
    if action in ("xp_dar", "xp_quitar"):
        # Only masters give XP; the level waits until the player levels up.
        if not request.is_master:
            raise PermissionDenied("Solo el máster da experiencia.")
        amount = _positive(post.get("cantidad"))
        if amount is None:
            raise SheetError("Escribe cuánta XP.")
        before = character.xp
        character.xp = before + amount if action == "xp_dar" else max(0, before - amount)
        msg = f"{name}: XP {before} → {character.xp}."
        if character.can_level_up:
            msg += f" Ya puede subir al nivel {character.level + 1}."
        return msg
    if action in ("insp_dar", "insp_gastar"):
        if action == "insp_gastar" and character.inspiration <= 0:
            raise SheetError(f"{name} no tiene inspiración.")
        character.inspiration += 1 if action == "insp_dar" else -1
        return ""
    if action == "magia_ajuste":
        # The master adds or takes away magic points over what the class gives.
        if not request.is_master:
            raise PermissionDenied("Solo el máster ajusta los puntos de magia.")
        try:
            bonus = int(post.get("ajuste", ""))
        except ValueError:
            raise SheetError("Escribe el ajuste: un número, positivo o negativo.")
        before = character.magic_points_max
        character.magic_points_bonus = bonus
        after = character.magic_points_max
        character.magic_points = rules.clamp(character.magic_points + after - before, 0, after)
        return (f"{name}: {character.class_magic_points} puntos de magia por {character.char_class.name} "
                f"nivel {character.level}, ajuste {bonus:+d}. Máximo {before} → {after}.")
    if action.startswith("unica_") and not request.campaign.is_aegis:
        raise SheetError("Esta campaña juega con el SRD: las habilidades únicas son una regla de Aegis.")
    if action == "unica_proponer":
        return propose_unique_skill(request, character, post)
    if action in ("unica_editar", "unica_quitar", "unica_retirar"):
        return change_unique_skill(request, character, action, post)
    if action in ("unica_aprobar", "unica_rechazar"):
        if not request.is_master:
            raise PermissionDenied("Solo el máster revisa las habilidades únicas.")
        skill = get_object_or_404(UniqueSkill, character=character, status=UniqueSkill.Status.PENDIENTE)
        return review_unique_skill(skill, "rechazar" if action == "unica_rechazar" else "aprobar", post)
    if action in ("conjuro_ensenar", "conjuro_olvidar"):
        # Spells are given by the master (bought, learnt in the story...), not chosen when levelling up.
        if not request.is_master:
            raise PermissionDenied("Solo el máster enseña conjuros.")
        if action == "conjuro_olvidar":
            spell = get_object_or_404(character.spells, pk=post.get("conjuro"))
            character.spells.remove(spell)
            return f"{name} olvida {spell.name}."
        spell = get_object_or_404(Spell, pk=post.get("conjuro"))
        if character.spells.filter(pk=spell.pk).exists():
            raise SheetError(f"{name} ya conoce {spell.name}.")
        character.spells.add(spell)
        return f"{name} aprende {spell.name}."
    if action in ("magia_gastar", "magia_recuperar"):
        if not character.has_magic:
            raise SheetError(f"{name} no usa puntos de magia.")
        character.magic_points = rules.clamp(character.magic_points + (1 if action == "magia_recuperar" else -1),
                                             0, character.magic_points_max)
        return ""
    if action == "anadido":
        # Aegis combat magic: an add-on goes with an attack (or a defence) and costs its «a».
        addon = get_object_or_404(ClassAddon, pk=_positive(post.get("anadido")) or 0, char_class_id=character.char_class_id)
        cost = _positive(post.get("coste")) or addon.cost
        if cost not in addon.costs:
            raise SheetError(f"{addon.name} cuesta {addon.cost_text}.")
        if character.magic_points < cost:
            raise SheetError(f"No quedan puntos de magia para {addon.name}: cuesta {cost}a y le quedan {character.magic_points}.")
        character.magic_points -= cost
        return (f"{name} usa {addon.name} ({cost}a): le quedan {character.magic_points} "
                f"de {character.magic_points_max} puntos de magia.")
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
        if character.dead:
            raise SheetError(f"{name} está muerto: no puede descansar.")
        character.temp_hp, character.temp_ap = character.hp, character.max_ap
        if character.has_magic:
            character.magic_points = character.magic_points_max
        return f"{name} hace un descanso largo: vida, AP, puntos de magia y usos al máximo."
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
    `accion`, optionally with an argument after a colon («accion:2»).
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
# Editar ficha (máster): anything on a sheet, with or without the rules
# ---------------------------------------------------------------------------

@campaign_member
def character_edit(request, campaign, pk):
    """The master changes whatever they want on a character or NPC sheet: identity, race and
    class (or new ones), level, XP, hit points, AC, AP, magic, scores, the armor worn; and can
    give it a unique weapon or an armor of their own. Only data that would break is refused."""
    master_only(request)
    character = character_in_campaign(campaign, pk, request.user)
    worn = character.equipped_armor
    data = (request.POST, request.FILES) if request.method == "POST" else ()
    form = CharacterMasterForm(*data, instance=character, campaign=campaign)
    weapon_form = BespokeWeaponFields(*data[:1])
    armor_form = BespokeArmorFields(*data[:1])
    worn_form = None
    if worn:
        worn_form = EquippedArmorForm(*data[:1], initial={
            "worn_type": worn.armor_type.type, "worn_ap": worn.ap + worn.armor_type.ap,
            "worn_magic_ap": worn.magic_ap + worn.armor_type.magic_ap})
    forms_ = [f for f in (form, weapon_form, armor_form, worn_form) if f is not None]
    if request.method == "POST" and all(f.is_valid() for f in forms_):
        with transaction.atomic():
            character = form.save()
            if worn_form is not None:
                armor_type, _ = ArmorType.objects.get_or_create(type=worn_form.cleaned_data["worn_type"],
                                                                defaults={"ap": 0, "magic_ap": 0})
                worn.armor_type = armor_type
                worn.ap = worn_form.cleaned_data["worn_ap"] - armor_type.ap
                worn.magic_ap = worn_form.cleaned_data["worn_magic_ap"] - armor_type.magic_ap
                worn.save(update_fields=["armor_type", "ap", "magic_ap"])
            given = []
            if weapon_form.weapon_data():
                given.append(bespoke.give(character, bespoke.weapon(weapon_form.weapon_data())).name)
            if armor_form.armor_data():
                given.append(bespoke.give(character, bespoke.armor(armor_form.armor_data())).name)
        msg = f"Ficha de {character.name} guardada."
        if given:
            msg += " Lleva ahora " + names_text(given) + "."
        messages.success(request, msg)
        return redirect("sheet", campaign_id=campaign.pk, pk=character.pk)
    return render(request, "character_edit.html", {
        "campaign": campaign, "is_master": True, "character": character, "form": form, "weapon_form": weapon_form,
        "armor_form": armor_form, "worn_form": worn_form, "worn": worn,
        "groups": [
            ("Identidad", ["name", "player", "status", "portrait", "background", "dead"]),
            ("Raza y clase", ["race", "new_race", "new_race_speed", "subrace", "char_class", "new_class", "new_class_magic", "subclass"]),
            ("Nivel y números", ["level", "xp", "hp", "temp_hp", "ac", "temp_ap", "inspiration", "magic_points", "magic_points_bonus"]),
            ("Características", list(rules.ABILITY_KEYS)),
        ],
    })


# ---------------------------------------------------------------------------
# Creador de personajes: 6 steps with a Django form each; the draft is kept in
# the session. A player's character is «pendiente» until the master approves it.
# Masters also make NPCs with it (`npc`): same steps, no player, free scores and
# their own hit points if they want; the NPC is a template of the campaign.
# ---------------------------------------------------------------------------

STEPS = [gettext_noop("Raza y linaje"), gettext_noop("Clase"), gettext_noop("Características"), gettext_noop("Nombre, retrato y trasfondo"), gettext_noop("Equipo inicial"), gettext_noop("Habilidad única"), gettext_noop("Resumen")]
STEP_FORMS = [RaceStepForm, ClassStepForm, ScoresStepForm, IdentityStepForm, EquipmentStepForm, UniqueSkillStepForm]
LAST_STEP = len(STEPS) - 1
UNIQUE_SKILL_STEP = 5  # Aegis only


def hit_die(char_class):
    """Sides of the class hit die, read from its SRD description («Dado de puntos de golpe: 1d10…»)."""
    match = re.search(r"Dado de puntos de golpe: 1d(\d+)", (char_class.description if char_class else "") or "")
    return int(match.group(1)) if match else None


def first_level_hp(char_class, constitution):
    """At level 1: the maximum of the hit die plus the Constitution modifier (SRD), at least 1."""
    die = hit_die(char_class)
    return max(1, die + rules.ability_modifier(constitution)) if die else None


def npc_hp(char_class, constitution, level, aegis):
    """An NPC's hit points when the master leaves them empty: the class at level 1 (a d8 if the
    class does not say its die) and the average of each level above, at least 1 per level."""
    die = hit_die(char_class) or 8
    con = rules.ability_modifier(constitution)
    per_level = (rules.LEVEL_UP_HIT_DIE // 2 + 1) if aegis else (die // 2 + 1)
    return max(1, die + con) + (level - 1) * max(1, per_level + con)


WEAPON_FIELDS = [name for name in EquipmentStepForm.base_fields if name.startswith("weapon_")]
ARMOR_FIELDS = [name for name in EquipmentStepForm.base_fields if name.startswith("armor_")]


def _new_draft(npc=False):
    return {"method": "libre" if npc else "puntos", "scores": {key: 10 if npc else 8 for key in rules.ABILITY_KEYS},
            "weapons": [], "armor": None}


def _initial(draft, step):
    if step == 0:
        return {"race": draft.get("race"), "subrace": draft.get("subrace"), **(draft.get("race_custom") or {})}
    if step == 1:
        return {"char_class": draft.get("char_class"), **(draft.get("class_custom") or {})}
    if step == 2:
        return {"method": draft.get("method", "puntos"), **draft.get("scores", {})}
    if step == 3:
        return {"name": draft.get("name", ""), "background": draft.get("background", ""), "player": draft.get("player"),
                "hp": draft.get("hp"), "level": draft.get("level") or 1, "ac": draft.get("ac")}
    if step == 4:
        return {"weapons": draft.get("weapons", []), "armor": draft.get("armor"),
                **(draft.get("weapon_fields") or {}), **(draft.get("armor_fields") or {})}
    return {"name": draft.get("skill_name", ""), "description": draft.get("skill_description", "")}


def _keep_portrait(draft, form):
    """Save an uploaded portrait into the draft. Called even when another field of the step
    is wrong: the browser empties the file box, so the image would be lost otherwise."""
    upload = (getattr(form, "cleaned_data", None) or {}).get("portrait")
    if upload:
        draft["portrait"] = default_storage.save(f"retratos/{get_valid_filename(upload.name)}", upload)


def _store(draft, step, form):
    data = form.cleaned_data
    if step == 0:
        draft["race"] = data["race"].pk if data.get("race") else None
        draft["subrace"] = data["subrace"].pk if data.get("subrace") else None
        name = (data.get("race_name") or "").strip()
        draft["race_custom"] = {"race_name": name, "race_speed": data.get("race_speed")} if name else None
    elif step == 1:
        draft["char_class"] = data["char_class"].pk if data.get("char_class") else None
        name = (data.get("class_name") or "").strip()
        draft["class_custom"] = {"class_name": name, "class_magic": data.get("class_magic")} if name else None
    elif step == 2:
        draft["method"], draft["scores"] = data["method"], form.scores()
    elif step == 3:
        draft["name"], draft["background"] = data["name"].strip(), data["background"]
        if "player" in form.fields:
            draft["player"] = data["player"].pk if data.get("player") else None
        for name in IdentityStepForm.NPC_ONLY:
            if name in form.fields:
                draft[name] = data.get(name)
        _keep_portrait(draft, form)
    elif step == 4:
        draft["weapons"] = [item.pk for item in data["weapons"]]
        draft["armor"] = data["armor"].pk if data.get("armor") else None
        if "weapon_name" in form.fields:
            draft["weapon_fields"] = {name: data.get(name) for name in WEAPON_FIELDS}
            draft["armor_fields"] = {name: data.get(name) for name in ARMOR_FIELDS}
            draft["bespoke_weapon"], draft["bespoke_armor"] = form.weapon_data(), form.armor_data()
    elif step == 5:
        draft["skill_name"], draft["skill_description"] = data["name"].strip(), data["description"].strip()


def draft_problems(draft, npc=False, aegis=True):
    """What is still missing before the character can be sent, as (step, message)."""
    problems = []
    for step, form in [(0, RaceStepForm(_initial(draft, 0), npc=npc)), (1, ClassStepForm(_initial(draft, 1), npc=npc, aegis=aegis)),
                       (2, ScoresStepForm(_initial(draft, 2), free=npc))]:
        if not form.is_valid():
            problems += [(step, message) for errors in form.errors.values() for message in errors]
    if not draft.get("name"):
        problems.append((3, "Falta el nombre."))
    return problems


def _step_url(campaign, step, npc=False):
    return reverse("npc_creator" if npc else "creator", args=[campaign.pk]) + f"?paso={rules.clamp(step, 0, LAST_STEP)}"


def _form_kwargs(form_class, campaign, is_master, npc):
    if form_class is IdentityStepForm:
        return {"campaign": campaign, "is_master": is_master, "npc": npc}
    if form_class is ScoresStepForm:
        return {"free": npc}
    if form_class is ClassStepForm:
        return {"npc": npc, "aegis": campaign.is_aegis}  # the classes of Aegis only in its campaigns
    if form_class in (RaceStepForm, EquipmentStepForm):
        return {"npc": npc}
    return {}


def _target_step(going, step, skipped):
    """Where a button sends: «siguiente», «atras» or a step of the list on top («paso-3»)."""
    if going and going.startswith("paso-") and going[5:].isdigit():
        target = rules.clamp(int(going[5:]), 0, LAST_STEP)
        return target + 1 if target in skipped else target
    target = step - 1 if going == "atras" else step + 1
    if target in skipped:
        target += -1 if going == "atras" else 1
    return target


@campaign_member
def creator(request, campaign):
    return _creator(request, campaign, npc=False)


@campaign_member
def npc_creator(request, campaign):
    """The same creator, for a master's NPC."""
    master_only(request)
    return _creator(request, campaign, npc=True)


def _creator(request, campaign, npc):
    key = ("creador-pnj:" if npc else "creador:") + str(campaign.pk)
    draft = request.session.get(key) or _new_draft(npc)
    try:
        step = rules.clamp(int(request.POST.get("paso", request.GET.get("paso", 0))), 0, LAST_STEP)
    except ValueError:
        step = 0
    is_master = campaign.is_master(request.user)
    going = request.POST.get("ir")
    jumping = bool(going and going.startswith("paso-"))
    form = None
    skipped = set() if campaign.is_aegis else {UNIQUE_SKILL_STEP}  # an Aegis rule
    if step in skipped:
        return redirect(_step_url(campaign, step + 1, npc))
    if request.method == "POST" and going == "empezar":
        request.session[key] = _new_draft(npc)
        return redirect(_step_url(campaign, 0, npc))
    if step < LAST_STEP:
        form_class = STEP_FORMS[step]
        kwargs = _form_kwargs(form_class, campaign, is_master, npc)
        if request.method == "POST":
            form = form_class(request.POST, request.FILES, **kwargs)
            if form.is_valid():
                _store(draft, step, form)
            elif step == 3:
                _keep_portrait(draft, form)
            # Going back or jumping to another step keeps what is valid and leaves the rest.
            if form.is_valid() or going == "atras" or jumping:
                request.session[key] = draft
                return redirect(_step_url(campaign, _target_step(going, step, skipped), npc))
        else:
            form = form_class(initial=_initial(draft, step), **kwargs)
    elif request.method == "POST":
        if going == "atras" or jumping:
            return redirect(_step_url(campaign, _target_step(going, step, skipped), npc))
        if not draft_problems(draft, npc, campaign.is_aegis):
            character = _create_character(request, campaign, draft, npc)
            request.session.pop(key, None)
            if npc:
                messages.success(request, f"PNJ creado: {character.name}. Está en los PNJ de la campaña y en las plantillas, "
                                          "para copiarlo las veces que quieras.")
            elif character.status == Character.Status.PENDIENTE:
                messages.success(request, f"{character.name} está pendiente: el máster lo aprobará o lo rechazará.")
            else:
                messages.success(request, f"{character.name} se ha unido al grupo.")
            return redirect("characters", campaign_id=campaign.pk)
    request.session[key] = draft
    return render(request, "creator.html", _creator_context(request, campaign, draft, step, form, is_master, npc))


def _creator_context(request, campaign, draft, step, form, is_master, npc=False):
    bound = form is not None and form.is_bound
    race = Race.objects.filter(pk=draft.get("race")).first()
    subrace = Subrace.objects.filter(pk=draft.get("subrace")).first()
    char_class = CharacterClass.objects.filter(pk=draft.get("char_class")).first()
    scores = {**{key: 8 for key in rules.ABILITY_KEYS}, **(draft.get("scores") or {})}
    method = draft.get("method", "libre" if npc else "puntos")
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
    # Players never see the pieces, races and classes a master made by hand.
    catalog = {} if npc else {"bespoke": False}
    weapon_choices = []
    for item in (Item.objects.filter(weapon__isnull=False, template__isnull=True, **catalog)
                 .select_related("weapon").prefetch_related("weapon__attacks__dice").order_by("name")):
        damage = ", ".join(" + ".join(f"{d.amount}d{d.sides}" for d in a.dice.all() if not d.per_extra_level) or str(a.extra)
                           for a in item.weapon.attacks.all())
        weapon_choices.append({"item": item, "damage": damage, "checked": str(item.pk) in chosen_weapons})
    level = (draft.get("level") or 1) if npc else 1
    bespoke_armor = draft.get("bespoke_armor") if npc else None
    if bespoke_armor:
        max_ap = bespoke_armor["ap"]
    else:
        max_ap = (armor.armor.ap + armor.armor.armor_type.ap) if armor else 0
    if npc:
        hp = draft.get("hp") or npc_hp(char_class, scores["constitution"], level, campaign.is_aegis)
    else:
        hp = first_level_hp(char_class, scores["constitution"])
    return {
        "campaign": campaign, "is_master": is_master, "step": step, "form": form, "draft": draft, "npc": npc,
        "steps": [{"n": i, "num": num, "label": label, "current": i == step} for num, (i, label) in enumerate(
            ((i, label) for i, label in enumerate(STEPS) if campaign.is_aegis or i != UNIQUE_SKILL_STEP), start=1)],
        "last_step": LAST_STEP,
        "races": [{"race": r, "checked": str(r.pk) == chosen_race, "subraces": [
            {"subrace": s, "checked": str(s.pk) == chosen_subrace} for s in r.subraces.all()]}
            for r in Race.objects.filter(**catalog).prefetch_related("subraces").order_by("name")],
        "race": race, "subrace": subrace,
        "race_custom": (draft.get("race_custom") or {}).get("race_name", "") if npc else "",
        "race_traits": RaceAbility.objects.filter(race=race, level_required__lte=1).select_related("ability") if race else [],
        "subrace_traits": SubraceAbility.objects.filter(subrace=subrace, level_required__lte=1).select_related("ability") if subrace else [],
        "classes": [{"c": c, "die": hit_die(c), "checked": str(c.pk) == chosen_class}
                    for c in CharacterClass.objects.filter(**catalog, **({} if campaign.is_aegis else {"aegis_only": False}))
                    .order_by("name")],
        "char_class": char_class, "hit_die": hit_die(char_class),
        "class_custom": (draft.get("class_custom") or {}).get("class_name", "") if npc else "",
        "class_traits": ClassAbility.objects.filter(char_class=char_class, level_required__lte=1).select_related("ability") if char_class else [],
        "method": method,
        "stats": [{"key": key, "abbr": rules.ABILITY_ABBR[key], "label": ScoresStepForm.base_fields[key].label,
                   "value": scores[key], "mod": rules.ability_modifier(scores[key])} for key in rules.ABILITY_KEYS],
        "score_min": 1 if npc else 3, "score_max": 30 if npc else 20,
        "points_left": rules.POINT_BUY_TOTAL - spent, "points_total": rules.POINT_BUY_TOTAL,
        "standard": rules.STANDARD_ARRAY, "point_costs": sorted(rules.POINT_BUY_COST.items()),
        "weapon_choices": weapon_choices,
        "armor_choices": [{"item": i, "checked": str(i.pk) == chosen_armor}
                          for i in Item.objects.filter(armor__isnull=False, template__isnull=True, **catalog)
                          .select_related("armor__armor_type").order_by("name")],
        "no_armor": not chosen_armor,
        "weapons": Item.objects.filter(pk__in=draft.get("weapons", [])).order_by("name"), "armor": armor,
        "bespoke_weapon": draft.get("bespoke_weapon") if npc else None, "bespoke_armor": bespoke_armor,
        "level": level, "max_ap": max_ap, "hp": hp,
        "ac": (draft.get("ac") if npc else None) or 10 + rules.ability_modifier(scores["dexterity"]),
        "player": None if npc else (User.objects.filter(pk=draft.get("player")).first() if is_master else request.user),
        "problems": draft_problems(draft, npc, campaign.is_aegis) if step == LAST_STEP else [],
        "portrait_url": default_storage.url(draft["portrait"]) if draft.get("portrait") else "",
    }


@transaction.atomic
def _create_character(request, campaign, draft, npc=False):
    is_master = campaign.is_master(request.user)
    race_custom = (draft.get("race_custom") or {}) if npc else {}
    class_custom = (draft.get("class_custom") or {}) if npc else {}
    race = (bespoke.race(race_custom["race_name"], race_custom.get("race_speed")) if race_custom
            else Race.objects.get(pk=draft["race"]))
    char_class = (bespoke.char_class(class_custom["class_name"], class_custom.get("class_magic")) if class_custom
                  else CharacterClass.objects.get(pk=draft["char_class"]))
    scores = draft["scores"]
    level = (draft.get("level") or 1) if npc else 1
    if npc:
        player = None  # an NPC template of the campaign
        hp = draft.get("hp") or npc_hp(char_class, scores["constitution"], level, campaign.is_aegis)
        ac = draft.get("ac") or 10 + rules.ability_modifier(scores["dexterity"])
    else:
        player = User.objects.filter(pk=draft.get("player")).first() if is_master else request.user
        hp = first_level_hp(char_class, scores["constitution"]) or 1
        ac = 10 + rules.ability_modifier(scores["dexterity"])
    character = Character.objects.create(
        name=draft["name"], race=race, subrace_id=None if race_custom else draft.get("subrace"), char_class=char_class,
        player=player, is_npc=npc, level=level, xp=rules.XP_THRESHOLDS[level - 1],
        status=Character.Status.APROBADO if is_master else Character.Status.PENDIENTE,
        hp=hp, temp_hp=hp, ac=ac,
        background=draft.get("background", ""), portrait=draft.get("portrait", ""), **scores,
    )
    character.campaigns.add(campaign)
    if draft.get("skill_name") and campaign.is_aegis:
        # The unique skill waits for a master, unless a master is creating the character.
        skill = UniqueSkill(character=character)
        if is_master:
            skill.approve(draft["skill_name"], draft["skill_description"])
        else:
            skill.propose(draft["skill_name"], draft["skill_description"])
            skill.save()
    for pk in draft.get("weapons", []) + ([draft["armor"]] if draft.get("armor") else []):
        template = Item.objects.filter(pk=pk, template__isnull=True).first()
        if template is None or (template.bespoke and not npc):
            continue
        bespoke.give(character, template)
    if npc and draft.get("bespoke_weapon"):
        bespoke.give(character, bespoke.weapon(draft["bespoke_weapon"]))
    if npc and draft.get("bespoke_armor"):
        bespoke.give(character, bespoke.armor(draft["bespoke_armor"]))  # worn instead of a catalog armor
    character.temp_ap = character.max_ap
    character.magic_points = character.magic_points_max  # what the class gives at its level, full
    character.save(update_fields=["temp_ap", "magic_points"])
    return character


# ---------------------------------------------------------------------------
# Subir de nivel: the master gives XP, and once it reaches the next level the
# player (or a master) goes up from the sheet, one level at a time, choosing what
# that level gives with the campaign's ruleset (rules.py): hit points, the subclass,
# the ability score improvement or a feat at the feat levels, and in the SRD new
# spells. Magic points follow the class and level in both.
# ---------------------------------------------------------------------------

class LevelUpError(Exception):
    """The level cannot be saved: nothing is changed and the message is shown."""


def level_up_plan(character, campaign):
    """What going up to the next level gives `character` with the campaign's ruleset,
    and what there is to choose."""
    level, ruleset, char_class = character.level + 1, campaign.ruleset, character.char_class
    aegis = ruleset == rules.AEGIS
    features = []
    for label, link_model, field, source in granted_sources(character):
        for link in link_model.objects.filter(**{field: source}, level_required=level).select_related("ability"):
            features.append({"source": f"{label} · {source.name}", "ability": link.ability})
    subclasses = []
    if level >= rules.SUBCLASS_LEVEL and character.subclass_id is None:
        for subclass in character.char_class.subclasses.order_by("name"):
            links = SubclassAbility.objects.filter(subclass=subclass, level_required__lte=level).select_related("ability")
            subclasses.append({"subclass": subclass, "features": [link.ability for link in links.order_by("level_required", "ability__name")]})
    feat_level = rules.is_feat_level(level, ruleset, char_class.name)
    feats = []
    if feat_level:
        # Feats it does not have yet, and no unique feat another character of its campaigns has (game rule 6).
        held, campaigns = character.all_ability_ids(), list(character.campaigns.all())
        feats = [feat for feat in Ability.objects.filter(feat=True, feat_min_level__lte=level).exclude(pk__in=held).order_by("name")
                 if not (feat.unique and unique_ability_conflicts(character, campaigns, [feat.pk]))]
    magic = max(0, rules.class_magic_points(char_class.magic, level) + character.magic_points_bonus)
    # SRD: new spells from the class list, as many as its table adds, up to the highest slot.
    new_cantrips, new_spells, spells, max_slot = 0, 0, [], 0
    if not aegis:
        new_cantrips, new_spells = rules.srd_new_spells(char_class.name, level)
        max_slot = rules.highest_slot(char_class.magic, level)
        candidates = char_class.spells.filter(level__lte=max_slot).exclude(pk__in=character.spells.values("pk"))
        if not new_cantrips:
            candidates = candidates.exclude(level=0)
        if not new_spells:
            candidates = candidates.filter(level=0)
        spells = list(candidates.order_by("level", "name"))
    return {
        "level": level, "ruleset": ruleset, "aegis": aegis, "con": character.modifier("con"),
        # Aegis: the 1d4 typed by the player. SRD: the class hit die (None if its description does not say).
        "die": rules.LEVEL_UP_HIT_DIE if aegis else hit_die(char_class),
        "features": features, "subclasses": subclasses, "feat_level": feat_level, "feats": feats,
        "magic_before": character.magic_points_max, "magic_after": magic,
        "spells": spells, "new_cantrips": new_cantrips, "new_spells": new_spells, "max_slot": max_slot,
    }


@transaction.atomic
def apply_level_up(character_pk, level, form):
    """Save one level up with the form's choices and return the message for the sheet."""
    character = Character.objects.select_for_update().select_related("char_class").get(pk=character_pk)
    if character.level + 1 != level or not character.can_level_up:
        raise LevelUpError(f"{character.name} ya no puede subir al nivel {level}.")
    data, old_level, old_con = form.cleaned_data, character.level, character.modifier("con")
    done = []
    increases = form.increases()
    for key, points in increases.items():
        setattr(character, key, getattr(character, key) + points)
    if increases:
        done.append(" y ".join(f"{rules.ABILITY_ABBR[key]} +{points}" for key, points in increases.items()))
    feat = form.feat()
    if feat is not None:
        done.append(f"dote {feat.name}")
    if data.get("subclass"):
        character.subclass = data["subclass"]
        done.append(f"subclase {character.subclass.name}")
    magic_before = character.magic_points_max
    character.level = level
    # The new level's magic points: the maximum and the current ones go up the same.
    magic_after = character.magic_points_max
    character.magic_points = rules.clamp(character.magic_points + magic_after - magic_before, 0, magic_after)

    con = character.modifier("con")
    if "hp_roll" in form.fields:  # Aegis: the 1d4 rolled at the table
        gain = rules.level_up_hit_points(data["hp_roll"], con)
        how = f"1d{rules.LEVEL_UP_HIT_DIE}: {data['hp_roll']}, Constitución {con:+d}"
    elif "hp_gain" in form.fields:  # SRD, a class whose hit die is unknown
        gain, how = data["hp_gain"], "a mano"
    else:  # SRD: the class hit die, fixed or rolled here
        die, fixed = form.plan["die"], data["hp_method"] == "fijo"
        result = rules.fixed_hit_points(die) if fixed else secrets.randbelow(die) + 1
        gain = rules.level_up_hit_points(result, con)
        how = f"{'valor fijo' if fixed else f'tirada 1d{die}:'} {result}, Constitución {con:+d}"
    # A new Constitution modifier counts for the levels already had too (SRD).
    if con != old_con:
        gain += (con - old_con) * old_level
        how += f", {(con - old_con) * old_level:+d} por la Constitución de los niveles anteriores"
    before = character.hp
    character.hp += gain
    change_hp(character, gain)
    try:
        # Unique abilities (game rule 6): the new level, subclass or feat may grant one.
        character.full_clean(exclude=["portrait"])
        character.save()
        if feat is not None:
            row = CharacterAbility(character=character, ability=feat)
            row.full_clean()
            row.save()
    except ValidationError as error:
        raise LevelUpError(" ".join(error.messages))
    spells = list(data.get("spells") or [])
    if spells:
        character.spells.add(*spells)
        done.append("conjuros: " + names_text(spell.name for spell in spells))

    msg = f"{character.name} sube al nivel {level}. Vida máxima {before} → {character.hp} ({how})."
    if magic_after != magic_before:
        msg += f" Puntos de magia máximos {magic_before} → {magic_after}."
    if done:
        text = "; ".join(done)
        msg += f" {text[0].upper()}{text[1:]}."
    if character.can_level_up:
        msg += f" Todavía puede subir al nivel {level + 1}."
    return msg


@campaign_member
def level_up(request, campaign, pk):
    """The «Subir de nivel» screen. Players level up their own characters; masters, any in their campaign."""
    character = character_in_campaign(campaign, pk, request.user)
    if not can_edit_character(request.user, character, campaign):
        raise PermissionDenied("Esta ficha no es tuya.")
    sheet_url = reverse("sheet", args=[campaign.pk, character.pk])
    if not character.can_level_up:
        next_xp = rules.xp_for_next_level(character.level)
        messages.error(request, f"{character.name} está en el nivel {character.level}"
                       + (f" y necesita {next_xp} XP para el siguiente: tiene {character.xp}." if next_xp else ", el máximo."))
        return redirect(sheet_url)
    plan = level_up_plan(character, campaign)
    form = LevelUpForm(request.POST if request.method == "POST" else None, character=character, plan=plan)
    if request.method == "POST":
        # The choices were made for one level: if it is done already (sent twice), do not repeat them.
        if request.POST.get("nivel") != str(plan["level"]):
            messages.error(request, f"{character.name} ya está en el nivel {character.level}.")
            return redirect(sheet_url)
        if form.is_valid():
            try:
                msg = apply_level_up(character.pk, plan["level"], form)
            except LevelUpError as error:
                form.add_error(None, str(error))
            else:
                messages.success(request, msg)
                return redirect(sheet_url)
    return render(request, "level_up.html", _level_up_context(request, campaign, character, plan, form))


def _level_up_context(request, campaign, character, plan, form):
    bound = form.is_bound
    chosen_subclass = str(form.data.get("subclass", "")) if bound else ""
    choice = str(form.data.get("choice", "")) if bound else LevelUpForm.IMPROVEMENT
    chosen_spells = {str(pk) for pk in form.data.getlist("spells")} if bound else set()
    spell_groups = {}
    for spell in plan["spells"]:
        spell_groups.setdefault(spell.level, []).append({"spell": spell, "checked": str(spell.pk) in chosen_spells})
    con, die = plan["con"], plan["die"]
    return {
        "campaign": campaign, "is_master": request.is_master,
        "character": character, "plan": plan, "form": form,
        "hp_range": (rules.level_up_hit_points(1, con), rules.level_up_hit_points(die, con)) if die else None,
        "hp_fixed_total": rules.level_up_hit_points(rules.fixed_hit_points(die), con) if die and not plan["aegis"] else None,
        "hp_fixed": rules.fixed_hit_points(die) if die else None,
        "hp_method": form.data.get("hp_method", "fijo") if bound else "fijo",
        "spell_groups": [{"level": level, "spells": spells, "open": any(s["checked"] for s in spells)}
                         for level, spells in sorted(spell_groups.items())],
        "stats": [{"key": key, "abbr": rules.ABILITY_ABBR[key], "label": label, "score": getattr(character, key),
                   "mod": rules.ability_modifier(getattr(character, key))} for key, label in LevelUpForm.SCORES],
        "asi_first": form.data.get("asi_first", "") if bound else "",
        "asi_second": form.data.get("asi_second", "") if bound else "",
        "subclasses": [{**option, "checked": str(option["subclass"].pk) == chosen_subclass} for option in plan["subclasses"]],
        "improvement_checked": choice == LevelUpForm.IMPROVEMENT,
        "feats": [{"feat": feat, "checked": str(feat.pk) == choice} for feat in plan["feats"]],
    }
