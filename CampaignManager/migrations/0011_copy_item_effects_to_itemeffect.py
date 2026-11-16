"""Copy the rows of the old Item.effects link table into ItemEffect.

Item.effects becomes a many-to-many through ItemEffect. Django cannot add
`through` to an existing ManyToManyField, so the field is removed (0012) and
created again (0013). This migration runs before that and keeps the existing
links; they get an empty value. On a database without item effects it does nothing.
"""
from django.db import migrations


def copy_to_itemeffect(apps, schema_editor):
    Item = apps.get_model("CampaignManager", "Item")
    ItemEffect = apps.get_model("CampaignManager", "ItemEffect")
    OldLink = Item._meta.get_field("effects").remote_field.through
    ItemEffect.objects.bulk_create(
        ItemEffect(item_id=row.item_id, effect_id=row.effect_id)
        for row in OldLink.objects.all()
    )


def copy_back(apps, schema_editor):
    # Reverse: the old link table has no value column, so values are lost.
    Item = apps.get_model("CampaignManager", "Item")
    ItemEffect = apps.get_model("CampaignManager", "ItemEffect")
    OldLink = Item._meta.get_field("effects").remote_field.through
    OldLink.objects.bulk_create(
        OldLink(item_id=row.item_id, effect_id=row.effect_id)
        for row in ItemEffect.objects.all()
    )


class Migration(migrations.Migration):

    dependencies = [
        ("CampaignManager", "0010_attack_stat_bonus_itemeffect_and_more"),
    ]

    operations = [
        migrations.RunPython(copy_to_itemeffect, copy_back),
    ]
