import time
import json
import socket
import threading
import os
from datetime import datetime, timedelta

from pyais import decode as pyais_decode
import utils.mqtt as TTSmqtt
from utils.logger import get_logger
from utils.gen_conv import genereer_hash, remap_keys, convert_speed, convert_enum_values, flatten_multilevel

import asyncio
import websockets
import json
import threading
import time
import logging


logger = get_logger(__name__)
MQTT_CLIENT = "proces:ais-aisstream"

with open(r'mapping_ais_aisstream.json', 'r') as file:
    MAPPING = json.load(file)


# mapping = "nmea-element" : "TTS element"

class Aisstream:
    def start():
        """
        Start de decoder door MQTT te subscriben én de server connectie op te zetten.
        """
        try:
            AisstreamMqtt.subscribe("decoder", "ais/+/aisstream")
        except Exception as e:
            logger.error(f"Fout bij MQTT subscribe: {e}")

        try:
            Aisstream.connect('0.0.0.0', 6201, "in:ais-aisstream", "in/ais-aisstream/raw")
        except Exception as e:
            logger.error(f"Connectie mislukt: {e}")

    def connect(AStoken, mqttclient, mqtttopic):
        """
        Verbindt met AISstream via WebSocket en publiceert ontvangen berichten via MQTT.

        Args:
            AStoken (str): API-token voor AISstream.
            mqttclient (str): MQTT client identifier.
            mqtttopic (str): MQTT topic waar berichten naartoe gestuurd worden.
        """

        def handle_websocket():
            async def websocket_loop():
                uri = "wss://stream.aisstream.io/v0/stream"
                subscribe_message = {
                    "APIKey": AStoken,
                    "BoundingBoxes": [[[-90, -180], [90, 180]]]
                }

                try:
                    async with websockets.connect(uri) as websocket:
                        await websocket.send(json.dumps(subscribe_message))
                        logger.info(f"WebSocket verbonden voor [{mqttclient}]")

                        async for message in websocket:
                            try:
                                received = int(time.time() * 1000)
                                payload = {
                                    "raw": json.dumps(message),
                                    "received": received,
                                    "msgtype": "ais-aissstream",
                                    "msghash": geneeer_hash(message),
                                    "gateway": mqttclient
                                }
                                TTSmqtt.start_publisher(mqttclient, mqtttopic)(json.dumps(payload))

                            except json.JSONDecodeError:
                                logger.error("Fout bij decoderen JSON-bericht")
                            except Exception as e:
                                logger.error(f"Fout bij verwerken WebSocket-data: {e}")
                except Exception as e:
                    logger.error(f"Kan geen verbinding maken met AIS-stream: {e}")

            asyncio.run(websocket_loop())

        logger.info(f"Start WebSocket connectie [{mqttclient}]...")
        threading.Thread(target=handle_websocket, daemon=True).start()

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

        message_type = raw_data.get("MessageType")

        if not message_type or "Message" not in raw_data:
            logger.warning("Ongeldig bericht ontvangen")
            return

        ais_message = raw_data['Message'].get(message_type, {})
        msgid = ais_message.get('MessageID', 0)
        flat_data = flatten_multilevel(ais_message, prefix='')


class AisstreamMqtt:
    @staticmethod
    def subscribe(client, topic):
        """
        Start een MQTT-subscriber op het opgegeven topic.

        Args:
            client (str): Naam van de client (bijvoorbeeld 'ais').
            topic (str): Het MQTT-topic waarop geluisterd moet worden.
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

                - Decodeert JSON payload.
                - Verwerkt deze met Nmea.process().

                Args:
                    client: MQTT client object.
                    userdata: Gebruikersdata (standaard None).
                    message: Het MQTT bericht object.
                """
        try:
            payload = json.loads(message.payload.decode("utf-8"))
            payload["received"] = payload.get("received", int(time.time() * 1000))
            logger.debug(f"Ontvangen bericht: {payload}")
            Aisstream.process(payload)

        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error: {e} - Payload: {message.payload.decode('utf-8')}")
        except Exception as e:
            logger.exception(f"Onverwachte fout in on_message: {e}")

    @staticmethod
    def publish(client, topic, payload):
        """
        Publiceer een bericht naar een MQTT-topic.

        Args:
            client (str): Naam van de client.
            topic (str): MQTT-topic waar het bericht naartoe gestuurd wordt.
            payload (dict): JSON-serialiseerbare payload.
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