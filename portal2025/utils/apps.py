import os
from django.apps import AppConfig


class UtilsConfig(AppConfig):
    name = 'utils'
    _mqtt_started = False  # Flag to track MQTT task status

    def ready(self):
        if os.environ.get('RUN_MAIN') != 'true':  # only run in main process
            from utils.myscheduler import start_mqtt_task
            start_mqtt_task()
            self._mqtt_started = True
