"""Scanner backends: nRF serial NDJSON, BlueZ/Bleak, and a replay/demo generator."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from datetime import datetime, timedelta
from pathlib import Path

from core.parse import Advertisement, adv_type_name, normalize_address
from core.timeutil import utcnow

LOG = logging.getLogger("btlogger.scanner.backends")

SERIAL_PORT = os.environ.get("SERIAL_PORT", "/dev/ttyACM0")
SERIAL_BAUD = int(os.environ.get("SERIAL_BAUD", "115200"))
HCI_ADAPTER = os.environ.get("HCI_ADAPTER") or None


def _adv(
    address: str,
    payload: bytes,
    rssi: int,
    address_type: str = "random",
    adv_type: str = "ADV_IND",
    source: str = "unknown",
    when: datetime | None = None,
) -> Advertisement:
    return Advertisement(
        time=when or utcnow(),
        address=normalize_address(address),
        address_type=address_type,
        rssi=rssi,
        adv_type=adv_type,
        payload=payload,
        source=source,
    )


async def serial_backend(queue: asyncio.Queue, stop: asyncio.Event) -> None:
    import serial

    port = SERIAL_PORT
    LOG.info("opening serial %s @ %s", port, SERIAL_BAUD)
    while not stop.is_set():
        try:
            ser = serial.Serial(port, SERIAL_BAUD, timeout=1)
        except Exception as exc:
            LOG.warning("serial open failed: %s", exc)
            await asyncio.sleep(2)
            continue
        try:
            while not stop.is_set():
                line = await asyncio.to_thread(ser.readline)
                if not line:
                    continue
                text = line.decode("utf-8", errors="replace").strip()
                if not text.startswith("{"):
                    continue
                try:
                    msg = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if msg.get("t") != "adv":
                    continue
                payload = bytes.fromhex(msg.get("adv") or msg.get("payload") or "")
                await queue.put(
                    _adv(
                        address=msg.get("addr") or msg["address"],
                        payload=payload,
                        rssi=int(msg.get("rssi", 0)),
                        address_type=msg.get("at") or msg.get("address_type") or "random",
                        adv_type=adv_type_name(msg.get("evt") if "evt" in msg else msg.get("adv_type")),
                        source="nrf-serial",
                    )
                )
        finally:
            ser.close()
            await asyncio.sleep(0.5)


async def bleak_backend(queue: asyncio.Queue, stop: asyncio.Event) -> None:
    from bleak import BleakScanner

    LOG.info("starting bleak scanner adapter=%s", HCI_ADAPTER or "default")

    def _cb(device, adv):
        payload = bytes(getattr(adv, "raw", b"") or b"")
        if not payload:
            parts = bytearray()
            if getattr(adv, "local_name", None):
                name = adv.local_name.encode("utf-8", errors="replace")[:27]
                parts.append(len(name) + 1)
                parts.append(0x09)
                parts.extend(name)
            uuids = getattr(adv, "service_uuids", None) or []
            uuid16 = []
            for u in uuids:
                compact = u.replace("-", "").lower()
                if compact.startswith("0000") and compact.endswith("00001000800000805f9b34fb"):
                    uuid16.append(int(compact[4:8], 16))
            if uuid16:
                parts.append(1 + 2 * len(uuid16))
                parts.append(0x03)
                for value in uuid16:
                    parts.extend(int(value).to_bytes(2, "little"))
            mfg = getattr(adv, "manufacturer_data", None) or {}
            for company_id, data in mfg.items():
                blob = int(company_id).to_bytes(2, "little") + bytes(data)
                parts.append(len(blob) + 1)
                parts.append(0xFF)
                parts.extend(blob)
            payload = bytes(parts)
        address_type = "random"
        details = getattr(device, "details", None)
        if isinstance(details, dict):
            props = details.get("props") or {}
            atype = str(props.get("AddressType") or "").lower()
            if "public" in atype:
                address_type = "public"
        item = _adv(
            address=device.address,
            payload=payload,
            rssi=int(getattr(adv, "rssi", None) or getattr(device, "rssi", None) or 0),
            address_type=address_type,
            source="bleak",
        )
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            pass

    kwargs = {}
    if HCI_ADAPTER:
        kwargs["adapter"] = HCI_ADAPTER
    scanner = BleakScanner(detection_callback=_cb, **kwargs)
    await scanner.start()
    try:
        await stop.wait()
    finally:
        await scanner.stop()


def _field(typ: int, data: bytes) -> bytes:
    return bytes([len(data) + 1, typ]) + data


def _payload(*parts: bytes) -> bytes:
    return b"".join(parts)


def _sample_catalog() -> list[dict]:
    flags = _field(0x01, b"\x06")
    return [
        {
            "address": "A4:C1:38:11:22:01",
            "address_type": "public",
            "payload": _payload(flags, _field(0x09, b"ATC_112201"), _field(0x16, bytes.fromhex("1a1800dc6238"))),
            "pattern": "always",
            "rssi": -62,
        },
        {
            "address": "C8:5C:A2:00:10:02",
            "address_type": "public",
            "payload": _payload(flags, _field(0x03, bytes.fromhex("0d18")), _field(0x09, b"Charge 5")),
            "pattern": "workout",
            "rssi": -71,
        },
        {
            "address": "F4:4E:FC:77:01:03",
            "address_type": "random",
            "payload": _payload(
                flags,
                _field(
                    0xFF,
                    bytes.fromhex("4c000215fda50693a4e24fb1afcfc6eb0764782500010002c5"),
                ),
            ),
            "pattern": "office",
            "rssi": -68,
        },
        {
            "address": "5A:9E:10:44:20:04",
            "address_type": "random",
            "payload": _payload(flags, _field(0xFF, bytes.fromhex("4c0012") + bytes(range(20)))),
            "pattern": "commute",
            "rssi": -80,
        },
        {
            "address": "60:77:71:AA:00:05",
            "address_type": "public",
            "payload": _payload(flags, _field(0x09, b"Apple TV"), _field(0xFF, bytes.fromhex("4c001007021c11b0"))),
            "pattern": "evening",
            "rssi": -55,
        },
        {
            "address": "D0:CF:5E:12:00:06",
            "address_type": "public",
            "payload": _payload(
                flags,
                _field(0x03, bytes.fromhex("aafe")),
                _field(0x16, bytes.fromhex("aafe00e5") + bytes(range(16))),
            ),
            "pattern": "always",
            "rssi": -59,
        },
        {
            "address": "7C:D9:5C:88:10:07",
            "address_type": "random",
            "payload": _payload(flags, _field(0xFF, bytes.fromhex("0600010920020a7cd95c8810"))),
            "pattern": "office",
            "rssi": -74,
        },
        {
            "address": "B8:27:EB:00:00:08",
            "address_type": "public",
            "payload": _payload(flags, _field(0x09, b"ruuvitag"), _field(0xFF, bytes.fromhex("99040512fc5394c37c0004fff8"))),
            "pattern": "always",
            "rssi": -66,
        },
        {
            "address": "28:6A:BA:33:01:09",
            "address_type": "random",
            "payload": _payload(flags, _field(0xFF, bytes.fromhex("4c000f05a018c0b219"))),
            "pattern": "commute",
            "rssi": -84,
        },
        {
            "address": "E4:5F:01:90:00:0A",
            "address_type": "public",
            "payload": _payload(flags, _field(0x09, b"ESP32-weather"), _field(0xFF, bytes.fromhex("e502aabb"))),
            "pattern": "always",
            "rssi": -70,
        },
        {
            "address": "00:1A:7D:DA:71:0B",
            "address_type": "public",
            "payload": _payload(flags, _field(0x09, b"WH-1000XM4"), _field(0x03, bytes.fromhex("0d18"))),
            "pattern": "evening",
            "rssi": -60,
        },
        {
            "address": "3C:22:FB:10:00:0C",
            "address_type": "random",
            "payload": _payload(flags, _field(0xFF, bytes.fromhex("4c00100702111b"))),
            "pattern": "office",
            "rssi": -77,
        },
    ]


def _in_pattern(pattern: str, when: datetime) -> bool:
    hour = when.hour
    weekday = when.weekday()
    if pattern == "always":
        return True
    if pattern == "office":
        return weekday < 5 and 8 <= hour <= 17
    if pattern == "commute":
        return weekday < 5 and hour in (7, 8, 9, 17, 18, 19)
    if pattern == "evening":
        return 18 <= hour <= 23
    if pattern == "workout":
        return hour in (6, 7, 12, 18, 19)
    return True


async def replay_backend(queue: asyncio.Queue, stop: asyncio.Event) -> None:
    """Seed a week of patterned history, then emit live fake advertisements."""
    catalog = _sample_catalog()
    now = utcnow()
    start = now - timedelta(days=7)
    LOG.info("replay: seeding history from %s", start.isoformat())
    cursor = start
    emitted = 0
    while cursor < now and not stop.is_set():
        for device in catalog:
            if not _in_pattern(device["pattern"], cursor):
                continue
            if random.random() > 0.35:
                continue
            rssi = device["rssi"] + random.randint(-8, 6)
            await queue.put(
                _adv(
                    device["address"],
                    device["payload"],
                    rssi,
                    device["address_type"],
                    source="replay",
                    when=cursor,
                )
            )
            emitted += 1
        cursor += timedelta(minutes=2)
        if emitted % 400 == 0:
            await asyncio.sleep(0)
    LOG.info("replay: seeded %s historical advertisements", emitted)

    while not stop.is_set():
        now = utcnow()
        for device in catalog:
            if not _in_pattern(device["pattern"], now):
                continue
            if random.random() > 0.5:
                continue
            rssi = device["rssi"] + random.randint(-8, 6)
            await queue.put(
                _adv(
                    device["address"],
                    device["payload"],
                    rssi,
                    device["address_type"],
                    source="replay",
                    when=now,
                )
            )
        await asyncio.sleep(2.0)


async def auto_backend(queue: asyncio.Queue, stop: asyncio.Event) -> None:
    port = Path(SERIAL_PORT)
    if port.exists():
        LOG.info("auto: trying serial %s", port)
        probe_ok = await _serial_looks_like_observer(str(port))
        if probe_ok:
            await serial_backend(queue, stop)
            return
        LOG.info("auto: serial present but not btlogger observer firmware")
    try:
        from bleak import BleakScanner  # noqa: F401

        LOG.info("auto: using bleak/BlueZ")
        await bleak_backend(queue, stop)
        return
    except Exception as exc:
        LOG.warning("auto: bleak unavailable (%s), falling back to replay", exc)
    await replay_backend(queue, stop)


async def _serial_looks_like_observer(port: str) -> bool:
    try:
        import serial
    except Exception:
        return False
    try:
        ser = serial.Serial(port, SERIAL_BAUD, timeout=1.5)
    except Exception:
        return False
    try:
        deadline = asyncio.get_running_loop().time() + 3
        buf = b""
        while asyncio.get_running_loop().time() < deadline:
            chunk = await asyncio.to_thread(ser.read, 256)
            buf += chunk
            if b'"t":"hello"' in buf or b'"t": "hello"' in buf or b'"t":"adv"' in buf:
                return True
            if not chunk:
                await asyncio.sleep(0.2)
        return False
    finally:
        ser.close()
