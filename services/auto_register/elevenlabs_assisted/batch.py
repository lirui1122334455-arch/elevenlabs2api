from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterable

from .proxy_preflight import normalize_proxy_url, safe_proxy_label


MAX_BATCH_COUNT = 20
MAX_BATCH_CONCURRENCY = 3
EXIT_COOLDOWN_SECONDS = 24 * 3600
SAME_EXIT_SKIP_MESSAGE = (
    "ElevenLabs allows one free account per IP; this exit was already used "
    "for a free account and will not be reused"
)


def parse_count(payload: Any) -> int:
    if payload is None or payload == "":
        return 1
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    raw = payload.get("count", 1)
    if raw is None or raw == "":
        return 1
    try:
        count = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("count must be an integer") from exc
    if count < 1 or count > MAX_BATCH_COUNT:
        raise ValueError(f"count must be between 1 and {MAX_BATCH_COUNT}")
    return count


def parse_proxy_urls(*values: Any) -> tuple[str, ...]:
    parts: list[str] = []
    for value in values:
        if value in {None, ""}:
            continue
        if isinstance(value, (list, tuple)):
            parts.extend(str(item) for item in value)
            continue
        parts.extend(re.split(r"[\n,;]+", str(value)))
    unique: list[str] = []
    seen: set[str] = set()
    for part in parts:
        proxy = normalize_proxy_url(part.strip())
        key = exit_key(proxy)
        if key in seen:
            continue
        seen.add(key)
        unique.append(proxy)
    return tuple(unique)


def exit_key(proxy_url: str) -> str:
    return safe_proxy_label(proxy_url)


def _parse_timestamp(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def used_exit_keys(
    records: Iterable[dict[str, Any]],
    *,
    now: datetime | None = None,
    cooldown_seconds: float = EXIT_COOLDOWN_SECONDS,
) -> set[str]:
    current = now or datetime.now(timezone.utc)
    used: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        status = str(record.get("status") or "").strip().lower()
        if status in {"failed", "registering"}:
            continue
        key = str(record.get("exit_label") or "").strip()
        if not key:
            continue
        stamped = _parse_timestamp(record.get("updated_at") or record.get("created_at"))
        if stamped is not None and (current - stamped).total_seconds() > cooldown_seconds:
            continue
        used.add(key)
    return used


def unused_exits(available: Iterable[str], used: Iterable[str]) -> list[str]:
    blocked = {str(item) for item in used}
    unused: list[str] = []
    seen: set[str] = set()
    values = list(available)
    if not values:
        values = [""]
    for proxy in values:
        key = exit_key(proxy)
        if key in seen or key in blocked:
            continue
        seen.add(key)
        unused.append(proxy)
    return unused


def concurrency_for(unused_count: int, requested: int) -> int:
    return max(0, min(MAX_BATCH_CONCURRENCY, int(unused_count), int(requested)))

