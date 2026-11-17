from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("CampaignManager", "0015_copy_character_abilities"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="character",
            name="abilities",
        ),
    ]
