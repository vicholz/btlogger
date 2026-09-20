"""Ingest BLE advertisements into TimescaleDB."""

from __future__ import annotations

import asyncio
import logging
import os
import signal

from core.classify import classify
from core.db import Store, connect_pool
from core.parse import Advertisement, parse_ad
from core.timeutil import utcnow

from .backends import auto_backend, bleak_backend, replay_backend, serial_backend

LOG = logging.getLogger("btlogger.scanner")


def setup_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


async def handle_one(store: Store, adv: Advertisement) -> None:
    adv.parsed = parse_ad(adv.payload)
    adv.classification = classify(adv.parsed, adv.address, adv.address_type)
    await store.upsert_advertisement(adv)


async def run() -> None:
    setup_logging()
    backend_name = os.environ.get("SCANNER_BACKEND", "auto").lower()
    LOG.info("starting scanner backend=%s", backend_name)
    pool = None
    for attempt in range(40):
        try:
            pool = await connect_pool()
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            break
        except Exception as exc:
            LOG.warning("database not ready (%s), retry %s/40", exc, attempt + 1)
            if pool is not None:
                await pool.close()
                pool = None
            await asyncio.sleep(2)
    if pool is None:
        raise RuntimeError("could not connect to database")
    store = Store(pool)
    queue: asyncio.Queue[Advertisement] = asyncio.Queue(maxsize=5000)
    stop = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    if backend_name == "serial":
        producer = asyncio.create_task(serial_backend(queue, stop))
    elif backend_name == "bleak":
        producer = asyncio.create_task(bleak_backend(queue, stop))
    elif backend_name == "replay":
        producer = asyncio.create_task(replay_backend(queue, stop))
    else:
        producer = asyncio.create_task(auto_backend(queue, stop))

    ingested = 0
    try:
        while not stop.is_set():
            try:
                adv = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            try:
                await handle_one(store, adv)
                ingested += 1
                if ingested % 100 == 0:
                    LOG.info("ingested %s advertisements (queue=%s)", ingested, queue.qsize())
            except Exception:
                LOG.exception("failed to ingest advertisement from %s", adv.address)
            finally:
                queue.task_done()
    finally:
        stop.set()
        producer.cancel()
        try:
            await producer
        except (asyncio.CancelledError, Exception):
            pass
        await store.flush_open_visits()
        await pool.close()
        LOG.info("scanner stopped after %s advertisements", ingested)


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
