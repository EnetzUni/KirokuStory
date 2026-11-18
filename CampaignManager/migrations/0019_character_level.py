"""The level is stored: the master gives XP and the player levels up from the
sheet. Existing characters keep the level their XP gave them."""
import django.core.validators
from django.db import migrations, models

# rules.XP_THRESHOLDS when the level was calculated, copied so this migration
# does not change if the table does.
XP_THRESHOLDS = [
    0, 300, 900, 2700, 6500, 14000, 23000, 34000, 48000, 64000,
    85000, 100000, 120000, 140000, 165000, 195000, 225000, 265000, 305000, 355000,
]


def level_from_xp(apps, schema_editor):
    Character = apps.get_model("CampaignManager", "Character")
    for character in Character.objects.all():
        character.level = sum(1 for threshold in XP_THRESHOLDS if character.xp >= threshold) or 1
        character.save(update_fields=["level"])


class Migration(migrations.Migration):

    dependencies = [
        ("CampaignManager", "0018_admin_groups"),
    ]

    operations = [
        migrations.AddField(
            model_name="character",
            name="level",
            field=models.PositiveSmallIntegerField(default=1, help_text="Level reached. It can be behind the XP until the player levels up", validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(20)]),
        ),
        migrations.RunPython(level_from_xp, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="character",
            constraint=models.CheckConstraint(condition=models.Q(("level__gte", 1), ("level__lte", 20)), name="level_from_1_to_max"),
        ),
    ]
