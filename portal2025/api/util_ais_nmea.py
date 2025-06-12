import time
import json
import socket
import threading
import os
from datetime import datetime, timedelta

from pyais import decode as pyais_decode
import utils.mqtt as TTSmqtt
from utils.logger import get_logger
from utils.gen_conv import genereer_hash, remap_keys, convert_speed, convert_enum_values

logger = get_logger(__name__)
MQTT_CLIENT = "proces:ais-nmea"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, 'mapping_ais_nmea.json'), 'r') as file:
    MAPPING = json.load(file)


class Nmea:
    def start():
        """
        Start de decoder door MQTT te subscriben én de server connectie op te zetten.
        """
        try:
            NmeaMqtt.subscribe("decoder", "ais/+/nmea")
        except Exception as e:
            logger.error(f"Fout bij MQTT subscribe: {e}")

        try:
            Nmea.connect('0.0.0.0', 6201, "in:ais-nmea_nl-deurne", "in/ais-nmea_nl-deurne/raw")
        except Exception as e:
            logger.error(f"Connectie mislukt: {e}")

    @staticmethod
    def connect(host, port, mqttclient, mqtttopic):
        """
        Start een server op één poort en detecteer automatisch of het TCP of UDP verkeer is.
        Ontvangen berichten worden geparsed en via MQTT gepubliceerd.

        Args:
            port (int): Poort waarop geluisterd wordt.
            host (str): IP adres om op te luisteren.
            mqttclient (str): MQTT client identifier.
            mqtttopic (str): MQTT topic waar berichten naartoe gestuurd worden.
        """

        def handle_udp():
            try:
                udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                udp_sock.bind((host, port))
                logger.debug(f"UDP server {mqttclient} luistert op {host}:{port}")
                while True:
                    data, addr = udp_sock.recvfrom(4096)
                    handle_data(data, addr[0], "UDP")
            except Exception as e:
                logger.error(f"Kan UDP niet starten op poort {port}: {e}")

        def handle_tcp():
            try:
                tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                tcp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                tcp_sock.bind((host, port))
                tcp_sock.listen(5)
                logger.debug(f"TCP server {mqttclient} luistert op {host}:{port}")
                while True:
                    conn, addr = tcp_sock.accept()
                    threading.Thread(target=handle_tcp_client, args=(conn, addr), daemon=True).start()
            except Exception as e:
                logger.error(f"Kan TCP niet starten op poort {port}: {e}")

        def handle_tcp_client(conn, addr):
            with conn:
                while True:
                    try:
                        data = conn.recv(4096)
                        if not data:
                            break
                        handle_data(data, addr[0], "TCP")
                    except Exception as e:
                        logger.error(f"TCP client {mqttclient} fout ({addr[0]}): {e}")
                        break

        def handle_data(data, ip, protocol):
            try:
                raw_text = data.decode("utf-8", errors="ignore").strip()
                if not raw_text:
                    return
                payload = {
                        "raw"     : raw_text,
                        "msgtype" : "ais-nmea",
                        "msghash" : genereer_hash(raw_text),
                        "received": int(time.time() * 1000),
                        "gateway" : ip
                }
                TTSmqtt.start_publisher(mqttclient, mqtttopic)(json.dumps(payload))
                logger.info(f"[{protocol}] Bericht ontvangen van {ip}: {raw_text}")
            except Exception as e:
                logger.error(f"Fout bij verwerken van bericht ({ip} via {protocol}): {e}")

        logger.info(f"Start UDP en TCP voor [[{mqttclient}]...")
        threading.Thread(target=handle_udp, daemon=True).start()
        threading.Thread(target=handle_tcp, daemon=True).start()
        logger.info(f"Connection [{mqttclient}] gestart op [{host}:{port}] voor zowel TCP als UDP")

    @staticmethod
    def process(payload):
        """
        Verwerk een payload met mogelijk meerdere NMEA-regels.

        NL: Controleert of er een 'nmea' veld aanwezig is in de payload, decodeert en remapt geldige NMEA-zinnen.
        EN: Checks if payload contains 'nmea' field, decodes and remaps valid NMEA sentences.

        Args:
            payload (dict): JSON payload met o.a. een 'nmea' sleutel.
        """
        raw_data = payload.get("raw")
        received = payload.get("received", int(time.time() * 1000))

        if not raw_data:
            logger.warning("Geen 'nmea'  data gevonden in payload.")
            return

        for msg in raw_data.splitlines():
            if "!" in msg and "*" in msg:
                try:
                    msg_type, decoded_payload = Nmea.decoder(msg)
                    if decoded_payload:

                        if msg_type in [6, 7, 11, 12, 13, 22]:
                            continue

                        if msg_type not in [1, 2, 3, 4, 5, 18, 19, 21]:
                            logger.info(f"{msg_type = } | {decoded_payload = }")

                        new_payload = Nmea.remap_payload(msg_type, decoded_payload, msg, payload, received)
                        if new_payload:
                            logger.debug(f"Bericht succesvol gedecodeerd: {new_payload.get('formated', {})}")
                            gateway = new_payload.get("gateway", "onbekend")
                                    NmeaMqtt.publish(MQTT_CLIENT, f"ais/{gateway}/processed", new_payload)
                        else:
                            logger.debug("Decoder returneerde geen payload na remapping.")
                    else:
                        logger.debug("Decoder returneerde geen payload.....")
                except Exception as e:
                    logger.warning(f"payload process error: {e}, {msg_type}, {decoded_payload}")

    @staticmethod
    def decoder(nmeamsg):
        """
        Decodeer een enkele NMEA-regel en converteer enums.

        NL: Decodeert een NMEA-zin naar een dictionary, converteert enums en snelheden.
        EN: Decodes a single NMEA sentence into dictionary, converts enums and speed values.

        Args:
            nmeamsg (str): De NMEA-zin die gedecodeerd moet worden.

        Returns:
            tuple: (msg_type, decoded_payload)
                - msg_type (int): AIS berichttype.
                - decoded_payload (dict): Gedecodeerde informatie met evt. extra tekstvelden.
        """
        try:
            data_decoded = pyais_decode(nmeamsg)
            data_dict = data_decoded.asdict()
            decoded = {}

            for key, value in data_dict.items():
                if isinstance(value, bytes):
                    value = value.hex()
                conv_value, conv_name = convert_enum_values(value)
                decoded[key] = conv_value
                if conv_name is not None:
                    decoded[f"{key}_txt"] = conv_name
                if "speed" in decoded:
                    value = decoded["speed"]
                    speed_converted = convert_speed(value, "kt")
                    if speed_converted:
                        decoded["speed_mps"] = speed_converted["m/s"]
                        decoded["speed_kph"] = speed_converted["km/h"]
                        decoded["speed_kt"] = speed_converted["kt"]
                    del decoded["speed"]

            return decoded.get("msg_type"), decoded

        except Exception as e:
            message = str(e)
            level = logger.debug if "Missing fragment numbers" in message else logger.error
            level(f"Fout bij decoderen van NMEA ({nmeamsg}): {e}", exc_info=True)
            return None, None

    @staticmethod
    def remap_payload(msg_type, decoded_payload, nmeamsg, payload, received):
        """
        Bouw nieuwe payload op met transformaties, mappings en tijdverwerking.

        NL: Zet de AIS data om naar TTS formaat incl. timestamps en hash.
        EN: Converts AIS data to TTS format including timestamps and hash.

        Args:
            msg_type (int): AIS berichttype.
            decoded_payload (dict): Gedecodeerde AIS-data.
            nmeamsg (str): Originele NMEA-string.
            payload (dict): Binnengekomen oorspronkelijke payload.
            received (int): Tijdstip van ontvangst in ms sinds epoch.

        Returns:
            dict or None: Nieuwe payload of None bij fout.
        """

        def correct_timestamp(base_dt, reference_dt):
            if base_dt > reference_dt:
                base_dt -= timedelta(minutes=1)
            return int(base_dt.timestamp() * 1000)

        try:
            msg_key = f"ais{msg_type:02}"
            convert_payload = {msg_key: decoded_payload}
            formated = remap_keys(convert_payload, MAPPING)
            received_dt = datetime.fromtimestamp(received / 1000)
            if not formated:
                return None
        except Exception as e:
            logger.error(f"Fout bij converteren decoded_payload: {e}", exc_info=True)
            return None

        try:
            if msg_type in [1, 2, 3, 4, 9, 18, 19, 21]:
                position_dt = datetime(
                        year=decoded_payload.get("year", received_dt.year),
                        month=decoded_payload.get("month", received_dt.month),
                        day=decoded_payload.get("day", received_dt.day),
                        hour=decoded_payload.get("hour", received_dt.hour),
                        minute=decoded_payload.get("minute", received_dt.minute),
                        second=decoded_payload.get("second", received_dt.second),
                )
                formated["position_timestamp"] = correct_timestamp(position_dt, received_dt)

            elif msg_type == 5:
                eta_dt = datetime(
                        year=decoded_payload.get("year", received_dt.year),
                        month=decoded_payload.get("month", received_dt.month),
                        day=decoded_payload.get("day", received_dt.day),
                        hour=decoded_payload.get("hour", received_dt.hour),
                        minute=decoded_payload.get("minute", received_dt.minute),
                        second=decoded_payload.get("second", received_dt.second),
                )
                formated["eta_timestamp"] = correct_timestamp(eta_dt, received_dt)

        except Exception as e:
            message = str(e)
            level = logger.debug if any(x in message for x in ["out of range", "must be in"]) else logger.error
            level(f"Fout bij verwerken van datum: {e}", exc_info=True)

        try:
            return {
                    **payload,
                    "nmea"    : nmeamsg,
                    "msghash" : genereer_hash(nmeamsg),
                    "data"    : {msg_key: decoded_payload},
                    "formated": formated,
            }

        except Exception as e:
            logger.error(f"Fout bij opbouwen nieuwe payload: {e}", exc_info=True)
            return None


class NmeaMqtt:
    @staticmethod
    def subscribe(client, topic):
        """
        Start een MQTT-subscriber op het opgegeven topic.

        NL: Start een MQTT-abonnement en koppel de on_message functie.
        EN: Starts MQTT subscription and attaches on_message callback.

        Args:
            client (str): Naam van de client (bijv. 'ais').
            topic (str): MQTT-topic waarop geluisterd moet worden.
        """
        subscriber = TTSmqtt.start_subscriber(f"{MQTT_CLIENT}_{client}", topic)
        if subscriber:
            subscriber.on_message = NmeaMqtt.custom_on_message
            logger.warning(f"Subscriber gestart op topic: {topic}")
        else:
            logger.error(f"Kon geen subscriber starten op topic: {topic}")

    @staticmethod
    def custom_on_message(client, userdata, message):
        """
        Callback bij binnenkomend MQTT-bericht.

        NL: Ontvangt MQTT-bericht, decodeert JSON en stuurt door naar NMEA processor.
        EN: Receives MQTT message, decodes JSON and sends to NMEA processor.

        Args:
            client: MQTT client object.
            userdata: Gebruikersdata (meestal None).
            message: Binnenkomend MQTT bericht object.
        """
        try:
            payload = json.loads(message.payload.decode("utf-8"))
            payload["received"] = payload.get("received", int(time.time() * 1000))
            logger.debug(f"Ontvangen bericht: {payload}")
            Nmea.process(payload)

        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error: {e} - Payload: {message.payload.decode('utf-8')}")
        except Exception as e:
            logger.exception(f"Onverwachte fout in on_message: {e}")

    @staticmethod
    def publish(client, topic, payload):
        """
        Publiceer een bericht naar een MQTT-topic.

        NL: Stuurt een JSON payload naar een MQTT-topic.
        EN: Sends a JSON payload to an MQTT topic.

        Args:
            client (str): Naam van de client.
            topic (str): MQTT-topic waar het bericht heen moet.
            payload (dict): Te verzenden payload (JSON).
        """
        publisher = TTSmqtt.start_publisher(f"{MQTT_CLIENT}_{client}", topic)
        if publisher:
            try:
                publisher(json.dumps(payload))
                logger.info(f"Bericht gepubliceerd op {topic}: {payload}")
            except Exception as e:
                logger.error(f"Fout bij publiceren op {topic}: {e}")
        else:
            logger.error(f"Kan geen publisher starten voor {client} op {topic}")