import os
import glob
from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command
from django.conf import settings
from utils.models import AppliedDataFile

class Command(BaseCommand):
    help = (
        "Scant utils/dataloads/ op .json-bestanden, laadt elk nieuw bestand in "
        "via loaddata en registreert ze in AppliedDataFile. "
        "Bestanden die al in de DB staan, worden overgeslagen."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--path',
            help=(
                "Optioneel: pad naar de map met .json-files. "
                "Standaard: <BASE_DIR>/utils/dataloads/"
            ),
            required=False,
        )

    def handle(self, *args, **options):
        # 1) Bepaal de folder met JSON-bestanden
        if options['path']:
            dataload_folder = options['path']
        else:
            # BASE_DIR is de map waar manage.py in zit
            dataload_folder = os.path.join(settings.BASE_DIR, 'utils', 'dataloads')

        if not os.path.isdir(dataload_folder):
            raise CommandError(f"Folder niet gevonden: {dataload_folder!r}")

        # 2) Vind alle .json-bestanden in die folder (niet recursief)
        pattern = os.path.join(dataload_folder, '*.json')
        files = sorted(glob.glob(pattern))
        if not files:
            self.stdout.write(self.style.WARNING(
                f"Geen .json-bestanden gevonden in {dataload_folder}."
            ))
            return

        # 3) Loop over alle gevonden bestanden
        for fullpath in files:
            filename = os.path.basename(fullpath)

            # Controleer of dit bestand al is ingeladen
            if AppliedDataFile.objects.filter(filename=filename).exists():
                self.stdout.write(self.style.NOTICE(
                    f"Overschlaan: {filename} (al ingeladen)"
                ))
                continue

            # 4) Probeer nu in te lezen via loaddata
            self.stdout.write(f"Inladen: {filename} ...")
            try:
                # Hiermee gebruik je Django’s fixture-loader
                call_command('loaddata', fullpath, verbosity=0)
            except Exception as e:
                raise CommandError(f"Fout bij loaddata van {filename}: {e}")

            # 5) Registreer dat het bestand is toegepast
            AppliedDataFile.objects.create(filename=filename)
            self.stdout.write(self.style.SUCCESS(
                f"'{filename}' succesvol ingeladen en geregistreerd."
            ))

        self.stdout.write(self.style.SUCCESS("Alle bestanden afgehandeld."))
