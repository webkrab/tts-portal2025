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
    MQTT_TOPIC = "process/gpstracking"
    MQTT_CLIENT_NAME = "gpstracking_TrackerMessage"
    CACHE_REFRESH_INTERVAL = 60  # seconden
    SAVE_INTERVAL = 15  # seconden

    tracker_cache: dict[str, TrackerIdentifier] = {}
    message_buffer: dict[str, dict] = {}
    buffer_lock_msg = threading.Lock()
    tracker_buffer: dict[str, dict] = {}
    buffer_lock_tracker = threading.Lock()
    _mapping_cache: dict = None

    @staticmethod
    def get_decoder_field_mapping():
        if GpsTrackingUtilDB._mapping_cache is None:
            GpsTrackingUtilDB._mapping_cache = {
                field.name: field.dbfield if field.dbfield else None
                for field in TrackerDecoderField.objects.all()
            }
            logger.info(f"Decoder field mapping geladen ({len(GpsTrackingUtilDB._mapping_cache)} velden).")
        return GpsTrackingUtilDB._mapping_cache

    @staticmethod
    def refresh_tracker_cache():
        new_cache = {}
        all_identifiers = TrackerIdentifier.objects.select_related("tracker", "identifier_type")
        for ti in all_identifiers:
            new_cache[ti.identkey] = ti
        GpsTrackingUtilDB.tracker_cache = new_cache
        logger.info(f"Tracker cache vernieuwd ({len(new_cache)} items)")

    @staticmethod
    def start_tracker_cache_loop():
        def loop():
            while True:
                time.sleep(GpsTrackingUtilDB.CACHE_REFRESH_INTERVAL)
                GpsTrackingUtilDB.refresh_tracker_cache()

        threading.Thread(target=loop, daemon=True).start()
        GpsTrackingUtilDB.refresh_tracker_cache()

    @staticmethod
    def get_or_create_tracker_identifier(identity) -> tuple[TrackerIdentifier | None, Tracker | None]:
        identkey = identity.get("identkey")
        identtype = identity.get("identtype")
        identid = identity.get("identid")
        tcUniqueId = identity.get("tcUniqueId")

        if not (identkey and identtype and identid):
            logger.warning(f"Onvoldoende data om identifier aan te maken of bij te werken: {identity}")
            return None, None

        try:
            tracker_identifier = TrackerIdentifier.objects.filter(identkey=identkey).first()
            if tracker_identifier:
                GpsTrackingUtilDB.tracker_cache[identkey] = tracker_identifier
                return tracker_identifier, tracker_identifier.tracker

            identifier_type = TrackerIdentifierType.objects.get(code=identtype)
            tracker = Tracker.objects.create()

            tracker_identifier = TrackerIdentifier.objects.create(
                tracker=tracker,
                identifier_type=identifier_type,
                external_id=identid,
                identkey=identkey,
            )

            GpsTrackingUtilDB.tracker_cache[identkey] = tracker_identifier
            logger.info(f"Aangemaakte nieuwe TrackerIdentifier: {identkey}")
            return tracker_identifier, tracker

        except TrackerIdentifierType.DoesNotExist:
            logger.warning(f"Onbekend identifier_type '{identtype}' voor {identkey}")
        except Exception as e:
            logger.exception(f"Fout bij aanmaken/bijwerken van TrackerIdentifier {identkey}: {e}")

        return None, None

    @staticmethod
    def process_mqtt_message(message_str: str):
        try:
            msg = json.loads(message_str)
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

            tracker_identifier, tracker = GpsTrackingUtilDB.get_or_create_tracker_identifier(identity)
            if not tracker_identifier:
                return

            try:
                position = Point(float(data['longitude']), float(data['latitude']))
            except (KeyError, ValueError) as e:
                position = None

            msg_entry = {
                    "tracker_identifier": tracker_identifier,
                    "msgtype"           : msgtype,
                    "content"           : data,
                    "raw"               : raw,
                    "dbcall"            : formated,
                    "message_timestamp" : received,
                    "position"          : position,
                    "sha256_key"        : msghash,
            }

            # Store message in buffer
            with GpsTrackingUtilDB.buffer_lock_msg:
                if msghash not in GpsTrackingUtilDB.message_buffer:
                    GpsTrackingUtilDB.message_buffer[msghash] = msg_entry

            if formated:
                tracker_entry = formated.copy()  # ensure we work with a copy
                tracker_entry["position"] = position
                tid = tracker_identifier.tracker.id
                position_ts = tracker_entry.get("position_timestamp") or received
                meta_ts = tracker_entry.get("meta_timestamp") or received

                with GpsTrackingUtilDB.buffer_lock_tracker:
                    existing = GpsTrackingUtilDB.tracker_buffer.get(tid)
                    if not existing:
                        temp_entry = tracker_entry.copy()
                        temp_entry['id'] = tid
                        GpsTrackingUtilDB.tracker_buffer[tid] = temp_entry
                    else:
                        location_fields = ["altitude", "speed", "heading", "position", "position_timestamp"]
                        for k, v in list(tracker_entry.items()):  # Safe copy for iteration
                            if k not in existing:
                                existing[k] = v
                            elif k in location_fields:
                                if existing.get("position_timestamp") is None or existing["position_timestamp"] <= position_ts:
                                    existing[k] = v
                                    existing["position_timestamp"] = position_ts
                            else:
                                if existing.get("meta_timestamp") is None or existing["meta_timestamp"] <= meta_ts:
                                    existing[k] = v
                                    existing["meta_timestamp"] = meta_ts

        except Exception as e:
            logger.exception(f"Fout bij verwerken van MQTT bericht: {e}")

    @staticmethod
    def save_buffer_to_db():
        start = time.time()

        # Verwerk TrackerMessages
        with GpsTrackingUtilDB.buffer_lock_msg:
            msg_items = list(GpsTrackingUtilDB.message_buffer.values())
            GpsTrackingUtilDB.message_buffer.clear()

        if msg_items:
            messages = [TrackerMessage(**item) for item in msg_items]
            TrackerMessage.objects.bulk_create(messages, ignore_conflicts=True)
            logger.info(f"{len(messages)} tracker.messages opgeslagen in de database. {round(time.time() - start, 3)}s")
        else:
            logger.warning(f"0 tracker.messages opgeslagen in de database. {round(time.time() - start, 3)}s")

        start = time.time()

        # Verwerk Tracker-updates
        with GpsTrackingUtilDB.buffer_lock_tracker:
            trackers_items = list(GpsTrackingUtilDB.tracker_buffer.values())
            GpsTrackingUtilDB.tracker_buffer.clear()

        if trackers_items:
            ids = [item['id'] for item in trackers_items if 'id' in item]
            existing_trackers = {tracker.id: tracker for tracker in Tracker.objects.filter(id__in=ids)}

            updated_trackers = []
            update_fields = set()

            for item in trackers_items:
                tracker_id = item.get('id')
                tracker = existing_trackers.get(tracker_id)

                if not tracker:
                    logger.warning(f"Tracker met id {tracker_id} niet gevonden voor update.")
                    continue

                # Velden bijwerken op basis van de input
                for key, value in item.items():
                    if key != 'id':
                        setattr(tracker, key, value)
                        update_fields.add(key)

                # Speciale logica: screenname vullen vanuit screenlink als die leeg is
                if (not tracker.screen_name or tracker.screen_name.strip() == '') and item.get('screen_name'):
                    tracker.screen_name = item['screen_name']
                    update_fields.add('screen_name')

                # Speciale logica: icon vullen vanuit icon als die leeg is
                if (not tracker.icon or tracker.icon.strip() == '') and item.get('icon'):
                    tracker.icon = item['icon']
                    update_fields.add('icon')

                updated_trackers.append(tracker)

            if updated_trackers and update_fields:
                try:
                    Tracker.objects.bulk_update(updated_trackers, fields=list(update_fields))
                    logger.info(f"{len(updated_trackers)} tracker.trackers geüpdatet in de database. {round(time.time() - start, 3)}s")
                except Exception as e:
                    logger.exception(f"Fout bij bulk_update van trackers, fallback naar individuele updates: {e}")
                    for t in updated_trackers:
                        try:
                            t.save()
                        except Exception as ex:
                            logger.exception(f"Fout bij opslaan van individuele tracker {t.id}: {ex}")
        else:
            logger.warning(f"0 tracker.trackers opgeslagen in de database. {round(time.time() - start, 3)}s")

    @staticmethod
    def start_save_loop():
        def loop():
            while True:
                time.sleep(GpsTrackingUtilDB.SAVE_INTERVAL)
                GpsTrackingUtilDB.save_buffer_to_db()

        threading.Thread(target=loop, daemon=True).start()
        logger.info(f"Start buffer schrijf-loop elke {GpsTrackingUtilDB.SAVE_INTERVAL} seconden.")

    @staticmethod
    def start_mqtt_subscriber():
        client = TTSmqtt.start_subscriber(GpsTrackingUtilDB.MQTT_CLIENT_NAME, GpsTrackingUtilDB.MQTT_TOPIC)
        if not client:
            logger.error("Kon MQTT-subscriber niet starten.")
            return

        client.on_message = lambda c, u, m: GpsTrackingUtilDB.process_mqtt_message(m.payload.decode("utf-8"))
        logger.info("MQTT-subscriber actief.")

        GpsTrackingUtilDB.start_save_loop()
        GpsTrackingUtilDB.start_tracker_cache_loop()
        logger.info("Processor en cache-ververser gestart.")
