"""Copy the rows of the old Character.abilities link table into CharacterAbility.

Character.abilities becomes a many-to-many through CharacterAbility, which keeps
each ability's uses. Django cannot add `through` to an existing ManyToManyField,
so the field is removed (0016) and created again (0017). This migration runs
before that and keeps the existing links, with no uses or cooldown.
"""
from django.db import migrations


def copy_to_characterability(apps, schema_editor):
    Character = apps.get_model("CampaignManager", "Character")
    CharacterAbility = apps.get_model("CampaignManager", "CharacterAbility")
    OldLink = Character._meta.get_field("abilities").remote_field.through
    CharacterAbility.objects.bulk_create(
        CharacterAbility(character_id=row.character_id, ability_id=row.ability_id)
        for row in OldLink.objects.all()
    )


def copy_back(apps, schema_editor):
    # Reverse: the old link table has no uses, so they are lost.
    Character = apps.get_model("CampaignManager", "Character")
    CharacterAbility = apps.get_model("CampaignManager", "CharacterAbility")
    OldLink = Character._meta.get_field("abilities").remote_field.through
    OldLink.objects.bulk_create(
        OldLink(character_id=row.character_id, ability_id=row.ability_id)
        for row in CharacterAbility.objects.all()
    )


class Migration(migrations.Migration):

    dependencies = [
        ("CampaignManager", "0014_campaign_models_and_sheet_fields"),
    ]

    operations = [
        migrations.RunPython(copy_to_characterability, copy_back),
    ]
