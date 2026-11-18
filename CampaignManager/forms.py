"""Public forms of «Mesa de campaña» (ModelForms with their validation)."""
from django import forms
from django.forms import inlineformset_factory

from . import rules
from .models import (
    BoardCard, Character, CharacterClass, Item, Map, MapLocation, Page, Post, Quest, QuestObjective, Race, Scene,
    Session, Subclass, Subrace, TimelineEvent, User,
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
            "body": "Crónica", "xp_awarded": "XP repartida", "loot": "Botín",
            "master_notes": "Notas del máster (solo las ves tú)", "published": "Publicada (los jugadores la ven)",
        }
        widgets = {"played_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
                   "body": forms.Textarea(attrs={"rows": 10}), "master_notes": forms.Textarea(attrs={"rows": 3})}
        help_texts = {"body": "Escribe [[Nombre]] para enlazar una página del Mundo o la ficha de un personaje. Una línea en blanco separa párrafos."}

    def clean_number(self):
        number = self.cleaned_data["number"]
        if Session.objects.filter(campaign=self.campaign, number=number).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Ya hay una crónica con ese número en esta campaña.")
        return number


class EventForm(CampaignFormMixin, forms.ModelForm):
    class Meta:
        model = TimelineEvent
        fields = ["title", "kind", "hidden"]
        labels = {"title": "Qué ha pasado", "kind": "Tipo", "hidden": "Solo para el máster"}


class BoardCardForm(CampaignFormMixin, forms.ModelForm):
    class Meta:
        model = BoardCard
        fields = ["title", "text", "page", "classified"]
        labels = {"title": "Título", "text": "Texto", "page": "Página del Mundo", "classified": "Clasificada: los jugadores la verán oculta hasta que la reveles"}
        widgets = {"text": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, is_master=False, visible_pages=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["page"].queryset = visible_pages if visible_pages is not None else Page.objects.none()
        self.fields["page"].required = False
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


class QuestForm(CampaignFormMixin, forms.ModelForm):
    class Meta:
        model = Quest
        fields = ["title", "description", "status", "giver"]
        labels = {"title": "Título", "description": "Descripción", "status": "Estado", "giver": "Quién la encarga"}
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["giver"].queryset = Page.objects.filter(campaign=self.campaign).order_by("title")


ObjectiveFormSet = inlineformset_factory(
    Quest, QuestObjective, fields=["text", "done", "hidden", "order"], extra=2, can_delete=True,
    labels={"text": "Objetivo", "done": "Cumplido", "hidden": "Oculto (se revela al cumplirlo)", "order": "Orden"},
)


class PageForm(CampaignFormMixin, forms.ModelForm):
    class Meta:
        model = Page
        fields = ["title", "category", "parent", "summary", "body", "secret", "image", "location"]
        labels = {"title": "Título", "category": "Categoría", "parent": "Dentro de", "summary": "Resumen",
                  "body": "Lo que sabe el grupo", "secret": "Secreto del máster", "image": "Imagen",
                  "location": "Lugar del mapa (solo para lugares)"}
        widgets = {"body": forms.Textarea(attrs={"rows": 8}), "secret": forms.Textarea(attrs={"rows": 3}),
                   "summary": forms.Textarea(attrs={"rows": 2})}
        help_texts = {"body": "Escribe el nombre de otra página entre dobles corchetes, como [[Puerto Gris]], y se convierte en un enlace. Una línea en blanco separa párrafos.",
                      "summary": "Se ve en la vista previa al pasar sobre un enlace. Si lo dejas vacío, se usa la primera frase."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        others = Page.objects.filter(campaign=self.campaign, category__in=Page.PARENT_CATEGORIES)
        if self.instance.pk:
            others = others.exclude(pk=self.instance.pk)
        self.fields["parent"].queryset = others.order_by("title")
        self.fields["parent"].empty_label = "Ninguno"
        taken = Page.objects.filter(campaign=self.campaign, location__isnull=False).exclude(pk=self.instance.pk)
        self.fields["location"].queryset = (MapLocation.objects.filter(map__campaign=self.campaign)
                                            .exclude(page__in=taken).select_related("map").order_by("map__order", "name"))
        self.fields["location"].label_from_instance = lambda loc: f"{loc.map.name} · {loc.name}"

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("location") and cleaned.get("category") != Page.Category.LUGAR:
            self.add_error("location", "Solo las páginas de lugar pueden tener un lugar del mapa.")
        return cleaned


class MapForm(CampaignFormMixin, forms.ModelForm):
    class Meta:
        model = Map
        fields = ["name", "image", "order"]
        labels = {"name": "Nombre", "image": "Imagen", "order": "Orden"}


class LocationForm(forms.ModelForm):
    class Meta:
        model = MapLocation
        fields = ["name", "x", "y", "status"]
        labels = {"name": "Nombre", "x": "X (%)", "y": "Y (%)", "status": "Estado"}


# ---------------------------------------------------------------------------
# Creador de personajes: one form per step. The view keeps the cleaned values
# (as primary keys) in the session until the last step.
# ---------------------------------------------------------------------------

class RaceStepForm(forms.Form):
    race = forms.ModelChoiceField(Race.objects.order_by("name"), label="Raza", widget=forms.RadioSelect,
                                  error_messages={"required": "Elige una raza."})
    subrace = forms.ModelChoiceField(Subrace.objects.order_by("name"), label="Linaje", required=False, widget=forms.RadioSelect)

    def clean(self):
        cleaned = super().clean()
        race, subrace = cleaned.get("race"), cleaned.get("subrace")
        if race is None:
            return cleaned
        if subrace is not None and subrace.race_id != race.pk:
            self.add_error("subrace", f"{subrace} es un linaje de {subrace.race}, no de {race}.")
        elif subrace is None and race.subraces.exists():
            self.add_error("subrace", f"Elige el linaje de {race}.")
        return cleaned


class ClassStepForm(forms.Form):
    char_class = forms.ModelChoiceField(CharacterClass.objects.order_by("name"), label="Clase", widget=forms.RadioSelect,
                                        error_messages={"required": "Elige una clase."})


class ScoresStepForm(forms.Form):
    """Standard array (15, 14, 13, 12, 10, 8, one each) or point buy (27 points, 8 to 15)."""
    METHODS = [("estandar", "Valores estándar"), ("puntos", "Reparto de puntos")]
    method = forms.ChoiceField(choices=METHODS, label="Método", widget=forms.RadioSelect, initial="puntos")
    strength = forms.IntegerField(label="Fuerza", min_value=3, max_value=20)
    dexterity = forms.IntegerField(label="Destreza", min_value=3, max_value=20)
    constitution = forms.IntegerField(label="Constitución", min_value=3, max_value=20)
    intelligence = forms.IntegerField(label="Inteligencia", min_value=3, max_value=20)
    wisdom = forms.IntegerField(label="Sabiduría", min_value=3, max_value=20)
    charisma = forms.IntegerField(label="Carisma", min_value=3, max_value=20)

    def scores(self):
        return {key: self.cleaned_data[key] for key in rules.ABILITY_KEYS}

    def clean(self):
        cleaned = super().clean()
        if any(cleaned.get(key) is None for key in rules.ABILITY_KEYS):
            return cleaned
        values = [cleaned[key] for key in rules.ABILITY_KEYS]
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
    background = forms.CharField(label="Trasfondo", required=False, max_length=5000, widget=forms.Textarea(attrs={"rows": 6}))
    player = forms.ModelChoiceField(User.objects.none(), label="Jugador", required=False)

    def __init__(self, *args, campaign=None, is_master=False, **kwargs):
        super().__init__(*args, **kwargs)
        if is_master:
            self.fields["player"].queryset = campaign.players.order_by("username")
        else:
            del self.fields["player"]


class EquipmentStepForm(forms.Form):
    weapons = forms.ModelMultipleChoiceField(
        Item.objects.filter(weapon__isnull=False, template__isnull=True).order_by("name"),
        label="Armas", required=False, widget=forms.CheckboxSelectMultiple)
    armor = forms.ModelChoiceField(
        Item.objects.filter(armor__isnull=False, template__isnull=True).order_by("name"),
        label="Armadura", required=False, empty_label="Sin armadura", widget=forms.RadioSelect)


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
    """Hit points, the subclass at level 3, and at the feat levels the ability score
    improvement or a feat. Hit points follow the house rule: the 1d4 rolled at the
    table + Constitution. The improvement is two +1: the same score twice is +2."""
    SCORES = [(key, ScoresStepForm.base_fields[key].label) for key in rules.ABILITY_KEYS]
    IMPROVEMENT = "caracteristica"

    hp_roll = forms.IntegerField(
        min_value=1, max_value=rules.LEVEL_UP_HIT_DIE, label=f"Resultado del 1d{rules.LEVEL_UP_HIT_DIE}",
        error_messages={"required": f"Escribe el resultado del 1d{rules.LEVEL_UP_HIT_DIE}.",
                        "min_value": f"El 1d{rules.LEVEL_UP_HIT_DIE} da de 1 a {rules.LEVEL_UP_HIT_DIE}.",
                        "max_value": f"El 1d{rules.LEVEL_UP_HIT_DIE} da de 1 a {rules.LEVEL_UP_HIT_DIE}."})
    subclass = forms.ModelChoiceField(Subclass.objects.none(), label="Subclase", widget=forms.RadioSelect,
                                      error_messages={"required": "Elige una subclase."})
    # At a feat level with feats to choose: "caracteristica" or the pk of a feat.
    choice = forms.ChoiceField(label="Mejora de característica o dote", widget=forms.RadioSelect,
                               error_messages={"required": "Elige la mejora de característica o una dote."})
    asi_first = forms.ChoiceField(choices=SCORES, label="+1 a", required=False)
    asi_second = forms.ChoiceField(choices=SCORES, label="+1 a", required=False)

    def __init__(self, *args, character, plan, **kwargs):
        super().__init__(*args, **kwargs)
        self.character, self.plan = character, plan
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
