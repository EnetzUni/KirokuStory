"""Load the whole demo, in order, on a freshly migrated database:

1. datos_iniciales_srd: the SRD 5.2.1 rules catalog (no weapons).
2. armas_caseras: our homemade weapons.
3. demo_campana: the prototype's campaigns, users and everything in them.

It also copies the demo images (maps) into MEDIA_ROOT. The fixtures have fixed
primary keys, so load them only once, into empty tables:

    python manage.py migrate
    python manage.py cargar_demo
"""
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from CampaignManager.models import Campaign, Race

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
FIXTURE_ORDER = ["datos_iniciales_srd", "armas_caseras", "demo_campana"]


class Command(BaseCommand):
    help = "Load the rules fixtures and the demo campaigns, in order."

    def handle(self, *args, **options):
        if Race.objects.exists() or Campaign.objects.exists():
            raise CommandError(
                "The database already has data. The fixtures have fixed ids and would overwrite rows: "
                "start from an empty database (delete db.sqlite3 and run migrate)."
            )
        for name in FIXTURE_ORDER:
            call_command("loaddata", name, verbosity=options["verbosity"])
        source = FIXTURES / "demo_media"
        if source.exists():
            shutil.copytree(source, Path(settings.MEDIA_ROOT), dirs_exist_ok=True)
        self.stdout.write(self.style.SUCCESS(
            "Demo loaded. Users carlos (master), lucia, iker, ane and unai; password: mesa-demo-2026."
        ))
