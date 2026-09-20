"""Parse BLE advertising payloads into structured AD fields."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .numbers import company_name, format_uuid, service_name


AD_TYPES = {
    0x01: "flags",
    0x02: "incomplete_uuid16",
    0x03: "complete_uuid16",
    0x04: "incomplete_uuid32",
    0x05: "complete_uuid32",
    0x06: "incomplete_uuid128",
    0x07: "complete_uuid128",
    0x08: "short_name",
    0x09: "complete_name",
    0x0A: "tx_power",
    0x0D: "class_of_device",
    0x14: "slave_conn_interval_range",
    0x15: "solicitation_uuid16",
    0x16: "service_data_uuid16",
    0x17: "public_target_address",
    0x19: "appearance",
    0x1A: "advertising_interval",
    0x1B: "le_bluetooth_device_address",
    0x1C: "le_role",
    0x20: "service_data_uuid32",
    0x21: "service_data_uuid128",
    0x24: "uri",
    0x30: "broadcast_name",
    0xFF: "manufacturer",
}

ADV_TYPE_NAMES = {
    0: "ADV_IND",
    1: "ADV_DIRECT_IND",
    2: "ADV_SCAN_IND",
    3: "ADV_NONCONN_IND",
    4: "SCAN_RSP",
    5: "ADV_EXT",
}

FLAG_BITS = (
    (0x01, "LE Limited Discoverable"),
    (0x02, "LE General Discoverable"),
    (0x04, "BR/EDR Not Supported"),
    (0x08, "Simultaneous LE + BR/EDR (Controller)"),
    (0x10, "Simultaneous LE + BR/EDR (Host)"),
)


def _le_u16(data: bytes, offset: int = 0) -> int:
    return int.from_bytes(data[offset : offset + 2], "little")


def _le_u32(data: bytes, offset: int = 0) -> int:
    return int.from_bytes(data[offset : offset + 4], "little")


def _uuid16_list(data: bytes) -> list[str]:
    return [f"{_le_u16(data, i):04x}" for i in range(0, len(data) - 1, 2)]


def _uuid32_list(data: bytes) -> list[str]:
    return [f"{_le_u32(data, i):08x}" for i in range(0, len(data) - 3, 4)]


def _uuid128_list(data: bytes) -> list[str]:
    out = []
    for i in range(0, len(data) - 15, 16):
        raw = data[i : i + 16][::-1]
        out.append(raw.hex())
    return out


def _decode_name(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").rstrip("\x00")


@dataclass
class Advertisement:
    time: datetime
    address: str
    address_type: str
    rssi: int | None
    adv_type: str
    payload: bytes
    source: str = "unknown"
    parsed: dict = field(default_factory=dict)
    classification: dict = field(default_factory=dict)

    def to_record(self) -> dict:
        return {
            "time": self.time.astimezone(timezone.utc).isoformat(),
            "address": self.address.upper(),
            "address_type": self.address_type,
            "rssi": self.rssi,
            "adv_type": self.adv_type,
            "payload_hex": self.payload.hex(),
            "parsed": self.parsed,
            "classification": self.classification,
            "source": self.source,
        }


def normalize_address(address: str) -> str:
    cleaned = address.replace("-", ":").replace(".", ":").upper()
    if ":" not in cleaned and len(cleaned) == 12:
        cleaned = ":".join(cleaned[i : i + 2] for i in range(0, 12, 2))
    return cleaned


def parse_ad(payload: bytes | str) -> dict:
    if isinstance(payload, str):
        payload = bytes.fromhex(payload.replace(" ", "").replace("0x", ""))

    fields: list[dict] = []
    names: list[str] = []
    uuids: list[str] = []
    service_data: list[dict] = []
    manufacturer_id = None
    manufacturer_data = b""
    appearance = None
    tx_power = None
    flags = None
    flags_decoded: list[str] = []

    i = 0
    while i < len(payload):
        length = payload[i]
        if length == 0:
            break
        i += 1
        if i + length > len(payload):
            fields.append(
                {
                    "type": "truncated",
                    "type_id": None,
                    "hex": payload[i - 1 :].hex(),
                }
            )
            break
        ad_type = payload[i]
        value = payload[i + 1 : i + length]
        i += length
        type_name = AD_TYPES.get(ad_type, f"0x{ad_type:02x}")
        item: dict = {
            "type": type_name,
            "type_id": ad_type,
            "hex": value.hex(),
        }

        if ad_type == 0x01 and value:
            flags = value[0]
            flags_decoded = [label for bit, label in FLAG_BITS if flags & bit]
            item["flags"] = flags
            item["flags_decoded"] = flags_decoded
        elif ad_type in (0x02, 0x03, 0x15):
            parsed_uuids = _uuid16_list(value)
            item["uuids"] = [u.upper() for u in parsed_uuids]
            item["names"] = [service_name(u) or format_uuid(u) for u in parsed_uuids]
            uuids.extend(parsed_uuids)
        elif ad_type in (0x04, 0x05):
            parsed_uuids = _uuid32_list(value)
            item["uuids"] = [format_uuid(u) for u in parsed_uuids]
            item["names"] = [service_name(u) or format_uuid(u) for u in parsed_uuids]
            uuids.extend(parsed_uuids)
        elif ad_type in (0x06, 0x07):
            parsed_uuids = _uuid128_list(value)
            item["uuids"] = [format_uuid(u) for u in parsed_uuids]
            item["names"] = [service_name(u) or format_uuid(u) for u in parsed_uuids]
            uuids.extend(parsed_uuids)
        elif ad_type in (0x08, 0x09, 0x30):
            name = _decode_name(value)
            item["text"] = name
            names.append(name)
        elif ad_type == 0x0A and value:
            tx_power = int.from_bytes(value[:1], "little", signed=True)
            item["dbm"] = tx_power
        elif ad_type == 0x19 and len(value) >= 2:
            appearance = _le_u16(value)
            item["appearance"] = appearance
        elif ad_type == 0x16 and len(value) >= 2:
            uuid = f"{_le_u16(value):04x}"
            data = value[2:]
            entry = {
                "uuid": uuid.upper(),
                "name": service_name(uuid),
                "hex": data.hex(),
            }
            item.update(entry)
            service_data.append(entry)
            uuids.append(uuid)
        elif ad_type == 0x20 and len(value) >= 4:
            uuid = f"{_le_u32(value):08x}"
            data = value[4:]
            entry = {
                "uuid": format_uuid(uuid),
                "name": service_name(uuid),
                "hex": data.hex(),
            }
            item.update(entry)
            service_data.append(entry)
            uuids.append(uuid)
        elif ad_type == 0x21 and len(value) >= 16:
            uuid = value[:16][::-1].hex()
            data = value[16:]
            entry = {
                "uuid": format_uuid(uuid),
                "name": service_name(uuid),
                "hex": data.hex(),
            }
            item.update(entry)
            service_data.append(entry)
            uuids.append(uuid)
        elif ad_type == 0xFF and len(value) >= 2:
            manufacturer_id = _le_u16(value)
            manufacturer_data = value[2:]
            item["company_id"] = manufacturer_id
            item["company"] = company_name(manufacturer_id)
            item["data_hex"] = manufacturer_data.hex()

        fields.append(item)

    unique_uuids = []
    seen = set()
    for uuid in uuids:
        key = uuid.lower()
        if key not in seen:
            seen.add(key)
            unique_uuids.append(uuid.lower())

    name = None
    if names:
        name = max(names, key=len)

    return {
        "name": name,
        "uuids": unique_uuids,
        "uuid_names": [service_name(u) or format_uuid(u) for u in unique_uuids],
        "service_data": service_data,
        "manufacturer_id": manufacturer_id,
        "manufacturer_name": company_name(manufacturer_id) if manufacturer_id is not None else None,
        "manufacturer_data_hex": manufacturer_data.hex() if manufacturer_data else None,
        "appearance": appearance,
        "tx_power": tx_power,
        "flags": flags,
        "flags_decoded": flags_decoded,
        "fields": fields,
        "payload_hex": payload.hex(),
        "payload_len": len(payload),
    }


def adv_type_name(value: int | str | None) -> str:
    if value is None:
        return "ADV"
    if isinstance(value, str):
        return value
    return ADV_TYPE_NAMES.get(int(value), f"ADV_{int(value)}")
