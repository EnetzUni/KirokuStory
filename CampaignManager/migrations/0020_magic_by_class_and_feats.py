"""Magic points come from the class and the level, plus the master's adjustment;
abilities can be feats.

Existing data: the SRD classes get their kind of magic. A maximum a master had
set is kept as an adjustment over what the class gives. A character with no
maximum gets the class's magic points, full."""
import math

import django.core.validators
from django.db import migrations, models

# rules.py when this migration was written, copied so it does not change if they do.
MAGIC_BY_CLASS = {
    "Bardo": "completa", "Clérigo": "completa", "Druida": "completa", "Hechicero": "completa", "Mago": "completa",
    "Paladín": "media", "Explorador": "media", "Brujo": "pacto",
}
FULL = [2, 3, 8, 10, 16, 19, 23, 27, 36, 41, 47, 47, 54, 54, 62, 62, 71, 76, 82, 89]
PACT = [1, 2, 4, 4, 6, 6, 8, 8, 10, 10, 15, 15, 15, 15, 15, 15, 20, 20, 20, 20]


def class_magic_points(magic, level):
    if magic == "completa":
        return FULL[level - 1]
    if magic == "media":
        return FULL[math.ceil(level / 2) - 1]
    if magic == "pacto":
        return PACT[level - 1]
    return 0


def magic_from_class(apps, schema_editor):
    CharacterClass = apps.get_model("CampaignManager", "CharacterClass")
    Character = apps.get_model("CampaignManager", "Character")
    for name, magic in MAGIC_BY_CLASS.items():
        CharacterClass.objects.filter(name=name).update(magic=magic)
    for character in Character.objects.select_related("char_class"):
        from_class = class_magic_points(character.char_class.magic, character.level)
        if character.magic_points_max is None:
            character.magic_points_bonus = 0
            character.magic_points = from_class
        else:
            character.magic_points_bonus = character.magic_points_max - from_class
            character.magic_points = min(character.magic_points, character.magic_points_max)
        character.save(update_fields=["magic_points", "magic_points_bonus"])


class Migration(migrations.Migration):

    dependencies = [
        ("CampaignManager", "0019_character_level"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="character",
            name="magic_points_up_to_max",
        ),
        migrations.AddField(
            model_name="ability",
            name="feat",
            field=models.BooleanField(default=False, help_text="A feat: chosen when levelling up at levels 4, 8, 12, 16 and 20"),
        ),
        migrations.AddField(
            model_name="ability",
            name="feat_min_level",
            field=models.PositiveSmallIntegerField(default=4, help_text="Only for feats: the lowest level at which it can be chosen (e.g. 19 for epic boons, so from level 20)", validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(20)]),
        ),
        migrations.AddField(
            model_name="character",
            name="magic_points_bonus",
            field=models.IntegerField(default=0, help_text="The master's adjustment to the magic points the class gives at this level (can be negative)"),
        ),
        migrations.AddField(
            model_name="characterclass",
            name="magic",
            field=models.CharField(choices=[("completa", "Lanzador completo"), ("media", "Medio lanzador"), ("pacto", "Magia de pacto"), ("ninguna", "Sin magia")], default="ninguna", help_text="Gives the character's magic points at each level", max_length=10),
        ),
        migrations.RunPython(magic_from_class, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="character",
            name="magic_points_max",
        ),
    ]
