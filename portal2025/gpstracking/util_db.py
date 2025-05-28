import json
import threading
import time
from uuid import UUID
from queue import Queue, Empty
from collections import defaultdict

from django.contrib.gis.geos import Point
from django.db import IntegrityError

from gpstracking.models import (
    Tracker,
    TrackerIdentifier,
    TrackerIdentifierType,
    TrackerMessage,
    TrackerDecoderField,
    TrackerGroup
)
from utils.gen_conv import remap_keys
from utils.logger import get_logger
import utils.mqtt as TTSmqtt

logger = get_logger(__name__)


class GpsTrackingUtilDB:
    """
    Hulpmodule voor het verwerken van GPS-tracker MQTT berichten en synchronisatie met de database.
    """

    MQTT_TOPIC = "process/gpstracking"
    MQTT_CLIENT_NAME = "gpstracking_TrackerMessage"
    CACHE_REFRESH_INTERVAL = 60
    SAVE_INTERVAL = 15

    tracker_cache: dict[str, TrackerIdentifier] = {}
    tracker_buffer: dict[UUID, dict] = {}
    buffer_lock_tracker = threading.Lock()
    _mapping_cache: dict = None

    shutdown_event = threading.Event()
    message_queue = Queue()


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
        """
        Maakt een nieuwe `TrackerIdentifier` aan voor een tracker.
        Als deze al bestaat voor dezelfde combinatie van tracker en type, wordt de bestaande teruggegeven.

        Args:
            tracker (Tracker): Het trackerobject waarvoor de identifier wordt aangemaakt.
            code (str): De identifier type code, bv. 'ICAO', 'TCUID'.
            external_id (str): De externe identifier (bijv. ICAO-code).

        Returns:
            TrackerIdentifier: Het nieuw aangemaakte of reeds bestaande identificatieobject.
        """
        try:
            identifier_type = TrackerIdentifierType.objects.get(code=code)
            return TrackerIdentifier.objects.create(
                tracker=tracker,
                identifier_type=identifier_type,
                external_id=external_id,
            )
        except IntegrityError:
            logger.warning(
                f"Identifier '{external_id}' voor tracker '{tracker.id}|{tracker.custom_name}' van type '{code}' bestaat al. Probeer bestaande op te halen."
            )
            return TrackerIdentifier.objects.filter(
                tracker=tracker,
                identifier_type=identifier_type,
            ).first()

    @staticmethod
    def additional_identifiers_from_uniqueId(uniqueId: str, tracker):
        uniqueId = uniqueId.replace("ADSB", "ICAO")
        tc_split = uniqueId.split("-", 1)
        if len(tc_split) > 1:
            prefix, ident = tc_split
            if prefix in ["ICAO", "MMSI", "DMR", "GMS"]:
                if prefix == "DMR":
                    prefix = "DMR_RN"
                if not GpsTrackingUtilDB.find_tracker_identifier_by_identkey(f"{prefix}_{ident}"):
                    GpsTrackingUtilDB.create_tracker_identifier(tracker, prefix, ident)

    @staticmethod
    def tc_group_management(tc_group, identifier_type, tracker):  #TODO opruimen naar migratie
        if tracker.groups.exists():  # correcte check
            return

        convert_table = {
            "ZZ_TEMP_TC1_6" : "camende",
            "ZZ_TEMP_TC1_7" : "fwater",
            "ZZ_TEMP_TC1_8" : "bheijselaar",
            "ZZ_TEMP_TC1_10": "rb_wsc",
            "ZZ_TEMP_TC1_36": "rb_hsk",
            "ZZ_TEMP_TC1_42": "essn",
            "ZZ_TEMP_TC1_45": "rb_rkj",
            "ZZ_TEMP_TC1_47": "demo",
            "ZZ_TEMP_TC1_48": "rb_dhg",
            "ZZ_TEMP_TC1_51": "rb_msr",
            "ZZ_TEMP_TC1_52": "knrm_teh",
            "ZZ_TEMP_TC1_53": "knrm_sch",
            "ZZ_TEMP_TC1_56": "rb_hhw",
            "ZZ_TEMP_TC1_58": "rb_rkj_mob",
            "ZZ_TEMP_TC1_60": "rb_gve",
            "ZZ_TEMP_TC1_69": "rb_waz",
            "ZZ_TEMP_TC1_67": "vr_22_zld",

            "ZZ_TEMP_TC2_6" : "camende",
            "ZZ_TEMP_TC2_7" : "fwater",
            "ZZ_TEMP_TC2_8" : "bheijselaar",
            "ZZ_TEMP_TC2_10": "rb_wsc",
            "ZZ_TEMP_TC2_36": "rb_hsk",
            "ZZ_TEMP_TC2_42": "essn",
            "ZZ_TEMP_TC2_45": "rb_rkj",
            "ZZ_TEMP_TC2_47": "demo",
            "ZZ_TEMP_TC2_48": "rb_dhg",
            "ZZ_TEMP_TC2_51": "rb_msr",
            "ZZ_TEMP_TC2_52": "knrm_teh",
            "ZZ_TEMP_TC2_53": "knrm_sch",
            "ZZ_TEMP_TC2_56": "rb_hhw",
            "ZZ_TEMP_TC2_58": "rb_rkj_mob",
            "ZZ_TEMP_TC2_60": "rb_gve",
            "ZZ_TEMP_TC2_65": "rb_waz",


        }

        name = f'ZZ_TEMP_{identifier_type}_{tc_group}'
        if name in convert_table:
            tc_group = convert_table[name]

        tracker_group, created = TrackerGroup.objects.get_or_create(
                smartcode=str(tc_group),
                defaults={
                        'name'          : name,
                        'area'          : None,
                        'visible_fields': {},
                }
        )
        tracker.groups.add(tracker_group)
       # tracker.save()
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
                while not GpsTrackingUtilDB.shutdown_event.is_set():
                    time.sleep(GpsTrackingUtilDB.CACHE_REFRESH_INTERVAL)
                    GpsTrackingUtilDB.refresh_tracker_cache()
        threading.Thread(target=loop, daemon=True).start()
        GpsTrackingUtilDB.refresh_tracker_cache()

    # === IDENTIFIER LOGICA ===

    @staticmethod
    def get_or_create_tracker_identifier(identity, tc_group) -> TrackerIdentifier | None:
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

            tracker = None

            if ti_identkey and not ti_tc_uid and tcuid_identkey:
                tracker = ti_identkey.tracker
                GpsTrackingUtilDB.create_tracker_identifier(tracker, "TCUID", tc_unique_id)
                GpsTrackingUtilDB.additional_identifiers_from_uniqueId(tc_unique_id, tracker)

            elif ti_tc_uid and not ti_identkey and identkey:
                tracker = ti_tc_uid.tracker
                ti_identkey = GpsTrackingUtilDB.create_tracker_identifier(tracker, identtype, identid)

            elif not ti_identkey and not ti_tc_uid and (identkey or tcuid_identkey):
                tracker = Tracker.objects.create()
                logger.debug(f"Aangemaakt nieuwe tracker {tracker.id}")
                if identkey:
                    ti_identkey = GpsTrackingUtilDB.create_tracker_identifier(tracker, identtype, identid)
                if tcuid_identkey:
                    GpsTrackingUtilDB.create_tracker_identifier(tracker, "TCUID", tc_unique_id)
                    GpsTrackingUtilDB.additional_identifiers_from_uniqueId(tc_unique_id, tracker)
            else:
                tracker = ti_identkey.tracker
            if tracker and tc_group:  # TODO opruimen
                GpsTrackingUtilDB.tc_group_management(tc_group, identtype, tracker)

            return ti_identkey

        except TrackerIdentifierType.DoesNotExist:
            logger.warning(f"Onbekend identifier_type '{identtype}'")
        except Exception as e:
            logger.exception(f"Fout bij aanmaken of ophalen van TrackerIdentifier: {e}")

        return None

    # === VERWERKING ===

    @staticmethod
    def process_mqtt_message(message_str: str):
        """
        Verwerkt een MQTT-bericht en voegt deze toe aan de buffer.

        Args:
            message_str (str): JSON-string van het MQTT-bericht
        """
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
            tracker_identifier = GpsTrackingUtilDB.get_or_create_tracker_identifier(identity, data.get('tc_group',None))
            if not tracker_identifier:
                return

            msg_entry = {
                "tracker_identifier": tracker_identifier,
                "msgtype": msgtype,
                "content": data,
                "raw": raw,
                "dbcall": formated,
                "message_timestamp": received,
                "sha256_key": msghash,
            }

            try:
                position = Point(float(data['longitude']), float(data['latitude']))
                msg_entry["position"] = position
                position_timestamp = formated.get("position_timestamp", None)
                if position_timestamp:
                    msg_entry["position_timestamp"] = position_timestamp
            except (KeyError, ValueError):
                pass

            GpsTrackingUtilDB.message_queue.put(msg_entry)

            if formated:
                tracker_entry = formated.copy()
                if "position" in msg_entry:
                    tracker_entry["position"] = msg_entry["position"]

                tid = tracker_identifier.tracker.id
                position_ts = tracker_entry.get("position_timestamp") or received
                meta_ts = tracker_entry.get("meta_timestamp") or received

                with GpsTrackingUtilDB.buffer_lock_tracker:
                    existing = GpsTrackingUtilDB.tracker_buffer.get(tid)
                    if not existing:
                        tracker_entry['id'] = tid
                        GpsTrackingUtilDB.tracker_buffer[tid] = tracker_entry
                        print("new", GpsTrackingUtilDB.tracker_buffer[tid])
                    else:
                        for k, v in tracker_entry.items():
                            if k not in existing or (
                                k in ["altitude", "speed", "course", "position", "position_timestamp"]
                                and (existing.get("position_timestamp") is None or existing["position_timestamp"] <= position_ts)
                            ) or (
                                existing.get("meta_timestamp") is None or existing["meta_timestamp"] <= meta_ts
                            ):
                                existing[k] = v
                        GpsTrackingUtilDB.tracker_buffer[tid] = existing
                        print("update", GpsTrackingUtilDB.tracker_buffer[tid])


        except Exception as e:
            logger.exception(f"Fout bij verwerken van MQTT bericht: {e}")

    @staticmethod
    def save_buffer_to_db():
        start = time.time()

        # === TrackerMessages verwerken ===
        msg_items = []
        while not GpsTrackingUtilDB.message_queue.empty():
            try:
                msg_items.append(GpsTrackingUtilDB.message_queue.get_nowait())
            except Empty:
                break

        if msg_items:
            TrackerMessage.objects.bulk_create(
                    [TrackerMessage(**item) for item in msg_items],
                    ignore_conflicts=True
            )
            logger.info(f"{len(msg_items)} tracker.messages opgeslagen ({round(time.time() - start, 3)}s)")
        else:
            logger.error("0 tracker.messages opgeslagen")

        start = time.time()

        # === Tracker status-updates verwerken ===
        with GpsTrackingUtilDB.buffer_lock_tracker:
            tracker_items = list(GpsTrackingUtilDB.tracker_buffer.values())
            GpsTrackingUtilDB.tracker_buffer.clear()

        if tracker_items:
            ids = [item['id'] for item in tracker_items]
            existing_trackers = {t.id: t for t in Tracker.objects.filter(id__in=ids)}
            updated_trackers = []
            fields_per_tracker = {}
            valid_fields = {f.name for f in Tracker._meta.get_fields()}

            # Log ontbrekende trackers
            missing_ids = set(ids) - existing_trackers.keys()
            if missing_ids:
                logger.warning(f"{len(missing_ids)} tracker.ids niet gevonden in DB: {missing_ids}")

            for item in tracker_items:
                tid = item['id']
                tracker = existing_trackers.get(tid)
                if not tracker:
                    continue

                changed = set()
                for key, val in item.items():
                    if key == 'id' or key not in valid_fields:
                        continue

                    # Skip None en lege strings (voor strings)
                    if val is None or (isinstance(val, str) and val.strip() == ""):
                        continue

                    current_value = getattr(tracker, key)

                    if key in ['custom_name', 'icon']:
                        if current_value is None or str(current_value).strip() == "":
                            setattr(tracker, key, val)
                            changed.add(key)
                    else:
                        if current_value != val:
                            setattr(tracker, key, val)
                            changed.add(key)

                if changed:
                    updated_trackers.append(tracker)
                    fields_per_tracker[tracker.pk] = changed

            # === Groepeer per unieke veldenset ===
            grouped = defaultdict(list)
            for tracker in updated_trackers:
                fieldset = frozenset(fields_per_tracker[tracker.pk])
                grouped[fieldset].append(tracker)

            # === Bulk update per veldenset ===
            for fieldset, group in grouped.items():
                try:
                    Tracker.objects.bulk_update(group, list(fieldset))
                    logger.debug(f"{len(group)} tracker.trackers bulk-geüpdatet met velden: {list(fieldset)}.")
                except Exception as e:
                    logger.exception(f"Fout bij bulk_update van trackers voor veldenset {fieldset}: {e}")
                    for t in group:
                        try:
                            t.save(update_fields=list(fieldset))
                        except Exception as ex:
                            logger.exception(f"Fout bij individuele save van tracker {t.id}: {ex}")

            logger.info(f"{len(updated_trackers)} tracker.tracker opgeslagen ({round(time.time() - start, 3)}s)")
        else:
            logger.error("0 tracker.trackers opgeslagen")

    @staticmethod
    def start_save_loop():
        """
        Start een achtergrondproces dat periodiek de buffers opslaat in de database.
        """
        def loop():
            while True:
                while not GpsTrackingUtilDB.shutdown_event.is_set():
                    time.sleep(GpsTrackingUtilDB.SAVE_INTERVAL)
                    GpsTrackingUtilDB.save_buffer_to_db()
        threading.Thread(target=loop, daemon=True).start()
        logger.info(f"Start buffer schrijf-loop elke {GpsTrackingUtilDB.SAVE_INTERVAL} seconden.")

    @staticmethod
    def start_mqtt_subscriber():
        """
        Start de MQTT-subscriber en koppel het berichtverwerkingssysteem.
        """
        client = TTSmqtt.start_subscriber(GpsTrackingUtilDB.MQTT_CLIENT_NAME, GpsTrackingUtilDB.MQTT_TOPIC)
        if not client:
            logger.error("Kon MQTT-subscriber niet starten.")
            return
        client.on_message = lambda c, u, m: GpsTrackingUtilDB.process_mqtt_message(m.payload.decode("utf-8"))
        logger.info("MQTT-subscriber actief.")
        GpsTrackingUtilDB.start_save_loop()
        GpsTrackingUtilDB.start_tracker_cache_loop()
