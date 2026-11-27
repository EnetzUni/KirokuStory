"""World pages and character sheets point to each other: a page lists the sheets it is about (its
NPC, the troops of a faction or a place). And an NPC of the bestiary can have its own speed, which
until now always came from its race (empty: still the race's)."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('CampaignManager', '0033_route_colours_quest_covers_combat_sides'),
    ]

    operations = [
        migrations.AddField(
            model_name='character',
            name='own_speed',
            field=models.PositiveSmallIntegerField(blank=True, help_text="In meters. Only when it is not the race's (an NPC of the bestiary); empty: the race's", null=True),
        ),
        migrations.AddField(
            model_name='page',
            name='sheets',
            field=models.ManyToManyField(blank=True, help_text='Sheets of this page: its NPC, the troops of a faction or a place, a character from here', related_name='world_pages', to='CampaignManager.character'),
        ),
    ]
