"""The Arcane Warrior, a class of Aegis (our variant of the SRD 5.2.1).

Schema: classes can have combat magic (CharacterClass.Magic.COMBATE: magic points «a», 10 + 2
per level, spent on the class's add-ons instead of spells), a mark for the classes that only
Aegis campaigns offer, and add-ons (ClassAddon). The «Reglas» admin group (migration 0018)
edits the add-ons and «Campañas» views them.

Data: in a database that already has the SRD catalog, the Arcane Warrior is made from the
Explorador: the same class abilities except «Lanzamiento de conjuros», which combat magic
replaces, and the same subclasses. An empty database gets it from fixtures/clases_aegis.json
instead (cargar_demo), since the SRD catalog is loaded after the migrations.
"""
import django.core.validators
import django.db.models.deletion
from django.contrib.auth.management import create_permissions
from django.db import migrations, models

# fixtures/clases_aegis.json when this was written, copied so it does not change if the fixture does.
NAME = "Arcane Warrior"
BASE = "Explorador"
REPLACED = "Lanzamiento de conjuros"
MAGIC_ABILITY = "Magia de combate"
MAGIC_ABILITY_TEXT = (
    "No lanzas conjuros: tu magia va en tus armas. Tienes puntos de magia («a»): 10 + 2 por nivel de Arcane Warrior, "
    "y los recuperas todos con un descanso largo. Los gastas en los añadidos de tu clase (de espada, de arco, buffs y "
    "de defensa), que tienes en la pestaña «Añadidos» de tu ficha.\n"
    "Un añadido no gasta una acción más de lo que cuesta la acción a la que se suma: disparar una flecha cuesta una "
    "acción, y dispararla con «Flecha Brillante», también. Todos, salvo los de defensa, necesitan que el ataque acierte. "
    "Las salvaciones contra tus añadidos son de Destreza, contra 12 + tu modificador de Destreza."
)
MAGIC_PARAGRAPH = (
    "\nMagia de combate (Aegis): no lanza conjuros. Tiene puntos de magia («a»), 10 + 2 por nivel, y los gasta en "
    "los añadidos de su clase, que se suman a sus ataques con espada y arco o a su defensa. Salvación a superar: "
    "12 + su modificador de Destreza."
)
# kind, cost, highest cost (when the player chooses), name, effect. In the order of the class sheet.
ADDONS = [
    ("espada", 2, None, "Empuje y Derribo", "Empuja 5 m y derriba."),
    ("espada", 3, None, "Área de Dolor", "En un radio de 5 m, 2d8 de daño (1d8 si se salva), con salvación de Destreza."),
    ("espada", 5, None, "Espada Veloz", "Hace un dash de 10 m en la dirección del ataque, 2d8 de daño."),
    ("arco", 1, None, "Flecha", "Una flecha normal."),
    ("arco", 2, None, "Flecha Brillante", "1d6 de daño radiante. Brilla hasta mi próximo turno: si vuelve a ser atacado "
                                          "en el mismo turno, tengo ventaja para atacarle."),
    ("arco", 2, None, "Flecha Precisa", "+3 al golpe, 1d6 de daño."),
    ("arco", 3, None, "Flecha Fantasma", "Golpea a todos los enemigos de la línea recta en la que se dispara la flecha."),
    ("arco", 5, None, "Flecha Trueno", "4d8 de daño eléctrico (2d8 si se salva), con salvación de Destreza. En un radio "
                                       "de 5 m, 2d8 (1d8 si se salva), con salvación de Destreza."),
    ("arco", 5, None, "Bombardeo Perfecto", "Tres flechas a objetivos distintos, +5 al golpe."),
    ("buff", 2, None, "Veneno", "1d8. En su próximo turno tiene desventaja en todas las tiradas."),
    ("buff", 2, None, "Marca", "Todos los ataques en su contra, +1d8."),
    ("buff", 3, None, "Lentitud", "Su velocidad se reduce a la mitad."),
    ("buff", 3, None, "Bloqueo Mágico", "En su próximo turno no puede usar magia."),
    ("buff", 4, None, "Ceguera", "Durante su próximo turno queda cegado."),
    ("buff", 5, None, "Parálisis", "Durante su próximo turno queda paralizado."),
    ("defensa", 1, None, "Poder atentivo", "Equivale a activar guardia. El ataque que se hace al activarlo hace un 50 % "
                                           "de daño adicional."),
    ("defensa", 1, None, "Evasión brillante", "Equivale a activar postura defensiva, con 3 de CA en vez de 2."),
    ("defensa", 1, 5, "Escudo", "4 × a de vida temporal en forma de escudo, hasta mi próximo turno."),
    ("defensa", 2, None, "Armadura Reforzada", "6 de AP y 6 de MAP, hasta mi próximo turno."),
    ("defensa", 2, None, "¿Efectos Secundarios?", "Al hacer cualquier tirada de salvación, si me salvo no recibo ningún "
                                                  "efecto, diga lo que diga el ataque original."),
]


def class_description(base_description):
    """The Explorador's, for this class, without its spellcasting focus, and with its magic."""
    text = (base_description.replace("por nivel de explorador", f"por nivel de {NAME}")
            .replace(" canalizador druídico (rama de muérdago),", ""))
    return text + MAGIC_PARAGRAPH


def add_arcane_warrior(apps, schema_editor=None):
    CharacterClass = apps.get_model("CampaignManager", "CharacterClass")
    Ability = apps.get_model("CampaignManager", "Ability")
    ClassAbility = apps.get_model("CampaignManager", "ClassAbility")
    Subclass = apps.get_model("CampaignManager", "Subclass")
    SubclassAbility = apps.get_model("CampaignManager", "SubclassAbility")
    ClassAddon = apps.get_model("CampaignManager", "ClassAddon")
    base = CharacterClass.objects.filter(name=BASE, bespoke=False).first()
    if base is None or CharacterClass.objects.filter(name=NAME).exists():
        return  # an empty database (the fixture brings it), or it is there already
    warrior = CharacterClass.objects.create(name=NAME, primary_ability=base.primary_ability, magic="combate",
                                            description=class_description(base.description), aegis_only=True)
    for link in ClassAbility.objects.filter(char_class=base).select_related("ability").order_by("level_required", "id"):
        if link.ability.name != REPLACED:
            ClassAbility.objects.create(char_class=warrior, ability_id=link.ability_id, level_required=link.level_required)
    magic = Ability.objects.create(name=MAGIC_ABILITY, description=MAGIC_ABILITY_TEXT)
    ClassAbility.objects.create(char_class=warrior, ability=magic, level_required=1)
    for subclass in Subclass.objects.filter(char_class=base).order_by("id"):
        copy = Subclass.objects.create(name=subclass.name, description=subclass.description, char_class=warrior)
        for link in SubclassAbility.objects.filter(subclass=subclass).order_by("level_required", "id"):
            SubclassAbility.objects.create(subclass=copy, ability_id=link.ability_id, level_required=link.level_required)
    for order, (kind, cost, max_cost, name, effect) in enumerate(ADDONS):
        ClassAddon.objects.create(char_class=warrior, kind=kind, cost=cost, max_cost=max_cost, name=name, effect=effect,
                                  order=order)


def addon_permissions(apps, schema_editor):
    # Permissions are normally created after all migrations: create them now.
    for app_config in apps.get_app_configs():
        app_config.models_module = True
        create_permissions(app_config, apps=apps, verbosity=0)
        app_config.models_module = None
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    addon = Permission.objects.filter(content_type__app_label="CampaignManager", content_type__model="classaddon")
    reglas = Group.objects.filter(name="Reglas").first()
    if reglas is not None:
        reglas.permissions.add(*addon)
    campanas = Group.objects.filter(name="Campañas").first()
    if campanas is not None:
        campanas.permissions.add(*addon.filter(codename="view_classaddon"))


def forwards(apps, schema_editor):
    addon_permissions(apps, schema_editor)
    add_arcane_warrior(apps, schema_editor)


class Migration(migrations.Migration):

    dependencies = [
        ('CampaignManager', '0031_location_importance'),
        ('auth', '0012_alter_user_first_name_max_length'),
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [
        migrations.AddField(
            model_name='characterclass',
            name='aegis_only',
            field=models.BooleanField(default=False, help_text='A class of Aegis: campaigns with the standard SRD 5.2.1 do not offer it'),
        ),
        migrations.AlterField(
            model_name='characterclass',
            name='magic',
            field=models.CharField(choices=[('completa', 'Lanzador completo'), ('media', 'Medio lanzador'), ('pacto', 'Magia de pacto'), ('combate', 'Magia de combate (añadidos)'), ('ninguna', 'Sin magia')], default='ninguna', help_text="Gives the character's magic points at each level", max_length=10),
        ),
        migrations.CreateModel(
            name='ClassAddon',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('espada', 'Espada'), ('arco', 'Arco'), ('buff', 'Buffs'), ('defensa', 'Defensa')], max_length=10)),
                ('name', models.CharField(max_length=100)),
                ('cost', models.PositiveSmallIntegerField(help_text='Magic points («a») it costs', validators=[django.core.validators.MinValueValidator(1)])),
                ('max_cost', models.PositiveSmallIntegerField(blank=True, help_text='Only when the player chooses how many points to spend (Escudo: from 1a to 5a): the most it can cost', null=True)),
                ('effect', models.TextField(help_text='What it adds to the attack or to the defence')),
                ('order', models.PositiveSmallIntegerField(default=0, help_text='Its place on the class sheet')),
                ('char_class', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='addons', to='CampaignManager.characterclass')),
            ],
            options={
                'ordering': ['char_class', 'order', 'id'],
                'constraints': [models.UniqueConstraint(fields=('char_class', 'name'), name='unique_class_addon'), models.CheckConstraint(condition=models.Q(('max_cost__isnull', True), ('max_cost__gt', models.F('cost')), _connector='OR'), name='addon_max_cost_above_cost', violation_error_message='The highest cost must be above the cost.')],
            },
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
