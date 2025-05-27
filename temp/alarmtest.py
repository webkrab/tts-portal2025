import time
import requests

# Traccar server URL (pas aan indien nodig)
TRACCAR_URL = "http://1.lifeguardtracking.nl:5055"

# Testinstellingen
DEVICE_ID = "FWteletubbie"
LAT = 52
LON = 6
INTERVAL_SECONDS = 16

# Alarmtypes
alarms = [
    "general", "sos", "vibration", "movement", "lowspeed", "overspeed", "fallDown",
    "lowPower", "lowBattery", "fault", "powerOff", "powerOn", "door", "lock", "unlock",
    "geofence", "geofenceEnter", "geofenceExit", "gpsAntennaCut", "accident", "tow",
    "idle", "highRpm", "hardAcceleration", "hardBraking", "hardCornering", "laneChange",
    "fatigueDriving", "powerCut", "powerRestored", "jamming", "temperature", "parking",
    "bonnet", "footBrake", "fuelLeak", "tampering", "removing", ""
]

# Loop door elk alarmtype
for alarm in alarms:
    params = {
        "id": DEVICE_ID,
        "lat": LAT,
        "lon": LON,
        "alarm": alarm
    }
    try:
        response = requests.get(TRACCAR_URL, params=params)
        print(f"Sent alarm: {alarm} - Status: {response.status_code}")
    except Exception as e:
        print(f"Fout bij versturen van alarm '{alarm}': {e}")
    time.sleep(INTERVAL_SECONDS)
