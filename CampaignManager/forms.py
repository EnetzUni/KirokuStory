"""Public forms of «Kiroku Story» (ModelForms with their validation)."""
from django import forms
from django.db.models import Exists, OuterRef, Q
from django.forms import inlineformset_factory

from . import bespoke, rules, weather
from .campaign_models import COLOR_PALETTE
from .mapping import parse_path
from .permissions import npc_templates_for
from .models import (
    BoardCard, Campaign, Character, CharacterClass, Item, Map, MapLocation, Page, Post, Quest, QuestObjective, Race,
    Rumour, RumourCategory, Scene, Session, Spell, Stat, Subclass, Subrace, TimelineEvent, User,
    time_zone_choices,
)


class CampaignFormMixin:
    """ModelForm for a model with a campaign: the campaign comes from the URL."""

    def __init__(self, *args, campaign=None, **kwargs):
        self.campaign = campaign
        super().__init__(*args, **kwargs)
        if campaign is not None and hasattr(self.instance, "campaign_id"):
            self.instance.campaign = campaign


class SessionForm(CampaignFormMixin, forms.ModelForm):
    class Meta:
        model = Session
        fields = ["number", "title", "played_on", "world_day", "body", "xp_awarded", "loot", "master_notes", "published"]
        labels = {
            "number": "Número", "title": "Título", "played_on": "Fecha de la partida", "world_day": "Día en el mundo",
            "body": "Lo que pasó", "xp_awarded": "XP repartida", "loot": "Botín",
            "master_notes": "Notas del máster (solo las ves tú)", "published": "Publicada (los jugadores la ven)",
        }
        widgets = {"played_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
                   "body": forms.Textarea(attrs={"rows": 10}), "master_notes": forms.Textarea(attrs={"rows": 3})}
        help_texts = {"body": "Escribe [[Nombre]] para enlazar una página del Mundo o la ficha de un personaje. Una línea en blanco separa párrafos."}

    def clean_number(self):
        number = self.cleaned_data["number"]
        if Session.objects.filter(campaign=self.campaign, number=number).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Ya hay una sesión con ese número en esta campaña.")
        return number


class EventForm(CampaignFormMixin, forms.ModelForm):
    class Meta:
        model = TimelineEvent
        fields = ["title", "kind", "hidden", "description", "secret"]
        labels = {"title": "Qué ha pasado", "kind": "Tipo", "hidden": "Solo para el máster",
                  "description": "Descripción", "secret": "Secreto del máster (solo lo ves tú)"}


class BoardCardForm(CampaignFormMixin, forms.ModelForm):
    class Meta:
        model = BoardCard
        fields = ["kind", "title", "text", "image", "page", "classified"]
        labels = {"kind": "Tipo", "title": "Título", "text": "Texto", "image": "Foto", "page": "Página del Mundo",
                  "classified": "Clasificada: los jugadores la verán oculta hasta que la reveles"}
        widgets = {"text": forms.Textarea(attrs={"rows": 3}), "kind": forms.RadioSelect}

    def clean(self):
        cleaned = super().clean()
        cleaned["kind"] = cleaned.get("kind") or BoardCard.Kind.PISTA
        if cleaned["kind"] != BoardCard.Kind.PERSONA:
            cleaned["image"] = None  # only people have a photo
        return cleaned

    def __init__(self, *args, is_master=False, visible_pages=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["page"].queryset = visible_pages if visible_pages is not None else Page.objects.none()
        self.fields["page"].required = False
        self.fields["kind"].required = False
        if not is_master:
            del self.fields["classified"]


class SceneForm(CampaignFormMixin, forms.ModelForm):
    class Meta:
        model = Scene
        fields = ["title", "location", "participants", "is_open"]
        labels = {"title": "Título", "location": "Lugar", "participants": "Participan", "is_open": "Abierta"}
        widgets = {"participants": forms.CheckboxSelectMultiple}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["location"].queryset = Page.objects.filter(campaign=self.campaign).order_by("title")
        self.fields["participants"].queryset = Character.objects.filter(campaigns=self.campaign).order_by("is_npc", "name")


class PostForm(forms.Form):
    """A message in a scene. `as_who` is a character id or "narrador"."""

    as_who = forms.ChoiceField(label="Escribir como")
    text = forms.CharField(label="Mensaje", widget=forms.Textarea(attrs={"rows": 4}), max_length=5000,
                           help_text="Empieza con /tirar 1d20+3 para tirar dados con el mensaje.")
    whisper = forms.BooleanField(label="Susurro al máster", required=False)
    whisper_to = forms.ModelChoiceField(label="Susurrar a", queryset=Character.objects.none(), required=False,
                                        empty_label="Nadie: lo ve toda la mesa")

    def __init__(self, *args, options=(), whisper_targets=None, is_master=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["as_who"].choices = list(options)
        if is_master:
            del self.fields["whisper"]
            self.fields["whisper_to"].queryset = whisper_targets
        else:
            del self.fields["whisper_to"]


class ColorPaletteWidget(forms.Widget):
    """A colour chosen with one click among the palette's swatches, none, or any other with the
    browser's colour picker («Otro»). Sends the colour as #RRGGBB, or "" for none."""
    template_name = "widgets/paleta_color.html"
    OTHER = "otro"

    def __init__(self, palette=COLOR_PALETTE, attrs=None):
        super().__init__(attrs)
        self.palette = palette

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        value = (value or "").upper()
        known = {color.upper() for color, _ in self.palette}
        other = bool(value) and value not in known
        context["widget"].update(
            swatches=[{"color": color, "label": label, "checked": value == color.upper()} for color, label in self.palette],
            none_checked=not value, other_checked=other, other_value=(value if other else self.palette[0][0]).lower(),
            other_name=f"{name}_{self.OTHER}", group_label=self.attrs.get("aria-label", ""))
        return context

    def value_from_datadict(self, data, files, name):
        choice = data.get(name, "")
        return data.get(f"{name}_{self.OTHER}", "") if choice == self.OTHER else choice

    def id_for_label(self, id_):
        return ""  # a group of swatches: the label names the group (aria-label), not one of them


class CoverInput(forms.ClearableFileInput):
    """An image field shown as its picture now, «Quitar la portada» and the file box."""
    template_name = "widgets/portada.html"

    def __init__(self, attrs=None):
        super().__init__({"accept": "image/*", **(attrs or {})})


class PageImageInput(CoverInput):
    """The image of a world page in its editor: a button to choose it and «Quitar la imagen».
    The picture itself shows in the editor's preview, above it."""
    template_name = "widgets/imagen_pagina.html"


class QuestForm(CampaignFormMixin, forms.ModelForm):
    """An adventure: status, the sessions it spans and (in the formset) its objectives. Its colour
    and header image help tell it apart; a mission without a colour takes its adventure's."""

    class Meta:
        model = Quest
        fields = ["title", "parent", "color", "cover", "description", "status", "start_session", "end_session", "giver",
                  "secret", "hidden"]
        labels = {"title": "Título", "parent": "Dentro de la aventura", "description": "Descripción", "status": "Estado",
                  "giver": "Quién la encarga", "color": "Color", "cover": "Portada",
                  "start_session": "Empieza en la sesión", "end_session": "Termina en la sesión",
                  "secret": "Secreto del máster (solo lo ves tú)", "hidden": "Oculta: los jugadores no la ven"}
        help_texts = {"end_session": "Déjalo vacío mientras está en curso. Al completarla, fallarla o acabarla sin dato, la sesión en que acabó.",
                      "start_session": "",
                      "description": "Los jugadores la leen. Lo que no deben saber va en el secreto del máster.",
                      "parent": "Vacío: es una aventura (un arco). Si eliges una, esta es una de sus misiones y se cuenta dentro de ella.",
                      "color": "Para distinguirla de un vistazo en «Aventuras», la línea del tiempo y el inicio. "
                               "Una misión sin color usa el de su aventura.",
                      "cover": "Una imagen de cabecera, como la portada de la campaña: se ve en pequeño encima de la aventura, también plegada."}
        widgets = {"description": forms.Textarea(attrs={"rows": 3}), "secret": forms.Textarea(attrs={"rows": 3}),
                   "color": ColorPaletteWidget(attrs={"aria-label": "Color"}), "cover": CoverInput()}

    def clean_color(self):
        return (self.cleaned_data.get("color") or "").upper()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["giver"].queryset = Page.objects.filter(campaign=self.campaign).order_by("title")
        sessions = Session.objects.filter(campaign=self.campaign).order_by("number")
        for name in ("start_session", "end_session"):
            self.fields[name].queryset = sessions
            self.fields[name].label_from_instance = lambda s: f"{s.number} · {s.title}"
        self.fields["end_session"].empty_label = "Todavía no ha terminado"
        self.fields["start_session"].empty_label = "Sin sesión"
        # Only adventures (no missions) can hold missions, and an adventure with missions stays one.
        adventures = Quest.objects.filter(campaign=self.campaign, parent__isnull=True).order_by("title")
        if self.instance.pk:
            adventures = adventures.exclude(pk=self.instance.pk)
        self.fields["parent"].queryset = adventures
        self.fields["parent"].empty_label = "Ninguna: es una aventura"
        if self.instance.pk and self.instance.missions.exists():
            self.fields["parent"].disabled = True
            self.fields["parent"].help_text = "Tiene misiones dentro, así que es una aventura."


# The adventure's checklist. One empty form: the «new objective» box. With JavaScript,
# «Añadir objetivo» turns it into a row of the list and empties it for the next one, so
# there is no limit; the order is given when saving (new ones go at the end).
ObjectiveFormSet = inlineformset_factory(
    Quest, QuestObjective, fields=["text", "done", "hidden"], extra=1, can_delete=True,
    labels={"text": "Objetivo", "done": "Cumplido", "hidden": "Oculto (se revela al cumplirlo)"},
    widgets={"text": forms.TextInput(attrs={"class": "entrada", "maxlength": 300})},
)


class PageForm(CampaignFormMixin, forms.ModelForm):
    """A page of the world. The editor (world_edit.html) lays it out: title and category on top, the two
    texts with their link finder, and the rest (visibility, image, where it is, its sheets) to one side."""

    class Meta:
        model = Page
        fields = ["title", "category", "revealed", "parent", "summary", "body", "secret", "image", "location", "sheets"]
        labels = {"title": "Título", "category": "Categoría", "revealed": "Visible para el grupo", "parent": "Dentro de",
                  "summary": "Resumen", "body": "Lo que sabe el grupo", "secret": "Secreto del máster", "image": "Imagen",
                  "location": "Lugar del mapa", "sheets": "Fichas"}
        widgets = {"title": forms.TextInput(attrs={"class": "editor-titulo", "placeholder": "Título de la página",
                                                   "autocomplete": "off"}),
                   "category": forms.RadioSelect, "revealed": forms.CheckboxInput(attrs={"role": "switch"}),
                   "body": forms.Textarea(attrs={"rows": 9, "placeholder": "Lo que el grupo sabe o ha oído."}),
                   "secret": forms.Textarea(attrs={"rows": 4, "placeholder": "Lo que solo sabes tú."}),
                   "summary": forms.Textarea(attrs={"rows": 2}), "image": PageImageInput,
                   "sheets": forms.CheckboxSelectMultiple(attrs={"class": "lista-fichas"})}
        help_texts = {"body": "Para enlazar otra página, escribe su nombre entre dobles corchetes, como [[Puerto Gris]], "
                              "o [[Puerto Gris|el puerto]] para enlazar otras palabras. Una línea en blanco separa párrafos.",
                      "revealed": "Si no, los jugadores no la ven: ni en el menú ni en los enlaces.",
                      "summary": "Se ve en la vista previa al pasar sobre un enlace. Si lo dejas vacío, se usa la primera frase.",
                      "location": "Solo para lugares.", "parent": "Solo un lugar o una facción.", "secret": "",
                      "sheets": "Las fichas de esta página: la de su PNJ, las tropas de una facción o de un lugar, un personaje de aquí. "
                                "La página las enseña con sus números y cada ficha enlaza de vuelta. Los jugadores solo ven las del grupo."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.fields["title"].widget.attrs["autofocus"] = True  # a new page starts by its name
        others = Page.objects.filter(campaign=self.campaign, category__in=Page.PARENT_CATEGORIES)
        if self.instance.pk:
            others = others.exclude(pk=self.instance.pk)
        self.fields["parent"].queryset = others.order_by("title")
        self.fields["parent"].empty_label = "Ninguno"
        taken = Page.objects.filter(campaign=self.campaign, location__isnull=False).exclude(pk=self.instance.pk)
        self.fields["location"].queryset = (MapLocation.objects.filter(map__campaign=self.campaign)
                                            .exclude(page__in=taken).select_related("map").order_by("map__order", "name"))
        self.fields["location"].label_from_instance = lambda loc: f"{loc.map.name} · {loc.name}"
        # The party and the NPC templates the master can open from this campaign (not the copies of a fight),
        # the ones already on this page first.
        templates = npc_templates_for(self.campaign).values("pk")
        linked = Page.sheets.through.objects.filter(page_id=self.instance.pk, character_id=OuterRef("pk"))
        self.fields["sheets"].queryset = (Character.objects.filter(Q(campaigns=self.campaign, is_npc=False) | Q(pk__in=templates))
                                          .distinct().annotate(linked=Exists(linked)).order_by("-linked", "is_npc", "name"))
        self.fields["sheets"].label_from_instance = sheet_label

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("location") and cleaned.get("category") != Page.Category.LUGAR:
            self.add_error("location", "Solo las páginas de lugar pueden tener un lugar del mapa.")
        return cleaned


def sheet_label(character):
    """«Leah · PJ» or «Samurái Takeda · PNJ, nivel 5»: who each box of «Fichas» is."""
    return f"{character.name} · PNJ, nivel {character.level}" if character.is_npc else f"{character.name} · PJ"


class MapForm(CampaignFormMixin, forms.ModelForm):
    class Meta:
        model = Map
        fields = ["name", "image", "order"]
        labels = {"name": "Nombre", "image": "Imagen", "order": "Orden"}


class LocationForm(forms.ModelForm):
    class Meta:
        model = MapLocation
        fields = ["name", "x", "y", "status", "importance"]
        labels = {"name": "Nombre", "x": "X (%)", "y": "Y (%)", "status": "Estado", "importance": "Importancia"}


# ---------------------------------------------------------------------------
# Creador de personajes: one form per step. The view keeps the cleaned values
# (as primary keys) in the session until the last step.
# ---------------------------------------------------------------------------

class RaceStepForm(forms.Form):
    race = forms.ModelChoiceField(Race.objects.filter(bespoke=False).order_by("name"), label="Raza", widget=forms.RadioSelect,
                                  error_messages={"required": "Elige una raza."})
    subrace = forms.ModelChoiceField(Subrace.objects.order_by("name"), label="Linaje", required=False, widget=forms.RadioSelect)
    # NPCs only: a race the master writes (an immortal, a beast...). It takes the place of the one chosen.
    race_name = forms.CharField(label="O una raza nueva", required=False, max_length=100)
    race_speed = forms.IntegerField(label="Velocidad (m)", required=False, min_value=0, max_value=200, initial=9)

    def __init__(self, *args, npc=False, **kwargs):
        super().__init__(*args, **kwargs)
        if npc:
            self.fields["race"].queryset = Race.objects.order_by("name")  # the master's own races too
            self.fields["race"].required = False
        else:
            del self.fields["race_name"], self.fields["race_speed"]

    def clean(self):
        cleaned = super().clean()
        if (cleaned.get("race_name") or "").strip():
            cleaned["race"], cleaned["subrace"] = None, None
            return cleaned
        race, subrace = cleaned.get("race"), cleaned.get("subrace")
        if race is None:
            if "race_name" in self.fields and not self.has_error("race"):
                self.add_error("race", "Elige una raza o escribe una nueva.")
            return cleaned
        if subrace is not None and subrace.race_id != race.pk:
            self.add_error("subrace", f"{subrace} es un linaje de {subrace.race}, no de {race}.")
        elif subrace is None and race.subraces.exists():
            self.add_error("subrace", f"Elige el linaje de {race}.")
        return cleaned


class ClassStepForm(forms.Form):
    char_class = forms.ModelChoiceField(CharacterClass.objects.filter(bespoke=False).order_by("name"), label="Clase",
                                        widget=forms.RadioSelect, error_messages={"required": "Elige una clase."})
    # NPCs only: a class the master writes, with the magic it gives.
    class_name = forms.CharField(label="O una clase nueva", required=False, max_length=100)
    class_magic = forms.ChoiceField(label="Su magia", required=False, choices=CharacterClass.Magic.choices,
                                    initial=CharacterClass.Magic.NINGUNA)

    def __init__(self, *args, npc=False, aegis=True, **kwargs):
        super().__init__(*args, **kwargs)
        if npc:
            self.fields["char_class"].queryset = CharacterClass.objects.order_by("name")
            self.fields["char_class"].required = False
        else:
            del self.fields["class_name"], self.fields["class_magic"]
        if not aegis:  # a campaign with the standard SRD: not our own classes
            self.fields["char_class"].queryset = self.fields["char_class"].queryset.filter(aegis_only=False)

    def clean(self):
        cleaned = super().clean()
        if (cleaned.get("class_name") or "").strip():
            cleaned["char_class"] = None
        elif "class_name" in self.fields and cleaned.get("char_class") is None and not self.has_error("char_class"):
            self.add_error("char_class", "Elige una clase o escribe una nueva.")
        return cleaned


class ScoresStepForm(forms.Form):
    """Standard array (15, 14, 13, 12, 10, 8, one each) or point buy (27 points, 8 to 15).
    NPCs made by a master can also take any score from 1 to 30 («libre»)."""
    METHODS = [("estandar", "Valores estándar"), ("puntos", "Reparto de puntos")]
    FREE = ("libre", "Libre")
    method = forms.ChoiceField(choices=METHODS, label="Método", widget=forms.RadioSelect, initial="puntos")
    strength = forms.IntegerField(label="Fuerza", min_value=3, max_value=20)
    dexterity = forms.IntegerField(label="Destreza", min_value=3, max_value=20)
    constitution = forms.IntegerField(label="Constitución", min_value=3, max_value=20)
    intelligence = forms.IntegerField(label="Inteligencia", min_value=3, max_value=20)
    wisdom = forms.IntegerField(label="Sabiduría", min_value=3, max_value=20)
    charisma = forms.IntegerField(label="Carisma", min_value=3, max_value=20)

    def __init__(self, *args, free=False, **kwargs):
        super().__init__(*args, **kwargs)
        if free:
            self.fields["method"].choices = [*self.METHODS, self.FREE]
            for key in rules.ABILITY_KEYS:
                self.fields[key] = forms.IntegerField(label=self.fields[key].label, min_value=1, max_value=rules.MASTER_MAX_SCORE)

    def scores(self):
        return {key: self.cleaned_data[key] for key in rules.ABILITY_KEYS}

    def clean(self):
        cleaned = super().clean()
        if any(cleaned.get(key) is None for key in rules.ABILITY_KEYS):
            return cleaned
        values = [cleaned[key] for key in rules.ABILITY_KEYS]
        if cleaned.get("method") == "libre":
            return cleaned  # only offered to NPCs; each field already checks 1 to 30
        if cleaned.get("method") == "estandar":
            if sorted(values, reverse=True) != rules.STANDARD_ARRAY:
                raise forms.ValidationError("Con los valores estándar, reparte 15, 14, 13, 12, 10 y 8: uno en cada característica.")
        elif not all(value in rules.POINT_BUY_COST for value in values):
            raise forms.ValidationError("Con el reparto de puntos, cada característica va de 8 a 15.")
        elif rules.point_buy_spent(values) != rules.POINT_BUY_TOTAL:
            left = rules.POINT_BUY_TOTAL - rules.point_buy_spent(values)
            raise forms.ValidationError(
                f"Te quedan {left} puntos por gastar." if left > 0 else f"Te has pasado {-left} puntos de los {rules.POINT_BUY_TOTAL}.")
        return cleaned


class IdentityStepForm(forms.Form):
    name = forms.CharField(label="Nombre", max_length=100)
    portrait = forms.ImageField(label="Retrato", required=False)
    background = forms.CharField(label="Trasfondo", required=False, max_length=10000, widget=forms.Textarea(attrs={"rows": 6}))
    player = forms.ModelChoiceField(User.objects.none(), label="Jugador", required=False)
    # NPCs only: level, hit points and armor class need not follow the rules (a bestiary entry, a boss...).
    level = forms.IntegerField(label="Nivel", required=False, min_value=1, max_value=rules.MAX_LEVEL, initial=1)
    hp = forms.IntegerField(label="Vida máxima", required=False, min_value=1, max_value=9999,
                            help_text="Vacía: la de la clase (dado de golpe + Constitución a nivel 1, y la media de cada nivel más).")
    ac = forms.IntegerField(label="Clase de armadura", required=False, min_value=0, max_value=60,
                            help_text="Vacía: 10 + el modificador de Destreza.")
    NPC_ONLY = ("level", "hp", "ac")

    def __init__(self, *args, campaign=None, is_master=False, npc=False, **kwargs):
        super().__init__(*args, **kwargs)
        if is_master and not npc:
            self.fields["player"].queryset = campaign.players.order_by("username")
        else:
            del self.fields["player"]
        if not npc:
            for name in self.NPC_ONLY:
                del self.fields[name]


class BespokeWeaponFields(forms.Form):
    """A unique weapon written by the master. Leave the name empty for none."""
    STATS = [("str", "Fuerza"), ("dex", "Destreza")]
    weapon_name = forms.CharField(label="Nombre del arma única", required=False, max_length=100)
    weapon_description = forms.CharField(label="Su historia o lo que hace", required=False, max_length=3000,
                                         widget=forms.Textarea(attrs={"rows": 3}))
    weapon_damage = forms.CharField(label="Dados de daño", required=False, max_length=60,
                                    help_text="Por ejemplo «2d6» o «1d10 + 1d4». Vacío si solo hace daño fijo.")
    weapon_extra = forms.IntegerField(label="Daño fijo", required=False, min_value=-50, max_value=200, initial=0)
    weapon_stat_bonus = forms.ChoiceField(label="Suma al daño", required=False,
                                          choices=[("", "Nada"), *STATS])
    weapon_hit_bonus = forms.IntegerField(label="Bonificador de golpe", required=False, min_value=-20, max_value=40, initial=0)
    weapon_hit_stat = forms.ChoiceField(label="Golpea con", choices=STATS, initial="str", required=False)
    weapon_range = forms.IntegerField(label="Alcance (m)", required=False, min_value=0, max_value=1000, initial=1)
    weapon_pierce = forms.IntegerField(label="Penetrante", required=False, min_value=0, max_value=50,
                                       help_text="AP que ignora. 0 o vacío: nada.")
    weapon_shred = forms.IntegerField(label="Triturante", required=False, min_value=0, max_value=50,
                                      help_text="AP que quita después del golpe.")

    def clean_weapon_damage(self):
        text = self.cleaned_data.get("weapon_damage", "")
        try:
            bespoke.parse_dice(text)
        except ValueError as error:
            raise forms.ValidationError(f"«{error}» no son dados: escribe por ejemplo 2d6 o 1d10 + 1d4.")
        return text

    def weapon_data(self):
        data = self.cleaned_data
        name = (data.get("weapon_name") or "").strip()
        if not name:
            return None
        return {"name": name, "description": data.get("weapon_description", ""),
                "dice": bespoke.parse_dice(data.get("weapon_damage")), "extra": data.get("weapon_extra") or 0,
                "stat_bonus": data.get("weapon_stat_bonus") or "", "hit_bonus": data.get("weapon_hit_bonus") or 0,
                "hit_stat": data.get("weapon_hit_stat") or "str", "range": data.get("weapon_range"),
                "pierce": data.get("weapon_pierce") or 0, "shred": data.get("weapon_shred") or 0}


class BespokeArmorFields(forms.Form):
    """An armor of the master's own: its type and how many AP it gives in total."""
    armor_name = forms.CharField(label="Nombre de la armadura propia", required=False, max_length=100)
    armor_type = forms.ChoiceField(label="Tipo", required=False, choices=[])
    armor_ap = forms.IntegerField(label="AP en total", required=False, min_value=0, max_value=200, initial=0)
    armor_magic_ap = forms.IntegerField(label="AP mágico en total", required=False, min_value=0, max_value=200, initial=0)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["armor_type"].choices = [(name, name) for name in bespoke.armor_type_names()]

    def armor_data(self):
        data = self.cleaned_data
        name = (data.get("armor_name") or "").strip()
        if not name:
            return None
        return {"name": name, "type": data.get("armor_type") or bespoke.ARMOR_TYPE_NAMES[0],
                "ap": data.get("armor_ap") or 0, "magic_ap": data.get("armor_magic_ap") or 0}


class EquipmentStepForm(BespokeWeaponFields, BespokeArmorFields):
    weapons = forms.ModelMultipleChoiceField(
        Item.objects.filter(weapon__isnull=False, template__isnull=True, bespoke=False).order_by("name"),
        label="Armas", required=False, widget=forms.CheckboxSelectMultiple)
    armor = forms.ModelChoiceField(
        Item.objects.filter(armor__isnull=False, template__isnull=True, bespoke=False).order_by("name"),
        label="Armadura", required=False, empty_label="Sin armadura", widget=forms.RadioSelect)

    def __init__(self, *args, npc=False, **kwargs):
        super().__init__(*args, **kwargs)
        if npc:  # the master's own pieces made before can be chosen again
            self.fields["weapons"].queryset = Item.objects.filter(weapon__isnull=False, template__isnull=True).order_by("name")
            self.fields["armor"].queryset = Item.objects.filter(armor__isnull=False, template__isnull=True).order_by("name")
        else:
            for name in list(self.fields):
                if name.startswith(("weapon_", "armor_")):
                    del self.fields[name]


class CharacterMasterForm(forms.ModelForm):
    """Everything on a sheet, as the master wants it, whatever the rules say: level, hit
    points, AP, speed, scores up to 40... Only what keeps the data whole is checked (a subrace of
    its race, a subclass of its class). A race or class can also be written anew."""
    new_race = forms.CharField(label="O una raza nueva", required=False, max_length=100)
    new_race_speed = forms.IntegerField(label="Su velocidad (m)", required=False, min_value=0, max_value=200)
    new_class = forms.CharField(label="O una clase nueva", required=False, max_length=100)
    new_class_magic = forms.ChoiceField(label="Su magia", required=False, choices=CharacterClass.Magic.choices,
                                        initial=CharacterClass.Magic.NINGUNA)

    class Meta:
        model = Character
        fields = ["name", "player", "status", "portrait", "background", "race", "subrace", "char_class", "subclass",
                  "level", "xp", "hp", "temp_hp", "ac", "own_speed", "temp_ap", "inspiration", "magic_points", "magic_points_bonus",
                  *rules.ABILITY_KEYS, "dead", "acts_with"]
        labels = {"name": "Nombre", "player": "Jugador", "status": "Estado", "portrait": "Retrato", "background": "Trasfondo",
                  "race": "Raza", "subrace": "Linaje", "char_class": "Clase", "subclass": "Subclase", "level": "Nivel",
                  "xp": "XP", "hp": "Vida máxima", "temp_hp": "Vida actual", "ac": "Clase de armadura",
                  "own_speed": "Velocidad propia (m)", "temp_ap": "AP actuales",
                  "inspiration": "Inspiración", "magic_points": "Puntos de magia actuales",
                  "magic_points_bonus": "Ajuste de puntos de magia", "dead": "Muerto", "acts_with": "Actúa a la vez con",
                  **{key: rules.ABILITY_NAMES[key] for key in rules.ABILITY_KEYS}}
        help_texts = {"magic_points_bonus": "Se suma a lo que da la clase a su nivel (puede ser negativo).",
                      "own_speed": "Vacía: la de su raza. Para quien corre, vuela o se arrastra a otro paso.",
                      "temp_ap": "El máximo sale de la armadura equipada: cámbialo abajo, en «Armadura equipada».",
                      "level": "Cambiarlo aquí no aplica lo que da el nivel (subclase, mejoras): es tu decisión.",
                      "acts_with": "Una pareja que pelea como una sola (Shayne y Aurox): en los turnos, si van seguidos, "
                                   "tienen un único turno y entran juntos al combate. Vale para los dos."}
        widgets = {"background": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, campaign=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["player"].queryset = campaign.players.order_by("username") if campaign else User.objects.none()
        self.fields["player"].required = False
        self.fields["race"].required = self.fields["char_class"].required = False
        self.fields["race"].queryset = Race.objects.order_by("name")
        self.fields["char_class"].queryset = CharacterClass.objects.order_by("name")
        self.fields["subrace"].queryset = Subrace.objects.select_related("race").order_by("race__name", "name")
        self.fields["subrace"].label_from_instance = lambda s: f"{s.race} · {s.name}"
        self.fields["subclass"].queryset = Subclass.objects.select_related("char_class").order_by("char_class__name", "name")
        self.fields["subclass"].label_from_instance = lambda s: f"{s.char_class} · {s.name}"
        for key in rules.ABILITY_KEYS:
            self.fields[key] = forms.IntegerField(label=rules.ABILITY_NAMES[key], min_value=1, max_value=rules.MASTER_MAX_SCORE)
        self.fields["level"] = forms.IntegerField(label="Nivel", min_value=1, max_value=rules.MAX_LEVEL,
                                                  help_text=self.Meta.help_texts["level"])
        if self.instance.is_npc:
            del self.fields["player"], self.fields["status"]
            # Its partner: another NPC template of the campaign (a copy keeps its template's).
            key = self.instance.template_id or self.instance.pk
            self.fields["acts_with"].queryset = (npc_templates_for(campaign).exclude(pk=key).order_by("name")
                                                 if campaign else Character.objects.none())
            self.fields["acts_with"].empty_label = "Nadie: actúa solo"
        else:
            del self.fields["acts_with"]

    def save(self, commit=True):
        """A pair goes both ways: its new partner acts with it too, and an old one stops."""
        before = Character.objects.filter(pk=self.instance.pk).values_list("acts_with_id", flat=True).first()
        character = super().save(commit)
        if commit and "acts_with" in self.fields and character.template_id is None:
            partner = character.acts_with_id
            if before and before != partner:
                Character.objects.filter(pk=before, acts_with=character).update(acts_with=None)
            if partner:
                Character.objects.filter(pk=partner).update(acts_with=character)
        return character

    def clean(self):
        cleaned = super().clean()
        if (cleaned.get("new_race") or "").strip():
            cleaned["race"] = bespoke.race(cleaned["new_race"], cleaned.get("new_race_speed"))
            cleaned["subrace"] = None
        elif cleaned.get("race") is None:
            self.add_error("race", "Elige una raza o escribe una nueva.")
        if (cleaned.get("new_class") or "").strip():
            cleaned["char_class"] = bespoke.char_class(cleaned["new_class"], cleaned.get("new_class_magic"))
            cleaned["subclass"] = None
        elif cleaned.get("char_class") is None:
            self.add_error("char_class", "Elige una clase o escribe una nueva.")
        race, subrace = cleaned.get("race"), cleaned.get("subrace")
        if race and subrace and subrace.race_id != race.pk:
            self.add_error("subrace", f"{subrace.name} es un linaje de {subrace.race}, no de {race}.")
        char_class, subclass = cleaned.get("char_class"), cleaned.get("subclass")
        if char_class and subclass and subclass.char_class_id != char_class.pk:
            self.add_error("subclass", f"{subclass.name} es una subclase de {subclass.char_class}, no de {char_class}.")
        if cleaned.get("magic_points") is not None and cleaned["magic_points"] < 0:
            self.add_error("magic_points", "No pueden ser negativos.")
        if char_class and cleaned.get("magic_points") is not None and cleaned.get("level"):
            top = max(0, rules.class_magic_points(char_class.magic, cleaned["level"]) + (cleaned.get("magic_points_bonus") or 0))
            if cleaned["magic_points"] > top:
                self.add_error("magic_points", f"Con su clase, nivel y ajuste el máximo es {top}: sube el ajuste si quieres más.")
        return cleaned


class EquippedArmorForm(forms.Form):
    """The armor the character wears, changed by the master: its type and its AP."""
    worn_type = forms.ChoiceField(label="Tipo", choices=[])
    worn_ap = forms.IntegerField(label="AP en total", min_value=0, max_value=200)
    worn_magic_ap = forms.IntegerField(label="AP mágico en total", min_value=0, max_value=200)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["worn_type"].choices = [(name, name) for name in bespoke.armor_type_names()]


class UniqueSkillForm(forms.Form):
    """A unique skill as the player proposes it, or as a master approves it (changed or not)."""
    name = forms.CharField(label="Nombre de la habilidad", max_length=100,
                           error_messages={"required": "Ponle un nombre a la habilidad única."})
    description = forms.CharField(label="Qué hace", max_length=3000, widget=forms.Textarea(attrs={"rows": 4}),
                                  error_messages={"required": "Describe qué hace la habilidad única."})


class UniqueSkillStepForm(UniqueSkillForm):
    """Creator step: optional, but a name needs its description and the other way round."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.required = False

    def clean(self):
        cleaned = super().clean()
        name, description = (cleaned.get("name") or "").strip(), (cleaned.get("description") or "").strip()
        if name and not description:
            self.add_error("description", "Describe qué hace la habilidad única, o deja también el nombre en blanco.")
        if description and not name:
            self.add_error("name", "Ponle un nombre a la habilidad única, o deja también la descripción en blanco.")
        return cleaned


# ---------------------------------------------------------------------------
# Subir de nivel: one level at a time. The view works out what the level gives
# (`plan`) and the form keeps only the fields that level needs.
# ---------------------------------------------------------------------------

class LevelUpForm(forms.Form):
    """Hit points, the subclass at level 3, at the feat levels the ability score
    improvement or a feat, and in the SRD new spells. Hit points: in Aegis the 1d4
    rolled at the table, in the SRD the class hit die (fixed or rolled), both +
    Constitution. The improvement is two +1: the same score twice is +2."""
    SCORES = [(key, ScoresStepForm.base_fields[key].label) for key in rules.ABILITY_KEYS]
    IMPROVEMENT = "caracteristica"
    HP_METHODS = [("fijo", "Valor fijo"), ("tirar", "Tirar el dado")]

    hp_roll = forms.IntegerField(
        min_value=1, max_value=rules.LEVEL_UP_HIT_DIE, label=f"Resultado del 1d{rules.LEVEL_UP_HIT_DIE}",
        error_messages={"required": f"Escribe el resultado del 1d{rules.LEVEL_UP_HIT_DIE}.",
                        "min_value": f"El 1d{rules.LEVEL_UP_HIT_DIE} da de 1 a {rules.LEVEL_UP_HIT_DIE}.",
                        "max_value": f"El 1d{rules.LEVEL_UP_HIT_DIE} da de 1 a {rules.LEVEL_UP_HIT_DIE}."})
    # SRD: the class hit die, fixed or rolled; typed when the class does not say which die it uses.
    hp_method = forms.ChoiceField(choices=HP_METHODS, initial="fijo", label="Puntos de golpe", widget=forms.RadioSelect)
    hp_gain = forms.IntegerField(min_value=1, max_value=99, label="Puntos de golpe ganados")
    subclass = forms.ModelChoiceField(Subclass.objects.none(), label="Subclase", widget=forms.RadioSelect,
                                      error_messages={"required": "Elige una subclase."})
    spells = forms.ModelMultipleChoiceField(Spell.objects.none(), required=False, label="Conjuros nuevos",
                                            widget=forms.CheckboxSelectMultiple)
    # At a feat level with feats to choose: "caracteristica" or the pk of a feat.
    choice = forms.ChoiceField(label="Mejora de característica o dote", widget=forms.RadioSelect,
                               error_messages={"required": "Elige la mejora de característica o una dote."})
    asi_first = forms.ChoiceField(choices=SCORES, label="+1 a", required=False)
    asi_second = forms.ChoiceField(choices=SCORES, label="+1 a", required=False)

    def __init__(self, *args, character, plan, **kwargs):
        super().__init__(*args, **kwargs)
        self.character, self.plan = character, plan
        if plan["aegis"]:
            del self.fields["hp_method"], self.fields["hp_gain"]
        else:
            del self.fields["hp_roll"]
            del self.fields["hp_method" if plan["die"] is None else "hp_gain"]
        if plan["spells"]:
            self.fields["spells"].queryset = Spell.objects.filter(pk__in=[spell.pk for spell in plan["spells"]])
        else:
            del self.fields["spells"]
        if plan["subclasses"]:
            self.fields["subclass"].queryset = character.char_class.subclasses.all()
        else:
            del self.fields["subclass"]
        if not plan["feat_level"]:
            del self.fields["choice"], self.fields["asi_first"], self.fields["asi_second"]
        elif plan["feats"]:
            self.fields["choice"].choices = [(self.IMPROVEMENT, "Mejora de característica")] + [
                (str(feat.pk), feat.name) for feat in plan["feats"]]
        else:
            del self.fields["choice"]  # no feats to choose: the improvement it is

    def clean_spells(self):
        spells = self.cleaned_data["spells"]
        cantrips = sum(1 for spell in spells if spell.level == 0)
        if cantrips > self.plan["new_cantrips"]:
            n = self.plan["new_cantrips"]
            raise forms.ValidationError(f"Elige como mucho {n} {'truco nuevo' if n == 1 else 'trucos nuevos'}.")
        if len(spells) - cantrips > self.plan["new_spells"]:
            n = self.plan["new_spells"]
            raise forms.ValidationError(f"Elige como mucho {n} {'conjuro nuevo' if n == 1 else 'conjuros nuevos'} de nivel 1 o más.")
        return spells

    def feat(self):
        """The feat chosen instead of the ability score improvement, or None."""
        choice = self.cleaned_data.get("choice")
        if not choice or choice == self.IMPROVEMENT:
            return None
        return next(feat for feat in self.plan["feats"] if str(feat.pk) == choice)

    def increases(self):
        """{ability key: points} of the ability score improvement, {} if there is none."""
        if not self.plan["feat_level"] or self.feat() is not None:
            return {}
        points = {}
        for name in ("asi_first", "asi_second"):
            key = self.cleaned_data.get(name)
            if key:
                points[key] = points.get(key, 0) + 1
        return points

    def clean(self):
        cleaned = super().clean()
        if not self.plan["feat_level"] or self.feat() is not None or self.errors.get("choice"):
            return cleaned
        for name in ("asi_first", "asi_second"):
            if not cleaned.get(name):
                self.add_error(name, "Elige a qué característica va este punto.")
        labels = dict(self.SCORES)
        for key, points in self.increases().items():
            if getattr(self.character, key) + points > rules.MAX_SCORE:
                self.add_error(None, f"{labels[key]} no puede pasar de {rules.MAX_SCORE}: "
                                     f"tiene {getattr(self.character, key)}.")
        return cleaned


# ---------------------------------------------------------------------------
# Asistente de campaña: one tab after another, from the basic data to the world,
# its places, factions, NPCs, lore, plots and the first session.
# ---------------------------------------------------------------------------

class CampaignBasicsForm(forms.ModelForm):
    """Tab 1: the least a campaign needs. Whoever creates it becomes its master."""

    class Meta:
        model = Campaign
        fields = ["name", "description", "start_date", "ruleset", "status"]
        labels = {"name": "Nombre de la campaña", "description": "De qué va", "start_date": "Fecha de inicio",
                  "ruleset": "Reglamento", "status": "Estado"}
        help_texts = {"description": "La premisa, en pocas líneas. La leen los jugadores.",
                      "ruleset": "Aegis es nuestra variante del SRD (vida 1d4 por nivel, habilidades únicas...). Se puede cambiar luego.",
                      "start_date": "El día en que empezó (o empezará) a jugarse."}
        widgets = {"description": forms.Textarea(attrs={"rows": 5}), "ruleset": forms.RadioSelect, "status": forms.RadioSelect,
                   "start_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}


class CampaignTableForm(forms.ModelForm):
    """Tab 2: who sits at the table, and the master's notice on the home page."""

    class Meta:
        model = Campaign
        fields = ["masters", "players", "notice"]
        labels = {"masters": "Másters", "players": "Jugadores", "notice": "Aviso del máster"}
        help_texts = {"masters": "Quienes dirigen: ven y cambian todo, también los secretos.",
                      "players": "Ven lo que reveles. Cada uno creará su personaje desde «Personajes».",
                      "notice": "Sale en la portada de la campaña: la próxima sesión, qué traer..."}
        widgets = {"masters": forms.CheckboxSelectMultiple, "players": forms.CheckboxSelectMultiple,
                   "notice": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        users = User.objects.order_by("username")
        self.fields["masters"].queryset = users
        self.fields["players"].queryset = users
        self.fields["players"].required = False

    def clean(self):
        cleaned = super().clean()
        masters, players = cleaned.get("masters"), cleaned.get("players")
        if masters is not None and self.user is not None and self.user not in masters:
            self.add_error("masters", "Tú tienes que seguir siendo máster de la campaña.")
        if masters is not None and players is not None:
            both = sorted(u.username for u in set(masters) & set(players))
            if both:
                self.add_error("players", f"{', '.join(both)} no puede ser máster y jugador a la vez.")
        return cleaned


class CampaignWorldForm(forms.ModelForm):
    """Tab 3, the campaign's part: when it is in the world, and the cover of its home page."""

    class Meta:
        model = Campaign
        fields = ["world_year", "world_day", "time_zone", "cover"]
        labels = {"world_year": "Año en el mundo", "world_day": "Día en el mundo", "time_zone": "Zona horaria", "cover": "Portada"}
        help_texts = {"world_year": "Por ejemplo «2401 dk» o «año 1203».", "world_day": "Por ejemplo «Día 9 de Brumaluz».",
                      "cover": "La imagen de la portada de la campaña.",
                      "time_zone": "El reloj que comparte todo el mundo de la campaña (la capa del tiempo del mapa lo muestra)."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The list comes from the machine's time zone data, so it is built here and not in the model.
        self.fields["time_zone"].widget = forms.Select(choices=time_zone_choices())


class WorldPageForm(forms.Form):
    """Tab 3, the world itself: its page in «Mundo», at the top of every place."""
    title = forms.CharField(label="Nombre del mundo", max_length=200,
                            help_text="El continente, el reino o el plano donde pasa todo. Los lugares irán dentro.")
    summary = forms.CharField(label="Resumen", required=False, widget=forms.Textarea(attrs={"rows": 2}),
                              help_text="Una frase: sale al pasar el ratón por un enlace al mundo.")
    body = forms.CharField(label="Lo que sabe el grupo", required=False, widget=forms.Textarea(attrs={"rows": 7}),
                           help_text="Geografía, países, idiomas, religiones, cómo se cuentan los años... Escribe [[Nombre]] para enlazar otra página.")
    secret = forms.CharField(label="Secreto del máster", required=False, widget=forms.Textarea(attrs={"rows": 4}),
                             help_text="La verdad del mundo, que los jugadores no ven.")
    revealed = forms.BooleanField(label="Los jugadores ven esta página", required=False, initial=True)


class CampaignMapForm(forms.ModelForm):
    """Tab 4: a map with its image. Its places are put on it later, with a click."""

    class Meta:
        model = Map
        fields = ["name", "image"]
        labels = {"name": "Nombre del mapa", "image": "Imagen del mapa"}
        help_texts = {"image": "JPG, PNG o WebP. Los lugares se ponen encima desde «Mapa», pulsando donde están."}


class WorldEntryForm(forms.Form):
    """One page of a list tab (a place, a faction, an NPC, an object or a story)."""
    title = forms.CharField(label="Nombre", max_length=200)
    category = forms.ChoiceField(label="Tipo", choices=[])
    parent = forms.ModelChoiceField(Page.objects.none(), label="Dentro de", required=False,
                                    help_text="Un lugar o una facción a la que pertenece.")
    summary = forms.CharField(label="Resumen", required=False, max_length=500, help_text="Una frase.")
    body = forms.CharField(label="Lo que sabe el grupo", required=False, widget=forms.Textarea(attrs={"rows": 4}))
    secret = forms.CharField(label="Secreto del máster", required=False, widget=forms.Textarea(attrs={"rows": 2}))
    revealed = forms.BooleanField(label="Los jugadores la ven", required=False,
                                  help_text="Sin marcar, solo existe para ti hasta que la reveles.")

    def __init__(self, *args, campaign=None, categories=(), default_parent=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].choices = [(key, label) for key, label in Page.Category.choices if key in categories]
        if len(categories) == 1:
            self.fields["category"].initial = categories[0]
            self.fields["category"].widget = forms.HiddenInput()
        else:
            self.fields["category"].initial = categories[0] if categories else None
        self.fields["parent"].queryset = Page.objects.filter(
            campaign=campaign, category__in=Page.PARENT_CATEGORIES).order_by("title")
        self.fields["parent"].initial = default_parent


WorldEntryFormSet = forms.formset_factory(WorldEntryForm, extra=3)


class PlotForm(forms.Form):
    """One plot of tab 9: an adventure (an arc) or one of its missions, with its objectives."""
    title = forms.CharField(label="Título", max_length=200)
    parent = forms.ModelChoiceField(Quest.objects.none(), label="Dentro de la aventura", required=False,
                                    empty_label="Ninguna: es una aventura",
                                    help_text="Elige una para que esta sea una de sus misiones.")
    status = forms.ChoiceField(label="Estado", choices=Quest.Status.choices, initial=Quest.Status.ACTIVA)
    description = forms.CharField(label="De qué va", required=False, widget=forms.Textarea(attrs={"rows": 3}),
                                  help_text="Los jugadores la leen.")
    objectives = forms.CharField(label="Objetivos", required=False, widget=forms.Textarea(attrs={"rows": 3}),
                                 help_text="Uno por línea.")
    hidden_objectives = forms.CharField(label="Objetivos ocultos", required=False, widget=forms.Textarea(attrs={"rows": 2}),
                                        help_text="Uno por línea. Los jugadores ven «Objetivo oculto» hasta que lo marques cumplido.")
    secret = forms.CharField(label="Secreto del máster", required=False, widget=forms.Textarea(attrs={"rows": 2}))
    hidden = forms.BooleanField(label="Oculta: los jugadores no la ven", required=False)

    def __init__(self, *args, campaign=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["parent"].queryset = Quest.objects.filter(campaign=campaign, parent__isnull=True).order_by("title")


PlotFormSet = forms.formset_factory(PlotForm, extra=2)


# ---------------------------------------------------------------------------
# Rumores
# ---------------------------------------------------------------------------

class RumourForm(CampaignFormMixin, forms.ModelForm):
    """A rumour, written by a master. Who said it: a page of the world (an NPC or a faction)
    or free text; where: a place of the world; when: a session."""

    class Meta:
        model = Rumour
        fields = ["category", "text", "source_page", "source", "place", "session", "truth", "published"]
        labels = {"category": "Categoría", "text": "Qué se dice", "source_page": "Quién lo dijo (del Mundo)",
                  "source": "Quién lo dijo (si no tiene página)", "place": "Dónde se oyó", "session": "En qué sesión",
                  "truth": "Qué hay de verdad (solo lo ves tú)", "published": "Publicado: los jugadores lo ven"}
        help_texts = {"text": "Escribe [[Nombre]] para enlazar una página del Mundo.",
                      "source_page": "Un PNJ o una facción de «Mundo». Los jugadores solo ven el enlace si la página está revelada.",
                      "source": "Por ejemplo «Una tabernera de Puerto Gris» o «Un pregonero».",
                      "place": "Un lugar de «Mundo».", "session": "", "category": "",
                      "truth": "Los jugadores nunca lo ven.", "published": "Si no, solo lo ven los másters."}
        widgets = {"text": forms.Textarea(attrs={"rows": 4}), "truth": forms.RadioSelect}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        pages = Page.objects.filter(campaign=self.campaign).order_by("title")
        fields = self.fields
        fields["category"].queryset = RumourCategory.objects.filter(campaign=self.campaign)
        fields["source_page"].queryset = pages.filter(category__in=[Page.Category.PNJ, Page.Category.FACCION])
        fields["place"].queryset = pages.filter(category=Page.Category.LUGAR)
        fields["session"].queryset = Session.objects.filter(campaign=self.campaign).order_by("-number")
        fields["session"].label_from_instance = lambda session: f"Sesión {session.number}: {session.title}"
        for name, empty in (("category", "— Sin categoría —"), ("source_page", "— Nadie del Mundo —"),
                            ("place", "— Ningún lugar —"), ("session", "— Ninguna —")):
            fields[name].empty_label = empty


class RumourCategoryForm(forms.ModelForm):
    class Meta:
        model = RumourCategory
        fields = ["name", "color"]
        labels = {"name": "Nombre", "color": "Color"}
        widgets = {"color": forms.TextInput(attrs={"type": "color"})}

    def __init__(self, *args, campaign=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.campaign = campaign
        if campaign is not None:
            self.instance.campaign = campaign

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        others = RumourCategory.objects.filter(campaign=self.campaign, name__iexact=name).exclude(pk=self.instance.pk)
        if others.exists():
            raise forms.ValidationError("Ya hay una categoría con ese nombre.")
        return name


# ---------------------------------------------------------------------------
# Mapa: capa del tiempo
# ---------------------------------------------------------------------------

class WeatherRegionForm(forms.Form):
    """A region of the map linked to a real place. The region: a location of the map, or a
    polygon drawn on it (x,y in % of the image). The place: what the master typed, found with
    Open-Meteo's geocoding (or coordinates), unless a suggestion already set latitude/longitude."""
    name = forms.CharField(label="Nombre de la región", max_length=100)
    location = forms.ModelChoiceField(MapLocation.objects.none(), label="Un lugar del mapa", required=False,
                                      empty_label="— Dibujarla en el mapa —")
    points = forms.CharField(label="Puntos del dibujo", required=False, widget=forms.HiddenInput)
    place = forms.CharField(label="Lugar real", max_length=200,
                            help_text="Un país, una ciudad («Bilbao, España») o coordenadas («43.26, -2.93»).")
    latitude = forms.FloatField(required=False, min_value=-90, max_value=90, widget=forms.HiddenInput)
    longitude = forms.FloatField(required=False, min_value=-180, max_value=180, widget=forms.HiddenInput)

    def __init__(self, *args, map=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.map = map
        if map is not None:
            self.fields["location"].queryset = map.locations.order_by("name")

    def clean_points(self):
        if not self.cleaned_data.get("points"):
            return []
        points = parse_path(self.cleaned_data["points"])
        if points is None:
            raise forms.ValidationError("El dibujo no es válido: vuelve a dibujar la región.")
        return points

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("location") and len(cleaned.get("points") or []) < 3:
            self.add_error(None, "Elige un lugar del mapa o dibuja la región con al menos 3 puntos.")
        text = (cleaned.get("place") or "").strip()
        if not text:
            return cleaned
        lat, lon = cleaned.get("latitude"), cleaned.get("longitude")
        if lat is not None and lon is not None:  # a suggestion was chosen
            cleaned["place_name"] = text
            return cleaned
        try:
            found = weather.search_places(text, limit=1)
        except weather.WeatherUnavailable:
            self.add_error("place", "Ahora no se puede buscar el lugar. Escribe sus coordenadas («43.26, -2.93»).")
            return cleaned
        if not found:
            self.add_error("place", "No encuentro ese lugar. Prueba con «Ciudad, País» o con coordenadas.")
            return cleaned
        cleaned.update(place_name=found[0]["name"], latitude=found[0]["latitude"], longitude=found[0]["longitude"])
        return cleaned

