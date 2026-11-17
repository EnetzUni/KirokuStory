from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("CampaignManager", "0016_remove_character_abilities"),
    ]

    operations = [
        migrations.AddField(
            model_name="character",
            name="abilities",
            field=models.ManyToManyField(blank=True, through="CampaignManager.CharacterAbility", to="CampaignManager.ability"),
        ),
    ]
