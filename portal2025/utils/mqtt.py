import paho.mqtt.client as mqtt
from django.conf import settings
from utils.logger import get_logger
import threading
import uuid
import time

logger = get_logger(__name__)

BROKER_IP = settings.MQTT_BROKER_IP
PORT = settings.MQTT_PORT
KEEPALIVE = getattr(settings, "MQTT_KEEPALIVE", 120)

mqtt_clients = {}
publish_lock = threading.Lock()

logger.debug(f"Zoek MQTT broker op {BROKER_IP}:{PORT}")


def on_connect(client, userdata, flags, rc):
    """
    Callback die wordt aangeroepen bij connectie met de MQTT broker.

    Args:
        client (mqtt.Client): De MQTT client instantie.
        userdata (dict): De gebruikersdata die aan de client is gekoppeld.
        flags (dict): Response flags van de broker.
        rc (int): Resultaatcode van de verbinding (0 = succes).
    """
    rc_messages = {
        0: "Succesvol verbonden",
        1: "Onjuist protocol",
        2: "Ongeldige client-ID",
        3: "Server niet beschikbaar",
        4: "Verkeerde gebruikersnaam/wachtwoord",
        5: "Geen toestemming",
        6: "Ongeldige pakketgrootte",
        7: "Slechte socketverbinding",
        8: "Onbekende fout"
    }

    client_id = userdata.get("client_id", "MQTT Client")
    if rc == 0:
        logger.info(f"[{client_id}] Verbonden met MQTT Broker")
    else:
        error_msg = rc_messages.get(rc, f"Onbekende fout (code: {rc})")
        logger.warning(f"[{client_id}] Verbinding mislukt: {error_msg}")


def on_disconnect(client, userdata, rc):
    """
    Callback bij het verbreken van de verbinding met de broker.
    Probeert automatisch opnieuw te verbinden.

    Args:
        client (mqtt.Client): De MQTT client instantie.
        userdata (dict): Gebruikersdata gekoppeld aan de client.
        rc (int): Disconnect return code.
    """
    client_id = userdata.get("client_id", "MQTT Client")
    logger.warning(f"[{client_id}] Verbroken verbinding (code: {rc}). Probeert opnieuw...")

    reconnect_attempts = 0
    while rc != 0:
        sleep_time = min(2 ** reconnect_attempts, 60)
        time.sleep(sleep_time)
        try:
            rc = client.reconnect()
            logger.info(f"[{client_id}] Opnieuw verbonden met MQTT Broker")
        except Exception as e:
            logger.error(f"[{client_id}] Reconnect poging mislukt: {e}")
        reconnect_attempts += 1


def on_message(client, userdata, message):
    """
    Callback bij ontvangst van een bericht.

    Args:
        client (mqtt.Client): De client die het bericht ontving.
        userdata (dict): Gebruikersdata.
        message (mqtt.MQTTMessage): Het ontvangen bericht.
    """
    client_id = userdata.get("client_id", "MQTT Client")
    payload = message.payload.decode("utf-8")
    logger.info(f"[{client_id}] Ontvangen bericht op {message.topic}: {payload}")


def client_disconnect(client):
    """
    Stopt de event-loop van de client en logt de afsluiting.

    Args:
        client (mqtt.Client): De MQTT client om te stoppen.
    """
    client_id = client._client_id.decode("utf-8")
    client.loop_stop()
    logger.info(f"MQTT-client {client_id} gestopt.")


def start_publisher(client_name, topic):
    """
    Start een MQTT-publisher of hergebruikt een bestaande.

    Args:
        client_name (str): Unieke naam van de client (bijv. app/module naam).
        topic (str): Het MQTT-topic waarop gepubliceerd wordt.

    Returns:
        Callable[[str], None] | None: Een functie die berichten publiceert op het topic,
        of None bij fout.
    """
    if client_name in mqtt_clients:
        logger.info(f"[{client_name}] Hergebruik bestaande MQTT-client.")
        return mqtt_clients[client_name]["publish"]

    try:
        client_id = f"{client_name}_{uuid.uuid4().hex[:8]}"
        client = mqtt.Client(client_id=client_id, callback_api_version=mqtt.CallbackAPIVersion.VERSION1)
        client.user_data_set({"client_id": client_id})
        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.reconnect_delay_set(min_delay=1, max_delay=10)
        client.connect(BROKER_IP, PORT, KEEPALIVE)
        client.loop_start()

        def publish_message(message):
            """
            Publiceert een bericht op het topic met thread-safe lock.

            Args:
                message (str): Het bericht om te publiceren.
            """
            with publish_lock:
                result = client.publish(topic, message, qos=1)
                result.wait_for_publish()
                if result.rc == mqtt.MQTT_ERR_SUCCESS:
                    logger.debug(f"[{client_id}] Bericht gepubliceerd op {topic}: {message}")
                else:
                    logger.error(f"[{client_id}] Publiceren mislukt op {topic} met foutcode {result.rc}")

        mqtt_clients[client_name] = {
            "client": client,
            "client_id": client_id,
            "publish": publish_message
        }
        return publish_message

    except Exception as e:
        logger.error(f"[{client_name}] Fout bij starten MQTT Publisher: {e}")
        return None


def start_subscriber(client_name, topic):
    """
    Start een MQTT-subscriber of hergebruikt een bestaande. Abonneert op het opgegeven topic.

    Args:
        client_name (str): Unieke naam van de client.
        topic (str): Het topic om op te luisteren.

    Returns:
        mqtt.Client | None: De MQTT client of None bij fout.
    """
    if client_name in mqtt_clients:
        logger.info(f"[{client_name}] Hergebruik bestaande MQTT-client.")
        return mqtt_clients[client_name]["client"]

    logger.info(f"[{client_name}] Probeer verbinding met broker: {BROKER_IP}:{PORT}")
    try:
        client_id = f"{client_name}_{uuid.uuid4().hex[:8]}"
        client = mqtt.Client(client_id=client_id, callback_api_version=mqtt.CallbackAPIVersion.VERSION1)
        client.user_data_set({"client_id": client_id})
        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_message = on_message
        client.reconnect_delay_set(min_delay=1, max_delay=10)
        client.connect(BROKER_IP, PORT, KEEPALIVE)
        client.subscribe(topic, qos=1)
        client.loop_start()

        mqtt_clients[client_name] = {
            "client": client,
            "client_id": client_id
        }
        logger.info(f"[{client_id}] Geabonneerd op {topic}")
        return client

    except Exception as e:
        logger.error(f"[{client_name}] Fout bij starten MQTT Subscriber: {e}")
        return None


def get_all_active_clients():
    """
    Geeft een lijst van alle actieve MQTT clients en hun ID's.

    Returns:
        List[dict]: Lijst met dicts, elk met 'client_name' en 'client_id'.
    """
    active_clients = [
        {"client_name": name, "client_id": data["client_id"]}
        for name, data in mqtt_clients.items()
    ]
    logger.info(f"Actieve clients: {active_clients}")
    return active_clients
