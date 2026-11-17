"""Two admin groups with different permissions (E4a option 4):

- «Reglas» adds, changes, deletes and views the rules catalog.
- «Campañas» does the same with the campaign models and only views the catalog
  (to choose races, items... in its forms).
"""
from django.contrib.auth.management import create_permissions
from django.db import migrations

RULES = [
    "race", "subrace", "characterclass", "subclass", "ability", "raceability", "subraceability",
    "classability", "subclassability", "spell", "component", "attack", "diceroll", "armordamage",
    "effect", "item", "itemeffect", "weapon", "armor", "armortype",
]
CAMPAIGNS = [
    "campaign", "character", "characterability", "inventory", "activeeffect", "session", "timelineevent",
    "page", "quest", "questobjective", "boardcard", "boardlink", "scene", "post", "map", "maplocation",
    "partymove", "encounter", "combatant",
]
ALL = ("add", "change", "delete", "view")


def make_groups(apps, schema_editor):
    # Permissions are normally created after all migrations: create them now.
    for app_config in apps.get_app_configs():
        app_config.models_module = True
        create_permissions(app_config, apps=apps, verbosity=0)
        app_config.models_module = None
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    def perms(models, actions):
        codenames = [f"{action}_{model}" for model in models for action in actions]
        found = Permission.objects.filter(content_type__app_label="CampaignManager", codename__in=codenames)
        missing = set(codenames) - set(found.values_list("codename", flat=True))
        if missing:
            raise RuntimeError(f"Missing permissions: {sorted(missing)}")
        return found

    reglas, _ = Group.objects.get_or_create(name="Reglas")
    reglas.permissions.set(perms(RULES, ALL))
    campanas, _ = Group.objects.get_or_create(name="Campañas")
    campanas.permissions.set(list(perms(CAMPAIGNS, ALL)) + list(perms(RULES, ("view",))))


def remove_groups(apps, schema_editor):
    apps.get_model("auth", "Group").objects.filter(name__in=["Reglas", "Campañas"]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("CampaignManager", "0017_character_abilities_through"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [migrations.RunPython(make_groups, remove_groups)]
