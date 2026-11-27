"""Colours and pictures to tell things apart: each stretch of the party's route gets its own colour
(copper, as before, until the master picks another), adventures and missions an optional colour and
header image, and each combatant the side of the table its card goes to (empty: automatic)."""

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('CampaignManager', '0032_arcane_warrior'),
    ]

    operations = [
        migrations.AddField(
            model_name='combatant',
            name='side',
            field=models.CharField(blank=True, choices=[('grupo', 'Con el grupo'), ('enemigos', 'Contra el grupo')], help_text='Empty: player characters with the group, the rest against it', max_length=10),
        ),
        migrations.AddField(
            model_name='partymove',
            name='color',
            field=models.CharField(default='#BC6C25', max_length=7, validators=[django.core.validators.RegexValidator('^#[0-9A-Fa-f]{6}$', 'Write a colour as #RRGGBB.')]),
        ),
        migrations.AddField(
            model_name='quest',
            name='color',
            field=models.CharField(blank=True, help_text="Empty: no colour, or for a mission its adventure's", max_length=7, validators=[django.core.validators.RegexValidator('^#[0-9A-Fa-f]{6}$', 'Write a colour as #RRGGBB.')]),
        ),
        migrations.AddField(
            model_name='quest',
            name='cover',
            field=models.ImageField(blank=True, help_text="A small header image, like the campaign's cover", upload_to='aventuras/'),
        ),
    ]
