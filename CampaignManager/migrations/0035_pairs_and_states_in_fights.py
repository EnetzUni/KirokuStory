"""Pairs and states in fights.

- An NPC template can act with another in one activation (Shayne and Aurox, of the bestiary):
  `Character.acts_with`.
- An enemy without a sheet can suffer states too: an active effect is on a character or, in a
  fight, on a combatant.
- The house states the bestiary uses and the catalog lacked (the SRD ones were there already):
  Aterrado, Atado, Congelado, Quemado, Sangrando and Suspendido, with the bestiary's own words.
  Added here to a database that already has the catalog; an empty one gets them from
  fixtures/armas_caseras.json (ids 39 to 44), which is loaded after the migrations.
"""

import django.db.models.deletion
from django.db import migrations, models

HARD_CONTROL = ("Control duro: solo uno a la vez (aturdido, congelado, suspendido, paralizado); al librarse, "
                "inmune a todos hasta el final de su siguiente activación.")
STATES = [
    ("Aterrado", "No puede acercarse a lo que le aterra y tiene desventaja en ataques y pruebas mientras lo vea. "
                 "Puede gastar sus 2 acciones en calmarse y terminar el efecto.", ""),
    ("Atado", "Puede moverse, pero no atacar ni usar las manos.", ""),
    ("Congelado", "Velocidad 0. " + HARD_CONTROL, "Hasta el final de su siguiente activación"),
    ("Quemado", "Al inicio de cada activación recibe 1d6 de fuego que ignora AP. No se acumula. Se apaga gastando "
                "1 acción, rodando por el suelo o con agua.", ""),
    ("Sangrando", "Pierde los PV del Sangrado al inicio de cada fase de su bando (una vez por ronda) hasta recibir "
                  "curación o tratar la herida.", "Hasta curar la herida (máx. 5 rondas)"),
    ("Suspendido", "Sujeto en el aire por hilos de atadura. " + HARD_CONTROL, "Hasta el final de su siguiente activación"),
]


def add_states(apps, schema_editor):
    Effect = apps.get_model("CampaignManager", "Effect")
    if not Effect.objects.exists():  # an empty database: the fixture brings them, with its fixed ids
        return
    for name, description, duration in STATES:
        if not Effect.objects.filter(name=name).exists():
            Effect.objects.create(name=name, description=description, duration=duration)


def remove_states(apps, schema_editor):
    Effect = apps.get_model("CampaignManager", "Effect")
    Effect.objects.filter(name__in=[name for name, _, _ in STATES], activeeffect__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('CampaignManager', '0034_page_sheets_own_speed'),
    ]

    operations = [
        migrations.AddField(
            model_name='character',
            name='acts_with',
            field=models.ForeignKey(blank=True, help_text='Only for NPCs: the NPC template it acts with, in one activation', limit_choices_to={'is_npc': True, 'template__isnull': True}, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='CampaignManager.character'),
        ),
        migrations.AlterField(
            model_name='activeeffect',
            name='character',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='CampaignManager.character'),
        ),
        migrations.AddField(
            model_name='activeeffect',
            name='combatant',
            field=models.ForeignKey(blank=True, help_text='Only for an enemy without a sheet, in its fight', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='effects', to='CampaignManager.combatant'),
        ),
        migrations.AddConstraint(
            model_name='activeeffect',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('character__isnull', False), ('combatant__isnull', True)), models.Q(('character__isnull', True), ('combatant__isnull', False)), _connector='OR'), name='active_effect_on_character_or_combatant'),
        ),
        migrations.RunPython(add_states, remove_states),
    ]
