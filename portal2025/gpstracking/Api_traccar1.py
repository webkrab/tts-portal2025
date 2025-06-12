import json
import time
import urllib.parse
import requests
import threading
import websocket

from utils.gen_conv import convert_speed, flatten_multilevel, remap_keys, genereer_hash, convert_to_unixtimestamp
from gpstracking.models import TrackerDecoder, TrackerIdentifierType
from gpstracking.utils_geotracker import get_decoder_mapping, update_mapping_if_missing
from utils.logger import get_logger
import utils.mqtt as TTSmqtt

logger = get_logger(__name__)

# ======================
# 🔧 Configuratie
# ======================
SERVERID = "1"
TRACCAR_URL = f"{SERVERID}.lifeguardtracking.nl:8082"
EMAIL = "fred@thetrackingsolution.nl"
PASSWORD = "doemaarwat"


MQTT_CLIENT = f"proces:TC{SERVERID}"
MQTT_TOPIC = f"IN/TC/LT{SERVERID}"


class Traccar:
    """
    Traccar-client voor ophalen en verwerken van GPS-tracking data via REST en WebSocket.
    """
    def __init__(self):
        # Bind deze instance als globale referentie voor fallback of debug
        global traccar_client
        traccar_client = self

        self.ws = None
        self.ws_thread = None
        self.session_key = None
        self.MAPPING_STN = {}
        self.IDENTTYPE = TrackerIdentifierType.objects.all()

    def start(self):
        # Start de subscribe-thread met instance callback
        threading.Thread(
            target=TcMqtt.subscribe,
            args=(f"{MQTT_CLIENT}-process", MQTT_TOPIC, self._on_mqtt_message),
            daemon=True
        ).start()
        # Start de herstart-timer voor Traccar
        threading.Thread(target=self.restart_loop, daemon=True).start()

    def restart_loop(self):
        while True:
            try:
                logger.info(f"Refresh data van TC{SERVERID}...")
                self.session_key = self.get_session_key(EMAIL, PASSWORD, TRACCAR_URL)
                if not self.session_key:
                    logger.error("Kan geen sessie opzetten, afsluiten.")

                # 🔄 Devices ophalen via REST API en verwerken
                self.fetch_devices_via_api(self.session_key)
                if self.ws:
                    self.ws.close()
                    self.ws_thread.join()
                logger.info("Traccar client starting...")
                self.connect_websocket()
            except Exception as e:
                logger.error(f"Fout bij (her)start van WebSocket: {e}")
            time.sleep(5 * 60)

    def get_session_key(self, email, password, url):
        login_url = f'http://{url}/api/session'
        params = urllib.parse.urlencode({'email': email, 'password': password})
        headers = {'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'}

        with requests.Session() as session:
            response = session.post(login_url, data=params, headers=headers)
            if response.status_code == 200:
                return session.cookies.get_dict().get('JSESSIONID')
            logger.error(f"Login mislukt: {response.status_code} - {response.text}")
            return None

    def fetch_devices_via_api(self, session_key):
        url = f"http://{TRACCAR_URL}/api/devices"
        headers = {'Cookie': f'JSESSIONID={session_key}'}

        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            devices = response.json()
            message = {"devices": devices}
            rawmessage = {"raw": message, "received": int(time.time() * 1000)}
            TcMqtt.publish(f"{MQTT_CLIENT}-raw", MQTT_TOPIC, rawmessage)
            logger.info(f"{len(devices)} devices opgehaald en verwerkt via API.")
        except Exception as e:
            logger.error(f"Exception bij ophalen devices: {e}")

    ### Websocket
    def connect_websocket(self):
        ws_url = f"ws://{TRACCAR_URL}/api/socket"
        headers = {'Cookie': f'JSESSIONID={self.session_key}'}

        self.ws = websocket.WebSocketApp(
            ws_url,
            header=headers,
            on_open=self.on_open,
            on_message=self.on_ws_message,
            on_error=self.on_error,
            on_close=self.on_close
        )

        def run_ws():
            logger.info("WebSocket verbinding starten...")
            self.ws.run_forever()

        self.ws_thread = threading.Thread(target=run_ws, daemon=True)
        self.ws_thread.start()

    def on_open(self, ws):
        logger.info("WebSocket verbonden")

    def on_close(self, ws, code, msg):
        logger.info(f"WebSocket verbroken: {code} - {msg}")

    def on_error(self, ws, error):
        logger.error(f"WebSocket Fout: {error}")

    def on_ws_message(self, ws, message):
        data = json.loads(message)
        rawmessage = {"raw": data, "received": int(time.time() * 1000)}
        TcMqtt.publish(f"{MQTT_CLIENT}-raw", MQTT_TOPIC, rawmessage)

    def _on_mqtt_message(self, client, userdata, message):
        try:
            payload = json.loads(message.payload.decode('utf-8'))
            logger.info(f"Ontvangen bericht op topic '{message.topic}': {payload}")
            self.process(payload)
        except Exception as e:
            logger.error(f"Onverwachte fout in on_message: {e}", exc_info=True)

    def process(self, message):
        try:
            msgdata = message if isinstance(message, dict) else json.loads(message)
            data = msgdata.get('raw')
            received = msgdata.get('received')
            if not data or received is None:
                logger.error(f"Geen RAW data beschikbaar: {message}")
                return

            for msgtype, items in data.items():
                key = f"TC_{msgtype}"
                if isinstance(items, list):
                    for item in items:
                        self._handle_item(key, item, received)
        except Exception as e:
            logger.error(f"JSON Fout: {e} - Inhoud: {message}")

    def _handle_item(self, msgtype, item, received_ts):
        device_id = item.get('deviceId') or item.get('id')
        logger.debug(f"[device_id={device_id}] Bericht ontvangen van type '{msgtype}'")
        try:
            input_message = {
                'raw': item,
                'msgtype': msgtype,
                'msghash': genereer_hash(json.dumps(item)),
                'received': received_ts,
                'gateway': f"lt{SERVERID}",
                'identtype': f"TC{SERVERID}"
            }
            self.decoder(input_message)
        except Exception as e:
            logger.error(f"Error handling item: {e} – {item}")

    def decoder(self, mqttdata):
        try:
            rawdata = mqttdata.get('raw', {})
            msgtype = mqttdata.get('msgtype')
            identtype = mqttdata.get('identtype')

            if not all([rawdata, msgtype, identtype]):
                logger.warning("Ontbrekende velden in MQTT bericht")
                return

            if msgtype == "TC_positions":
                identid = f'{rawdata.get("deviceId")}'
            elif msgtype == "TC_devices":
                identid = f'{rawdata.get("id")}'
            elif msgtype == "TC_events":
                identid = f'{rawdata.get("deviceId")}'
            else:
                logger.warning(f"{msgtype} kent geen identid logica")
                identid = None

            identity = {
                'identkey': f"{identtype}_{identid}",
                'identtype': identtype,
                'identid': identid
            }

            if "protocol" in rawdata:
                msgtype = f'{msgtype}_{rawdata["protocol"]}'

            flat_data = flatten_multilevel(rawdata, prefix='')
            identity["tcUniqueId"] = flat_data.get("uniqueId")

            for ts_field in ('lastUpdate', 'serverTime', 'deviceTime', 'fixTime'):
                flat_data[f"{ts_field}Ms"] = convert_to_unixtimestamp(flat_data.get(ts_field))

            if 'speed' in flat_data:
                flat_data['speeds'] = convert_speed(flat_data['speed'], 'kt')

            mapping = get_decoder_mapping(self, identtype, msgtype)
            stdata, missing = remap_keys(flat_data, mapping)
            if missing:
                update_mapping_if_missing(self, identtype, msgtype, missing)
            stdata = {k: v for k, v in stdata.items() if v is not None}
            if not stdata:
                logger.error(f"Geen st_data mapping voor type: {msgtype}")
                return

            mqttdata.update({
                'identity': identity,
                'data': stdata,
                'msghash': genereer_hash(json.dumps(stdata)),
                'msgtype': msgtype
            })
            self.sender(mqttdata)
        except Exception as e:
            logger.error(f"Fout bij decoderen: {e} {mqttdata}")

    def sender(self, mqttdata):
        TcMqtt.publish(f"{MQTT_CLIENT}-save", "process/gpstracking", mqttdata)


class TcMqtt:
    @staticmethod
    def subscribe(client_name, topic, on_message_callback):
        subscriber = TTSmqtt.start_subscriber(client_name, topic)
        if not subscriber:
            logger.error(f"Kon geen subscriber {client_name} starten op topic: {topic}")
            return
        subscriber.on_message = TcMqtt.custom_on_message
        # Zorg dat de netwerk-loop draait
        if hasattr(subscriber, 'loop_start'):
            subscriber.loop_start()
        elif hasattr(subscriber, 'loop_forever'):
            threading.Thread(target=subscriber.loop_forever, daemon=True).start()
        logger.warning(f"Subscriber {client_name} gestart op topic: {topic}")


    @staticmethod
    def custom_on_message(client, userdata, message):
        try:
            payload = json.loads(message.payload.decode("utf-8"))
            logger.debug(f"Ontvangen bericht op topic '{message.topic}': {payload}")
            if payload:
                traccar_client.process(payload)
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error: {e} - topic: {message.topic} Payload: {message.payload.decode('utf-8')}")
        except Exception as e:
            logger.exception(f"Onverwachte fout in on_message: {e}")

    @staticmethod
    def publish(client_name, topic, payload):
        try:
            publisher = TTSmqtt.start_publisher(client_name, topic)
            if not publisher:
                logger.error(f"Kan geen publisher starten voor {client_name} op {topic}")
                return
            publisher(json.dumps(payload))
            logger.debug(f"Bericht gepubliceerd op {topic}: {payload}")
        except Exception as e:
            logger.error(f"Publish {client_name} error {e}")

def start():
    # Initialiseer en start de Traccar-client bij module-import
    traccar_client = Traccar()
    traccar_client.start()
