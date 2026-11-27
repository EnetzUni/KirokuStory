import re
import zoneinfo

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models, transaction
from django.db.models.functions import Upper
from django.db.models.signals import m2m_changed, post_delete, pre_delete
from django.dispatch import receiver
from django.utils.translation import gettext_noop

from . import rules


# ---------------------------------------------------------------------------
# Users and campaigns
# ---------------------------------------------------------------------------

class User(AbstractUser):
    # username, email and a hashed password are inherited from AbstractUser.

    class SheetView(models.TextChoices):
        WEB = "web", "Vista web"
        FICHA = "ficha", "Vista ficha"

    # How this user likes to see character sheets: the web cards or the paper sheet layout.
    sheet_view = models.CharField(max_length=5, choices=SheetView.choices, default=SheetView.WEB)


def time_zone_choices():
    """The system's IANA time zones, for forms. Not stored in the model (nor its migration):
    the list depends on the machine's time zone data."""
    return [(name, name.replace("_", " ")) for name in sorted(zoneinfo.available_timezones())]


def validate_time_zone(value):
    try:
        zoneinfo.ZoneInfo(value)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        raise ValidationError(f"«{value}» is not a time zone, e.g. Europe/Madrid.")


class Campaign(models.Model):
    class Ruleset(models.TextChoices):
        AEGIS = rules.AEGIS, "Aegis"
        SRD = rules.SRD, "SRD 5.2.1"

    class Status(models.TextChoices):
        ACTIVA = "activa", "Activa"
        PAUSADA = "pausada", "Pausada"
        TERMINADA = "terminada", "Terminada"

    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    start_date = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVA)
    # Aegis: our variant of the SRD 5.2.1 with our additions. Or the standard SRD.
    ruleset = models.CharField(max_length=10, choices=Ruleset.choices, default=Ruleset.AEGIS,
                               help_text="The rules the campaign plays with (see rules.py)")
    cover = models.ImageField(upload_to="portadas/", blank=True)
    notice = models.TextField(blank=True, help_text="«Aviso del máster» on the home page")
    world_day = models.CharField(max_length=100, blank=True, help_text="Day in the world, e.g. «3 de Brumario»")
    world_year = models.CharField(max_length=100, blank=True)
    # The one clock everybody in the campaign shares (the map's weather layer shows it).
    time_zone = models.CharField(max_length=64, default="Europe/Madrid", validators=[validate_time_zone],
                                 help_text="IANA name, e.g. Europe/Madrid")

    # dirige / juega en
    masters = models.ManyToManyField(User, related_name="dm_campaigns")
    players = models.ManyToManyField(User, related_name="player_campaigns", blank=True)

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["status"])]

    def __str__(self):
        return self.name

    def _role(self, user):
        """"master", "player" or None. Remembered on this instance: a request asks many times."""
        if not user.is_authenticated:
            return None
        cache = self.__dict__.setdefault("_roles", {})
        if user.pk not in cache:
            if self.masters.filter(pk=user.pk).exists():
                cache[user.pk] = "master"
            elif self.players.filter(pk=user.pk).exists():
                cache[user.pk] = "player"
            else:
                cache[user.pk] = None
        return cache[user.pk]

    def is_master(self, user):
        return self._role(user) == "master"

    def is_member(self, user):
        return self._role(user) is not None

    @property
    def is_aegis(self):
        """Plays with our additions: unique skills, 1d4 hit points, feats every 4 levels..."""
        return self.ruleset == self.Ruleset.AEGIS

    @property
    def party(self):
        """Approved player characters of the campaign."""
        return self.characters.filter(is_npc=False, status=Character.Status.APROBADO)

    @property
    def last_session(self):
        return self.sessions.filter(published=True).order_by("-number").first()


# ---------------------------------------------------------------------------
# Characters
# ---------------------------------------------------------------------------

class Character(models.Model):
    class Status(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente"
        APROBADO = "aprobado", "Aprobado"

    name = models.CharField(max_length=100)
    xp = models.IntegerField(default=0)
    # The master gives XP; the level goes up when the player levels up from the
    # sheet and makes that level's choices. Until then the XP is ahead of it.
    level = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(rules.MAX_LEVEL)],
        help_text="Level reached. It can be behind the XP until the player levels up",
    )
    hp = models.IntegerField(help_text="Maximum hit points")
    temp_hp = models.IntegerField(help_text="Current hit points")
    ac = models.IntegerField(help_text="Armor class")
    # The speed comes from the race; an NPC that runs, flies or crawls has its own.
    own_speed = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="In meters. Only when it is not the race's (an NPC of the bestiary); empty: the race's")
    temp_ap = models.IntegerField(default=0, help_text="Current armor points")
    inspiration = models.IntegerField(default=0)
    strength = models.IntegerField()
    dexterity = models.IntegerField()
    constitution = models.IntegerField()
    intelligence = models.IntegerField()
    wisdom = models.IntegerField()
    charisma = models.IntegerField()
    portrait = models.ImageField(upload_to="retratos/", blank=True)
    background = models.TextField(blank=True)
    notes = models.TextField(blank=True, help_text="The player's own notes")
    # Characters made by a player wait for a master's approval.
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.APROBADO)
    # Knocked down and death (house rule): at 0 HP or less the character is knocked
    # down; it dies at minus half its maximum HP (rules.death_hp) or when knocked down
    # a second time in the same fight. `knocked_down_in` is the fight of the last
    # knockdown; a new fight starts with a clean slate.
    dead = models.BooleanField(default=False)
    knocked_down_in = models.ForeignKey(
        "Encounter", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="The fight in which the character was last knocked down",
    )
    # Current magic points. The maximum comes from the class and the level
    # (rules.class_magic_points), plus the master's adjustment.
    magic_points = models.PositiveIntegerField(default=0)
    magic_points_bonus = models.IntegerField(
        default=0, help_text="The master's adjustment to the magic points the class gives at this level (can be negative)")

    # controla (optional), define, pertenece a. The subclass must belong to the
    # character's class and the subrace to its race (checked in clean()).
    player = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    char_class = models.ForeignKey("CharacterClass", on_delete=models.PROTECT)
    subclass = models.ForeignKey("Subclass", on_delete=models.PROTECT, null=True, blank=True)
    race = models.ForeignKey("Race", on_delete=models.PROTECT)
    subrace = models.ForeignKey("Subrace", on_delete=models.PROTECT, null=True, blank=True)

    # contiene: many campaigns, or none. Deleting a campaign only removes the link,
    # so the character is kept.
    campaigns = models.ManyToManyField("Campaign", blank=True, related_name="characters")

    # NPCs. A template is an NPC with no template ("Goblin"); a copy is an NPC that
    # points to the template it was made from ("Goblin 2"). Copies are independent:
    # each has its own HP, inventory and effects. Deleting a template deletes its copies.
    is_npc = models.BooleanField(default=False)
    template = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="copies",
        limit_choices_to={"is_npc": True, "template__isnull": True},
        help_text="Only for NPC copies: the NPC template this character was made from",
    )
    # A pair of the bestiary that acts in one activation (Shayne and Aurox): each template points to
    # the other, and their copies keep it. In «Turnos» two partners side by side take one turn.
    acts_with = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        limit_choices_to={"is_npc": True, "template__isnull": True},
        help_text="Only for NPCs: the NPC template it acts with, in one activation",
    )

    # posee (with uses, through CharacterAbility), conoce, lleva, sufre
    abilities = models.ManyToManyField("Ability", through="CharacterAbility", blank=True)
    spells = models.ManyToManyField("Spell", blank=True)
    items = models.ManyToManyField("Item", through="Inventory")
    effects = models.ManyToManyField("Effect", through="ActiveEffect")

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["is_npc", "status"])]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(template__isnull=True) | models.Q(is_npc=True),
                name="template_only_for_npcs",
                violation_error_message="Only NPCs can have a template.",
            ),
            models.CheckConstraint(
                condition=models.Q(level__gte=1, level__lte=rules.MAX_LEVEL),
                name="level_from_1_to_max",
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def is_npc_template(self):
        return self.is_npc and self.template_id is None

    # ---- Calculated, not stored (game rule 9) ----
    @property
    def xp_level(self):
        """Level the XP is enough for, with the SRD table."""
        return rules.level_for_xp(self.xp)

    @property
    def can_level_up(self):
        return self.xp_level > self.level

    @property
    def speed(self):
        """In meters: its own (an NPC of the bestiary) or its race's."""
        return self.race.speed if self.own_speed is None else self.own_speed

    @property
    def initiative(self):
        return rules.ability_modifier(self.dexterity)

    @property
    def passive_perception(self):
        return 10 + rules.ability_modifier(self.wisdom)

    @property
    def death_hp(self):
        """At this many hit points or fewer the character dies."""
        return rules.death_hp(self.hp)

    @property
    def knocked_down(self):
        return not self.dead and self.temp_hp <= 0

    @property
    def equipped_armor(self):
        return Armor.objects.filter(equipped=True, item__inventory__character=self).select_related("armor_type", "item").first()

    @property
    def max_ap(self):
        """Armor.ap + ArmorType.ap of the equipped armor, 0 without armor (game rule 2)."""
        armor = self.equipped_armor
        return armor.ap + armor.armor_type.ap if armor else 0

    @property
    def max_magic_ap(self):
        armor = self.equipped_armor
        return armor.magic_ap + armor.armor_type.magic_ap if armor else 0

    @property
    def class_magic_points(self):
        """Magic points the class gives at the character's level, before the master's adjustment."""
        return rules.class_magic_points(self.char_class.magic, self.level) if self.char_class_id else 0

    @property
    def magic_points_max(self):
        return max(0, self.class_magic_points + self.magic_points_bonus)

    @property
    def has_magic(self):
        return self.magic_points_max > 0

    @property
    def combat_magic(self):
        """Aegis: the class spends its magic points on add-ons, not on spells (the Arcane Warrior)."""
        return self.char_class_id is not None and self.char_class.magic == CharacterClass.Magic.COMBATE

    @property
    def addon_save_dc(self):
        """«Salvación a superar» against its add-ons: 12 + Dexterity modifier (rules.addon_save_dc)."""
        return rules.addon_save_dc(rules.ability_modifier(self.dexterity))

    def all_ability_ids(self):
        """IDs of the abilities held directly or granted by race, subrace, class and
        subclass at the character's level (game rules 6 and 7)."""
        direct = set(self.abilities.values_list("pk", flat=True)) if self.pk else set()
        return direct | granted_ability_ids(
            self.race if self.race_id else None,
            self.subrace,
            self.char_class if self.char_class_id else None,
            self.subclass,
            level=self.level,
        )

    def modifier(self, stat):
        """Modifier of an ability score by its code: "str", "dex", "con", "int", "wis" or "cha"."""
        return rules.ability_modifier(getattr(self, rules.ABILITY_BY_CODE[stat]))

    def clean(self):
        errors = {}
        if self.subrace_id is not None and self.race_id is not None and self.subrace.race_id != self.race_id:
            errors["subrace"] = f"{self.subrace} is a subrace of {self.subrace.race}, not of {self.race}."
        if (
            self.subclass_id is not None
            and self.char_class_id is not None
            and self.subclass.char_class_id != self.char_class_id
        ):
            errors["subclass"] = f"{self.subclass} is a subclass of {self.subclass.char_class}, not of {self.char_class}."
        if self.char_class_id is not None and self.magic_points > self.magic_points_max:
            errors["magic_points"] = f"The maximum is {self.magic_points_max} magic points."
        if self.template_id is not None:
            if self.template_id == self.pk:
                errors["template"] = "A character cannot be its own template."
            elif self.pk is not None and self.copies.exists():
                errors["template"] = "This NPC is a template with copies, so it cannot be a copy."
        # Unique abilities (game rule 6), with the campaigns and abilities already
        # saved. The admin form checks the ones being saved instead and sets the flag.
        if self.pk is not None and not getattr(self, "_unique_abilities_checked", False):
            conflicts = unique_ability_conflicts(self, self.campaigns.all(), self.all_ability_ids())
            if conflicts:
                errors[NON_FIELD_ERRORS] = conflicts
        if errors:
            raise ValidationError(errors)

    @transaction.atomic
    def make_copy(self, name=None, campaigns=()):
        """Create a new NPC from this template and return it.

        Copies the stats, race and subrace, class and subclass, abilities,
        spells, inventory and active effects.
        Item copies (individual weapons and armor) are copied again so each NPC
        has its own; item templates carried directly (gold, potions...) are
        shared. Campaigns are not copied: pass them in `campaigns`.
        """
        if not self.is_npc_template:
            raise ValueError("Only NPC templates can be copied.")
        if self.abilities.filter(unique=True).exists():
            raise ValueError("An NPC with a unique ability cannot have copies.")

        copy = Character.objects.get(pk=self.pk)
        copy.pk = None
        copy._state.adding = True
        copy.template = self
        copy.name = name or f"{self.name} {self.copies.count() + 1}"
        copy.save()

        CharacterAbility.objects.bulk_create(
            CharacterAbility(
                character=copy, ability_id=row.ability_id, max_uses=row.max_uses, uses_left=row.uses_left,
                recharge=row.recharge, cooldown_rounds=row.cooldown_rounds, cooldown_left=row.cooldown_left,
            )
            for row in self.characterability_set.all()
        )
        copy.spells.set(self.spells.all())
        copy.campaigns.set(campaigns)

        for row in self.inventory_set.select_related("item"):
            item = row.item
            if not item.is_template:
                # The copy carries it right away, so it keeps what the template has equipped.
                item = item.make_copy(keep_equipped=True)
            Inventory.objects.create(character=copy, item=item, quantity=row.quantity)

        for row in self.activeeffect_set.all():
            ActiveEffect.objects.create(
                character=copy, effect=row.effect, remaining_turns=row.remaining_turns
            )

        return copy


class CharacterAbility(models.Model):
    """An ability a character holds directly (posee), with its uses.

    The master sets the uses and cooldowns (open decision: they are not taken
    from the SRD text). Abilities granted by race, class... appear on the sheet
    as traits; give the character a row here too if the ability has uses.
    """

    class Recharge(models.TextChoices):
        CORTO = "corto", "Descanso corto"
        LARGO = "largo", "Descanso largo"
        NINGUNO = "ninguno", "Ninguno"

    character = models.ForeignKey("Character", on_delete=models.CASCADE)
    ability = models.ForeignKey("Ability", on_delete=models.CASCADE)
    max_uses = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Empty: no limited uses")
    uses_left = models.PositiveSmallIntegerField(default=0)
    recharge = models.CharField(max_length=10, choices=Recharge.choices, default=Recharge.NINGUNO)
    cooldown_rounds = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Empty: no cooldown")
    cooldown_left = models.PositiveSmallIntegerField(default=0)

    class Meta:
        verbose_name_plural = "character abilities"
        ordering = ["character", "ability__name"]
        constraints = [
            models.UniqueConstraint(fields=["character", "ability"], name="unique_character_ability"),
            models.CheckConstraint(
                condition=models.Q(max_uses__isnull=True) | models.Q(uses_left__lte=models.F("max_uses")),
                name="uses_left_up_to_max",
            ),
            models.CheckConstraint(
                condition=models.Q(cooldown_rounds__isnull=True) | models.Q(cooldown_left__lte=models.F("cooldown_rounds")),
                name="cooldown_left_up_to_rounds",
            ),
        ]

    def __str__(self):
        return f"{self.character} - {self.ability}"

    @property
    def has_uses(self):
        return self.max_uses is not None

    @property
    def has_cooldown(self):
        return self.cooldown_rounds is not None

    @property
    def tracked(self):
        """Shown under «Habilidades con usos» on the sheet."""
        return self.has_uses or self.has_cooldown

    @property
    def ready(self):
        if self.has_cooldown and self.cooldown_left > 0:
            return False
        if self.has_uses and self.uses_left <= 0:
            return False
        return True

    def use(self):
        """Spend a use and start the cooldown. Raises ValueError if it is not ready."""
        if not self.ready:
            raise ValueError(f"{self.ability} is not ready.")
        if self.has_uses:
            self.uses_left -= 1
        if self.has_cooldown:
            self.cooldown_left = self.cooldown_rounds

    def recover(self, rest):
        """Short or long rest: cooldowns end. A long rest gives back every use;
        a short rest only those that recharge with a short rest."""
        self.cooldown_left = 0
        if self.has_uses and (rest == self.Recharge.LARGO or self.recharge == self.Recharge.CORTO):
            self.uses_left = self.max_uses

    def clean(self):
        if self.character_id is None or self.ability_id is None or not self.ability.unique:
            return
        # An NPC with a unique ability cannot have copies (game rule 11).
        if self.character.copies.exists():
            raise ValidationError({"ability": "This NPC template has copies, so it cannot have a unique ability."})
        # Unique abilities (game rule 6), for rows created directly.
        if self._state.adding:
            conflicts = unique_ability_conflicts(self.character, self.character.campaigns.all(), [self.ability_id])
            if conflicts:
                raise ValidationError({"ability": conflicts})


class UniqueSkill(models.Model):
    """A character's unique skill (habilidad única), game rule 17.

    The player describes it when creating the character or later from the sheet,
    and a master approves it (changing it if needed) or rejects it with a note.
    Once approved it is an Ability with unique = true that the character holds,
    so rule 6 applies to it. One per character.
    """

    class Status(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente"
        APROBADA = "aprobada", "Aprobada"
        RECHAZADA = "rechazada", "Rechazada"

    character = models.OneToOneField("Character", on_delete=models.CASCADE, related_name="unique_skill")
    name = models.CharField(max_length=100)
    description = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDIENTE)
    master_note = models.TextField(blank=True, help_text="The master's note to the player: why it was rejected or changed")
    # Created on approval. Deleting the ability deletes this row, and the player can propose another.
    ability = models.OneToOneField("Ability", on_delete=models.CASCADE, null=True, blank=True, related_name="unique_skill")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status="aprobada", ability__isnull=False)
                | (~models.Q(status="aprobada") & models.Q(ability__isnull=True)),
                name="unique_skill_ability_when_approved",
            ),
        ]

    def __str__(self):
        return f"{self.character} - {self.name} ({self.get_status_display()})"

    def propose(self, name, description):
        """The player's text, new or rewritten: it waits for a master again."""
        if self.status == self.Status.APROBADA and self.pk:
            raise ValueError("This unique skill is already approved.")
        self.name, self.description = name, description
        self.status, self.master_note = self.Status.PENDIENTE, ""

    @transaction.atomic
    def approve(self, name, description, note=""):
        """Approve it as proposed or with the master's changes: the character gets a
        unique Ability with this name and description. Raises ValidationError if the
        character cannot have it (rule 11: an NPC template with copies)."""
        if self.status == self.Status.APROBADA and self.pk:
            raise ValueError("This unique skill is already approved.")
        ability = Ability.objects.create(name=name, description=description, unique=True)
        row = CharacterAbility(character=self.character, ability=ability)
        row.full_clean()
        row.save()
        self.name, self.description, self.master_note = name, description, note
        self.status, self.ability = self.Status.APROBADA, ability
        self.save()

    def reject(self, note=""):
        self.status, self.master_note = self.Status.RECHAZADA, note
        self.save()


class Race(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    speed = models.IntegerField(help_text="In meters")
    # Made by a master for one character (NPC creator, sheet editor): kept out of the players' lists.
    bespoke = models.BooleanField(default=False, help_text="Made by a master for a character: players do not see it in the catalog lists")

    # otorga
    abilities = models.ManyToManyField("Ability", through="RaceAbility")

    def __str__(self):
        return self.name


class Subrace(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    # tiene
    race = models.ForeignKey("Race", on_delete=models.CASCADE, related_name="subraces")

    # otorga
    abilities = models.ManyToManyField("Ability", through="SubraceAbility")

    def __str__(self):
        return self.name


class CharacterClass(models.Model):
    class Magic(models.TextChoices):
        # The magic points each kind gives by level are in rules.class_magic_points().
        COMPLETA = "completa", "Lanzador completo"
        MEDIA = "media", "Medio lanzador"
        PACTO = "pacto", "Magia de pacto"
        # Aegis: no spells; the points («a») go to the class's add-ons (ClassAddon).
        COMBATE = "combate", "Magia de combate (añadidos)"
        NINGUNA = "ninguna", "Sin magia"

    name = models.CharField(max_length=100)
    primary_ability = models.CharField(max_length=50)
    description = models.TextField(blank=True)
    magic = models.CharField(max_length=10, choices=Magic.choices, default=Magic.NINGUNA,
                             help_text="Gives the character's magic points at each level")
    # Made by a master for one character (NPC creator, sheet editor): kept out of the players' lists.
    bespoke = models.BooleanField(default=False, help_text="Made by a master for a character: players do not see it in the catalog lists")
    # Our own classes, such as the Arcane Warrior: campaigns that play with the standard SRD do not offer them.
    aegis_only = models.BooleanField(default=False, help_text="A class of Aegis: campaigns with the standard SRD 5.2.1 do not offer it")

    # otorga, puede lanzar
    abilities = models.ManyToManyField("Ability", through="ClassAbility")
    spells = models.ManyToManyField("Spell", blank=True)

    class Meta:
        verbose_name_plural = "character classes"

    def __str__(self):
        return self.name

    def addon_groups(self):
        """Aegis: its add-ons in the groups of the class sheet (Espada, Arco, Buffs, Defensa),
        as [{"kind", "label", "addons"}]. Empty for a class without add-ons."""
        addons = list(self.addons.all())
        return [{"kind": kind, "label": label, "addons": [a for a in addons if a.kind == kind]}
                for kind, label in ClassAddon.Kind.choices if any(a.kind == kind for a in addons)]


class Subclass(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    # tiene
    char_class = models.ForeignKey("CharacterClass", on_delete=models.CASCADE, related_name="subclasses")

    # otorga
    abilities = models.ManyToManyField("Ability", through="SubclassAbility")

    class Meta:
        verbose_name_plural = "subclasses"

    def __str__(self):
        return self.name


class ClassAddon(models.Model):
    """Aegis: an add-on («añadido») of a class with combat magic, such as the Arcane Warrior.
    It goes with an attack (or a defence) without costing another action, and costs magic
    points («a»). The rules all add-ons share are in rules.py and partials/addon_rules.html."""

    class Kind(models.TextChoices):
        # The groups of the class sheet, in its order.
        ESPADA = "espada", gettext_noop("Espada")
        ARCO = "arco", gettext_noop("Arco")
        BUFF = "buff", gettext_noop("Buffs")
        DEFENSA = "defensa", gettext_noop("Defensa")

    char_class = models.ForeignKey("CharacterClass", on_delete=models.CASCADE, related_name="addons")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    name = models.CharField(max_length=100)
    cost = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)], help_text="Magic points («a») it costs")
    max_cost = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="Only when the player chooses how many points to spend (Escudo: from 1a to 5a): the most it can cost")
    effect = models.TextField(help_text="What it adds to the attack or to the defence")
    order = models.PositiveSmallIntegerField(default=0, help_text="Its place on the class sheet")

    class Meta:
        ordering = ["char_class", "order", "id"]
        constraints = [
            models.UniqueConstraint(fields=["char_class", "name"], name="unique_class_addon"),
            models.CheckConstraint(condition=models.Q(max_cost__isnull=True) | models.Q(max_cost__gt=models.F("cost")),
                                   name="addon_max_cost_above_cost",
                                   violation_error_message="The highest cost must be above the cost."),
        ]

    def __str__(self):
        return f"{self.char_class} - {self.name} ({self.cost_text})"

    @property
    def cost_text(self):
        """As on the class sheet: «2a», or «1a-5a» when the player chooses."""
        return f"{self.cost}a-{self.max_cost}a" if self.max_cost else f"{self.cost}a"

    @property
    def costs(self):
        """The points it can cost: its cost, or every value the player can choose."""
        return list(range(self.cost, (self.max_cost or self.cost) + 1))


# ---------------------------------------------------------------------------
# Abilities
# ---------------------------------------------------------------------------

class Ability(models.Model):
    name = models.CharField(max_length=100)
    unique = models.BooleanField(
        default=False,
        help_text="If true, only one character per campaign can have it",
    )
    description = models.TextField(blank=True)
    # Feats (dotes): at the feat levels (rules.FEAT_LEVELS: 4, 8, 12, 16 and 20) the
    # player chooses one instead of the ability score improvement.
    feat = models.BooleanField(default=False, help_text="A feat: chosen when levelling up at levels 4, 8, 12, 16 and 20")
    feat_min_level = models.PositiveSmallIntegerField(
        default=rules.FEAT_LEVELS[0],
        validators=[MinValueValidator(1), MaxValueValidator(rules.MAX_LEVEL)],
        help_text="Only for feats: the lowest level at which it can be chosen (e.g. 19 for epic boons, so from level 20)",
    )

    # aplica
    effects = models.ManyToManyField("Effect", blank=True)

    class Meta:
        verbose_name_plural = "abilities"

    def __str__(self):
        return self.name

    def clean(self):
        if not self.unique or self.pk is None:
            return
        errors = []
        # An NPC with a unique ability cannot have copies (game rule 11).
        if Character.objects.filter(abilities=self, copies__isnull=False).exists():
            errors.append("An NPC template with copies has this ability, so it cannot be unique.")
        # Only one character per campaign can have a unique ability (game rule 6).
        errors += campaign_clashes(self, ability_holders(self))
        if errors:
            raise ValidationError({"unique": errors})


class RaceAbility(models.Model):
    race = models.ForeignKey("Race", on_delete=models.CASCADE)
    ability = models.ForeignKey("Ability", on_delete=models.CASCADE)
    level_required = models.IntegerField(default=1)

    class Meta:
        verbose_name_plural = "race abilities"
        constraints = [
            models.UniqueConstraint(fields=["race", "ability"], name="unique_race_ability"),
        ]

    def __str__(self):
        return f"{self.race} - {self.ability} (lvl {self.level_required})"

    def clean(self):
        # A unique ability granted by a race goes to every character of that race (game rule 6).
        if self.race_id is not None:
            gaining = [c for c in Character.objects.filter(race_id=self.race_id) if c.level >= self.level_required]
            validate_granted_ability(self.ability_id, gaining)


class ClassAbility(models.Model):
    char_class = models.ForeignKey("CharacterClass", on_delete=models.CASCADE)
    ability = models.ForeignKey("Ability", on_delete=models.CASCADE)
    level_required = models.IntegerField(default=1)

    class Meta:
        verbose_name_plural = "class abilities"
        constraints = [
            models.UniqueConstraint(fields=["char_class", "ability"], name="unique_class_ability"),
        ]

    def __str__(self):
        return f"{self.char_class} - {self.ability} (lvl {self.level_required})"

    def clean(self):
        # A unique ability granted by a class goes to every character of that class (game rule 6).
        if self.char_class_id is not None:
            gaining = [c for c in Character.objects.filter(char_class_id=self.char_class_id) if c.level >= self.level_required]
            validate_granted_ability(self.ability_id, gaining)


class SubraceAbility(models.Model):
    subrace = models.ForeignKey("Subrace", on_delete=models.CASCADE)
    ability = models.ForeignKey("Ability", on_delete=models.CASCADE)
    level_required = models.IntegerField(default=1)

    class Meta:
        verbose_name_plural = "subrace abilities"
        constraints = [
            models.UniqueConstraint(fields=["subrace", "ability"], name="unique_subrace_ability"),
        ]

    def __str__(self):
        return f"{self.subrace} - {self.ability} (lvl {self.level_required})"

    def clean(self):
        # A unique ability granted by a subrace goes to every character of that subrace (game rule 6).
        if self.subrace_id is not None:
            gaining = [c for c in Character.objects.filter(subrace_id=self.subrace_id) if c.level >= self.level_required]
            validate_granted_ability(self.ability_id, gaining)


class SubclassAbility(models.Model):
    subclass = models.ForeignKey("Subclass", on_delete=models.CASCADE)
    ability = models.ForeignKey("Ability", on_delete=models.CASCADE)
    level_required = models.IntegerField(default=1)

    class Meta:
        verbose_name_plural = "subclass abilities"
        constraints = [
            models.UniqueConstraint(fields=["subclass", "ability"], name="unique_subclass_ability"),
        ]

    def __str__(self):
        return f"{self.subclass} - {self.ability} (lvl {self.level_required})"

    def clean(self):
        # A unique ability granted by a subclass goes to every character of that subclass (game rule 6).
        if self.subclass_id is not None:
            gaining = [c for c in Character.objects.filter(subclass_id=self.subclass_id) if c.level >= self.level_required]
            validate_granted_ability(self.ability_id, gaining)


# ---------------------------------------------------------------------------
# Spells and combat
# ---------------------------------------------------------------------------

class Spell(models.Model):
    name = models.CharField(max_length=100)
    level = models.IntegerField(default=0, help_text="0 = cantrip (truco)")
    # Reactions describe their trigger, so some casting times are long sentences.
    casting_time = models.CharField(max_length=200)
    # Empty for ranges that are not a distance: "Vista", "Ilimitado" or "Especial".
    range = models.IntegerField(null=True, blank=True, help_text="In meters. Empty if not a distance (Vista, Ilimitado, Especial)")
    duration = models.CharField(max_length=50)
    description = models.TextField(blank=True)

    # requiere, realiza, aplica
    components = models.ManyToManyField("Component", blank=True)
    attacks = models.ManyToManyField("Attack", blank=True)
    effects = models.ManyToManyField("Effect", blank=True)

    def __str__(self):
        return self.name


class Component(models.Model):
    type = models.CharField(max_length=50)

    def __str__(self):
        return self.type


class Stat(models.TextChoices):
    """Ability scores that attacks use, by the codes stored in the data."""
    STRENGTH = "str", "Fuerza"
    DEXTERITY = "dex", "Destreza"


class Attack(models.Model):
    name = models.CharField(max_length=100)
    extra = models.IntegerField(default=0, help_text="Fixed damage that does not depend on dice")
    # Some attacks have no set range (bombs). In melee, 1 square = 1 m.
    range = models.IntegerField(null=True, blank=True, help_text="In meters; in melee, 1 square = 1 m. Empty if the attack has no set range")
    area = models.IntegerField(default=0, help_text="In meters")
    cooldown = models.IntegerField(default=0, help_text="In rounds")
    # Damage = base dice + extra + this ability's modifier + dice per extra level.
    stat_bonus = models.CharField(
        max_length=3,
        choices=Stat.choices,
        null=True,
        blank=True,
        help_text="Ability whose modifier is added to the damage (some blunt attacks add Strength)",
    )

    # tira, inflige, aplica
    dice = models.ManyToManyField("DiceRoll", blank=True)
    armor_damages = models.ManyToManyField("ArmorDamage", blank=True)
    effects = models.ManyToManyField("Effect", blank=True)

    def __str__(self):
        return self.name

    @property
    def base_dice(self):
        return self.dice.filter(per_extra_level=False)

    @property
    def scaling_dice(self):
        """Dice added once per extra level. Spells only: weapons ignore them."""
        return self.dice.filter(per_extra_level=True)

    def damage(self, character=None, extra_levels=0):
        """The damage with numbers (game rule 4), as (dice, bonus).

        dice: [(amount, sides)], the base dice plus the dice per extra level rolled
        `extra_levels` times, adding up dice with the same sides.
        bonus: `extra` plus `character`'s modifier for `stat_bonus`, if the attack has one.
        """
        amounts = {}
        for roll in self.dice.all():
            times = extra_levels if roll.per_extra_level else 1
            if times:
                amounts[roll.sides] = amounts.get(roll.sides, 0) + roll.amount * times
        bonus = self.extra
        if self.stat_bonus:
            if character is None:
                raise ValueError(f"{self} adds a {self.get_stat_bonus_display()} modifier: pass the character.")
            bonus += character.modifier(self.stat_bonus)
        return [(amount, sides) for sides, amount in amounts.items()], bonus

    def damage_text(self, character=None, extra_levels=0):
        """The damage with numbers as text, such as "2d10 + 8" or "1d4 - 1"."""
        dice, bonus = self.damage(character, extra_levels)
        text = " + ".join(f"{amount}d{sides}" for amount, sides in dice)
        if not text:
            return str(bonus)
        if bonus:
            text += f" {'+' if bonus > 0 else '-'} {abs(bonus)}"
        return text


class DiceRoll(models.Model):
    amount = models.IntegerField(help_text="Number of dice")
    sides = models.IntegerField(help_text="Sides per die")
    per_extra_level = models.BooleanField(
        default=False,
        help_text="Rolled once per extra level: spell slot levels above the spell's level, "
        "or character levels 5, 11 and 17 reached for cantrips. Not used by weapons.",
    )

    def __str__(self):
        dice = f"{self.amount}d{self.sides}"
        return f"{dice} per extra level" if self.per_extra_level else dice


class ArmorDamage(models.Model):
    # "None" = the attack has no ArmorDamage linked.
    # "Both" = the attack has one of each linked (Attack.armor_damages is many-to-many).
    # Penetrante: the damage ignores `amount` AP, which stay unchanged. Triturante:
    # subtracts `amount` AP after the damage is dealt. With both, the damage is dealt
    # against the unchanged AP and the triturante AP are subtracted afterwards.
    class Type(models.TextChoices):
        PENETRATING = "penetrating", "Penetrante"
        SHREDDING = "shredding", "Triturante"

    type = models.CharField(max_length=20, choices=Type.choices)
    amount = models.IntegerField(help_text="Number of AP points affected")

    def __str__(self):
        return f"{self.get_type_display()} {self.amount}"


# ---------------------------------------------------------------------------
# Effects
# ---------------------------------------------------------------------------

class EffectQuerySet(models.QuerySet):
    def conditions(self):
        """The states a fighter can suffer (Agarrado, Quemado...): not the traits of weapons and armor
        (SANGRADO, PESADA...), which the house writes in capitals."""
        return (self.filter(itemeffect__isnull=True, armortype__isnull=True).exclude(name=Upper("name"))
                .distinct().order_by("name"))


class Effect(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    # Optional: conditions such as "Agarrado" have no duration of their own.
    duration = models.CharField(max_length=50, blank=True)

    objects = EffectQuerySet.as_manager()

    def __str__(self):
        return self.name


class ActiveEffect(models.Model):
    """A state on a character for some rounds; or, in a fight, on an enemy without a sheet (its combatant)."""
    character = models.ForeignKey("Character", on_delete=models.CASCADE, null=True, blank=True)
    combatant = models.ForeignKey("Combatant", on_delete=models.CASCADE, null=True, blank=True, related_name="effects",
                                  help_text="Only for an enemy without a sheet, in its fight")
    effect = models.ForeignKey("Effect", on_delete=models.CASCADE)
    remaining_turns = models.IntegerField()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(character__isnull=False, combatant__isnull=True)
                | models.Q(character__isnull=True, combatant__isnull=False),
                name="active_effect_on_character_or_combatant",
            ),
        ]

    def __str__(self):
        return f"{self.character or self.combatant} - {self.effect} ({self.remaining_turns} turns)"


# ---------------------------------------------------------------------------
# Items and equipment
# ---------------------------------------------------------------------------

class Item(models.Model):
    name = models.CharField(max_length=100)
    price = models.IntegerField(null=True, blank=True)
    description = models.TextField(blank=True)
    consumable = models.BooleanField(default=False)
    # Made by a master for one character (NPC creator, sheet editor): kept out of the players' lists.
    bespoke = models.BooleanField(default=False, help_text="Made by a master for a character: players do not see it in the catalog lists")

    # Item templates. A template is an item with no template: a catalog entry such
    # as "Espada larga" or "Oro". A copy is one physical piece made from a template,
    # with its own Weapon or Armor data (equipped, upgrades). Characters carry copies
    # of weapons and armor; generic items (gold, potions...) are carried directly.
    # A template cannot be deleted while it has copies.
    template = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="copies",
        limit_choices_to={"template__isnull": True},
        help_text="Only for copies: the item template this piece was made from",
    )

    # aplica: each effect with its own value, the X of the trait (SANGRADO 3)
    effects = models.ManyToManyField("Effect", through="ItemEffect")

    def __str__(self):
        return self.name

    @property
    def is_template(self):
        return self.template_id is None

    @property
    def is_equipment(self):
        """True for weapons and armor, which characters carry as individual copies."""
        return hasattr(self, "weapon") or hasattr(self, "armor")

    def clean(self):
        if self.template_id is not None:
            if self.template_id == self.pk:
                raise ValidationError({"template": "An item cannot be its own template."})
            if self.pk is not None and self.copies.exists():
                raise ValidationError({"template": "This item is a template with copies, so it cannot be a copy."})

    @transaction.atomic
    def make_copy(self, keep_equipped=False):
        """Create a new copy of this item, including its Weapon or Armor, and return it.

        The copy gets this item's data and points to the template: this item if it
        is a template, or this item's template if it is itself a copy. It starts
        unequipped, since no character carries it yet (game rule 5), unless
        `keep_equipped`, for a copy that is added to an inventory right away.
        """
        new = Item.objects.create(
            name=self.name,
            price=self.price,
            description=self.description,
            consumable=self.consumable,
            template=self.template or self,
        )
        ItemEffect.objects.bulk_create(
            ItemEffect(item=new, effect_id=row.effect_id, value=row.value)
            for row in self.itemeffect_set.all()
        )
        if hasattr(self, "weapon"):
            weapon = Weapon.objects.create(
                item=new, to_hit=self.weapon.to_hit, equipped=self.weapon.equipped and keep_equipped
            )
            weapon.attacks.set(self.weapon.attacks.all())
        if hasattr(self, "armor"):
            Armor.objects.create(
                item=new,
                armor_type=self.armor.armor_type,
                ap=self.armor.ap,
                magic_ap=self.armor.magic_ap,
                equipped=self.armor.equipped and keep_equipped,
            )
        return new


class ItemEffect(models.Model):
    item = models.ForeignKey("Item", on_delete=models.CASCADE)
    effect = models.ForeignKey("Effect", on_delete=models.CASCADE)
    value = models.IntegerField(
        null=True,
        blank=True,
        help_text="The trait's X (SANGRADO 3, EMPUJE 2…). Empty if the trait has no value",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["item", "effect"], name="unique_item_effect"),
        ]

    def __str__(self):
        value = "" if self.value is None else f" {self.value}"
        return f"{self.item} - {self.effect}{value}"


class Inventory(models.Model):
    character = models.ForeignKey("Character", on_delete=models.CASCADE)
    item = models.ForeignKey("Item", on_delete=models.CASCADE)
    quantity = models.IntegerField(default=1)

    class Meta:
        verbose_name_plural = "inventories"
        constraints = [
            models.UniqueConstraint(fields=["character", "item"], name="unique_inventory_item"),
        ]

    def __str__(self):
        return f"{self.character} - {self.item} x{self.quantity}"

    def clean(self):
        if self.item_id is None:
            return
        if self.item.is_template and self.item.is_equipment:
            raise ValidationError(
                {"item": "Weapons and armor are carried as copies: make a copy of this template and carry that."}
            )
        # Only one armor equipped per character (game rule 5).
        armor = getattr(self.item, "armor", None)
        if armor is not None and armor.equipped and self.character_id is not None:
            other = (
                Armor.objects.filter(equipped=True, item__inventory__character_id=self.character_id)
                .exclude(item_id=self.item_id)
                .first()
            )
            if other is not None:
                raise ValidationError(
                    {"item": f"This armor is equipped and {self.character} already has {other} equipped: only one armor can be equipped."}
                )


def validate_equipped(equipment):
    """Game rules 5 and 12 for a Weapon or Armor: only copies can be equipped,
    and only while a character carries them."""
    if not equipment.equipped or equipment.item_id is None:
        return
    if equipment.item.is_template:
        raise ValidationError({"equipped": "A template cannot be equipped: equip one of its copies."})
    if not Inventory.objects.filter(item_id=equipment.item_id).exists():
        raise ValidationError({"equipped": "No character carries this item: add it to an inventory before equipping it."})


# Optional integer, an ability ("str" or "dex") and an optional multiplier:
# "dex", "2 + str", "-3 + dex x2" (= -3 + 2 × the Dexterity modifier).
TO_HIT_FORMULA = rf"^\s*([+-]?\d+\s*\+\s*)?({'|'.join(Stat.values)})(\s*x\s*\d+)?\s*$"
validate_to_hit = RegexValidator(
    TO_HIT_FORMULA,
    'Write an optional integer, "str" or "dex", and an optional multiplier: "dex", "2 + str", "-3 + dex x2".',
)


def parse_to_hit(formula):
    """Split a to-hit formula into (integer, ability, multiplier): "-3 + dex x2" -> (-3, "dex", 2)."""
    match = re.match(TO_HIT_FORMULA, formula)
    if match is None:
        raise ValueError(f"Not a to-hit formula: {formula!r}")
    integer, stat, multiplier = match.groups()
    return (
        int(re.search(r"[+-]?\d+", integer).group()) if integer else 0,
        stat,
        int(re.search(r"\d+", multiplier).group()) if multiplier else 1,
    )


class Weapon(models.Model):
    # es un
    item = models.OneToOneField("Item", on_delete=models.CASCADE)
    to_hit = models.CharField(
        max_length=50,
        validators=[validate_to_hit],
        help_text='Formula: optional integer + "str" or "dex" + optional multiplier, '
        'e.g. "dex", "2 + str", "-3 + dex x2" (= -3 + 2 × Dexterity modifier)',
    )
    equipped = models.BooleanField(default=False)

    # realiza
    attacks = models.ManyToManyField("Attack", blank=True)

    def __str__(self):
        return str(self.item)

    def to_hit_bonus(self, character):
        """`to_hit` with numbers for `character`: integer + ability modifier × multiplier (game rule 14)."""
        integer, stat, multiplier = parse_to_hit(self.to_hit)
        return integer + multiplier * character.modifier(stat)

    def clean(self):
        validate_equipped(self)


class Armor(models.Model):
    # es un, es de tipo
    item = models.OneToOneField("Item", on_delete=models.CASCADE)
    armor_type = models.ForeignKey("ArmorType", on_delete=models.PROTECT)
    ap = models.IntegerField(default=0, help_text="This piece's own AP, including upgrades")
    magic_ap = models.IntegerField(default=0, help_text="This piece's own magic AP")
    equipped = models.BooleanField(default=False)

    def __str__(self):
        return str(self.item)

    def clean(self):
        validate_equipped(self)
        # Only one armor equipped per character (game rule 5).
        if self.equipped and self.item_id is not None:
            for character in Character.objects.filter(inventory__item_id=self.item_id):
                other = (
                    Armor.objects.filter(equipped=True, item__inventory__character=character)
                    .exclude(item_id=self.item_id)
                    .first()
                )
                if other is not None:
                    raise ValidationError(
                        {"equipped": f"{character} already has {other} equipped: only one armor can be equipped."}
                    )


class ArmorType(models.Model):
    type = models.CharField(max_length=50)
    ap = models.IntegerField(default=0, help_text="Added to the AP of every armor of this type")
    magic_ap = models.IntegerField(default=0, help_text="Added to the magic AP of every armor of this type")

    # aplica
    effects = models.ManyToManyField("Effect", blank=True)

    def __str__(self):
        return self.type


# ---------------------------------------------------------------------------
# Deleting NPC copies
# ---------------------------------------------------------------------------
# When an NPC copy is deleted, the item copies in its inventory (its individual
# weapons and armor) are deleted too. Item templates carried directly (gold,
# potions...) are shared with other characters, so they are kept. Player
# characters and NPC templates never delete their items.
# Signals are used instead of Character.delete() because copies are also deleted
# by the template's CASCADE and by the admin's bulk delete, which skip delete().

@receiver(pre_delete, sender=Character)
def remember_npc_copy_items(sender, instance, **kwargs):
    if instance.is_npc and instance.template_id is not None:
        instance._items_to_delete = list(
            Item.objects.filter(inventory__character=instance, template__isnull=False)
            .values_list("pk", flat=True)
        )


@receiver(post_delete, sender=Character)
def delete_npc_copy_items(sender, instance, **kwargs):
    item_ids = getattr(instance, "_items_to_delete", None)
    if item_ids:
        # The copy's inventory rows are already gone; skip any item another character holds.
        Item.objects.filter(pk__in=item_ids, inventory__isnull=True).delete()


@receiver(post_delete, sender=Inventory)
def unequip_dropped_item(sender, instance, **kwargs):
    """An item no character carries any more cannot stay equipped (game rule 5)."""
    if not Inventory.objects.filter(item_id=instance.item_id).exists():
        Weapon.objects.filter(item_id=instance.item_id, equipped=True).update(equipped=False)
        Armor.objects.filter(item_id=instance.item_id, equipped=True).update(equipped=False)


# ---------------------------------------------------------------------------
# Unique abilities (game rule 6)
# ---------------------------------------------------------------------------
# A unique ability can belong to only one character per campaign. A character's
# abilities are those held directly plus those granted by its race, subrace,
# class and subclass once its level reaches level_required (rule 7). Characters
# without a campaign never clash with anyone.
#
# Checked in the character's admin form and Character.clean(), in Ability.clean()
# when an ability is made unique, in the clean() of the four ability link tables,
# and by the signals below when campaigns or abilities are added in code.

def granted_ability_ids(*sources, level=None):
    """IDs of the abilities granted by these races, subraces, classes or subclasses
    (None is skipped), up to `level` if given (game rule 7)."""
    links = {
        Race: (RaceAbility, "race"),
        Subrace: (SubraceAbility, "subrace"),
        CharacterClass: (ClassAbility, "char_class"),
        Subclass: (SubclassAbility, "subclass"),
    }
    ids = set()
    for source in sources:
        if source is None:
            continue
        link_model, field = links[type(source)]
        rows = link_model.objects.filter(**{field: source})
        if level is not None:
            rows = rows.filter(level_required__lte=level)
        ids.update(rows.values_list("ability_id", flat=True))
    return ids


def ability_holders(ability):
    """Characters who have `ability`, held directly or granted at their level."""
    candidates = Character.objects.filter(
        models.Q(abilities=ability)
        | models.Q(race__abilities=ability)
        | models.Q(subrace__abilities=ability)
        | models.Q(char_class__abilities=ability)
        | models.Q(subclass__abilities=ability)
    ).distinct()
    ids = [character.pk for character in candidates if ability.pk in character.all_ability_ids()]
    return Character.objects.filter(pk__in=ids)


def join_names(objects):
    names = sorted(str(obj) for obj in objects)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def unique_ability_conflicts(character, campaigns, ability_ids):
    """Messages for each unique ability among `ability_ids` that a character other
    than `character` already has in one of `campaigns`."""
    campaign_ids = {campaign.pk for campaign in campaigns}
    if not campaign_ids:
        return []
    messages = []
    for ability in Ability.objects.filter(pk__in=ability_ids, unique=True):
        others = ability_holders(ability).filter(campaigns__in=campaign_ids)
        if character.pk is not None:
            others = others.exclude(pk=character.pk)
        for other in others:
            for campaign in other.campaigns.filter(pk__in=campaign_ids):
                messages.append(f"{other} already has the unique ability {ability} in {campaign}.")
    return messages


def campaign_clashes(ability, characters, campaigns=None):
    """Messages for each campaign where more than one of `characters` would have
    the unique `ability`. `campaigns` limits the campaigns looked at."""
    by_campaign = {}
    for character in characters:
        for campaign in character.campaigns.all() if campaigns is None else campaigns:
            by_campaign.setdefault(campaign, set()).add(character)
    return [
        f"{join_names(members)} would share the unique ability {ability} in {campaign}."
        for campaign, members in by_campaign.items()
        if len(members) > 1
    ]


def validate_granted_ability(ability_id, characters):
    """Granting a unique ability to a race, subrace, class or subclass gives it to
    all `characters` of it: no campaign can end up with two characters having it."""
    if ability_id is None:
        return
    ability = Ability.objects.get(pk=ability_id)
    if ability.unique:
        clashes = campaign_clashes(ability, set(ability_holders(ability)) | set(characters))
        if clashes:
            raise ValidationError({"ability": clashes})


@receiver(m2m_changed, sender=Character.campaigns.through)
def check_unique_abilities_on_join(sender, instance, action, reverse, pk_set, **kwargs):
    """A character cannot join a campaign where another character has one of its unique abilities."""
    if action != "pre_add" or getattr(instance, "_unique_abilities_checked", False):
        return
    if not reverse:  # character.campaigns.add(...)
        messages = unique_ability_conflicts(
            instance, Campaign.objects.filter(pk__in=pk_set), instance.all_ability_ids()
        )
    else:  # campaign.characters.add(...): also check the characters joining together
        joining = list(Character.objects.filter(pk__in=pk_set))
        messages = []
        held = {}
        for character in joining:
            ability_ids = character.all_ability_ids()
            messages += unique_ability_conflicts(character, [instance], ability_ids)
            for ability in Ability.objects.filter(pk__in=ability_ids, unique=True):
                held.setdefault(ability, []).append(character)
        for ability, characters in held.items():
            messages += campaign_clashes(ability, characters, campaigns=[instance])
    if messages:
        raise ValidationError(messages)


@receiver(m2m_changed, sender=Character.abilities.through)
def check_unique_abilities_on_gain(sender, instance, action, reverse, pk_set, **kwargs):
    """A character cannot get a unique ability another character has in one of its campaigns."""
    if action != "pre_add" or getattr(instance, "_unique_abilities_checked", False):
        return
    if not reverse:  # character.abilities.add(...)
        messages = unique_ability_conflicts(instance, instance.campaigns.all(), pk_set)
    elif instance.unique:  # ability.character_set.add(...): also check the characters getting it together
        gaining = list(Character.objects.filter(pk__in=pk_set))
        messages = []
        for character in gaining:
            messages += unique_ability_conflicts(character, character.campaigns.all(), [instance.pk])
        messages += campaign_clashes(instance, gaining)
    else:
        return
    if messages:
        raise ValidationError(messages)


# ---------------------------------------------------------------------------
# Campaign models (diary, world, quests, board, scenes, maps, turns)
# ---------------------------------------------------------------------------
# At the end because they refer to the models above.
from .campaign_models import (  # noqa: E402,F401
    BoardCard, BoardLink, Combatant, Encounter, Map, MapLocation, Page, PartyMove,
    Post, Quest, QuestObjective, Rumour, RumourCategory, Scene, Session, TimelineEvent, WeatherRegion,
)
