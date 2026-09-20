"""Human-readable durations and presence helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def format_duration(seconds: int | float | None) -> str | None:
    if seconds is None:
        return None
    total = int(max(0, seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)


def is_present(last_seen: datetime | None, now: datetime | None = None, window_seconds: int = 30) -> bool:
    last_seen = as_utc(last_seen)
    if last_seen is None:
        return False
    now = as_utc(now) or utcnow()
    return (now - last_seen) <= timedelta(seconds=window_seconds)


def current_visit_seconds(
    last_visit_start: datetime | None,
    last_seen: datetime | None,
    now: datetime | None = None,
    present: bool | None = None,
    window_seconds: int = 30,
) -> int | None:
    last_visit_start = as_utc(last_visit_start)
    last_seen = as_utc(last_seen)
    if last_visit_start is None:
        return None
    now = as_utc(now) or utcnow()
    if present is None:
        present = is_present(last_seen, now, window_seconds)
    end = now if present else last_seen
    if end is None:
        return None
    return max(0, int((end - last_visit_start).total_seconds()))
