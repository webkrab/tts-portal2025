import os
from django.apps import AppConfig

class ApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'api'

    def ready(self):
        """Start de decoder zodra de app klaar is."""
        if 1==1:
            print("app: api is uitgezet")
        if os.environ.get('RUN_MAIN') == 'true':  # voorkomt dubbele uitvoering bij autoreload
            try:
                from api.util_ais_nmea import Nmea  # zorg dat dit pad klopt met je projectstructuur
                Nmea.start()
            except Exception as e:
                print(f"Fout bij starten van decoder: {e}")
