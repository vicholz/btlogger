from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from core.db import Store, connect_pool
from core.distance import DEFAULT_N, DEFAULT_TX_POWER_1M, PRESETS
from core.timeutil import as_utc, utcnow

STATIC = Path(__file__).resolve().parent / "static"


def _parse_dt(value: str | None, default: datetime | None = None) -> datetime | None:
    if not value:
        return default
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(400, f"invalid timestamp: {value}") from exc
    return as_utc(parsed)


def _path_loss(n: float | None, tx_power: int | None, env: str | None) -> tuple[float, int]:
    if n is None and env and env in PRESETS:
        n = float(PRESETS[env]["n"])
    return (
        float(n) if n is not None else DEFAULT_N,
        int(tx_power) if tx_power is not None else DEFAULT_TX_POWER_1M,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await connect_pool()
    app.state.store = Store(pool)
    try:
        yield
    finally:
        await pool.close()


app = FastAPI(title="btlogger", version="1.0.0", lifespan=lifespan)


def store() -> Store:
    return app.state.store


@app.get("/api/health")
async def health():
    return {"ok": True, "time": utcnow().isoformat()}


@app.get("/api/stats")
async def stats(hours: int = Query(24, ge=1, le=24 * 90)):
    return await store().stats(utcnow() - timedelta(hours=hours))


@app.get("/api/types")
async def types():
    return {"types": await store().types()}


@app.get("/api/devices")
async def devices(
    q: str | None = None,
    since: str | None = None,
    until: str | None = None,
    device_type: str | None = None,
    manufacturer: str | None = None,
    present: bool = False,
    repeats: bool = False,
    order: str = "last_seen",
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    n: float | None = Query(None, ge=1.0, le=6.0),
    tx_power: int | None = Query(None, ge=-100, le=20),
    env: str | None = None,
):
    path_n, tx = _path_loss(n, tx_power, env)
    rows = await store().list_devices(
        q=q,
        since=_parse_dt(since),
        until=_parse_dt(until),
        device_type=device_type,
        manufacturer=manufacturer,
        present_only=present,
        repeats_only=repeats,
        limit=limit,
        offset=offset,
        order=order,
        path_loss_n=path_n,
        tx_power_1m=tx,
    )
    return {"devices": rows, "count": len(rows), "path_loss_n": path_n, "tx_power_1m": tx}


@app.get("/api/devices/{address}")
async def device(
    address: str,
    n: float | None = Query(None, ge=1.0, le=6.0),
    tx_power: int | None = Query(None, ge=-100, le=20),
    env: str | None = None,
):
    path_n, tx = _path_loss(n, tx_power, env)
    row = await store().get_device(address, path_loss_n=path_n, tx_power_1m=tx)
    if not row:
        raise HTTPException(404, "device not found")
    return row


@app.get("/api/devices/{address}/history")
async def history(
    address: str,
    since: str | None = None,
    until: str | None = None,
    limit: int = Query(400, ge=1, le=2000),
):
    row = await store().get_device(address)
    if not row:
        raise HTTPException(404, "device not found")
    items = await store().history(row["id"], _parse_dt(since), _parse_dt(until), limit)
    return {"address": row["address"], "count": len(items), "sightings": items}


@app.get("/api/devices/{address}/visits")
async def visits(address: str, limit: int = Query(200, ge=1, le=1000)):
    row = await store().get_device(address)
    if not row:
        raise HTTPException(404, "device not found")
    items = await store().visits(row["id"], limit)
    return {
        "address": row["address"],
        "total_dwell_seconds": row["total_dwell_seconds"],
        "total_dwell_human": row["total_dwell_human"],
        "visit_count": row["visit_count"],
        "visits": items,
    }


@app.get("/api/devices/{address}/pattern")
async def device_pattern(address: str, days: int = Query(14, ge=1, le=90)):
    row = await store().get_device(address)
    if not row:
        raise HTTPException(404, "device not found")
    return await store().pattern(row["id"], days)


@app.get("/api/seen")
async def seen(
    since: str | None = None,
    until: str | None = None,
    limit: int = Query(500, ge=1, le=2000),
    n: float | None = Query(None, ge=1.0, le=6.0),
    tx_power: int | None = Query(None, ge=-100, le=20),
    env: str | None = None,
):
    end = _parse_dt(until) or utcnow()
    start = _parse_dt(since) or (end - timedelta(hours=24))
    if start >= end:
        raise HTTPException(400, "since must be before until")
    path_n, tx = _path_loss(n, tx_power, env)
    rows = await store().seen_in_range(start, end, limit, path_loss_n=path_n, tx_power_1m=tx)
    return {"since": start.isoformat(), "until": end.isoformat(), "count": len(rows), "devices": rows}


@app.get("/api/repeats")
async def repeats(
    min_visits: int = Query(2, ge=2, le=1000),
    limit: int = Query(200, ge=1, le=1000),
    n: float | None = Query(None, ge=1.0, le=6.0),
    tx_power: int | None = Query(None, ge=-100, le=20),
    env: str | None = None,
):
    path_n, tx = _path_loss(n, tx_power, env)
    rows = await store().repeats(min_visits, limit, path_loss_n=path_n, tx_power_1m=tx)
    return {"count": len(rows), "devices": rows}


@app.get("/api/pattern")
async def pattern(days: int = Query(14, ge=1, le=90)):
    return await store().pattern(None, days)


@app.get("/api/report")
async def report(
    since: str | None = None,
    until: str | None = None,
    n: float | None = Query(None, ge=1.0, le=6.0),
    tx_power: int | None = Query(None, ge=-100, le=20),
    env: str | None = None,
):
    end = _parse_dt(until) or utcnow()
    start = _parse_dt(since) or (end - timedelta(days=7))
    path_n, tx = _path_loss(n, tx_power, env)
    return await store().report(start, end, path_loss_n=path_n, tx_power_1m=tx)


@app.get("/api/distance/model")
async def distance_model():
    return {
        "default_n": DEFAULT_N,
        "default_tx_power_1m": DEFAULT_TX_POWER_1M,
        "presets": PRESETS,
        "default_bins_m": [1, 3, 8],
        "note": "d = 10^((P1m - RSSI) / (10 n)). Calibrate P1m with a known device at 1 meter.",
    }


@app.get("/api/report/distance")
async def report_distance(
    since: str | None = None,
    until: str | None = None,
    present: bool = False,
    n: float | None = Query(None, ge=1.0, le=6.0),
    tx_power: int | None = Query(None, ge=-100, le=20),
    env: str | None = None,
    bins: str = "1,3,8",
    limit: int = Query(500, ge=1, le=2000),
):
    path_n, tx = _path_loss(n, tx_power, env)
    end = _parse_dt(until)
    start = _parse_dt(since)
    return await store().distance_report(
        since=start,
        until=end,
        present_only=present,
        path_loss_n=path_n,
        tx_power_1m=tx,
        bins=bins,
        limit=limit,
    )


if STATIC.exists():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
async def index():
    index = STATIC / "index.html"
    if not index.exists():
        raise HTTPException(404, "dashboard not built")
    return FileResponse(index)
