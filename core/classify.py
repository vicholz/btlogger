"""Infer device type, manufacturer extras, and a stable identity hint."""

from __future__ import annotations

from .numbers import appearance_name, company_name, format_uuid, service_name
from .parse import parse_ad

APPLE_CONTINUITY = {
    0x01: "Apple Device",
    0x02: "iBeacon",
    0x05: "Apple AirDrop",
    0x07: "Apple AirPods",
    0x08: "Apple AirPlay",
    0x09: "Apple AirPlay Destination",
    0x0A: "Apple AirPlay Source",
    0x0C: "Apple Handoff",
    0x0D: "Apple Wi-Fi Settings",
    0x0E: "Apple Nearby",
    0x0F: "Apple Nearby Info",
    0x10: "Apple Nearby Action",
    0x12: "Apple Find My",
    0x16: "Apple Nearby Info",
    0x19: "Apple HomeKit",
}

SERVICE_TYPE_HINTS = {
    "180d": "Heart rate monitor",
    "1809": "Health thermometer",
    "1808": "Glucose meter",
    "1810": "Blood pressure monitor",
    "1812": "HID device",
    "1814": "Running speed sensor",
    "1816": "Cycling speed/cadence",
    "1818": "Cycling power meter",
    "181a": "Environmental sensor",
    "181c": "User data device",
    "181d": "Weight scale",
    "1822": "Pulse oximeter",
    "1826": "Fitness machine",
    "1843": "Audio device",
    "1844": "Audio device",
    "1853": "Audio device",
    "feaa": "Eddystone beacon",
    "fe2c": "Google Fast Pair device",
    "fd6f": "Exposure Notification beacon",
    "fd44": "Tile tracker",
    "feed": "Tile tracker",
    "fe0f": "Philips Hue",
    "fcb2": "Apple location advertisement",
}


def _mfg_bytes(parsed: dict) -> bytes:
    hex_data = parsed.get("manufacturer_data_hex") or ""
    return bytes.fromhex(hex_data) if hex_data else b""


def _has_uuid(parsed: dict, uuid: str) -> bool:
    target = uuid.replace("-", "").lower()
    return any(u.replace("-", "").lower() == target or u.lower() == target for u in parsed.get("uuids") or [])


def classify(parsed: dict | bytes | str, address: str = "", address_type: str = "random") -> dict:
    if not isinstance(parsed, dict):
        parsed = parse_ad(parsed)

    device_type = "Unknown BLE device"
    stable_id = None
    extra: dict = {}
    mfg_id = parsed.get("manufacturer_id")
    mfg_name = parsed.get("manufacturer_name") or company_name(mfg_id)
    mfg = _mfg_bytes(parsed)
    name = parsed.get("name")
    appearance = parsed.get("appearance")
    appearance_label = appearance_name(appearance)

    if mfg_id == 0x004C and mfg:
        kind = mfg[0]
        if kind == 0x02 and len(mfg) >= 23 and mfg[1] == 0x15:
            uuid = mfg[2:18].hex()
            major = int.from_bytes(mfg[18:20], "big")
            minor = int.from_bytes(mfg[20:22], "big")
            tx = int.from_bytes(mfg[22:23], "big", signed=True)
            device_type = "iBeacon"
            extra = {
                "ibeacon_uuid": format_uuid(uuid),
                "ibeacon_major": major,
                "ibeacon_minor": minor,
                "ibeacon_tx_power": tx,
            }
            stable_id = f"ibeacon:{uuid}:{major}:{minor}"
        else:
            device_type = APPLE_CONTINUITY.get(kind, "Apple device")
            extra["apple_continuity_type"] = f"0x{kind:02x}"
            extra["apple_continuity_name"] = APPLE_CONTINUITY.get(kind, "Unknown")
            if kind == 0x12:
                device_type = "Apple Find My / AirTag"
            elif kind in (0x07,):
                device_type = "Apple AirPods"
            elif kind in (0x0F, 0x10, 0x16):
                device_type = "Apple iPhone/iPad/Mac"
    elif _has_uuid(parsed, "feaa"):
        sdata = next((s for s in parsed.get("service_data") or [] if s.get("uuid", "").lower() == "FEAA".lower()), None)
        frame = bytes.fromhex(sdata["hex"]) if sdata and sdata.get("hex") else b""
        if frame:
            frame_type = frame[0]
            extra["eddystone_frame"] = frame_type
            if frame_type == 0x00 and len(frame) >= 18:
                device_type = "Eddystone-UID beacon"
                namespace = frame[2:12].hex()
                instance = frame[12:18].hex()
                extra["eddystone_namespace"] = namespace
                extra["eddystone_instance"] = instance
                stable_id = f"eddystone:{namespace}:{instance}"
            elif frame_type == 0x10:
                device_type = "Eddystone-URL beacon"
            elif frame_type == 0x20:
                device_type = "Eddystone-TLM beacon"
            else:
                device_type = "Eddystone beacon"
        else:
            device_type = "Eddystone beacon"
    elif _has_uuid(parsed, "fe2c"):
        device_type = "Google Fast Pair device"
    elif _has_uuid(parsed, "fd6f"):
        device_type = "Exposure Notification beacon"
    elif mfg_id == 0x0006:
        device_type = "Microsoft device"
        extra["microsoft"] = True
        if mfg[:1] == b"\x01":
            device_type = "Microsoft Swift Pair"
    elif mfg_id == 0x0075:
        device_type = "Samsung device"
    elif mfg_id in (0x00E0, 0x00B3) or _has_uuid(parsed, "feed") or _has_uuid(parsed, "fd44"):
        device_type = "Tile tracker"
    elif mfg_id == 0x0059:
        device_type = "Nordic Semiconductor device"
    elif mfg_id == 0x02E5:
        device_type = "Espressif / ESP32"
    elif mfg_id == 0x038F:
        device_type = "Xiaomi device"
    elif mfg_id == 0x0499:
        device_type = "Ruuvi sensor"
        stable_id = f"ruuvi:{address.upper()}" if address_type == "public" else None
    elif mfg_id == 0x0057:
        device_type = "Garmin device"
    elif mfg_id == 0x00F7:
        device_type = "Fitbit device"

    if device_type == "Unknown BLE device":
        for uuid in parsed.get("uuids") or []:
            hint = SERVICE_TYPE_HINTS.get(uuid.lower())
            if hint:
                device_type = hint
                break

    if device_type == "Unknown BLE device" and appearance_label and appearance:
        device_type = appearance_label

    if device_type == "Unknown BLE device" and name:
        lowered = name.lower()
        if "airpod" in lowered:
            device_type = "Apple AirPods"
        elif "watch" in lowered:
            device_type = "Smartwatch"
        elif any(token in lowered for token in ("iphone", "ipad", "macbook")):
            device_type = "Apple iPhone/iPad/Mac"
        elif "tv" in lowered:
            device_type = "Smart TV / streamer"

    if address_type == "public" and address:
        stable_id = stable_id or f"pub:{address.upper()}"
    elif stable_id is None and name and mfg_id is not None:
        stable_id = f"name:{name}|mfg:{mfg_id}"
    elif stable_id is None and name:
        stable_id = f"name:{name}"

    uuid_labels = []
    for uuid in parsed.get("uuids") or []:
        label = service_name(uuid)
        uuid_labels.append({"uuid": format_uuid(uuid), "name": label})

    return {
        "device_type": device_type,
        "manufacturer_id": mfg_id,
        "manufacturer_name": mfg_name,
        "appearance": appearance,
        "appearance_name": appearance_label,
        "name": name,
        "stable_id": stable_id,
        "uuids": uuid_labels,
        "extra": extra,
    }
