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
    def get_or_create_tracker_identifier(identity) -> TrackerIdentifier | None:
        identkey = (v := identity.get("identkey")) and v.upper() or None
        tc_unique_id = (v := identity.get("tcUniqueId")) and v.upper() or None
        identtype = (v := identity.get("identtype")) and v.upper() or None
        identid = (v := identity.get("identid")) and v.upper() or None

        if not identtype or not identid:
            logger.warning(f"Onvoldoende data om identifier aan te maken: {identity}")
            return None

        try:
            tc_unique_id = f"{tc_unique_id}" if tc_unique_id else None

            if tc_unique_id:
                if tc_unique_id:
                    tc_unique_id = tc_unique_id.replace("ADSB", "ICAO")
                    tc_split = tc_unique_id.split("-", 1)
                    if len(tc_split) > 1:
                        tc_prefix, tc_ident = tc_split
                        if tc_prefix in ["ICAO", "MMSI", "DMR", "GMS"]:
                            identtype, identid = tc_prefix, tc_ident



            tcuid_identkey = f"TCUID_{tc_unique_id}" if tc_unique_id else None

            # Probeer uit cache
            ti_identkey = GpsTrackingUtilDB.tracker_cache.get(identkey) if identkey else None
            ti_tc_uid = GpsTrackingUtilDB.tracker_cache.get(tcuid_identkey) if tcuid_identkey else None

            # Fallback op DB als niet in cache
            if not ti_identkey and identkey:
                ti_identkey = TrackerIdentifier.objects.filter(identkey=identkey).first()
            if not ti_tc_uid and tcuid_identkey:
                ti_tc_uid = TrackerIdentifier.objects.filter(identkey=tcuid_identkey).first()

            # CASE 1: identkey bestaat, tc_uid niet → voeg tc_uid toe
            if ti_identkey and not ti_tc_uid and tcuid_identkey:
                if not TrackerIdentifier.objects.filter(identkey=tcuid_identkey).exists():
                    logger.debug(f"Koppel nieuwe identifier {tcuid_identkey} aan bestaande tracker {ti_identkey.tracker.id}")
                    TrackerIdentifier.objects.create(
                            tracker=ti_identkey.tracker,
                            identifier_type=TrackerIdentifierType.objects.get(code="TCUID"),
                            external_id=tc_unique_id,
                    )

            # CASE 2: tc_uid bestaat, identkey niet → voeg identkey toe
            if ti_tc_uid and not ti_identkey and identkey:
                if not TrackerIdentifier.objects.filter(identkey=identkey).exists():
                    logger.debug(f"Koppel nieuwe identifier {identkey} aan bestaande tracker {ti_tc_uid.tracker.id}")
                    ti_identkey = TrackerIdentifier.objects.create(
                            tracker=ti_tc_uid.tracker,
                            identifier_type=TrackerIdentifierType.objects.get(code=identtype),
                            external_id=identid,
                    )
                else:
                    ti_identkey = TrackerIdentifier.objects.get(identkey=identkey)

            # CASE 3: Geen van beide bestaat → maak nieuwe tracker + identifiers
            if not ti_identkey and not ti_tc_uid and (identkey or tcuid_identkey):
                tracker = Tracker.objects.create()
                logger.debug(f"Aangemaakt nieuwe tracker {tracker.id} met identifiers: identkey={identkey}, tc_unique_id={tc_unique_id}")
                if identkey:
                    ti_identkey = TrackerIdentifier.objects.create(
                            tracker=tracker,
                            identifier_type=TrackerIdentifierType.objects.get(code=identtype),
                            external_id=identid,
                    )
                if tcuid_identkey:
                    TrackerIdentifier.objects.create(
                            tracker=tracker,
                            identifier_type=TrackerIdentifierType.objects.get(code="TCUID"),
                            external_id=tc_unique_id,
                    )

            return ti_identkey

        except TrackerIdentifierType.DoesNotExist:
            logger.warning(f"Onbekend identifier_type '{identtype}'")
        except Exception as e:
            logger.exception(f"Fout bij aanmaken of ophalen van TrackerIdentifier: {e}")

        return None

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

            tracker_identifier = GpsTrackingUtilDB.get_or_create_tracker_identifier(identity)
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

                if position:
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

                            else:
                                if existing.get("meta_timestamp") is None or existing["meta_timestamp"] <= meta_ts:
                                    existing[k] = v


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
            existing_trackers = {
                    tracker.id: tracker for tracker in Tracker.objects.filter(id__in=ids)
            }

            updated_trackers = []
            fields_per_tracker = {}
            valid_fields = {f.name for f in Tracker._meta.get_fields()}

            for item in trackers_items:
                tracker_id = item.get('id')
                tracker = existing_trackers.get(tracker_id)

                if not tracker:
                    logger.warning(f"Tracker met id {tracker_id} niet gevonden voor update.")
                    continue

                fields_changed = set()

                for key, value in item.items():
                    if key == 'id':
                        continue
                    if key not in valid_fields:
                        logger.warning(f"Ongeldig veld '{key}' genegeerd voor tracker {tracker_id}.")
                        continue

                    if key == 'screen_name':
                        if not tracker.screen_name or tracker.screen_name.strip() == '':
                            tracker.screen_name = value
                            fields_changed.add('screen_name')
                    elif key == 'icon':
                        if not tracker.icon or tracker.icon.strip() == '':
                            tracker.icon = value
                            fields_changed.add('icon')
                    else:
                        setattr(tracker, key, value)
                        fields_changed.add(key)

                if fields_changed:
                    updated_trackers.append(tracker)
                    fields_per_tracker[tracker.pk] = fields_changed

            # Groepeer per unieke veldenset
            from collections import defaultdict

            grouped = defaultdict(list)
            for tracker in updated_trackers:
                fieldset = frozenset(fields_per_tracker[tracker.pk])
                grouped[fieldset].append(tracker)

            # Bulk update per veldenset
            for fieldset, group in grouped.items():
                try:
                    Tracker.objects.bulk_update(group, fields=list(fieldset))
                    logger.info(f"{len(group)} tracker.trackers bulk-geüpdatet met velden: {fieldset}.")
                except Exception as e:
                    logger.exception(f"Fout bij bulk_update van trackers voor veldenset {fieldset}: {e}")
                    for t in group:
                        try:
                            t.save(update_fields=list(fieldset))
                        except Exception as ex:
                            logger.exception(f"Fout bij individuele save van tracker {t.id}: {ex}")
            logger.warning(f"Multiple tracker.trackers opgeslagen in de database. {round(time.time() - start, 3)}s")
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
