from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit


_PROXY_SCHEMES = {"http", "https", "socks4", "socks4a", "socks5", "socks5h"}


@dataclass(frozen=True)
class ProxyPreflightResult:
    proxy_label: str
    target_host: str
    status_code: int


def normalize_proxy_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in _PROXY_SCHEMES or not parsed.hostname or not parsed.port:
        raise ValueError("proxy must include a supported scheme, host, and port")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("proxy URL cannot contain a path, query, or fragment")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, "", "", ""))


def safe_proxy_label(value: str) -> str:
    normalized = normalize_proxy_url(value)
    if not normalized:
        return "direct"
    parsed = urlsplit(normalized)
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{parsed.scheme}://{host}:{parsed.port}"


def _is_local_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _remote_dns_proxy(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme.lower() != "socks5":
        return value
    return urlunsplit(("socks5h", parsed.netloc, "", "", ""))


def preflight_proxy(
    proxy_url: str,
    *,
    target_url: str = "https://elevenlabs.io/app/sign-up",
    timeout: float = 15,
    request_get: Callable[..., Any] | None = None,
) -> ProxyPreflightResult:
    proxy = normalize_proxy_url(proxy_url)
    parsed_proxy = urlsplit(proxy) if proxy else None
    if parsed_proxy is not None and _is_local_host(parsed_proxy.hostname or ""):
        try:
            with socket.create_connection((parsed_proxy.hostname or "", parsed_proxy.port or 0), timeout=timeout):
                pass
        except OSError as exc:
            raise RuntimeError(f"proxy listener unavailable at {safe_proxy_label(proxy)}") from exc

    if request_get is None:
        from curl_cffi import requests as curl_requests

        request_get = curl_requests.get
    try:
        request_options = {
            "timeout": timeout,
            "allow_redirects": True,
            "impersonate": "chrome",
        }
        if proxy:
            request_options["proxy"] = _remote_dns_proxy(proxy)
        response = request_get(target_url, **request_options)
    except Exception as exc:
        raise RuntimeError(f"ElevenLabs HTTPS preflight failed via {safe_proxy_label(proxy)}") from exc
    status_code = int(getattr(response, "status_code", 0) or 0)
    if status_code < 200 or status_code >= 400:
        raise RuntimeError(f"ElevenLabs HTTPS preflight returned HTTP {status_code}")
    final_url = str(getattr(response, "url", target_url) or target_url)
    target_host = urlsplit(final_url).hostname or urlsplit(target_url).hostname or ""
    return ProxyPreflightResult(safe_proxy_label(proxy), target_host, status_code)
