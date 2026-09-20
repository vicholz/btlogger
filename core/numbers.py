"""Bluetooth assigned-number lookups (company IDs, service UUIDs, appearances)."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from .appearances import APPEARANCES

_DEFAULT_DATA = Path(__file__).resolve().parent.parent / "data"


def data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", _DEFAULT_DATA))


@lru_cache(maxsize=1)
def company_names() -> dict[int, str]:
    path = data_dir() / "company_ids.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    return {int(k): v for k, v in raw.items()}


@lru_cache(maxsize=1)
def service_names() -> dict[str, str]:
    path = data_dir() / "service_uuids.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    return {str(k).replace("-", "").lower(): v for k, v in raw.items()}


def company_name(company_id: int | None) -> str | None:
    if company_id is None:
        return None
    return company_names().get(int(company_id))


def appearance_name(value: int | None) -> str | None:
    if value is None:
        return None
    name = APPEARANCES.get(int(value))
    if name:
        return name
    category = int(value) & 0xFFC0
    return APPEARANCES.get(category)


def normalize_uuid(uuid: str | int) -> str:
    if isinstance(uuid, int):
        if uuid <= 0xFFFF:
            return f"{uuid:04x}"
        if uuid <= 0xFFFFFFFF:
            return f"{uuid:08x}"
        return f"{uuid:032x}"
    text = str(uuid).replace("-", "").lower()
    if len(text) == 32 and text.endswith("00001000800000805f9b34fb"):
        return text[:4].lstrip("0").zfill(4) if text.startswith("0000") else text
    return text


def service_name(uuid: str | int) -> str | None:
    key = normalize_uuid(uuid)
    names = service_names()
    if key in names:
        return names[key]
    if len(key) == 4:
        return names.get(key)
    if len(key) == 32 and key.startswith("0000") and key.endswith("00001000800000805f9b34fb"):
        return names.get(key[4:8])
    return None


def format_uuid(uuid: str) -> str:
    key = normalize_uuid(uuid)
    if len(key) == 4:
        return key.upper()
    if len(key) == 8:
        return key.upper()
    if len(key) == 32:
        return (
            f"{key[0:8]}-{key[8:12]}-{key[12:16]}-{key[16:20]}-{key[20:32]}"
        )
    return str(uuid)
