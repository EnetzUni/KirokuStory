"""Models of a concrete campaign: diary, world wiki, quests, board, role-play
scenes, maps and combat turns (spec, section 4.2).

Imported at the end of models.py, so they belong to the CampaignManager app.
Anything hidden from players is filtered here, in the `visible_for(user,
campaign)` querysets, never by hiding it in the template.
"""
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from django.utils.text import slugify

from .models import Campaign, Character, User


class CampaignQuerySet(models.QuerySet):
    """Base for querysets with a campaign: masters see everything."""

    def of(self, campaign):
        return self.filter(campaign=campaign)

    def visible_for(self, user, campaign):
        qs = self.of(campaign)
        return qs if campaign.is_master(user) else self.hide_from_players(qs)

    def hide_from_players(self, qs):
        return qs


# ---------------------------------------------------------------------------
# Diary
# ---------------------------------------------------------------------------

class SessionQuerySet(CampaignQuerySet):
    def hide_from_players(self, qs):
        return qs.filter(published=True)


class Session(models.Model):
    """A session's chronicle."""

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="sessions")
    number = models.PositiveIntegerField()
    title = models.CharField(max_length=200)
    played_on = models.DateField(null=True, blank=True, help_text="Real date the session was played; empty if unknown")
    world_day = models.CharField(max_length=100, blank=True)
    body = models.TextField(blank=True, help_text="[[Name]] links to a world page or a player character")
    xp_awarded = models.PositiveIntegerField(default=0)
    loot = models.CharField(max_length=300, blank=True)
    master_notes = models.TextField(blank=True, help_text="Only masters see them")
    published = models.BooleanField(default=False)

    objects = SessionQuerySet.as_manager()

    class Meta:
        ordering = ["campaign", "number"]
        constraints = [models.UniqueConstraint(fields=["campaign", "number"], name="unique_session_number")]
        indexes = [models.Index(fields=["campaign", "published"])]

    def __str__(self):
        return f"{self.campaign} · Sesión {self.number}: {self.title}"


class TimelineEventQuerySet(CampaignQuerySet):
    def hide_from_players(self, qs):
        return qs.filter(hidden=False)


class TimelineEvent(models.Model):
    class Kind(models.TextChoices):
        # The spec's five kinds plus the prototype's «misión» and «secreto».
        COMBATE = "combate", "Combate"
        SOCIAL = "social", "Social"
        DESCUBRIMIENTO = "descubrimiento", "Descubrimiento"
        VIAJE = "viaje", "Viaje"
        GIRO = "giro", "Giro"
        MISION = "mision", "Misión"
        SECRETO = "secreto", "Secreto"

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="events")
    session = models.ForeignKey(Session, on_delete=models.SET_NULL, null=True, blank=True, related_name="events")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    world_day = models.CharField(max_length=100, blank=True)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.SOCIAL)
    characters = models.ManyToManyField(Character, blank=True, related_name="events")
    location = models.ForeignKey("MapLocation", on_delete=models.SET_NULL, null=True, blank=True, related_name="events")
    hidden = models.BooleanField(default=False)
    secret = models.TextField(blank=True, help_text="Only masters see it, even when the event is visible")

    objects = TimelineEventQuerySet.as_manager()

    class Meta:
        ordering = ["campaign", "session__number", "id"]
        indexes = [models.Index(fields=["campaign", "hidden"])]

    def __str__(self):
        return self.title

    def clean(self):
        if self.session_id and self.campaign_id and self.session.campaign_id != self.campaign_id:
            raise ValidationError({"session": "The session belongs to another campaign."})
        if self.location_id and self.campaign_id and self.location.map.campaign_id != self.campaign_id:
            raise ValidationError({"location": "The location belongs to another campaign."})


# ---------------------------------------------------------------------------
# World (wiki)
# ---------------------------------------------------------------------------

class PageQuerySet(CampaignQuerySet):
    def hide_from_players(self, qs):
        # Pages that are not revealed do not exist for players.
        return qs.filter(revealed=True)


class Page(models.Model):
    class Category(models.TextChoices):
        LUGAR = "lugar", "Lugar"
        PNJ = "pnj", "PNJ"
        FACCION = "faccion", "Facción"
        OBJETO = "objeto", "Objeto"
        HISTORIA = "historia", "Historia"

    # Only these categories can hold other pages (Hespern › Rontal, Academia › LaPlace).
    PARENT_CATEGORIES = (Category.LUGAR, Category.FACCION)

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="pages")
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, blank=True)
    category = models.CharField(max_length=10, choices=Category.choices, default=Category.LUGAR)
    parent = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="children",
        limit_choices_to={"category__in": PARENT_CATEGORIES},
        help_text="Only a place or a faction",
    )
    summary = models.TextField(blank=True)
    body = models.TextField(blank=True, help_text="[[Name]] links to another page")
    secret = models.TextField(blank=True, help_text="Only masters see it")
    image = models.ImageField(upload_to="mundo/", blank=True)
    revealed = models.BooleanField(default=False, help_text="Pages that are not revealed do not exist for players")
    location = models.OneToOneField(
        "MapLocation", on_delete=models.SET_NULL, null=True, blank=True, related_name="page",
        help_text="Only for places: the map location of this page",
    )

    objects = PageQuerySet.as_manager()

    class Meta:
        ordering = ["campaign", "title"]
        constraints = [models.UniqueConstraint(fields=["campaign", "slug"], name="unique_page_slug")]
        indexes = [models.Index(fields=["campaign", "category", "revealed"])]

    def __str__(self):
        return self.title

    def clean(self):
        errors = {}
        if self.parent_id:
            parent = self.parent
            if parent.category not in self.PARENT_CATEGORIES:
                errors["parent"] = "A page can only be inside a place or a faction."
            elif self.campaign_id and parent.campaign_id != self.campaign_id:
                errors["parent"] = "The parent page belongs to another campaign."
            else:
                # No cycles: walk up from the parent and never meet this page.
                seen, node = set(), parent
                while node is not None:
                    if node.pk == self.pk or node.pk in seen:
                        errors["parent"] = "A page cannot be inside itself or inside one of its own pages."
                        break
                    seen.add(node.pk)
                    node = node.parent
        if self.location_id:
            if self.category != self.Category.LUGAR:
                errors["location"] = "Only places can have a map location."
            elif self.campaign_id and self.location.map.campaign_id != self.campaign_id:
                errors["location"] = "The location belongs to another campaign."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.title)[:200] or "pagina"
            slug, n = base, 2
            while Page.objects.filter(campaign_id=self.campaign_id, slug=slug).exclude(pk=self.pk).exists():
                slug, n = f"{base}-{n}", n + 1
            self.slug = slug
        super().save(*args, **kwargs)

    def ancestors(self):
        """Pages above this one, from the top (Valcendra › Puerto Gris)."""
        out, node, seen = [], self.parent, set()
        while node is not None and node.pk not in seen:
            out.insert(0, node)
            seen.add(node.pk)
            node = node.parent
        return out

    def set_revealed(self, revealed):
        """Revealing a page shows its map location as known; hiding it hides the location."""
        self.revealed = revealed
        self.save(update_fields=["revealed"])
        if self.location_id:
            location = self.location
            if revealed and location.status == MapLocation.Status.OCULTO:
                location.status = MapLocation.Status.CONOCIDO
            elif not revealed:
                location.status = MapLocation.Status.OCULTO
            location.save(update_fields=["status"])


# ---------------------------------------------------------------------------
# Aventuras (the model keeps its first name, Quest)
# ---------------------------------------------------------------------------

class QuestQuerySet(CampaignQuerySet):
    def hide_from_players(self, qs):
        # A hidden adventure does not exist for players, nor do the missions inside it.
        return qs.filter(hidden=False).exclude(parent__hidden=True)


class Quest(models.Model):
    """An adventure: a story arc that starts in one session and ends in another (or is
    still going on), with the master's checklist of objectives. The sessions it covers
    are those from `start_session` to `end_session` (to the last one while in progress)."""

    class Status(models.TextChoices):
        ACTIVA = "activa", "En curso"
        COMPLETADA = "completada", "Completada"
        FALLIDA = "fallida", "Fallida"
        SIN_DATO = "sin_dato", "Sin dato"  # finished, but nobody wrote down how it ended
        TERMINADA = "terminada", "Terminada"  # an arc that is over, whatever came of its missions

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="quests")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVA)
    giver = models.ForeignKey(Page, on_delete=models.SET_NULL, null=True, blank=True, related_name="quests_given")
    start_session = models.ForeignKey(Session, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
                                      help_text="Session in which the adventure starts")
    end_session = models.ForeignKey(Session, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
                                    help_text="Session in which it was completed or failed; empty while in progress")
    secret = models.TextField(blank=True, help_text="Only masters see it")
    hidden = models.BooleanField(default=False, help_text="A hidden adventure does not exist for players")
    # An adventure (an arc) holds missions: quests with a parent. Only one level deep.
    parent = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="missions",
                               help_text="The adventure this mission belongs to; empty for an adventure")

    objects = QuestQuerySet.as_manager()

    class Meta:
        verbose_name = "adventure"
        ordering = ["campaign", "id"]
        indexes = [models.Index(fields=["campaign", "status"]), models.Index(fields=["campaign", "hidden"])]

    def __str__(self):
        return self.title

    def clean(self):
        for field in ("start_session", "end_session"):
            session = getattr(self, field)
            if session is not None and self.campaign_id and session.campaign_id != self.campaign_id:
                raise ValidationError({field: "Esa sesión es de otra campaña."})
        if self.parent_id:
            if self.parent_id == self.pk:
                raise ValidationError({"parent": "Una aventura no puede estar dentro de sí misma."})
            if self.campaign_id and self.parent.campaign_id != self.campaign_id:
                raise ValidationError({"parent": "Esa aventura es de otra campaña."})
            if self.parent.parent_id:
                raise ValidationError({"parent": "Esa ya es una misión: elige una aventura."})
            if self.pk and self.missions.exists():
                raise ValidationError({"parent": "Esta aventura tiene misiones: no puede ir dentro de otra."})
        if self.end_session_id:
            if self.status == self.Status.ACTIVA:
                raise ValidationError({"end_session": "Una aventura en curso todavía no ha terminado: deja vacía la sesión final o cambia el estado."})
            if self.start_session_id is None:
                raise ValidationError({"start_session": "Di en qué sesión empezó."})
            if self.end_session.number < self.start_session.number:
                raise ValidationError({"end_session": "No puede terminar antes de empezar."})

    def covers(self, session_number, last_number):
        """Whether the session with this number is part of the adventure."""
        if self.start_session is None:
            return False
        end = self.end_session.number if self.end_session else (last_number if self.status == self.Status.ACTIVA
                                                                  else self.start_session.number)
        return self.start_session.number <= session_number <= end


class QuestObjective(models.Model):
    quest = models.ForeignKey(Quest, on_delete=models.CASCADE, related_name="objectives")
    text = models.CharField(max_length=300)
    done = models.BooleanField(default=False)
    hidden = models.BooleanField(default=False, help_text="Revealed when it is marked as done")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["quest", "order", "id"]

    def __str__(self):
        return self.text

    def save(self, *args, **kwargs):
        # A hidden objective is revealed when it is fulfilled.
        if self.done:
            self.hidden = False
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# Board (pizarra)
# ---------------------------------------------------------------------------

# A big board (three times the first one each way) seen through a zoomable window,
# so a whole web of clues fits. Positions are pixels of the board at zoom 100 %.
BOARD_WIDTH, BOARD_HEIGHT = 3300, 1920
CARD_WIDTH, CARD_HEIGHT = 200, 120
BOARD_CENTRE_X, BOARD_CENTRE_Y = (BOARD_WIDTH - CARD_WIDTH) // 2, (BOARD_HEIGHT - CARD_HEIGHT) // 2
PERSON_CARD_HEIGHT = 250  # a person card has a photo on top, so it is taller


class BoardCard(models.Model):
    """A note pinned on the board: a clue, or a person (with a photo, crossed out with
    a red cross once the party has dealt with them)."""

    class Kind(models.TextChoices):
        PISTA = "pista", "Pista"
        PERSONA = "persona", "Persona"

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="board_cards")
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.PISTA)
    image = models.ImageField(upload_to="pizarra/", blank=True, help_text="Only for people: their photo or portrait")
    crossed = models.BooleanField(default=False, help_text="Only for people: dealt with, a red cross over the card")
    title = models.CharField(max_length=120)
    text = models.TextField(blank=True)
    x = models.PositiveIntegerField(default=BOARD_CENTRE_X, validators=[MaxValueValidator(BOARD_WIDTH - CARD_WIDTH)])
    y = models.PositiveIntegerField(default=BOARD_CENTRE_Y, validators=[MaxValueValidator(BOARD_HEIGHT - CARD_HEIGHT)])
    page = models.ForeignKey(Page, on_delete=models.SET_NULL, null=True, blank=True, related_name="board_cards")
    classified = models.BooleanField(default=False, help_text="Players see it as «CLASIFICADO» until it is revealed")
    revealed = models.BooleanField(default=False)
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="board_cards")

    objects = CampaignQuerySet.as_manager()

    class Meta:
        ordering = ["campaign", "id"]

    def __str__(self):
        return self.title

    @property
    def is_person(self):
        return self.kind == self.Kind.PERSONA

    @property
    def height(self):
        return PERSON_CARD_HEIGHT if self.is_person else CARD_HEIGHT

    def locked_for(self, is_master):
        """Classified and not revealed: players only see that it exists."""
        return self.classified and not self.revealed and not is_master


class BoardLink(models.Model):
    """A red thread between two cards. The pair is stored in order (card_a < card_b),
    so the same pair cannot be stored twice in either order."""

    card_a = models.ForeignKey(BoardCard, on_delete=models.CASCADE, related_name="links_a")
    card_b = models.ForeignKey(BoardCard, on_delete=models.CASCADE, related_name="links_b")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["card_a", "card_b"], name="unique_board_link"),
            models.CheckConstraint(condition=models.Q(card_a__lt=models.F("card_b")), name="board_link_in_order"),
        ]

    def __str__(self):
        return f"{self.card_a} — {self.card_b}"

    def clean(self):
        if self.card_a_id and self.card_b_id:
            if self.card_a_id == self.card_b_id:
                raise ValidationError("A card cannot be linked to itself.")
            if self.card_a.campaign_id != self.card_b.campaign_id:
                raise ValidationError("Both cards must be on the same board.")

    def save(self, *args, **kwargs):
        if self.card_a_id and self.card_b_id and self.card_a_id > self.card_b_id:
            self.card_a_id, self.card_b_id = self.card_b_id, self.card_a_id
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# Role-play scenes
# ---------------------------------------------------------------------------

class Scene(models.Model):
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="scenes")
    title = models.CharField(max_length=200)
    location = models.ForeignKey(Page, on_delete=models.SET_NULL, null=True, blank=True, related_name="scenes")
    is_open = models.BooleanField(default=True)
    participants = models.ManyToManyField(Character, blank=True, related_name="scenes")

    objects = CampaignQuerySet.as_manager()

    class Meta:
        ordering = ["campaign", "-is_open", "id"]
        indexes = [models.Index(fields=["campaign", "is_open"])]

    def __str__(self):
        return self.title


class PostQuerySet(models.QuerySet):
    def visible_for(self, user, campaign):
        """A whisper is seen only by its author, the player of the character it is
        whispered to, and the masters."""
        qs = self.filter(scene__campaign=campaign)
        if campaign.is_master(user):
            return qs
        return qs.filter(
            models.Q(whisper_to__isnull=True) | models.Q(author=user) | models.Q(whisper_to__player=user)
        )


class Post(models.Model):
    scene = models.ForeignKey(Scene, on_delete=models.CASCADE, related_name="posts")
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="posts")
    as_character = models.ForeignKey(
        Character, on_delete=models.SET_NULL, null=True, blank=True, related_name="posts",
        help_text="Empty: the narrator",
    )
    text = models.TextField(blank=True)
    roll = models.CharField(max_length=200, blank=True, help_text="Result of «/tirar», rolled on the server")
    whisper_to = models.ForeignKey(
        Character, on_delete=models.SET_NULL, null=True, blank=True, related_name="whispers",
        help_text="Only the author, this character's player and the masters see it",
    )
    created_at = models.DateTimeField(default=timezone.now)

    objects = PostQuerySet.as_manager()

    class Meta:
        ordering = ["scene", "created_at", "id"]

    def __str__(self):
        return f"{self.scene}: {self.text[:40]}"


# ---------------------------------------------------------------------------
# Maps
# ---------------------------------------------------------------------------

class Map(models.Model):
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="maps")
    name = models.CharField(max_length=100)
    image = models.ImageField(upload_to="mapas/", blank=True)
    order = models.PositiveIntegerField(default=0)

    objects = CampaignQuerySet.as_manager()

    class Meta:
        ordering = ["campaign", "order", "id"]

    def __str__(self):
        return self.name


class MapLocationQuerySet(models.QuerySet):
    def visible_for(self, user, campaign):
        qs = self.filter(map__campaign=campaign)
        return qs if campaign.is_master(user) else qs.exclude(status=MapLocation.Status.OCULTO)


class MapLocation(models.Model):
    class Status(models.TextChoices):
        OCULTO = "oculto", "Oculto"
        CONOCIDO = "conocido", "Conocido"
        VISITADO = "visitado", "Visitado"

    map = models.ForeignKey(Map, on_delete=models.CASCADE, related_name="locations")
    name = models.CharField(max_length=100)
    # Percentage of the image, so the position does not depend on its size.
    x = models.FloatField(validators=[MinValueValidator(0), MaxValueValidator(100)])
    y = models.FloatField(validators=[MinValueValidator(0), MaxValueValidator(100)])
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.CONOCIDO)

    objects = MapLocationQuerySet.as_manager()

    class Meta:
        ordering = ["map", "name"]
        indexes = [models.Index(fields=["map", "status"])]

    def __str__(self):
        return self.name


class PartyMove(models.Model):
    """One step of the party's route on a map."""

    map = models.ForeignKey(Map, on_delete=models.CASCADE, related_name="moves")
    location = models.ForeignKey(MapLocation, on_delete=models.CASCADE, related_name="moves")
    session = models.ForeignKey(Session, on_delete=models.SET_NULL, null=True, blank=True, related_name="moves")
    order = models.PositiveIntegerField(default=0)
    # The way followed from the previous stop to this one: [[x, y], ...] in % of the
    # image, without the two stops. Empty means a straight line.
    path = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["map", "order", "id"]

    def __str__(self):
        return f"{self.map}: {self.location}"

    def clean(self):
        if self.location_id and self.map_id and self.location.map_id != self.map_id:
            raise ValidationError({"location": "The location is on another map."})


class WeatherRegion(models.Model):
    """A region of a fictional map linked to a real place: the map's weather layer shows
    that place's live weather there, and shades it when it is night there.

    The region is a map location (a circle around its pin) or a polygon the master
    draws. Only masters link and unlink regions. `weather` keeps the last values
    fetched from Open-Meteo (weather.py), shown again if the API is down."""

    map = models.ForeignKey(Map, on_delete=models.CASCADE, related_name="weather_regions")
    name = models.CharField(max_length=100)
    location = models.ForeignKey(MapLocation, on_delete=models.CASCADE, null=True, blank=True,
                                 related_name="weather_regions", help_text="The region around this location's pin")
    # Or a polygon: [[x, y], ...] in % of the image, at least 3 points.
    points = models.JSONField(default=list, blank=True)
    place_name = models.CharField(max_length=200, help_text="The real place, e.g. «Bilbao, País Vasco, España»")
    latitude = models.FloatField(validators=[MinValueValidator(-90), MaxValueValidator(90)])
    longitude = models.FloatField(validators=[MinValueValidator(-180), MaxValueValidator(180)])
    weather = models.JSONField(null=True, blank=True, help_text="Last weather fetched (weather.py)")
    weather_at = models.DateTimeField(null=True, blank=True, help_text="When it was fetched")

    class Meta:
        ordering = ["map", "name"]

    def __str__(self):
        return f"{self.name} → {self.place_name}"

    def clean(self):
        if self.location_id and self.map_id and self.location.map_id != self.map_id:
            raise ValidationError({"location": "The location is on another map."})
        if not self.location_id and len(self.points or []) < 3:
            raise ValidationError("Choose a location of the map or draw a region of at least 3 points.")


# ---------------------------------------------------------------------------
# Rumours
# ---------------------------------------------------------------------------

DEFAULT_RUMOUR_CATEGORIES = [("Rumor", "#6B6B5E"), ("Comunicado oficial", "#9A6B00"), ("Noticia", "#1F4E89"), ("Leyenda", "#5B3E96")]
validate_color = RegexValidator(r"^#[0-9A-Fa-f]{6}$", "Write a colour as #RRGGBB.")


class RumourCategory(models.Model):
    """A kind of rumour with its colour («Comunicado oficial», gold). Each campaign has its
    own list, which the master edits; a new campaign starts with DEFAULT_RUMOUR_CATEGORIES."""

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="rumour_categories")
    name = models.CharField(max_length=60)
    color = models.CharField(max_length=7, default="#6B6B5E", validators=[validate_color])
    order = models.PositiveIntegerField(default=0)

    objects = CampaignQuerySet.as_manager()

    class Meta:
        ordering = ["campaign", "order", "name"]
        verbose_name_plural = "rumour categories"
        constraints = [models.UniqueConstraint(fields=["campaign", "name"], name="unique_rumour_category")]

    def __str__(self):
        return self.name

    @property
    def text_color(self):
        """Black or white, whichever reads better on the category's colour."""
        r, g, b = (int(self.color[i:i + 2], 16) for i in (1, 3, 5))
        return "#1A1A1A" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#FFFFFF"


@receiver(post_save, sender=Campaign)
def default_rumour_categories(sender, instance, created, raw=False, **kwargs):
    """A new campaign starts with a few categories its master can rename or recolour.
    Not when loading a fixture (raw): the fixture brings its own."""
    if created and not raw:
        RumourCategory.objects.bulk_create(
            RumourCategory(campaign=instance, name=name, color=color, order=order)
            for order, (name, color) in enumerate(DEFAULT_RUMOUR_CATEGORIES))


class RumourQuerySet(CampaignQuerySet):
    def hide_from_players(self, qs):
        return qs.filter(published=True)


class Rumour(models.Model):
    """«Rumores de <campaign>»: what NPCs say, official notices... The master writes them;
    players see the published ones. The truth is only for masters."""

    class Truth(models.TextChoices):
        SIN_DECIDIR = "sin_decidir", "Sin decidir"
        VERDAD = "verdad", "Verdad"
        MEDIAS = "medias", "Verdad a medias"
        MENTIRA = "mentira", "Mentira"

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="rumours")
    category = models.ForeignKey(RumourCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name="rumours")
    text = models.TextField(help_text="[[Name]] links to a world page")
    source = models.CharField(max_length=150, blank=True, help_text="Who said it, if it is not a page of the world")
    source_page = models.ForeignKey(Page, on_delete=models.SET_NULL, null=True, blank=True, related_name="rumours_told",
                                    help_text="Who said it: an NPC or faction of the world")
    place = models.ForeignKey(Page, on_delete=models.SET_NULL, null=True, blank=True, related_name="rumours_heard",
                              help_text="Where the party heard it: a place of the world")
    session = models.ForeignKey(Session, on_delete=models.SET_NULL, null=True, blank=True, related_name="rumours")
    truth = models.CharField(max_length=12, choices=Truth.choices, default=Truth.SIN_DECIDIR, help_text="Only masters see it")
    published = models.BooleanField(default=False, help_text="Players see it")
    created_at = models.DateTimeField(default=timezone.now)

    objects = RumourQuerySet.as_manager()

    class Meta:
        ordering = ["campaign", "-created_at", "-id"]
        indexes = [models.Index(fields=["campaign", "published"])]

    def __str__(self):
        return self.text[:60]

    def clean(self):
        errors = {}
        if self.category_id and self.category.campaign_id != self.campaign_id:
            errors["category"] = "The category belongs to another campaign."
        for field in ("source_page", "place", "session"):
            related = getattr(self, field)
            if related is not None and related.campaign_id != self.campaign_id:
                errors[field] = "It belongs to another campaign."
        if errors:
            raise ValidationError(errors)


# ---------------------------------------------------------------------------
# Combat turns
# ---------------------------------------------------------------------------

class Encounter(models.Model):
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="encounters")
    active = models.BooleanField(default=True)
    round = models.PositiveIntegerField(default=1)
    turn_index = models.PositiveIntegerField(default=0)

    objects = CampaignQuerySet.as_manager()

    class Meta:
        ordering = ["campaign", "-id"]
        indexes = [models.Index(fields=["campaign", "active"])]
        constraints = [
            models.UniqueConstraint(fields=["campaign"], condition=models.Q(active=True), name="one_active_encounter"),
        ]

    def __str__(self):
        return f"{self.campaign} · ronda {self.round}"


class Combatant(models.Model):
    encounter = models.ForeignKey(Encounter, on_delete=models.CASCADE, related_name="combatants")
    character = models.ForeignKey(Character, on_delete=models.CASCADE, null=True, blank=True, related_name="combatants")
    name = models.CharField(max_length=100, blank=True, help_text="For enemies without a character")
    initiative = models.IntegerField(null=True, blank=True)
    # Only for enemies without a character: characters keep their HP on their sheet.
    hp = models.IntegerField(null=True, blank=True)
    hp_max = models.IntegerField(null=True, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["encounter", "order", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(character__isnull=False) | ~models.Q(name=""),
                name="combatant_has_character_or_name",
            ),
        ]

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        return self.character.name if self.character_id else self.name

    @property
    def current_hp(self):
        return self.character.temp_hp if self.character_id else self.hp

    @property
    def max_hp(self):
        return self.character.hp if self.character_id else self.hp_max
