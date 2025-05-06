from django.apps import AppConfig

class UtilsConfig(AppConfig):
    name = 'utils'
    _mqtt_started = False  # Flag to track MQTT task status

    def ready(self):
        if 1==1:
            pass
        elif not self._mqtt_started:  # Check if task is already started
            from utils.myscheduler import start_mqtt_task
            start_mqtt_task()
            self._mqtt_started = True
        else:
            logging.debug("MQTT task already started.")