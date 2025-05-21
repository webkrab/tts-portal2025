import hashlib
from utils.logger import get_logger
import time
from enum import Enum
from dateutil import parser as dup
from datetime import datetime

# Logger instellen
logger = get_logger(__name__)
# Generators

def genereer_hash(msg):
    try:
        return hashlib.sha256(msg.encode()).hexdigest()
    except Exception as e:
        logger.error(f"Hash-generatie mislukt: {e} voor {msg}")
        return None

#convert
def remap_keys(data, mapping):
    result = {}
    unmapped_keys = []

    logger.debug(f"remap_keys: {data}")

    flat_data = flatten_multilevel(data, prefix='')

    for key, value in flat_data.items():
        if key in mapping:
            new_key = mapping[key]
            if new_key is not None and value is not None:
                result[new_key] = value
        else:
            unmapped_keys.append(key)

    if not result:
        logger.error(f"Geen overeenkomende keys gevonden in bericht: {data}")
        return None, unmapped_keys

    return result, unmapped_keys

def flatten_multilevel(data, prefix=''):
    flat_items = []

    if isinstance(data, dict):
        for k, v in data.items():
            full_key = f"{prefix}.{k}" if prefix else k
            flat_items.extend(flatten_multilevel(v, prefix=full_key))
    elif isinstance(data, list):
        for i, item in enumerate(data):
            full_key = f"{prefix}[{i}]"
            flat_items.extend(flatten_multilevel(item, prefix=full_key))
    else:
        flat_items.append((prefix, data))

    if prefix == '':  # we're at the root, time to repackage
        if isinstance(data, dict):
            return dict(flat_items)
        elif isinstance(data, list):
            return list(flat_items)
    return flat_items

def convert_speed(value, from_unit):
    """
    Converts any speed value to a dict with keys: 'm/s', 'km/h', 'kt', 'bft'.
    Return m/s
    """
    units_to_mps = {
        'm/s': 1,
        'km/h': 1 / 3.6,
        'mph': 0.44704,
        'kt': 0.514444,
        'ft/s': 0.3048,
        'bft': None,  # special handling
    }

    def mps_to_beaufort(mps):
        bft_table = [
            (0.0, 0.2), (0.3, 1.5), (1.6, 3.3), (3.4, 5.4),
            (5.5, 7.9), (8.0, 10.7), (10.8, 13.8), (13.9, 17.1),
            (17.2, 20.7), (20.8, 24.4), (24.5, 28.4), (28.5, 32.6),
            (32.7, float('inf'))
        ]
        for bft, (min_val, max_val) in enumerate(bft_table):
            if min_val <= mps <= max_val:
                return bft
        return None

    def beaufort_to_mps(bft):
        bft_midpoints = [
            0.1, 0.9, 2.45, 4.4, 6.7, 9.35, 12.3, 15.5, 19.0,
            22.6, 26.45, 30.55, 35.0
        ]
        if 0 <= bft < len(bft_midpoints):
            return bft_midpoints[bft]
        else:
            raise ValueError("Beaufort value must be between 0 and 12")

    try:
        # Convert input to m/s first
        from_unit = from_unit.lower()
        if from_unit == 'bft':
            value_in_mps = beaufort_to_mps(int(value))
        elif from_unit in units_to_mps:
            value_in_mps = value * units_to_mps[from_unit]
        else:
            raise ValueError(f"Invalid input unit: {from_unit}")

        # Create output dict
        output = {
            'm/s': round(value_in_mps, 1),
            'km/h': round(value_in_mps * 3.6, 1),
            'kt': round(value_in_mps / 0.514444, 1),
            'bft': mps_to_beaufort(value_in_mps)
        }
        return output

    except Exception as e:
        logger.error(f"Conversion error: {e} | value={value}, from_unit={from_unit}")
        return None

def convert_enum_values(obj):
    """Converteer Enum-objecten naar hun waarde en naam."""
    if isinstance(obj, Enum):
        return obj.value, obj.name if obj.value is not None else None
    return obj, None

def parse_to_unixtimestamp(ts):
    try:
        if isinstance(ts, datetime):
            # datetime -> UNIX tijd in ms
            return int(ts.timestamp() * 1000)
        elif isinstance(ts, (int, float)):
            # Max waarde van 32bit unix (19 januari 2038 om 03:14:07 UTC) dan is het waarschijnlijk sec.
            if ts < 2147483647:
                return int(float(ts) * 1000)
            else:
                return int(float(ts))
        elif isinstance(ts, str) and ts.isdigit():
            return int(float(ts) * 1000)
        else:
            # Probeer string-parsing van datum
            dt = dup.parse(ts)
            return int(dt.timestamp() * 1000)
    except Exception as e:
        logger.error(f"Datum/Tijd conversie naar unixtime mislukt voor {ts}: {e}")
        return None