from __future__ import annotations

import json
import re
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .proxy_preflight import normalize_proxy_url, safe_proxy_label


_HOST_PORT = re.compile(
    r"(?:(?P<scheme>https?|socks4a?|socks5h?)://)?"
    r"(?:(?P<user>[^:@\s]+)(?::(?P<password>[^@\s]*))?@)?"
    r"(?P<host>(?:\d{1,3}\.){3}\d{1,3}|[A-Za-z0-9.-]+)"
    r":(?P<port>\d{2,5})",
    re.IGNORECASE,
)


def is_dynamic_proxy_api(value: str) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    parsed = urlsplit(raw)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    path = parsed.path.lower()
    return "1024proxy" in host or path.endswith("/white/api") or "/white/api" in path


def dynamic_proxy_api_url(value: str, *, count: int = 1) -> str:
    raw = str(value or "").strip()
    if not is_dynamic_proxy_api(raw):
        raise ValueError("dynamic proxy API URL must be HTTPS")
    parsed = urlsplit(raw)
    query = [(key, item) for key, item in parse_qsl(parsed.query, keep_blank_values=True) if key.lower() != "num"]
    query.append(("num", str(max(1, int(count)))))
    if not any(key.lower() == "type" for key, _ in query):
        query.append(("type", "json"))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


def _endpoint_from_mapping(item: dict[str, Any]) -> str:
    host = str(item.get("host") or item.get("ip") or item.get("addr") or "").strip()
    port = str(item.get("port") or "").strip()
    if host and port.isdigit():
        scheme = str(item.get("protocol") or item.get("scheme") or "http").strip().lower() or "http"
        if scheme not in {"http", "https", "socks4", "socks4a", "socks5", "socks5h"}:
            scheme = "http"
        user = str(item.get("username") or item.get("user") or "").strip()
        password = str(item.get("password") or item.get("pass") or "").strip()
        auth = f"{user}:{password}@" if user or password else ""
        return f"{scheme}://{auth}{host}:{port}"
    for key in ("proxy", "url", "endpoint", "socks5", "http"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def parse_dynamic_proxy_payload(payload: Any, *, default_scheme: str = "http") -> list[str]:
    values: list[str] = []
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return []
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError:
            loaded = None
        if loaded is not None:
            payload = loaded
        else:
            for match in _HOST_PORT.finditer(text):
                scheme = (match.group("scheme") or default_scheme).lower()
                user = match.group("user") or ""
                password = match.group("password") or ""
                host = match.group("host")
                port = match.group("port")
                auth = f"{user}:{password}@" if user or password else ""
                values.append(f"{scheme}://{auth}{host}:{port}")
            return [normalize_proxy_url(value) for value in values]
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        nested = payload.get("data") or payload.get("proxies") or payload.get("list") or payload.get("result")
        items = nested if isinstance(nested, list) else [payload]
    else:
        return []
    for item in items:
        if isinstance(item, str):
            values.extend(parse_dynamic_proxy_payload(item, default_scheme=default_scheme))
            continue
        if isinstance(item, dict):
            endpoint = _endpoint_from_mapping(item)
            if endpoint:
                values.append(normalize_proxy_url(endpoint if "://" in endpoint else f"{default_scheme}://{endpoint}"))
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = safe_proxy_label(value)
        if key in seen:
            continue
        seen.add(key)
        unique.append(value)
    return unique


def fetch_dynamic_proxies(
    api_url: str,
    *,
    count: int = 1,
    timeout: float = 20,
    request_get: Callable[..., Any] | None = None,
) -> list[str]:
    url = dynamic_proxy_api_url(api_url, count=count)
    if request_get is None:
        import requests

        session = requests.Session()
        session.trust_env = False
        request_get = session.get
    response = request_get(url, timeout=timeout)
    status = int(getattr(response, "status_code", 0) or 0)
    if status < 200 or status >= 400:
        raise RuntimeError(f"dynamic proxy API returned HTTP {status}")
    try:
        payload: Any = response.json()
    except Exception:
        payload = getattr(response, "text", "") or ""
    proxies = parse_dynamic_proxy_payload(payload)
    if not proxies:
        raise RuntimeError("dynamic proxy API did not return a usable host:port")
    return proxies
