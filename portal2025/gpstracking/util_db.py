import json
import threading
import time
from datetime import datetime

from django.contrib.gis.geos import Point

from gpstracking.models import (
    Tracker,
    TrackerIdentifier,
    TrackerIdentifierType,
    TrackerMessage,
)

from utils.logger import get_logger
import utils.mqtt as TTSmqtt
logger = get_logger(__name__)


class GpsTrackingUtilDB:
    """
    Verwerkt binnenkomende GPS-trackingberichten via MQTT en slaat ze gestructureerd op.
    Bevat caching, buffering en automatische databaseverwerking.
    """

    # ===== Configuratie =====
    MQTT_TOPIC = "process/gpstracking"
    MQTT_CLIENT_NAME = "gpstracking_TrackerMessage"
    CACHE_REFRESH_INTERVAL = 60  # seconden
    SAVE_INTERVAL = 15  # seconden

    # ===== Caching & Buffers =====
    tracker_cache: dict[str, TrackerIdentifier] = {}
    message_buffer: dict[str, dict] = {}
    buffer_lock = threading.Lock()

    @staticmethod
    def refresh_tracker_cache():
        """
        Vernieuwt de tracker_identifier-cache uit de database.
        """
        new_cache = {}
        all_identifiers = TrackerIdentifier.objects.select_related("tracker", "identifier_type")
        for ti in all_identifiers:
            new_cache[ti.identkey] = ti
        GpsTrackingUtilDB.tracker_cache = new_cache
        logger.info(f"Tracker cache vernieuwd ({len(new_cache)} items)")

    @staticmethod
    def start_tracker_cache_loop():
        """
        Start een achtergrond thread om de cache periodiek te verversen.
        """
        def loop():
            while True:
                time.sleep(GpsTrackingUtilDB.CACHE_REFRESH_INTERVAL)
                GpsTrackingUtilDB.refresh_tracker_cache()

        threading.Thread(target=loop, daemon=True).start()
        GpsTrackingUtilDB.refresh_tracker_cache()

    @staticmethod
    def get_or_create_tracker_identifier(identkey: str, formated: dict) -> TrackerIdentifier | None:
        """
        Haalt een TrackerIdentifier op of maakt deze aan op basis van formated info.
        """
        if identkey in GpsTrackingUtilDB.tracker_cache:
            return GpsTrackingUtilDB.tracker_cache[identkey]

        try:
            identifier_type_name = formated.get("identtype")
            external_id = formated.get("identid")

            if not identifier_type_name or not external_id:
                logger.warning(f"Onvoldoende data om identifier aan te maken voor {identkey}")
                return None

            identifier_type = TrackerIdentifierType.objects.get(name=identifier_type_name)
            tracker = Tracker.objects.create(screen_name=identkey)

            tracker_identifier = TrackerIdentifier.objects.create(
                tracker=tracker,
                identifier_type=identifier_type,
                external_id=external_id
            )
            GpsTrackingUtilDB.tracker_cache[identkey] = tracker_identifier
            logger.info(f"Aangemaakte nieuwe TrackerIdentifier: {identkey}")
            return tracker_identifier

        except TrackerIdentifierType.DoesNotExist:
            logger.warning(f"Onbekend identifier_type '{identifier_type_name}' voor {identkey}")
        except Exception as e:
            logger.exception(f"Fout bij aanmaken van TrackerIdentifier {identkey}: {e}")
        return None

    @staticmethod
    def process_mqtt_message(message_str: str):
        """
        Verwerkt een binnengekomen MQTT-bericht en voegt het toe aan de buffer.
        """
        try:
            msg = json.loads(message_str)
            formated = msg.get("formated", {})
            data = msg.get("data", {})
            msghash = msg.get("msghash")
            received = msg.get("received")
            msgtype = msg.get("msgtype")

            if not (formated and msghash and received):
                logger.warning("Onvolledig bericht ontvangen, overgeslagen.")
                return

            identkey = formated.get("identkey")
            if not identkey:
                logger.warning("formated.identkey ontbreekt, bericht genegeerd.")
                return

            tracker_identifier = GpsTrackingUtilDB.get_or_create_tracker_identifier(identkey, formated)
            if not tracker_identifier:
                return

            position = None
            position_coords = formated.get("position")
            if position_coords and isinstance(position_coords, list) and len(position_coords) == 2:
                position = Point(position_coords[0], position_coords[1])

            new_entry = {
                "tracker_identifier": tracker_identifier,
                "msgtype": msgtype,
                "content": data,
                "message_timestamp": received,
                "position": position,
                "sha256_key": msghash
            }

            with GpsTrackingUtilDB.buffer_lock:
                existing = GpsTrackingUtilDB.message_buffer.get(msghash)
                if not existing or received < existing["message_timestamp"]:
                    GpsTrackingUtilDB.message_buffer[msghash] = new_entry

        except Exception as e:
            logger.exception(f"Fout bij verwerken van MQTT bericht: {e}")

    @staticmethod
    def save_buffer_to_db():
        """
        Slaat de verzamelde berichten op in de database.
        """
        with GpsTrackingUtilDB.buffer_lock:
            items = list(GpsTrackingUtilDB.message_buffer.values())
            GpsTrackingUtilDB.message_buffer.clear()

        if not items:
            return

        messages = [TrackerMessage(**item) for item in items]
        TrackerMessage.objects.bulk_create(messages, ignore_conflicts=True)
        logger.info(f"{len(messages)} berichten opgeslagen in de database.")

    @staticmethod
    def start_save_loop():
        """
        Start een thread die de buffer periodiek wegschrijft.
        """
        def loop():
            while True:
                time.sleep(GpsTrackingUtilDB.SAVE_INTERVAL)
                GpsTrackingUtilDB.save_buffer_to_db()

        threading.Thread(target=loop, daemon=True).start()
        logger.info("Start buffer schrijf-loop elke 15 seconden.")

    @staticmethod
    def start_mqtt_subscriber():
        """
        Start de MQTT-subscriber en initialisaties.
        """
        client = TTSmqtt.start_subscriber(GpsTrackingUtilDB.MQTT_CLIENT_NAME, GpsTrackingUtilDB.MQTT_TOPIC)
        if not client:
            logger.error("Kon MQTT-subscriber niet starten.")
            return

        client.on_message = lambda c, u, m: GpsTrackingUtilDB.process_mqtt_message(m.payload.decode("utf-8"))
        logger.info("MQTT-subscriber actief.")

        GpsTrackingUtilDB.start_save_loop()
        GpsTrackingUtilDB.start_tracker_cache_loop()

        logger.info("Processor en cache-ververser gestart.")
