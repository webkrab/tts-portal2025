from django.apps import AppConfig
import threading


class GpstrackingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'gpstracking'

    def ready(self):
        import gpstracking.signals

        def start_traccar():
            from gpstracking.Api_traccar import Traccar
            Traccar().start()

        threading.Thread(target=start_traccar, daemon=True).start()
        print("✅ Traccar background service gestart")
