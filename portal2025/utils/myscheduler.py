import time
import json
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from utils.mqtt import start_publisher


def start_mqtt_task():
    """Start de periodieke MQTT-publicatie."""
    publish_message = start_publisher("test_publisher", "in/ais/test/nmea")

    if not publish_message:
        print("Fout bij starten MQTT-publisher. Taak niet gestart.")
        return

    def publish_periodically():
        received = int(time.time() * 1000)  # Huidige tijd in milliseconden

        message_payload = {
                "raw"     : f"Testbericht om {time.strftime('%Y-%m-%d %H:%M:%S')}",
                "received": received,
                "msgtype" : "test",
                "msghash" : None,
                "gateway" : "test"
        }

        message_json = json.dumps(message_payload)  # JSON string maken
        publish_message(message_json)  # Bericht publiceren
        print(f"Verzonden: {message_json}")  # Debug print

    scheduler = BackgroundScheduler()
    scheduler.add_job(
            publish_periodically,
            IntervalTrigger(seconds=15),  # Verhoog naar 30 seconden om overlap te vermijden
            id='mqtt_publisher_task',
            name='Publiceer berichten',
            replace_existing=True
    )

    scheduler.start()
    print("Scheduler gestart voor periodieke publicatie.")
