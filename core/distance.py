"""Approximate distance from BLE RSSI using a log-distance path-loss model.

This is a rough range band, not a measurement. Walls, bodies, antenna
orientation, and advertising power swing the estimate by meters.
"""

from __future__ import annotations

import math
import os
from typing import Any

DEFAULT_N = float(os.environ.get("PATH_LOSS_N", "2.7"))
DEFAULT_TX_POWER_1M = int(os.environ.get("TX_POWER_1M", "-59"))
MAX_DISTANCE_M = 80.0

PRESETS = {
    "open": {"n": 2.0, "label": "Open air", "hint": "line of sight, little clutter"},
    "indoor": {"n": 2.7, "label": "Indoor", "hint": "typical room / office"},
    "dense": {"n": 3.5, "label": "Dense indoor", "hint": "walls, pockets, crowded RF"},
}

DEFAULT_BINS_M = (1.0, 3.0, 8.0)


def advertised_tx_power(parsed: dict | None) -> int | None:
    """RSSI at 1 m, if the advertisement actually carries that.

    iBeacon measured power is defined at 1 m. GAP TX Power and Eddystone
    calibrated power are *not* 1 m RSSI, so they are ignored here.
    """
    if not parsed:
        return None
    hex_data = parsed.get("manufacturer_data_hex") or ""
    if parsed.get("manufacturer_id") == 0x004C and hex_data:
        try:
            raw = bytes.fromhex(hex_data)
        except ValueError:
            raw = b""
        if len(raw) >= 23 and raw[0] == 0x02 and raw[1] == 0x15:
            return int.from_bytes(raw[22:23], "big", signed=True)
    return None


def estimate_distance_m(
    rssi: int | float | None,
    tx_power_1m: int | float | None = None,
    path_loss_n: float | None = None,
) -> float | None:
    if rssi is None:
        return None
    tx = DEFAULT_TX_POWER_1M if tx_power_1m is None else float(tx_power_1m)
    n = DEFAULT_N if path_loss_n is None else float(path_loss_n)
    if n <= 0:
        n = DEFAULT_N
    ratio = (tx - float(rssi)) / (10.0 * n)
    try:
        meters = 10 ** ratio
    except OverflowError:
        return MAX_DISTANCE_M
    if not math.isfinite(meters) or meters < 0:
        return None
    return min(MAX_DISTANCE_M, meters)


def format_distance(meters: float | None) -> str | None:
    if meters is None:
        return None
    if meters >= MAX_DISTANCE_M:
        return f"{int(MAX_DISTANCE_M)}+ m"
    if meters < 0.5:
        return "< 0.5 m"
    if meters < 10:
        return f"{meters:.1f} m"
    return f"{meters:.0f} m"


def parse_bins(raw: str | list[float] | tuple[float, ...] | None) -> list[float]:
    if raw is None or raw == "":
        return list(DEFAULT_BINS_M)
    if isinstance(raw, (list, tuple)):
        values = [float(x) for x in raw]
    else:
        values = []
        for part in str(raw).replace(" ", "").split(","):
            if not part:
                continue
            values.append(float(part))
    values = sorted({v for v in values if v > 0})
    return values or list(DEFAULT_BINS_M)


def bin_label(lo: float | None, hi: float | None) -> str:
    if lo is None and hi is not None:
        return f"< {format_distance(hi)}"
    if hi is None and lo is not None:
        return f"≥ {format_distance(lo)}"
    return f"{format_distance(lo)} – {format_distance(hi)}"


def bucket_for(meters: float | None, bins: list[float]) -> int:
    """Return bucket index, or -1 if unknown."""
    if meters is None:
        return -1
    for i, edge in enumerate(bins):
        if meters < edge:
            return i
    return len(bins)


def annotate_distance(
    data: dict[str, Any],
    rssi: int | float | None = None,
    path_loss_n: float | None = None,
    tx_power_1m: int | float | None = None,
) -> dict[str, Any]:
    parsed = data.get("last_parsed") if isinstance(data.get("last_parsed"), dict) else None
    advertised = advertised_tx_power(parsed)
    tx = advertised if advertised is not None else (
        DEFAULT_TX_POWER_1M if tx_power_1m is None else tx_power_1m
    )
    n = DEFAULT_N if path_loss_n is None else path_loss_n
    rssi = data.get("last_rssi") if rssi is None else rssi
    meters = estimate_distance_m(rssi, tx, n)
    data["distance_m"] = round(meters, 2) if meters is not None else None
    data["distance_human"] = format_distance(meters)
    data["distance_tx_power"] = int(tx) if tx is not None else None
    data["distance_tx_source"] = "advertised" if advertised is not None else "calibrated"
    data["distance_path_loss_n"] = n
    return data


def bucket_devices(
    devices: list[dict],
    bins: list[float],
) -> tuple[list[dict], list[dict]]:
    edges = [(None, bins[0])]
    for i in range(len(bins) - 1):
        edges.append((bins[i], bins[i + 1]))
    edges.append((bins[-1], None))
    buckets = [
        {
            "index": i,
            "min_m": lo,
            "max_m": hi,
            "label": bin_label(lo, hi),
            "count": 0,
            "devices": [],
        }
        for i, (lo, hi) in enumerate(edges)
    ]
    unknown = []
    for device in devices:
        idx = bucket_for(device.get("distance_m"), bins)
        if idx < 0:
            unknown.append(device)
            continue
        buckets[idx]["count"] += 1
        buckets[idx]["devices"].append(device)
    return buckets, unknown
