"""Rumours with their categories, the map's weather regions and the campaign's time zone.
Existing campaigns get the default rumour categories; the «Campañas» admin group
(migration 0018) can edit the three new models."""
import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.contrib.auth.management import create_permissions
from django.db import migrations, models

import CampaignManager.models

# campaign_models.DEFAULT_RUMOUR_CATEGORIES when this was written.
DEFAULT_CATEGORIES = [("Rumor", "#6B6B5E"), ("Comunicado oficial", "#9A6B00"), ("Noticia", "#1F4E89"), ("Leyenda", "#5B3E96")]


def categories_and_permissions(apps, schema_editor):
    Campaign = apps.get_model("CampaignManager", "Campaign")
    RumourCategory = apps.get_model("CampaignManager", "RumourCategory")
    for campaign in Campaign.objects.all():
        for order, (name, color) in enumerate(DEFAULT_CATEGORIES):
            RumourCategory.objects.get_or_create(campaign=campaign, name=name, defaults={"color": color, "order": order})
    # Permissions are normally created after all migrations: create them now.
    for app_config in apps.get_app_configs():
        app_config.models_module = True
        create_permissions(app_config, apps=apps, verbosity=0)
        app_config.models_module = None
    group = apps.get_model("auth", "Group").objects.filter(name="Campañas").first()
    if group is not None:
        group.permissions.add(*apps.get_model("auth", "Permission").objects.filter(
            content_type__app_label="CampaignManager", content_type__model__in=["rumourcategory", "rumour", "weatherregion"]))


class Migration(migrations.Migration):

    dependencies = [
        ('CampaignManager', '0029_bespoke_and_missions'),
        ('auth', '0012_alter_user_first_name_max_length'),
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [
        migrations.AddField(
            model_name='campaign',
            name='time_zone',
            field=models.CharField(default='Europe/Madrid', help_text='IANA name, e.g. Europe/Madrid', max_length=64, validators=[CampaignManager.models.validate_time_zone]),
        ),
        migrations.CreateModel(
            name='RumourCategory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=60)),
                ('color', models.CharField(default='#6B6B5E', max_length=7, validators=[django.core.validators.RegexValidator('^#[0-9A-Fa-f]{6}$', 'Write a colour as #RRGGBB.')])),
                ('order', models.PositiveIntegerField(default=0)),
                ('campaign', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='rumour_categories', to='CampaignManager.campaign')),
            ],
            options={
                'verbose_name_plural': 'rumour categories',
                'ordering': ['campaign', 'order', 'name'],
            },
        ),
        migrations.CreateModel(
            name='Rumour',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('text', models.TextField(help_text='[[Name]] links to a world page')),
                ('source', models.CharField(blank=True, help_text='Who said it, if it is not a page of the world', max_length=150)),
                ('truth', models.CharField(choices=[('sin_decidir', 'Sin decidir'), ('verdad', 'Verdad'), ('medias', 'Verdad a medias'), ('mentira', 'Mentira')], default='sin_decidir', help_text='Only masters see it', max_length=12)),
                ('published', models.BooleanField(default=False, help_text='Players see it')),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('campaign', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='rumours', to='CampaignManager.campaign')),
                ('place', models.ForeignKey(blank=True, help_text='Where the party heard it: a place of the world', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='rumours_heard', to='CampaignManager.page')),
                ('session', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='rumours', to='CampaignManager.session')),
                ('source_page', models.ForeignKey(blank=True, help_text='Who said it: an NPC or faction of the world', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='rumours_told', to='CampaignManager.page')),
                ('category', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='rumours', to='CampaignManager.rumourcategory')),
            ],
            options={
                'ordering': ['campaign', '-created_at', '-id'],
            },
        ),
        migrations.CreateModel(
            name='WeatherRegion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('points', models.JSONField(blank=True, default=list)),
                ('place_name', models.CharField(help_text='The real place, e.g. «Bilbao, País Vasco, España»', max_length=200)),
                ('latitude', models.FloatField(validators=[django.core.validators.MinValueValidator(-90), django.core.validators.MaxValueValidator(90)])),
                ('longitude', models.FloatField(validators=[django.core.validators.MinValueValidator(-180), django.core.validators.MaxValueValidator(180)])),
                ('weather', models.JSONField(blank=True, help_text='Last weather fetched (weather.py)', null=True)),
                ('weather_at', models.DateTimeField(blank=True, help_text='When it was fetched', null=True)),
                ('location', models.ForeignKey(blank=True, help_text="The region around this location's pin", null=True, on_delete=django.db.models.deletion.CASCADE, related_name='weather_regions', to='CampaignManager.maplocation')),
                ('map', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='weather_regions', to='CampaignManager.map')),
            ],
            options={
                'ordering': ['map', 'name'],
            },
        ),
        migrations.AddConstraint(
            model_name='rumourcategory',
            constraint=models.UniqueConstraint(fields=('campaign', 'name'), name='unique_rumour_category'),
        ),
        migrations.AddIndex(
            model_name='rumour',
            index=models.Index(fields=['campaign', 'published'], name='CampaignMan_campaig_6ef362_idx'),
        ),
        migrations.RunPython(categories_and_permissions, migrations.RunPython.noop),
    ]
