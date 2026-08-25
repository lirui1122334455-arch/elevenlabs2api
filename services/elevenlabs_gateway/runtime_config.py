from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


_PROXY_SCHEMES = {"http", "https", "socks4", "socks4a", "socks5", "socks5h"}
_DOMAIN_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_CAPTCHA_PROVIDERS = {"yescaptcha", "captcha_gateway"}


def _https_url(value: Any, default: str, name: str) -> str:
    raw = str(value or default).strip().rstrip("/")
    parsed = urlsplit(raw)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f"{name} must be an HTTPS URL without credentials")
    if parsed.query or parsed.fragment:
        raise ValueError(f"{name} cannot contain a query or fragment")
    return raw


def _proxy_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in _PROXY_SCHEMES or not parsed.hostname or not parsed.port:
        raise ValueError("proxy_url must include a supported scheme, host, and port")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("proxy_url cannot contain a path, query, or fragment")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, "", "", ""))


def _domains(value: Any) -> str:
    if isinstance(value, list):
        parts = [str(item).strip() for item in value]
    else:
        parts = [part.strip() for part in str(value or "").replace(";", ",").split(",")]
    result: list[str] = []
    seen: set[str] = set()
    for part in parts:
        domain = part.lower().lstrip("@")
        if not domain:
            continue
        if not _DOMAIN_RE.fullmatch(domain):
            raise ValueError(f"invalid mail domain: {domain}")
        if domain not in seen:
            seen.add(domain)
            result.append(domain)
    return ",".join(result)


def _seconds(value: Any, default: float, name: str, minimum: float, maximum: float) -> float:
    result = float(default if value in {None, ""} else value)
    if result < minimum or result > maximum:
        raise ValueError(f"{name} must be between {minimum:g} and {maximum:g} seconds")
    return result


def _captcha_provider(value: Any) -> str:
    provider = str(value or "yescaptcha").strip().lower().replace("-", "_")
    if provider == "gateway":
        provider = "captcha_gateway"
    if provider not in _CAPTCHA_PROVIDERS:
        raise ValueError("captcha_provider must be yescaptcha or captcha_gateway")
    return provider


def _safe_proxy_label(value: str) -> str:
    if not value:
        return "direct"
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{parsed.scheme}://{host}:{parsed.port}"


@dataclass(frozen=True)
class RuntimeConfig:
    api_key: str = ""
    api_base_url: str = "https://api.us.elevenlabs.io"
    proxy_url: str = ""
    request_timeout: float = 60.0
    generation_timeout: float = 240.0
    registration_timeout: float = 600.0
    captcha_provider: str = "yescaptcha"
    yescaptcha_api_key: str = ""
    yescaptcha_endpoint: str = "https://api.yescaptcha.com"
    captcha_gateway_api_key: str = ""
    captcha_gateway_endpoint: str = "https://sub.aixiangshu.com"
    yyds_api_key: str = ""
    yyds_api_base: str = "https://maliapi.215.im/v1"
    mail_domains: str = ""
    revision: int = 0

    @classmethod
    def from_mapping(cls, value: dict[str, Any] | None = None) -> "RuntimeConfig":
        raw = value or {}
        return cls(
            api_key=str(raw.get("api_key") or "").strip(),
            api_base_url=_https_url(
                raw.get("api_base_url"), "https://api.us.elevenlabs.io", "api_base_url"
            ),
            proxy_url=_proxy_url(raw.get("proxy_url")),
            request_timeout=_seconds(raw.get("request_timeout"), 60, "request_timeout", 5, 300),
            generation_timeout=_seconds(
                raw.get("generation_timeout"), 240, "generation_timeout", 30, 900
            ),
            registration_timeout=_seconds(
                raw.get("registration_timeout"), 600, "registration_timeout", 60, 1800
            ),
            captcha_provider=_captcha_provider(raw.get("captcha_provider")),
            yescaptcha_api_key=str(raw.get("yescaptcha_api_key") or "").strip(),
            yescaptcha_endpoint=_https_url(
                raw.get("yescaptcha_endpoint"), "https://api.yescaptcha.com", "yescaptcha_endpoint"
            ),
            captcha_gateway_api_key=str(raw.get("captcha_gateway_api_key") or "").strip(),
            captcha_gateway_endpoint=_https_url(
                raw.get("captcha_gateway_endpoint"),
                "https://sub.aixiangshu.com",
                "captcha_gateway_endpoint",
            ),
            yyds_api_key=str(raw.get("yyds_api_key") or "").strip(),
            yyds_api_base=_https_url(
                raw.get("yyds_api_base"), "https://maliapi.215.im/v1", "yyds_api_base"
            ),
            mail_domains=_domains(raw.get("mail_domains")),
            revision=max(0, int(raw.get("revision") or 0)),
        )

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        return cls.from_mapping(
            {
                "api_key": os.environ.get("ELEVENLABS_API_KEY", ""),
                "api_base_url": os.environ.get("ELEVENLABS_API_BASE_URL", ""),
                "proxy_url": os.environ.get("ELEVENLABS_PROXY_URL", ""),
                "request_timeout": os.environ.get("ELEVENLABS_REQUEST_TIMEOUT", ""),
                "generation_timeout": os.environ.get("ELEVENLABS_GENERATION_TIMEOUT", ""),
                "registration_timeout": os.environ.get("ELEVENLABS_REGISTRATION_TIMEOUT", ""),
                "captcha_provider": os.environ.get("ELEVENLABS_CAPTCHA_PROVIDER", ""),
                "yescaptcha_api_key": os.environ.get("YESCAPTCHA_API_KEY", ""),
                "yescaptcha_endpoint": os.environ.get("YESCAPTCHA_ENDPOINT", ""),
                "captcha_gateway_api_key": os.environ.get("CAPTCHA_GATEWAY_API_KEY", ""),
                "captcha_gateway_endpoint": os.environ.get("CAPTCHA_GATEWAY_ENDPOINT", ""),
                "yyds_api_key": os.environ.get("YYDS_API_KEY", ""),
                "yyds_api_base": os.environ.get("YYDS_API_BASE", ""),
                "mail_domains": os.environ.get("ELEVENLABS_MAIL_DOMAINS", ""),
            }
        )

    def selected_captcha_api_key(self) -> str:
        if self.captcha_provider == "captcha_gateway":
            return self.captcha_gateway_api_key
        return self.yescaptcha_api_key

    def selected_captcha_endpoint(self) -> str:
        if self.captcha_provider == "captcha_gateway":
            return self.captcha_gateway_endpoint
        return self.yescaptcha_endpoint

    def captcha_registration_error(self) -> str:
        if not self.selected_captcha_api_key():
            label = "Captcha Gateway" if self.captcha_provider == "captcha_gateway" else "YesCaptcha"
            return f"{label} API key is not configured"
        if self.captcha_provider == "captcha_gateway" and not self.proxy_url:
            return (
                "Captcha Gateway hCaptcha requires a task proxy; "
                "configure a proxy URL or select YesCaptcha for direct mode"
            )
        return ""

    def public(self) -> dict[str, Any]:
        return {
            "api_key_configured": bool(self.api_key),
            "api_base_url": self.api_base_url,
            "proxy_configured": bool(self.proxy_url),
            "proxy_label": _safe_proxy_label(self.proxy_url),
            "request_timeout": self.request_timeout,
            "generation_timeout": self.generation_timeout,
            "registration_timeout": self.registration_timeout,
            "captcha_provider": self.captcha_provider,
            "yescaptcha_key_configured": bool(self.yescaptcha_api_key),
            "yescaptcha_endpoint": self.yescaptcha_endpoint,
            "captcha_gateway_key_configured": bool(self.captcha_gateway_api_key),
            "captcha_gateway_endpoint": self.captcha_gateway_endpoint,
            "yyds_key_configured": bool(self.yyds_api_key),
            "yyds_api_base": self.yyds_api_base,
            "mail_domains": self.mail_domains,
            "revision": self.revision,
        }


class RuntimeConfigStore:
    def __init__(self, path: str | os.PathLike[str], defaults: RuntimeConfig | None = None) -> None:
        self.path = Path(path).resolve()
        self.defaults = defaults or RuntimeConfig.from_env()
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write(self.defaults)

    def get(self) -> RuntimeConfig:
        with self._lock:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("runtime config must be an object")
                return RuntimeConfig.from_mapping(payload)
            except FileNotFoundError:
                self._write(self.defaults)
                return self.defaults

    def update(self, payload: dict[str, Any]) -> RuntimeConfig:
        if not isinstance(payload, dict):
            raise ValueError("runtime config update must be an object")
        with self._lock:
            current = self.get()
            values = asdict(current)
            secret_fields = {
                "api_key": "clear_api_key",
                "yescaptcha_api_key": "clear_yescaptcha_api_key",
                "captcha_gateway_api_key": "clear_captcha_gateway_api_key",
                "yyds_api_key": "clear_yyds_api_key",
            }
            for field, clear_field in secret_fields.items():
                if payload.get(clear_field) is True:
                    values[field] = ""
                elif str(payload.get(field) or "").strip():
                    values[field] = str(payload[field]).strip()
            for field in (
                "api_base_url",
                "proxy_url",
                "request_timeout",
                "generation_timeout",
                "registration_timeout",
                "captcha_provider",
                "yescaptcha_endpoint",
                "captcha_gateway_endpoint",
                "yyds_api_base",
                "mail_domains",
            ):
                if field in payload:
                    values[field] = payload[field]
            values["revision"] = current.revision + 1
            updated = RuntimeConfig.from_mapping(values)
            self._write(updated)
            return updated

    def _write(self, config: RuntimeConfig) -> None:
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        temporary.write_text(
            json.dumps(asdict(config), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
