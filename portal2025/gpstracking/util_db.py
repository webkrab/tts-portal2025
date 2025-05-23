import json
import threading
import time
from collections import defaultdict

from django.contrib.gis.geos import Point
from gpstracking.models import (
    Tracker,
    TrackerIdentifier,
    TrackerIdentifierType,
    TrackerMessage,
    TrackerDecoderField,
)
from utils.gen_conv import remap_keys
from utils.logger import get_logger
import utils.mqtt as TTSmqtt

logger = get_logger(__name__)


class GpsTrackingUtilDB:
    MQTT_TOPIC = "process/gpstracking"
    MQTT_CLIENT_NAME = "gpstracking_TrackerMessage"
    CACHE_REFRESH_INTERVAL = 60
    SAVE_INTERVAL = 15

    tracker_cache: dict[str, TrackerIdentifier] = {}
    message_buffer: dict[str, dict] = {}
    buffer_lock_msg = threading.Lock()
    tracker_buffer: dict[str, dict] = {}
    buffer_lock_tracker = threading.Lock()
    _mapping_cache: dict = None

    # === HULPFUNCTIES ===

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
    def find_tracker_identifier_by_identkey(identkey: str) -> TrackerIdentifier | None:
        if not identkey:
            return None
        return GpsTrackingUtilDB.tracker_cache.get(identkey) or TrackerIdentifier.objects.filter(identkey=identkey).first()

    @staticmethod
    def create_tracker_identifier(tracker: Tracker, code: str, external_id: str) -> TrackerIdentifier:
        identifier_type = TrackerIdentifierType.objects.get(code=code)
        return TrackerIdentifier.objects.create(
            tracker=tracker,
            identifier_type=identifier_type,
            external_id=external_id,
        )

    @staticmethod
    def additional_identifiers_from_uniqueId(uniqueId: str, tracker):
        uniqueId = uniqueId.replace("ADSB", "ICAO")
        tc_split = uniqueId.split("-", 1)
        if len(tc_split) > 1:
            prefix, ident = tc_split
            if prefix in ["ICAO", "MMSI", "DMR", "GMS"]:
                if not GpsTrackingUtilDB.find_tracker_identifier_by_identkey(f"{prefix}_{ident}"):
                    GpsTrackingUtilDB.create_tracker_identifier(tracker, prefix, ident)


    # === CACHE LOGICA ===

    @staticmethod
    def refresh_tracker_cache():
        new_cache = {
            ti.identkey: ti for ti in TrackerIdentifier.objects.select_related("tracker", "identifier_type")
        }
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

    # === IDENTIFIER LOGICA ===

    @staticmethod
    def get_or_create_tracker_identifier(identity) -> TrackerIdentifier | None:
        try:
            identkey = (v := identity.get("identkey")) and v.upper()
            tc_unique_id = (v := identity.get("tcUniqueId")) and v.upper()
            identtype = (v := identity.get("identtype")) and v.upper()
            identid = (v := identity.get("identid")) and v.upper()

            if not identtype or not identid:
                logger.warning(f"Onvoldoende data om identifier aan te maken: {identity}")
                return None

            tcuid_identkey = f"TCUID_{tc_unique_id}" if tc_unique_id else None

            ti_identkey = GpsTrackingUtilDB.find_tracker_identifier_by_identkey(identkey)
            ti_tc_uid = GpsTrackingUtilDB.find_tracker_identifier_by_identkey(tcuid_identkey)

            # CASE 1
            if ti_identkey and not ti_tc_uid and tcuid_identkey:
                tracker = ti_identkey.tracker
                GpsTrackingUtilDB.create_tracker_identifier(tracker, "TCUID", tc_unique_id)
                GpsTrackingUtilDB.additional_identifiers_from_uniqueId(tc_unique_id, tracker)

            # CASE 2
            if ti_tc_uid and not ti_identkey and identkey:
                tracker = ti_tc_uid.tracker
                ti_identkey = GpsTrackingUtilDB.create_tracker_identifier(tracker, identtype, identid)

            # CASE 3
            if not ti_identkey and not ti_tc_uid and (identkey or tcuid_identkey):
                tracker = Tracker.objects.create()
                logger.debug(f"Aangemaakt nieuwe tracker {tracker.id}")
                if identkey:
                    ti_identkey = GpsTrackingUtilDB.create_tracker_identifier(tracker, identtype, identid)
                if tcuid_identkey:
                    GpsTrackingUtilDB.create_tracker_identifier(tracker, "TCUID", tc_unique_id)
                    GpsTrackingUtilDB.additional_identifiers_from_uniqueId(tc_unique_id, tracker)



            return ti_identkey

        except TrackerIdentifierType.DoesNotExist:
            logger.warning(f"Onbekend identifier_type '{identtype}'")
        except Exception as e:
            logger.exception(f"Fout bij aanmaken of ophalen van TrackerIdentifier: {e}")

        return None

    # === VERWERKING ===

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

            if not (msghash and received) or not identity:
                logger.warning("Onvolledig bericht ontvangen, overgeslagen.")
                return

            tracker_identifier = GpsTrackingUtilDB.get_or_create_tracker_identifier(identity)
            if not tracker_identifier:
                return

            try:
                position = Point(float(data['longitude']), float(data['latitude']))
            except (KeyError, ValueError):
                position = None

            msg_entry = {
                "tracker_identifier": tracker_identifier,
                "msgtype": msgtype,
                "content": data,
                "raw": raw,
                "dbcall": formated,
                "message_timestamp": received,
                "position": position,
                "sha256_key": msghash,
            }

            with GpsTrackingUtilDB.buffer_lock_msg:
                if msghash not in GpsTrackingUtilDB.message_buffer:
                    GpsTrackingUtilDB.message_buffer[msghash] = msg_entry

            if formated:
                tracker_entry = formated.copy()
                if position:
                    tracker_entry["position"] = position

                tid = tracker_identifier.tracker.id
                position_ts = tracker_entry.get("position_timestamp") or received
                meta_ts = tracker_entry.get("meta_timestamp") or received

                with GpsTrackingUtilDB.buffer_lock_tracker:
                    existing = GpsTrackingUtilDB.tracker_buffer.get(tid)
                    if not existing:
                        tracker_entry['id'] = tid
                        GpsTrackingUtilDB.tracker_buffer[tid] = tracker_entry
                    else:
                        for k, v in tracker_entry.items():
                            if k not in existing or (
                                k in ["altitude", "speed", "heading", "position", "position_timestamp"]
                                and (existing.get("position_timestamp") is None or existing["position_timestamp"] <= position_ts)
                            ) or (
                                existing.get("meta_timestamp") is None or existing["meta_timestamp"] <= meta_ts
                            ):
                                existing[k] = v

        except Exception as e:
            logger.exception(f"Fout bij verwerken van MQTT bericht: {e}")

    @staticmethod
    def save_buffer_to_db():
        start = time.time()
        with GpsTrackingUtilDB.buffer_lock_msg:
            msg_items = list(GpsTrackingUtilDB.message_buffer.values())
            GpsTrackingUtilDB.message_buffer.clear()

        if msg_items:
            TrackerMessage.objects.bulk_create([TrackerMessage(**item) for item in msg_items], ignore_conflicts=True)
            logger.info(f"{len(msg_items)} tracker.messages opgeslagen ({round(time.time() - start, 3)}s)")
        else:
            logger.warning("0 tracker.messages opgeslagen")

        start = time.time()
        with GpsTrackingUtilDB.buffer_lock_tracker:
            tracker_items = list(GpsTrackingUtilDB.tracker_buffer.values())
            GpsTrackingUtilDB.tracker_buffer.clear()

        if tracker_items:
            ids = [item['id'] for item in tracker_items]
            existing_trackers = {t.id: t for t in Tracker.objects.filter(id__in=ids)}
            updated_trackers = []
            fields_per_tracker = {}
            valid_fields = {f.name for f in Tracker._meta.get_fields()}

            for item in tracker_items:
                tracker = existing_trackers.get(item['id'])
                if not tracker:
                    continue
                changed = set()
                for key, val in item.items():
                    if key == 'id' or key not in valid_fields:
                        continue
                    if key in ['screen_name', 'icon'] and (not getattr(tracker, key)):
                        setattr(tracker, key, val)
                        changed.add(key)
                    elif key not in ['screen_name', 'icon']:
                        setattr(tracker, key, val)
                        changed.add(key)
                if changed:
                    updated_trackers.append(tracker)
                    fields_per_tracker[tracker.pk] = changed

            for fields, group in defaultdict(list, {
                frozenset(fields_per_tracker[t.pk]): [t for t in updated_trackers if frozenset(fields_per_tracker[t.pk]) == fields]
                for t in updated_trackers
            }).items():
                try:
                    Tracker.objects.bulk_update(group, list(fields))
                except Exception as e:
                    logger.exception(f"Fout bij bulk_update: {e}")
                    for t in group:
                        try:
                            t.save(update_fields=list(fields))
                        except Exception as ex:
                            logger.exception(f"Individuele save fout: {ex}")
        else:
            logger.warning("0 tracker.trackers opgeslagen")

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
