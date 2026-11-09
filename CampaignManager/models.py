from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models.signals import post_delete, pre_delete
from django.dispatch import receiver


# ---------------------------------------------------------------------------
# Users and campaigns
# ---------------------------------------------------------------------------

class User(AbstractUser):
    # username, email and a hashed password are inherited from AbstractUser.
    pass


class Campaign(models.Model):
    class Status(models.TextChoices):
        ACTIVA = "activa", "Activa"
        PAUSADA = "pausada", "Pausada"
        TERMINADA = "terminada", "Terminada"

    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    start_date = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVA)

    # dirige / juega en
    masters = models.ManyToManyField(User, related_name="dm_campaigns")
    players = models.ManyToManyField(User, related_name="player_campaigns", blank=True)

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Characters
# ---------------------------------------------------------------------------

class Character(models.Model):
    name = models.CharField(max_length=100)
    xp = models.IntegerField(default=0)
    hp = models.IntegerField(help_text="Maximum hit points")
    temp_hp = models.IntegerField(help_text="Current hit points")
    ac = models.IntegerField(help_text="Armor class")
    temp_ap = models.IntegerField(default=0, help_text="Current armor points")
    inspiration = models.IntegerField(default=0)
    strength = models.IntegerField()
    dexterity = models.IntegerField()
    constitution = models.IntegerField()
    intelligence = models.IntegerField()
    wisdom = models.IntegerField()
    charisma = models.IntegerField()

    # controla (optional), define, pertenece a
    player = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    char_class = models.ForeignKey("CharacterClass", on_delete=models.PROTECT)
    race = models.ForeignKey("Race", on_delete=models.PROTECT)

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

    # posee, conoce, lleva, sufre
    abilities = models.ManyToManyField("Ability", blank=True)
    spells = models.ManyToManyField("Spell", blank=True)
    items = models.ManyToManyField("Item", through="Inventory")
    effects = models.ManyToManyField("Effect", through="ActiveEffect")

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(template__isnull=True) | models.Q(is_npc=True),
                name="template_only_for_npcs",
                violation_error_message="Only NPCs can have a template.",
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def is_npc_template(self):
        return self.is_npc and self.template_id is None

    def clean(self):
        if self.template_id is not None:
            if self.template_id == self.pk:
                raise ValidationError({"template": "A character cannot be its own template."})
            if self.pk is not None and self.copies.exists():
                raise ValidationError({"template": "This NPC is a template with copies, so it cannot be a copy."})

    @transaction.atomic
    def make_copy(self, name=None, campaigns=()):
        """Create a new NPC from this template and return it.

        Copies the stats, abilities, spells, inventory and active effects.
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

        copy.abilities.set(self.abilities.all())
        copy.spells.set(self.spells.all())
        copy.campaigns.set(campaigns)

        for row in self.inventory_set.select_related("item"):
            item = row.item
            if not item.is_template:
                item = item.make_copy()
            Inventory.objects.create(character=copy, item=item, quantity=row.quantity)

        for row in self.activeeffect_set.all():
            ActiveEffect.objects.create(
                character=copy, effect=row.effect, remaining_turns=row.remaining_turns
            )

        return copy


class Race(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    speed = models.IntegerField(help_text="In meters")

    # otorga
    abilities = models.ManyToManyField("Ability", through="RaceAbility")

    def __str__(self):
        return self.name


class CharacterClass(models.Model):
    name = models.CharField(max_length=100)
    primary_ability = models.CharField(max_length=50)
    description = models.TextField(blank=True)

    # otorga, puede lanzar
    abilities = models.ManyToManyField("Ability", through="ClassAbility")
    spells = models.ManyToManyField("Spell", blank=True)

    class Meta:
        verbose_name_plural = "character classes"

    def __str__(self):
        return self.name


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

    # aplica
    effects = models.ManyToManyField("Effect", blank=True)

    class Meta:
        verbose_name_plural = "abilities"

    def __str__(self):
        return self.name

    def clean(self):
        # An NPC with a unique ability cannot have copies.
        if self.unique and self.pk is not None:
            if Character.objects.filter(abilities=self, copies__isnull=False).exists():
                raise ValidationError(
                    {"unique": "An NPC template with copies has this ability, so it cannot be unique."}
                )


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


# ---------------------------------------------------------------------------
# Spells and combat
# ---------------------------------------------------------------------------

class Spell(models.Model):
    name = models.CharField(max_length=100)
    level = models.IntegerField(default=0, help_text="0 = cantrip (truco)")
    casting_time = models.CharField(max_length=50)
    range = models.IntegerField(help_text="In meters")
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


class Attack(models.Model):
    name = models.CharField(max_length=100)
    extra = models.IntegerField(default=0, help_text="Fixed damage that does not depend on dice")
    range = models.IntegerField(help_text="In meters")
    area = models.IntegerField(default=0, help_text="In meters")
    cooldown = models.IntegerField(default=0, help_text="In rounds")

    # tira, inflige, aplica
    dice = models.ManyToManyField("DiceRoll", blank=True)
    armor_damages = models.ManyToManyField("ArmorDamage", blank=True)
    effects = models.ManyToManyField("Effect", blank=True)

    def __str__(self):
        return self.name


class DiceRoll(models.Model):
    amount = models.IntegerField(help_text="Number of dice")
    sides = models.IntegerField(help_text="Sides per die")

    def __str__(self):
        return f"{self.amount}d{self.sides}"


class ArmorDamage(models.Model):
    # "None" = the attack has no ArmorDamage linked.
    # "Both" = the attack has one of each linked (Attack.armor_damages is many-to-many).
    class Type(models.TextChoices):
        PENETRATING = "penetrating", "Penetrating"
        SHREDDING = "shredding", "Shredding"

    type = models.CharField(max_length=20, choices=Type.choices)
    amount = models.IntegerField(help_text="Number of AP points affected")

    def __str__(self):
        return f"{self.get_type_display()} {self.amount}"


# ---------------------------------------------------------------------------
# Effects
# ---------------------------------------------------------------------------

class Effect(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    duration = models.CharField(max_length=50)

    def __str__(self):
        return self.name


class ActiveEffect(models.Model):
    character = models.ForeignKey("Character", on_delete=models.CASCADE)
    effect = models.ForeignKey("Effect", on_delete=models.CASCADE)
    remaining_turns = models.IntegerField()

    def __str__(self):
        return f"{self.character} - {self.effect} ({self.remaining_turns} turns)"


# ---------------------------------------------------------------------------
# Items and equipment
# ---------------------------------------------------------------------------

class Item(models.Model):
    name = models.CharField(max_length=100)
    price = models.IntegerField(default=0)
    description = models.TextField(blank=True)
    consumable = models.BooleanField(default=False)

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

    # aplica
    effects = models.ManyToManyField("Effect", blank=True)

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
    def make_copy(self):
        """Create a new copy of this item, including its Weapon or Armor, and return it.

        The copy gets this item's data and points to the template: this item if it
        is a template, or this item's template if it is itself a copy.
        """
        new = Item.objects.create(
            name=self.name,
            price=self.price,
            description=self.description,
            consumable=self.consumable,
            template=self.template or self,
        )
        new.effects.set(self.effects.all())
        if hasattr(self, "weapon"):
            weapon = Weapon.objects.create(
                item=new, to_hit=self.weapon.to_hit, equipped=self.weapon.equipped
            )
            weapon.attacks.set(self.weapon.attacks.all())
        if hasattr(self, "armor"):
            Armor.objects.create(
                item=new,
                armor_type=self.armor.armor_type,
                ap=self.armor.ap,
                magic_ap=self.armor.magic_ap,
                equipped=self.armor.equipped,
            )
        return new


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
        if self.item_id is not None and self.item.is_template and self.item.is_equipment:
            raise ValidationError(
                {"item": "Weapons and armor are carried as copies: make a copy of this template and carry that."}
            )


def validate_not_equipped_template(equipment):
    """A template is a catalog entry, so only its copies can be equipped."""
    if equipment.equipped and equipment.item_id is not None and equipment.item.is_template:
        raise ValidationError({"equipped": "A template cannot be equipped: equip one of its copies."})


class Weapon(models.Model):
    # es un
    item = models.OneToOneField("Item", on_delete=models.CASCADE)
    to_hit = models.CharField(max_length=50, help_text="A number or a formula")
    equipped = models.BooleanField(default=False)

    # realiza
    attacks = models.ManyToManyField("Attack", blank=True)

    def __str__(self):
        return str(self.item)

    def clean(self):
        validate_not_equipped_template(self)


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
        validate_not_equipped_template(self)


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
