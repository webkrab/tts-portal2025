from django.apps import AppConfig
import threading
import os

class GpstrackingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'gpstracking'

    def ready(self):
        if os.environ.get('RUN_MAIN') != 'true':  # only run in main process
            return
        from utils.logger import get_logger
        logger = get_logger(__name__)

        import gpstracking.signals
        from gpstracking.util_db import GpsTrackingUtilDB

        GpsTrackingUtilDB().start_save_loop()
#        GpsTrackingUtilDB().start_mqtt_subscriber()

        def start_traccar():
            from gpstracking.Api_traccar import Traccar as tc1
            tc1().start()

        threading.Thread(target=start_traccar, daemon=True).start()

        def start_traccar2():
            from gpstracking.Api_traccar2 import Traccar as tc2
            tc2().start()

        threading.Thread(target=start_traccar2, daemon=True).start()

        logger.info("Traccar background service gestart")

