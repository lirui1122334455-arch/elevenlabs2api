from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_STORE_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def account_id(email: str) -> str:
    normalized = email.strip().lower().encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()[:20]


def _read_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    values = loaded if isinstance(loaded, list) else [loaded]
    return [dict(value) for value in values if isinstance(value, dict)]


def _write_records(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, path)


def subscription_snapshot(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    source = payload.get("subscription")
    if not isinstance(source, dict):
        source = payload
    snapshot: dict[str, Any] = {}
    string_fields = ("tier", "status", "currency", "billing_period", "character_refresh_period")
    number_fields = (
        "character_count",
        "character_limit",
        "next_character_count_reset_unix",
        "voice_slots_used",
        "voice_limit",
    )
    for key in string_fields:
        value = source.get(key)
        if isinstance(value, str):
            snapshot[key] = value[:80]
    for key in number_fields:
        value = source.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            snapshot[key] = int(value)
    return snapshot


def api_key_from_user_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    value = payload.get("xi_api_key")
    return str(value).strip() if isinstance(value, str) else ""


def save_credentials(
    path: Path,
    *,
    email: str,
    password: str,
    extra: dict[str, Any] | None = None,
) -> Path:
    normalized_email = email.strip().lower()
    identifier = account_id(normalized_email)
    with _STORE_LOCK:
        records = _read_records(path)
        existing = next(
            (
                record
                for record in records
                if str(record.get("id") or "") == identifier
                or str(record.get("email") or "").strip().lower() == normalized_email
            ),
            None,
        )
        timestamp = _now()
        record = dict(existing or {})
        record.update(
            {
                "id": identifier,
                "email": email.strip(),
                "password": password,
                "created_at": str(record.get("created_at") or timestamp),
                "updated_at": timestamp,
            }
        )
        if extra:
            record.update(extra)
        if existing is None:
            records.append(record)
        else:
            records[records.index(existing)] = record
        _write_records(path, records)
    return path


def load_credentials(path: Path) -> list[dict[str, Any]]:
    with _STORE_LOCK:
        return _read_records(path)


def find_credentials(path: Path, identifier: str) -> dict[str, Any] | None:
    with _STORE_LOCK:
        for record in _read_records(path):
            current_id = str(record.get("id") or account_id(str(record.get("email") or "")))
            if current_id == identifier:
                return record
    return None


def update_credentials(path: Path, identifier: str, updates: dict[str, Any]) -> dict[str, Any]:
    with _STORE_LOCK:
        records = _read_records(path)
        for index, record in enumerate(records):
            current_id = str(record.get("id") or account_id(str(record.get("email") or "")))
            if current_id != identifier:
                continue
            updated = {**record, **updates, "id": current_id, "updated_at": _now()}
            records[index] = updated
            _write_records(path, records)
            return updated
    raise KeyError(identifier)


def public_account(record: dict[str, Any]) -> dict[str, Any]:
    subscription = subscription_snapshot(record.get("subscription"))
    used = subscription.get("character_count")
    limit = subscription.get("character_limit")
    remaining = max(0, limit - used) if isinstance(used, int) and isinstance(limit, int) else None
    reset_unix = subscription.get("next_character_count_reset_unix")
    reset_at = ""
    if isinstance(reset_unix, int) and reset_unix > 0:
        try:
            reset_at = datetime.fromtimestamp(reset_unix, timezone.utc).replace(microsecond=0).isoformat()
        except (OSError, OverflowError, ValueError):
            reset_at = ""
    email = str(record.get("email") or "").strip()
    return {
        "id": str(record.get("id") or account_id(email)),
        "email": email,
        "password": str(record.get("password") or ""),
        "authenticated": bool(record.get("authenticated", True)),
        "api_key_configured": bool(str(record.get("api_key") or "").strip()),
        "status": str(subscription.get("status") or record.get("status") or "active"),
        "tier": str(subscription.get("tier") or ""),
        "quota_used": used,
        "quota_limit": limit,
        "quota_remaining": remaining,
        "quota_reset_at": reset_at,
        "quota_updated_at": str(record.get("quota_updated_at") or ""),
        "created_at": str(record.get("created_at") or ""),
        "updated_at": str(record.get("updated_at") or record.get("created_at") or ""),
    }
