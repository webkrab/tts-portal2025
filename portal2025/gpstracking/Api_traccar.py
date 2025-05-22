import json
import time
import urllib.parse
import requests
import websocket

from utils.gen_conv import convert_speed, flatten_multilevel, remap_keys, genereer_hash, convert_to_unixtimestamp
from gpstracking.models import TrackerDecoder, TrackerIdentifierType
from utils.logger import get_logger
from gpstracking.utils_geotracker import get_decoder_mapping, update_mapping_if_missing
from django.contrib.gis.geos import MultiPolygon, Polygon, Point

from gpstracking.util_db import GpsTrackingUtilDB

logger = get_logger(__name__)

# ======================
# 🔧 Configuratie
# ======================
TRACCAR_URL = "1.lifeguardtracking.nl:8082"
EMAIL = "django-Cellular"
PASSWORD = "django-Cellular"


class Traccar:
    def __init__(self):
        self.ws = None
        self.MAPPING_STN = {}  # ✅ Now it's an instance attribute
        self.IDENTTYPE = TrackerIdentifierType.objects.all()


    def start(self):
        print("🚀 Traccar client starting...")
        session_key = get_session_key(EMAIL, PASSWORD, TRACCAR_URL)
        if not session_key:
            logger.error("❌ Kan geen sessie opzetten, afsluiten.")
            return

        ws_url = f"ws://{TRACCAR_URL}/api/socket"
        headers = {'Cookie': f'JSESSIONID={session_key}'}

        self.ws = websocket.WebSocketApp(
            ws_url,
            header=headers,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close
        )

        print("🌐 Verbinden met WebSocket...")
        self.ws.run_forever()

    def on_open(self, ws):
        print("✅ WebSocket verbonden")

    def on_close(self, ws, code, msg):
        print(f"❌ WebSocket verbroken: {code} - {msg}")

    def on_error(self, ws, error):
        print(f"[WebSocket Fout]: {error}")

    def on_message(self, ws, message):
        self.process(message)

    def process(self, message):
        try:
            if isinstance(message, str):
                data = json.loads(message)
            elif isinstance(message, dict):
                data = message
            else:
                logger.warning(f"⚠️ Onverwacht berichttype: {type(message)}")
                return

            for msgtype, items in data.items():
                if isinstance(items, list):
                    for item in items:
                        input_message = {
                            "raw": item,
                            "msgtype": msgtype,
                            "msghash": genereer_hash(json.dumps(item)),
                            "received": int(time.time() * 1000),
                            "gateway": "lt1",
                            "identtype": "TC1"
                        }
                        self.decoder(input_message)

        except Exception as e:
            logger.error(f"[JSON Fout]: {e} - Inhoud: {message}")

    def decoder(self, mqttdata):
        """Decodeert en verwerkt één MQTT bericht"""
        rawdata = mqttdata.get("raw", {})
        msgtype = mqttdata.get("msgtype")
        identtype = mqttdata.get("identtype", None)
        print()

        if not all([rawdata, msgtype, identtype]):
            logger.warning("Ontbrekende velden in MQTT bericht")
            return

        if msgtype == "positions":
            identid = f'{rawdata.get("deviceId")}'
        elif msgtype == "devices":
            identid = f'{rawdata.get("id")}'
        else:
            logger.warning(f"{msgtype} kent geen decoder")
            return

        identity = {"identkey": f"{identtype}_{identid}",
                    "identtype": identtype,
                    "identid": identid
                    }


        if "protocol" in rawdata:
            msgtype = f'{msgtype}_{rawdata["protocol"]}'

        flat_data = flatten_multilevel(rawdata, prefix='')
        flat_data["lastUpdateMs"] = convert_to_unixtimestamp(flat_data.get("lastUpdate", None))
        flat_data["serverTimeMs"] = convert_to_unixtimestamp(flat_data.get("serverTime", None))
        flat_data["deviceTimeMs"] = convert_to_unixtimestamp(flat_data.get("deviceTime", None))
        flat_data["fixTimeMs"] = convert_to_unixtimestamp(flat_data.get("fixTime", None))
        flat_data["speeds"] = convert_speed(flat_data.get("speed", 0.0), "kt")

        mapping = get_decoder_mapping(self, identtype, msgtype)
        stdata, missing = remap_keys(flat_data, mapping)
        if missing:
            print("missing")
            update_mapping_if_missing(self, identtype, msgtype, missing)

        if not stdata:
            logger.error(f"Geen st_data mapping voor type: {msgtype} | {flat_data}")
            return

        if str(identid) == str(539):
            print("flat", flat_data, "\n stn", stdata )

        msghash = genereer_hash(json.dumps(stdata))
        mqttdata["identity"] = identity
        mqttdata["msgtype"] = msgtype
        mqttdata["data"] = stdata
        mqttdata["msghash"] = msghash

        self.sender(mqttdata)

    def sender(self, mqttdata):
        #logger.info(f"📤 Verzenden naar opslag: {json.dumps(mqttdata, indent=4)}\n=========================================================")
        # Implement actual sending logic here
        GpsTrackingUtilDB.process_mqtt_message(json.dumps(mqttdata))


def get_session_key(email, password, url):
    login_url = f'http://{url}/api/session'
    params = urllib.parse.urlencode({'email': email, 'password': password})
    headers = {'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'}

    with requests.Session() as session:
        response = session.post(login_url, data=params, headers=headers)
        if response.status_code == 200:
            cookies = session.cookies.get_dict()
            return cookies.get('JSESSIONID')
        print(f"[ERROR] Login mislukt: {response.status_code} - {response.text}")
        return None
