"""Unique skills: proposed by the player, approved or rejected by a master.
The «Campañas» admin group (migration 0018) can edit them."""
import django.db.models.deletion
from django.contrib.auth.management import create_permissions
from django.db import migrations, models


def campaigns_group_edits_unique_skills(apps, schema_editor):
    # Permissions are normally created after all migrations: create them now.
    for app_config in apps.get_app_configs():
        app_config.models_module = True
        create_permissions(app_config, apps=apps, verbosity=0)
        app_config.models_module = None
    group = apps.get_model("auth", "Group").objects.filter(name="Campañas").first()
    if group is not None:
        group.permissions.add(*apps.get_model("auth", "Permission").objects.filter(
            content_type__app_label="CampaignManager", content_type__model="uniqueskill"))


class Migration(migrations.Migration):

    dependencies = [
        ('CampaignManager', '0020_magic_by_class_and_feats'),
        ('auth', '0012_alter_user_first_name_max_length'),
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [
        migrations.CreateModel(
            name='UniqueSkill',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('description', models.TextField()),
                ('status', models.CharField(choices=[('pendiente', 'Pendiente'), ('aprobada', 'Aprobada'), ('rechazada', 'Rechazada')], default='pendiente', max_length=10)),
                ('master_note', models.TextField(blank=True, help_text="The master's note to the player: why it was rejected or changed")),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('ability', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='unique_skill', to='CampaignManager.ability')),
                ('character', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='unique_skill', to='CampaignManager.character')),
            ],
            options={
                'constraints': [models.CheckConstraint(condition=models.Q(models.Q(('ability__isnull', False), ('status', 'aprobada')), models.Q(models.Q(('status', 'aprobada'), _negated=True), ('ability__isnull', True)), _connector='OR'), name='unique_skill_ability_when_approved')],
            },
        ),
        migrations.RunPython(campaigns_group_edits_unique_skills, migrations.RunPython.noop),
    ]
