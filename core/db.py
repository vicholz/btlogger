"""TimescaleDB ingest and query helpers."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg

from .distance import annotate_distance, bucket_devices, parse_bins
from .timeutil import as_utc, current_visit_seconds, format_duration, is_present, utcnow

DEFAULT_URL = "postgresql://btlogger:btlogger@timescaledb:5432/btlogger"
IN_RANGE_SECONDS = int(os.environ.get("IN_RANGE_SECONDS", "30"))
VISIT_GAP_SECONDS = int(os.environ.get("VISIT_GAP_SECONDS", "120"))
SIGHTING_MIN_INTERVAL_MS = int(os.environ.get("SIGHTING_MIN_INTERVAL_MS", "1000"))


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_URL)


async def connect_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(database_url(), min_size=1, max_size=8)


def _hex(payload: bytes | None) -> str | None:
    if payload is None:
        return None
    return payload.hex()


def enrich_device(
    row: asyncpg.Record | dict | None,
    now: datetime | None = None,
    path_loss_n: float | None = None,
    tx_power_1m: int | float | None = None,
) -> dict | None:
    if row is None:
        return None
    data = dict(row)
    now = as_utc(now) or utcnow()
    last_seen = as_utc(data.get("last_seen"))
    first_seen = as_utc(data.get("first_seen"))
    present = is_present(last_seen, now, IN_RANGE_SECONDS)
    visit_secs = current_visit_seconds(
        data.get("last_visit_start"),
        last_seen,
        now=now,
        present=present,
        window_seconds=IN_RANGE_SECONDS,
    )
    last_visit_end = as_utc(data.get("last_visit_end"))
    last_closed = None
    if last_visit_end and data.get("last_visit_start"):
        last_closed = max(
            0,
            int((last_visit_end - as_utc(data["last_visit_start"])).total_seconds()),
        )
    known_span = None
    if first_seen and last_seen:
        known_span = max(0, int((last_seen - first_seen).total_seconds()))
    stored_dwell = int(data.get("total_dwell_seconds") or 0)
    if data.get("last_visit_start") and last_visit_end is None and visit_secs:
        stored_dwell += visit_secs
    data["total_dwell_seconds"] = stored_dwell
    parsed = data.get("last_parsed")
    if isinstance(parsed, str):
        try:
            data["last_parsed"] = json.loads(parsed)
        except json.JSONDecodeError:
            pass
    if data.get("last_payload") is not None and not isinstance(data["last_payload"], str):
        data["last_payload_hex"] = _hex(data["last_payload"])
        data.pop("last_payload", None)
    data["present"] = present
    data["current_visit_seconds"] = visit_secs if present else None
    data["current_visit_human"] = format_duration(visit_secs) if present else None
    data["last_visit_seconds"] = visit_secs if not present else last_closed
    data["last_visit_human"] = format_duration(data["last_visit_seconds"])
    data["total_dwell_human"] = format_duration(data.get("total_dwell_seconds"))
    data["known_span_seconds"] = known_span
    data["known_span_human"] = format_duration(known_span)
    data["in_range_window_seconds"] = IN_RANGE_SECONDS
    for key in ("first_seen", "last_seen", "last_visit_start", "last_visit_end"):
        value = as_utc(data.get(key))
        data[key] = value.isoformat() if value else None
    if data.get("service_uuids") is None:
        data["service_uuids"] = []
    annotate_distance(data, path_loss_n=path_loss_n, tx_power_1m=tx_power_1m)
    return data


class Store:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
        self._last_sighting: dict[int, datetime] = {}
        self._open_visits: dict[int, dict[str, Any]] = {}
        self._min_interval = timedelta(milliseconds=SIGHTING_MIN_INTERVAL_MS)
        self._gap = timedelta(seconds=VISIT_GAP_SECONDS)

    async def upsert_advertisement(self, adv) -> None:
        parsed = adv.parsed
        classified = adv.classification
        when = as_utc(adv.time) or utcnow()
        address = adv.address.upper()
        address_type = adv.address_type or "random"
        name = classified.get("name") or parsed.get("name")
        device_type = classified.get("device_type")
        manufacturer_id = classified.get("manufacturer_id")
        manufacturer_name = classified.get("manufacturer_name")
        appearance = classified.get("appearance")
        appearance_name = classified.get("appearance_name")
        identity_hint = classified.get("stable_id")
        uuids = [u.lower() for u in (parsed.get("uuids") or [])]
        payload = adv.payload
        rssi = adv.rssi
        adv_type = adv.adv_type
        parsed_json = json.dumps(parsed)

        async with self.pool.acquire() as conn:
            existing = await conn.fetchrow(
                """
                SELECT id, last_seen, last_visit_start, last_visit_end
                FROM devices
                WHERE address = $1 AND address_type = $2
                """,
                address,
                address_type,
            )
            if existing is None:
                row = await conn.fetchrow(
                    """
                    INSERT INTO devices (
                        address, address_type, first_seen, last_seen, last_rssi,
                        name, manufacturer_id, manufacturer_name, device_type,
                        appearance, appearance_name, identity_hint, service_uuids,
                        sighting_count, packet_count, visit_count, total_dwell_seconds,
                        last_visit_start, last_visit_end, last_payload, last_parsed
                    )
                    VALUES (
                        $1,$2,$3,$3,$4,
                        $5,$6,$7,$8,
                        $9,$10,$11,$12::text[],
                        0,1,1,0,
                        $3,NULL,$13,$14::jsonb
                    )
                    RETURNING id
                    """,
                    address,
                    address_type,
                    when,
                    rssi,
                    name,
                    manufacturer_id,
                    manufacturer_name,
                    device_type,
                    appearance,
                    appearance_name,
                    identity_hint,
                    uuids,
                    payload,
                    parsed_json,
                )
                device_id = row["id"]
                self._open_visits[device_id] = {
                    "start": when,
                    "end": when,
                    "sightings": 1,
                    "rssi": [rssi] if rssi is not None else [],
                }
            else:
                device_id = existing["id"]
                await conn.execute(
                    """
                    UPDATE devices SET
                        last_seen = $2,
                        last_rssi = COALESCE($3, last_rssi),
                        name = COALESCE($4, name),
                        manufacturer_id = COALESCE($5, manufacturer_id),
                        manufacturer_name = COALESCE($6, manufacturer_name),
                        device_type = COALESCE($7, device_type),
                        appearance = COALESCE($8, appearance),
                        appearance_name = COALESCE($9, appearance_name),
                        identity_hint = COALESCE($10, identity_hint),
                        service_uuids = CASE
                            WHEN $11::text[] = '{}'::text[] THEN service_uuids
                            ELSE (
                                SELECT ARRAY(SELECT DISTINCT unnest(service_uuids || $11::text[]))
                            )
                        END,
                        packet_count = packet_count + 1,
                        last_payload = $12,
                        last_parsed = $13::jsonb
                    WHERE id = $1
                    """,
                    device_id,
                    when,
                    rssi,
                    name,
                    manufacturer_id,
                    manufacturer_name,
                    device_type,
                    appearance,
                    appearance_name,
                    identity_hint,
                    uuids,
                    payload,
                    parsed_json,
                )
                await self._touch_visit(
                    conn,
                    device_id,
                    when,
                    rssi,
                    old_last_seen=as_utc(existing["last_seen"]),
                    old_visit_start=as_utc(existing["last_visit_start"]),
                    old_visit_end=as_utc(existing["last_visit_end"]),
                )

            last = self._last_sighting.get(device_id)
            if last is None or (when - last) >= self._min_interval:
                self._last_sighting[device_id] = when
                await conn.execute(
                    """
                    INSERT INTO sightings (time, device_id, rssi, adv_type, payload, parsed, device_type, name)
                    VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,$8)
                    """,
                    when,
                    device_id,
                    rssi,
                    adv_type,
                    payload,
                    parsed_json,
                    device_type,
                    name,
                )
                await conn.execute(
                    "UPDATE devices SET sighting_count = sighting_count + 1 WHERE id = $1",
                    device_id,
                )

    async def _touch_visit(
        self,
        conn: asyncpg.Connection,
        device_id: int,
        when: datetime,
        rssi: int | None,
        old_last_seen: datetime | None,
        old_visit_start: datetime | None,
        old_visit_end: datetime | None,
    ) -> None:
        open_visit = self._open_visits.get(device_id)
        if open_visit is None and old_visit_start and old_visit_end is None and old_last_seen:
            open_visit = {
                "start": old_visit_start,
                "end": old_last_seen,
                "sightings": 1,
                "rssi": [],
            }
            self._open_visits[device_id] = open_visit

        if open_visit and when - open_visit["end"] > self._gap:
            await self._close_visit(conn, device_id, open_visit)
            open_visit = None

        if open_visit is None:
            await conn.execute(
                """
                UPDATE devices
                SET last_visit_start = $2, last_visit_end = NULL,
                    visit_count = visit_count + 1
                WHERE id = $1
                """,
                device_id,
                when,
            )
            self._open_visits[device_id] = {
                "start": when,
                "end": when,
                "sightings": 1,
                "rssi": [rssi] if rssi is not None else [],
            }
            return

        open_visit["end"] = when
        open_visit["sightings"] += 1
        if rssi is not None:
            open_visit["rssi"].append(rssi)
        self._open_visits[device_id] = open_visit

    async def _close_visit(self, conn: asyncpg.Connection, device_id: int, visit: dict) -> None:
        rssi_vals = [v for v in visit.get("rssi") or [] if v is not None]
        duration = max(0, int((visit["end"] - visit["start"]).total_seconds()))
        await conn.execute(
            """
            INSERT INTO visits (
                device_id, start_time, end_time, duration_seconds,
                sightings, min_rssi, max_rssi, avg_rssi
            )
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            """,
            device_id,
            visit["start"],
            visit["end"],
            duration,
            visit.get("sightings") or 1,
            min(rssi_vals) if rssi_vals else None,
            max(rssi_vals) if rssi_vals else None,
            int(sum(rssi_vals) / len(rssi_vals)) if rssi_vals else None,
        )
        await conn.execute(
            """
            UPDATE devices
            SET last_visit_end = $2,
                total_dwell_seconds = total_dwell_seconds + $3
            WHERE id = $1
            """,
            device_id,
            visit["end"],
            duration,
        )
        self._open_visits.pop(device_id, None)

    async def flush_open_visits(self) -> None:
        async with self.pool.acquire() as conn:
            for device_id, visit in list(self._open_visits.items()):
                await self._close_visit(conn, device_id, visit)

    async def stats(self, since: datetime | None = None) -> dict:
        since = since or utcnow() - timedelta(hours=24)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    (SELECT COUNT(*) FROM devices) AS devices,
                    (SELECT COUNT(*) FROM devices WHERE last_seen >= $1) AS recently_seen,
                    (SELECT COUNT(*) FROM devices WHERE last_seen >= $2) AS present,
                    (SELECT COUNT(*) FROM sightings WHERE time >= $1) AS sightings,
                    (SELECT COALESCE(SUM(duration_seconds),0) FROM visits WHERE start_time >= $1)
                        + (
                            SELECT COALESCE(SUM(EXTRACT(EPOCH FROM (last_seen - last_visit_start))),0)
                            FROM devices
                            WHERE last_visit_end IS NULL AND last_visit_start IS NOT NULL
                              AND last_seen >= $1
                          ) AS dwell_seconds,
                    (SELECT MAX(last_seen) FROM devices) AS newest
                """,
                since,
                utcnow() - timedelta(seconds=IN_RANGE_SECONDS),
            )
        data = dict(row)
        newest = as_utc(data.get("newest"))
        data["newest"] = newest.isoformat() if newest else None
        data["dwell_human"] = format_duration(int(data.get("dwell_seconds") or 0))
        data["since"] = since.isoformat()
        return data

    async def list_devices(
        self,
        q: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        device_type: str | None = None,
        manufacturer: str | None = None,
        present_only: bool = False,
        repeats_only: bool = False,
        limit: int = 200,
        offset: int = 0,
        order: str = "last_seen",
        path_loss_n: float | None = None,
        tx_power_1m: int | float | None = None,
    ) -> list[dict]:
        clauses = ["TRUE"]
        args: list[Any] = []

        def add(clause: str, value) -> None:
            args.append(value)
            clauses.append(clause.replace("?", f"${len(args)}"))

        if q:
            args.append(f"%{q.lower()}%")
            n = len(args)
            clauses.append(
                f"(LOWER(address) LIKE ${n} OR LOWER(COALESCE(name,'')) LIKE ${n} "
                f"OR LOWER(COALESCE(device_type,'')) LIKE ${n} "
                f"OR LOWER(COALESCE(manufacturer_name,'')) LIKE ${n} "
                f"OR LOWER(COALESCE(identity_hint,'')) LIKE ${n})"
            )
        if since:
            add("last_seen >= ?", since)
        if until:
            add("first_seen <= ?", until)
        if device_type:
            add("device_type = ?", device_type)
        if manufacturer:
            add("manufacturer_name ILIKE ?", f"%{manufacturer}%")
        if present_only:
            add("last_seen >= ?", utcnow() - timedelta(seconds=IN_RANGE_SECONDS))
        if repeats_only:
            clauses.append("visit_count >= 2")

        order_sql = {
            "last_seen": "last_seen DESC",
            "first_seen": "first_seen DESC",
            "dwell": "total_dwell_seconds DESC, last_seen DESC",
            "visits": "visit_count DESC, last_seen DESC",
            "rssi": "last_rssi DESC NULLS LAST",
            "distance": "last_rssi DESC NULLS LAST",
            "name": "name ASC NULLS LAST",
        }.get(order, "last_seen DESC")

        args.extend([limit, offset])
        sql = f"""
            SELECT * FROM devices
            WHERE {' AND '.join(clauses)}
            ORDER BY {order_sql}
            LIMIT ${len(args) - 1} OFFSET ${len(args)}
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
        now = utcnow()
        return [enrich_device(r, now, path_loss_n, tx_power_1m) for r in rows]

    async def get_device(
        self,
        address: str,
        path_loss_n: float | None = None,
        tx_power_1m: int | float | None = None,
    ) -> dict | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM devices WHERE address = $1 ORDER BY last_seen DESC LIMIT 1",
                address.upper(),
            )
        return enrich_device(row, path_loss_n=path_loss_n, tx_power_1m=tx_power_1m)

    async def get_device_by_id(
        self,
        device_id: int,
        path_loss_n: float | None = None,
        tx_power_1m: int | float | None = None,
    ) -> dict | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM devices WHERE id = $1", device_id)
        return enrich_device(row, path_loss_n=path_loss_n, tx_power_1m=tx_power_1m)

    async def history(self, device_id: int, since: datetime | None, until: datetime | None, limit: int = 500) -> list[dict]:
        since = since or utcnow() - timedelta(days=7)
        until = until or utcnow()
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT time, rssi, adv_type, payload, parsed, device_type, name
                FROM sightings
                WHERE device_id = $1 AND time >= $2 AND time <= $3
                ORDER BY time DESC
                LIMIT $4
                """,
                device_id,
                since,
                until,
                limit,
            )
        out = []
        for row in rows:
            item = dict(row)
            item["time"] = as_utc(item["time"]).isoformat()
            item["payload_hex"] = _hex(item.pop("payload"))
            out.append(item)
        return out

    async def visits(self, device_id: int, limit: int = 200) -> list[dict]:
        async with self.pool.acquire() as conn:
            device = await conn.fetchrow(
                "SELECT last_visit_start, last_visit_end, last_seen FROM devices WHERE id = $1",
                device_id,
            )
            rows = await conn.fetch(
                """
                SELECT start_time, end_time, duration_seconds, sightings, min_rssi, max_rssi, avg_rssi
                FROM visits
                WHERE device_id = $1
                ORDER BY start_time DESC
                LIMIT $2
                """,
                device_id,
                limit,
            )
        now = utcnow()
        items = []
        if device and device["last_visit_start"] and device["last_visit_end"] is None:
            start = as_utc(device["last_visit_start"])
            last_seen = as_utc(device["last_seen"])
            present = is_present(last_seen, now)
            end = now if present else last_seen
            items.append(
                {
                    "start_time": start.isoformat(),
                    "end_time": None if present else as_utc(end).isoformat(),
                    "duration_seconds": max(0, int((end - start).total_seconds())),
                    "duration_human": format_duration(max(0, int((end - start).total_seconds()))),
                    "sightings": None,
                    "open": True,
                    "present": present,
                    "min_rssi": None,
                    "max_rssi": None,
                    "avg_rssi": None,
                }
            )
        for row in rows:
            item = dict(row)
            item["start_time"] = as_utc(item["start_time"]).isoformat()
            item["end_time"] = as_utc(item["end_time"]).isoformat()
            item["duration_human"] = format_duration(item["duration_seconds"])
            item["open"] = False
            item["present"] = False
            items.append(item)
        return items

    async def pattern(self, device_id: int | None = None, days: int = 14) -> dict:
        since = utcnow() - timedelta(days=days)
        where = "time >= $1"
        args: list[Any] = [since]
        if device_id is not None:
            args.append(device_id)
            where += f" AND device_id = ${len(args)}"
        sql = f"""
            SELECT EXTRACT(DOW FROM time)::int AS dow,
                   EXTRACT(HOUR FROM time)::int AS hour,
                   COUNT(*) AS sightings,
                   COUNT(DISTINCT device_id) AS devices
            FROM sightings
            WHERE {where}
            GROUP BY 1, 2
            ORDER BY 1, 2
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
        cells = [dict(r) for r in rows]
        return {"days": days, "since": since.isoformat(), "cells": cells}

    async def seen_in_range(
        self,
        since: datetime,
        until: datetime,
        limit: int = 500,
        path_loss_n: float | None = None,
        tx_power_1m: int | float | None = None,
    ) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT d.*,
                       COUNT(s.*)::bigint AS range_sightings,
                       MIN(s.time) AS range_first,
                       MAX(s.time) AS range_last,
                       AVG(s.rssi)::smallint AS range_avg_rssi
                FROM devices d
                JOIN sightings s ON s.device_id = d.id
                WHERE s.time >= $1 AND s.time <= $2
                GROUP BY d.id
                ORDER BY range_last DESC
                LIMIT $3
                """,
                since,
                until,
                limit,
            )
        now = utcnow()
        out = []
        for row in rows:
            item = enrich_device(row, now, path_loss_n, tx_power_1m)
            range_first = as_utc(row["range_first"])
            range_last = as_utc(row["range_last"])
            span = max(0, int((range_last - range_first).total_seconds())) if range_first and range_last else 0
            item["range_sightings"] = row["range_sightings"]
            item["range_first"] = range_first.isoformat() if range_first else None
            item["range_last"] = range_last.isoformat() if range_last else None
            item["range_avg_rssi"] = row["range_avg_rssi"]
            item["range_span_seconds"] = span
            item["range_span_human"] = format_duration(span)
            out.append(item)
        return out

    async def repeats(
        self,
        min_visits: int = 2,
        limit: int = 200,
        path_loss_n: float | None = None,
        tx_power_1m: int | float | None = None,
    ) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM devices
                WHERE visit_count >= $1
                ORDER BY visit_count DESC, total_dwell_seconds DESC
                LIMIT $2
                """,
                min_visits,
                limit,
            )
        now = utcnow()
        return [enrich_device(r, now, path_loss_n, tx_power_1m) for r in rows]

    async def types(self) -> list[str]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT device_type FROM devices WHERE device_type IS NOT NULL ORDER BY 1"
            )
        return [r[0] for r in rows]

    async def report(
        self,
        since: datetime,
        until: datetime,
        path_loss_n: float | None = None,
        tx_power_1m: int | float | None = None,
    ) -> dict:
        async with self.pool.acquire() as conn:
            summary = await conn.fetchrow(
                """
                SELECT
                    (SELECT COUNT(*) FROM devices WHERE last_seen >= $1 AND first_seen <= $2) AS devices,
                    (SELECT COUNT(*) FROM sightings WHERE time >= $1 AND time <= $2) AS sightings,
                    (SELECT COUNT(*) FROM visits WHERE start_time >= $1 AND start_time <= $2) AS visits,
                    (SELECT COALESCE(SUM(duration_seconds), 0) FROM visits
                     WHERE start_time >= $1 AND start_time <= $2) AS dwell_seconds
                """,
                since,
                until,
            )
            top_dwell = await conn.fetch(
                """
                SELECT d.address, d.name, d.device_type, d.manufacturer_name,
                       d.visit_count, d.total_dwell_seconds, d.last_seen,
                       d.last_rssi, d.last_parsed
                FROM devices d
                WHERE d.last_seen >= $1 AND d.first_seen <= $2
                ORDER BY d.total_dwell_seconds DESC
                LIMIT 20
                """,
                since,
                until,
            )
            top_types = await conn.fetch(
                """
                SELECT COALESCE(device_type, 'Unknown') AS device_type, COUNT(*) AS n
                FROM devices
                WHERE last_seen >= $1 AND first_seen <= $2
                GROUP BY 1
                ORDER BY n DESC
                LIMIT 15
                """,
                since,
                until,
            )
            top_mfg = await conn.fetch(
                """
                SELECT COALESCE(manufacturer_name, 'Unknown') AS manufacturer_name, COUNT(*) AS n
                FROM devices
                WHERE last_seen >= $1 AND first_seen <= $2
                GROUP BY 1
                ORDER BY n DESC
                LIMIT 15
                """,
                since,
                until,
            )
        now = utcnow()
        return {
            "since": since.isoformat(),
            "until": until.isoformat(),
            "devices": summary["devices"],
            "sightings": summary["sightings"],
            "visits": summary["visits"],
            "dwell_seconds": int(summary["dwell_seconds"] or 0),
            "dwell_human": format_duration(int(summary["dwell_seconds"] or 0)),
            "longest_seen": [enrich_device(r, now, path_loss_n, tx_power_1m) for r in top_dwell],
            "types": [dict(r) for r in top_types],
            "manufacturers": [dict(r) for r in top_mfg],
        }

    async def distance_report(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
        present_only: bool = False,
        path_loss_n: float | None = None,
        tx_power_1m: int | float | None = None,
        bins: str | list[float] | None = None,
        limit: int = 500,
    ) -> dict:
        edges = parse_bins(bins)
        devices = await self.list_devices(
            since=since,
            until=until,
            present_only=present_only,
            limit=limit,
            order="distance",
            path_loss_n=path_loss_n,
            tx_power_1m=tx_power_1m,
        )
        buckets, unknown = bucket_devices(devices, edges)
        measured = [d for d in devices if d.get("distance_m") is not None]
        nearest = min(measured, key=lambda d: d["distance_m"], default=None)
        farthest = max(measured, key=lambda d: d["distance_m"], default=None)
        return {
            "path_loss_n": path_loss_n,
            "tx_power_1m": tx_power_1m,
            "bins_m": edges,
            "present_only": present_only,
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
            "count": len(devices),
            "measured": len(measured),
            "unknown": len(unknown),
            "nearest": nearest,
            "farthest": farthest,
            "buckets": [
                {
                    "index": b["index"],
                    "min_m": b["min_m"],
                    "max_m": b["max_m"],
                    "label": b["label"],
                    "count": b["count"],
                    "devices": b["devices"],
                }
                for b in buckets
            ],
            "unmeasured": unknown,
            "note": "Approximate range from RSSI. Walls, pockets, and antenna orientation throw this off by meters.",
        }
