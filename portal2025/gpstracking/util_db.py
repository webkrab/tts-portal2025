import json
import threading
import time

from django.contrib.gis.geos import Point

from gpstracking.models import (
    Tracker,
    TrackerIdentifier,
    TrackerIdentifierType,
    TrackerMessage,
    TrackerDecoderField,
)
from utils.gen_conv import convert_speed, flatten_multilevel, remap_keys, genereer_hash
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
    _mapping_cache: dict = None  # cache voor decoder field mapping

    @staticmethod
    def get_decoder_field_mapping():
        """
        Haalt de mapping tussen decoder name -> dbfield op, en cached deze.
        """
        if GpsTrackingUtilDB._mapping_cache is None:
            GpsTrackingUtilDB._mapping_cache = {
                field.name: field.dbfield if field.dbfield else None
                for field in TrackerDecoderField.objects.all()
            }
            logger.info(f"Decoder field mapping geladen ({len(GpsTrackingUtilDB._mapping_cache)} velden).")
        return GpsTrackingUtilDB._mapping_cache

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

    def get_or_create_tracker_identifier(identity) -> TrackerIdentifier | None:
        """
        Haalt een TrackerIdentifier op via identkey. Als deze nog niet bestaat:
        - maakt of update een Tracker op basis van screen_name (= identkey)
        - maakt een nieuwe TrackerIdentifier aan
        """
        identkey = identity.get("identkey")
        identtype = identity.get("identtype")
        identid = identity.get("identid")

        if not (identkey and identtype and identid):
            logger.warning(f"Onvoldoende data om identifier aan te maken of bij te werken: {identity}")
            return None

        try:
            # Stap 1: Check of TrackerIdentifier al bestaat
            tracker_identifier = TrackerIdentifier.objects.filter(identkey=identkey).select_related("tracker").first()
            if tracker_identifier:
                # Cache bijwerken en retourneren
                GpsTrackingUtilDB.tracker_cache[identkey] = tracker_identifier
                return tracker_identifier

            # Stap 2: Ophalen van identifier type
            identifier_type = TrackerIdentifierType.objects.get(code=identtype)

            # Stap 3: Tracker aanmaken of bijwerken (niet uniek op screen_name dus filter+update+create)
            tracker = Tracker.objects.filter(screen_name=identkey).first()
            if not tracker:
                tracker = Tracker.objects.create(screen_name=identkey)
            else:
                # Eventueel extra updates als je andere velden hebt
                # tracker.status = ...
                # tracker.save()
                pass

            # Stap 4: Nieuwe TrackerIdentifier aanmaken
            tracker_identifier = TrackerIdentifier.objects.create(
                    tracker=tracker,
                    identifier_type=identifier_type,
                    external_id=identid,
                    identkey=identkey,
            )

            GpsTrackingUtilDB.tracker_cache[identkey] = tracker_identifier
            logger.info(f"Aangemaakte nieuwe TrackerIdentifier: {identkey}")
            return tracker_identifier

        except TrackerIdentifierType.DoesNotExist:
            logger.warning(f"Onbekend identifier_type '{identtype}' voor {identkey}")
        except Exception as e:
            logger.exception(f"Fout bij aanmaken/bijwerken van TrackerIdentifier {identkey}: {e}")
            return None

    @staticmethod
    def process_mqtt_message(message_str: str):
        """
        Verwerkt een binnengekomen MQTT-bericht en voegt het toe aan de buffer.
        """
        try:
            msg = json.loads(message_str)
            # data to elements
            data = msg.get("data", {})
            mapping = GpsTrackingUtilDB.get_decoder_field_mapping()
            formated, _ = remap_keys(data, mapping)
            msghash = msg.get("msghash")
            received = msg.get("received")
            msgtype = msg.get("msgtype")
            identity = msg.get("identity")
            raw = msg.get("raw")

            if not (msghash and received):
                logger.warning("Onvolledig bericht ontvangen, overgeslagen.")
                return

            if not identity:
                logger.warning("identity ontbreekt, bericht genegeerd.")
                return

            tracker_identifier = GpsTrackingUtilDB.get_or_create_tracker_identifier(identity)
            if not tracker_identifier:
                return

            position = None
            if "latitude" in data and "longitude" in data:
                position = Point(float(data['longitude']), float(data['latitude']))

            new_entry = {
                "tracker_identifier": tracker_identifier,
                "msgtype": msgtype,
                "content": data,
                "raw": raw,
                "dbcall": formated,
                "message_timestamp": received,
                "position": position,
                "sha256_key": msghash
            }

            with GpsTrackingUtilDB.buffer_lock:
                existing = GpsTrackingUtilDB.message_buffer.get(msghash)
                if not existing or received < existing["message_timestamp"]:
                    GpsTrackingUtilDB.message_buffer[msghash] = new_entry

            GpsTrackingUtilDB.save_buffer_to_db()

            #add logica om tracker object te updaten obv formated (veldnamen van model "Tracker" zijn 1 op 1 aan de keys van "formated")

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
