"""Slim protocol registration for grok2api auto-refill.

Produces SSO tokens via xconsole_client. Supports Cloud Temp Mail / YYDS Mail
+ optional ez-captcha, and a single-use proxy (random IP rotation is driven
by the Go caller).

Upstream project: https://github.com/chenyme/grok2api (Author: Chenyme, MIT).
This sidecar is part of a fork that adds protocol auto-register to the gateway.
See docs/AUTO_REGISTER.md for setup and ops.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import requests

ROOT = Path(__file__).resolve().parent
ENGINE_ROOT = ROOT / "engine"
SIGNUP_URL = "https://accounts.x.ai/sign-up?redirect=grok-com"


def _load_engine() -> None:
    engine = str(ENGINE_ROOT)
    if engine not in sys.path:
        sys.path.insert(0, engine)
    if not (ENGINE_ROOT / "xconsole_client").is_dir():
        raise RuntimeError(f"protocol engine missing: {ENGINE_ROOT}")


def _sanitize_proxy_env() -> None:
    for key in (
        "ALL_PROXY",
        "all_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
        "NO_PROXY",
        "no_proxy",
    ):
        os.environ.pop(key, None)


def _extract_xai_code(text: str, subject: str = "") -> str:
    haystack = f"{subject}\n{text}"
    match = re.search(r"(?<![A-Z0-9])([A-Z0-9]{3}-[A-Z0-9]{3})(?![A-Z0-9])", haystack, re.I)
    if match:
        return match.group(1).upper().replace("-", "")
    match = re.search(r"\b([A-Z0-9]{6})\b", haystack, re.I)
    if match and not match.group(1).isdigit():
        return match.group(1).upper()
    match = re.search(
        r"(?:code|otp|验证码|verification|verify)[^A-Za-z0-9]{0,40}([A-Z0-9]{3}-?[A-Z0-9]{3})",
        haystack,
        re.I,
    )
    if match:
        return match.group(1).upper().replace("-", "")
    return ""


def _cf_headers(config: dict, *, content_type: bool = False, bearer: str = "") -> dict[str, str]:
    headers: dict[str, str] = {}
    if content_type:
        headers["Content-Type"] = "application/json"
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    key = str(config.get("cloudflare_api_key") or "").strip()
    mode = str(config.get("cloudflare_auth_mode") or "x-admin-auth").strip().lower()
    if key and not bearer:
        if mode == "x-api-key":
            headers["X-API-Key"] = key
        elif mode == "x-custom-auth":
            headers["x-custom-auth"] = key
        elif mode == "x-admin-auth":
            headers["x-admin-auth"] = key
        elif mode not in {"none", "query-key"}:
            headers["Authorization"] = f"Bearer {key}"
    return headers


def _cf_path(config: dict, key: str, default: str) -> str:
    raw = str(config.get(key) or default).strip()
    if not raw.startswith("/"):
        raw = "/" + raw
    return raw


def _mail_session(config: dict) -> requests.Session:
    session = requests.Session()
    email_proxy = str(config.get("email_proxy") or "direct").strip().lower()
    if email_proxy in {"", "direct", "none", "off", "disabled"}:
        session.trust_env = False
    else:
        session.proxies.update({"http": email_proxy, "https": email_proxy})
    return session


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _split_domains(raw: Any) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for part in re.split(r"[,，\s;|]+", text):
        domain = part.strip().lower().lstrip("@")
        if not domain or domain in seen:
            continue
        seen.add(domain)
        out.append(domain)
    return out


def _phase(emit: Callable[[str], None] | None, phase: str, message: str) -> None:
    """Emit structured progress lines for the Go status UI."""
    line = f"[phase:{phase}] {message}"
    if emit:
        emit(line)
    else:
        print(line, flush=True)


def _cf_pick_list(data: Any) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("results", "hydra:member", "data", "messages", "mails", "domains", "items", "list"):
            value = data.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                nested = value.get("messages") or value.get("results") or value.get("domains")
                if isinstance(nested, list):
                    return nested
    return []


def _cf_extract_domain_name(item: Any) -> str:
    if isinstance(item, str):
        return item.strip().lower().lstrip("@")
    if isinstance(item, dict):
        for key in ("domain", "name", "value"):
            val = item.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip().lower().lstrip("@")
    return ""


def _cf_fetch_domains(config: dict) -> list[str]:
    """Best-effort domain discovery for Cloud Temp Mail deployments."""
    api_base = str(config.get("cloudflare_api_base") or "").rstrip("/")
    if not api_base:
        return []
    custom = str(config.get("cloudflare_path_domains") or config.get("mail_path_domains") or "").strip()
    candidates: list[str] = []
    if custom:
        candidates.append(_cf_path(config, "cloudflare_path_domains", custom))
    candidates.extend(
        [
            "/admin/domains",
            "/api/domains",
            "/open_api/domains",
            "/admin/domain_list",
            "/api/domain_list",
            "/open_api/settings",
            "/admin/settings",
            "/api/settings",
            "/open_api/get_settings",
        ]
    )
    found: list[str] = []
    seen: set[str] = set()
    session = _mail_session(config)
    for path in candidates:
        try:
            response = session.get(
                f"{api_base}{path}",
                headers=_cf_headers(config),
                timeout=15,
            )
            if not response.ok:
                continue
            data = response.json()
            # settings payloads often embed domain lists
            if isinstance(data, dict):
                for key in ("domains", "defaultDomains", "default_domains", "domainList", "domain_list"):
                    if key in data:
                        data = data[key]
                        break
                    nested = data.get("data")
                    if isinstance(nested, dict) and key in nested:
                        data = nested[key]
                        break
            items = data if isinstance(data, list) else _cf_pick_list(data)
            for item in items:
                domain = _cf_extract_domain_name(item)
                if domain and domain not in seen and "." in domain:
                    seen.add(domain)
                    found.append(domain)
            if found:
                return found
        except Exception:
            continue
    return found


def _cf_resolve_domains(config: dict, emit: Callable[[str], None] | None = None) -> list[str]:
    configured = _split_domains(
        config.get("defaultDomains")
        or config.get("mail_domains")
        or config.get("mailDomains")
    )
    auto = _truthy(config.get("mail_auto_domains"), default=True)
    if configured:
        domains = list(configured)
        if auto:
            discovered = _cf_fetch_domains(config)
            for domain in discovered:
                if domain not in domains:
                    domains.append(domain)
            if discovered:
                _phase(emit, "resolve_domains", f"cloudflare domains configured={len(configured)} + auto={len(discovered)} → {len(domains)}")
            else:
                _phase(emit, "resolve_domains", f"cloudflare domains configured={len(configured)} (auto empty)")
        else:
            _phase(emit, "resolve_domains", f"cloudflare domains configured={len(domains)}")
        return domains
    if not auto:
        raise ValueError("defaultDomains is required when mail_auto_domains is off")
    discovered = _cf_fetch_domains(config)
    if not discovered:
        raise ValueError(
            "no mail domains: set mailDomains or enable a Cloud Temp Mail instance that exposes domains"
        )
    _phase(emit, "resolve_domains", f"cloudflare auto-fetched domains={discovered}")
    return discovered


def _cf_message_text(item: dict) -> tuple[str, str]:
    subject = str(item.get("subject") or "")
    parts: list[str] = []
    for field in ("text", "raw", "content", "intro", "body", "snippet", "source"):
        value = item.get(field)
        if isinstance(value, str) and value.strip():
            parts.append(value)
    html = item.get("html") or []
    if isinstance(html, str):
        html = [html]
    for chunk in html:
        if isinstance(chunk, str):
            parts.append(re.sub(r"<[^>]+>", " ", chunk))
    return subject, "\n".join(parts)


class CloudTempMailReceiver:
    def __init__(self, email: str, jwt: str, config: dict) -> None:
        self.email = email
        self.jwt = jwt
        self.config = config
        self.api_base = str(config.get("cloudflare_api_base") or "").rstrip("/")
        self._consumed_message_ids: set[str] = set()
        self._consumed_codes: set[str] = set()

    def wait_for_code(self, timeout: float = 120, emit: Callable[[str], None] | None = None) -> str:
        if not self.api_base:
            raise RuntimeError("cloudflare_api_base is required")
        deadline = time.time() + timeout
        poll = max(0.5, min(float(self.config.get("mail_poll_interval") or 2), 5.0))
        path = _cf_path(self.config, "cloudflare_path_messages", "/api/mails")
        seen: dict[str, int] = {}
        last_report = 0.0
        while time.time() < deadline:
            remaining = int(max(0, deadline - time.time()))
            if emit and time.time() - last_report >= 15:
                _phase(emit, "wait_email_code", f"polling inbox remaining={remaining}s email={self.email}")
                last_report = time.time()
            try:
                response = _mail_session(self.config).get(
                    f"{self.api_base}{path}",
                    headers=_cf_headers(self.config, bearer=self.jwt),
                    params={"limit": 20, "offset": 0},
                    timeout=20,
                )
                response.raise_for_status()
                messages = _cf_pick_list(response.json())
            except Exception as exc:  # noqa: BLE001
                if emit and time.time() - last_report >= 15:
                    _phase(emit, "wait_email_code", f"poll error: {str(exc)[:120]}")
                time.sleep(poll)
                continue
            for raw in messages:
                item = dict(raw or {})
                msg_id = str(item.get("id") or item.get("msgid") or item.get("message_id") or "")
                if msg_id and msg_id in self._consumed_message_ids:
                    continue
                if msg_id:
                    attempts = int(seen.get(msg_id, 0))
                    if attempts >= 8:
                        continue
                    seen[msg_id] = attempts + 1
                subject, text = _cf_message_text(item)
                if msg_id and len(text) < 20:
                    for detail_url in (
                        f"{self.api_base}/api/mail/{msg_id}",
                        f"{self.api_base}{path}/{msg_id}",
                    ):
                        try:
                            detail_resp = _mail_session(self.config).get(
                                detail_url,
                                headers=_cf_headers(self.config, bearer=self.jwt),
                                timeout=20,
                            )
                            if detail_resp.ok:
                                detail = detail_resp.json()
                                if isinstance(detail, dict) and isinstance(detail.get("data"), dict):
                                    detail = detail["data"]
                                if isinstance(detail, dict):
                                    d_subject, d_text = _cf_message_text(detail)
                                    subject = subject or d_subject
                                    text = (text + "\n" + d_text).strip()
                                    break
                        except Exception:
                            continue
                code = _extract_xai_code(text, subject)
                if code and len(code) == 6 and code not in self._consumed_codes:
                    if msg_id:
                        self._consumed_message_ids.add(msg_id)
                    self._consumed_codes.add(code)
                    return code
            time.sleep(poll)
        raise RuntimeError("timeout waiting for xAI email verification code")

    def wait_for_verification_link(
        self,
        timeout: float = 180,
        emit: Callable[[str], None] | None = None,
        allowed_hosts: list[str] | tuple[str, ...] | set[str] = (),
    ) -> str:
        from elevenlabs_assisted.mailbox_link import wait_for_cloudflare_verification_link

        return wait_for_cloudflare_verification_link(
            self,
            timeout=timeout,
            allowed_hosts=allowed_hosts,
            emit=emit,
        )


def create_cloudflare_mailbox(
    config: dict,
    emit: Callable[[str], None] | None = None,
) -> tuple[str, CloudTempMailReceiver]:
    api_base = str(config.get("cloudflare_api_base") or "").rstrip("/")
    if not api_base:
        raise ValueError("cloudflare_api_base is required")
    path = _cf_path(config, "cloudflare_path_accounts", "/admin/new_address")
    domains = _cf_resolve_domains(config, emit=emit)
    strategy = str(config.get("mail_domain_strategy") or "rotate").strip().lower()
    if strategy == "random":
        order = list(domains)
        secrets.SystemRandom().shuffle(order)
    elif strategy == "first":
        order = list(domains)
    else:
        # rotate: try configured order, then shuffle remaining on retry
        order = list(domains)

    enable_prefix = _truthy(
        config.get("mail_random_subdomain")
        if config.get("mail_random_subdomain") is not None
        else config.get("enablePrefix"),
        default=True,
    )
    errors: list[str] = []
    for idx, domain in enumerate(order):
        local = secrets.token_hex(5)
        payload: dict[str, Any] = {
            "name": local,
            "domain": domain,
            "enablePrefix": enable_prefix,
        }
        # Some CF temp mail forks accept nameLength without fixed domain/name.
        if enable_prefix:
            payload["nameLength"] = len(local)
        _phase(
            emit,
            "create_mailbox",
            f"cloudflare try domain={domain} ({idx + 1}/{len(order)}) prefix={enable_prefix}",
        )
        try:
            response = _mail_session(config).post(
                f"{api_base}{path}",
                json=payload,
                headers=_cf_headers(config, content_type=True),
                timeout=30,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{domain}: {exc}")
            continue
        if response.status_code >= 400:
            errors.append(f"{domain}: HTTP {response.status_code} {response.text[:160]}")
            continue
        data = response.json()
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            data = data["data"]
        address = str(data.get("address") or "").strip().lower()
        jwt = str(data.get("jwt") or data.get("token") or "").strip()
        if not address or not jwt:
            errors.append(f"{domain}: missing address/jwt")
            continue
        _phase(emit, "create_mailbox", f"cloudflare ok address={address}")
        return address, CloudTempMailReceiver(address, jwt, config)

    # Last resort: let server pick domain (no domain field)
    if enable_prefix:
        _phase(emit, "create_mailbox", "cloudflare fallback create without domain")
        try:
            response = _mail_session(config).post(
                f"{api_base}{path}",
                json={"enablePrefix": True, "nameLength": 10},
                headers=_cf_headers(config, content_type=True),
                timeout=30,
            )
            if response.ok:
                data = response.json()
                if isinstance(data, dict) and isinstance(data.get("data"), dict):
                    data = data["data"]
                address = str(data.get("address") or "").strip().lower()
                jwt = str(data.get("jwt") or data.get("token") or "").strip()
                if address and jwt:
                    _phase(emit, "create_mailbox", f"cloudflare ok address={address}")
                    return address, CloudTempMailReceiver(address, jwt, config)
            errors.append(f"no-domain: HTTP {response.status_code} {response.text[:160]}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"no-domain: {exc}")

    raise RuntimeError(
        "Cloud Temp Mail create failed on all domains: " + "; ".join(errors[:6])
    )


# ──────────────────────── YYDS Mail (https://vip.215.im/docs) ────────────────────────
# Base: https://maliapi.215.im/v1
# Auth: X-API-Key: AC-xxx  OR  Authorization: Bearer <jwt|temp_token>

YYDS_API_BASE_DEFAULT = "https://maliapi.215.im/v1"


def _yyds_api_base(config: dict) -> str:
    base = str(
        config.get("yyds_api_base")
        or config.get("mail_api_base")
        or config.get("cloudflare_api_base")
        or ""
    ).strip().rstrip("/")
    if not base:
        base = YYDS_API_BASE_DEFAULT
    # Accept either https://maliapi.215.im or .../v1
    if base.endswith("/v1"):
        return base
    if base.rstrip("/").endswith("maliapi.215.im") or "215.im" in base:
        return base + "/v1"
    return base


def _yyds_auth_headers(config: dict, *, content_type: bool = False, temp_token: str = "") -> dict[str, str]:
    headers: dict[str, str] = {}
    if content_type:
        headers["Content-Type"] = "application/json"
    token = str(temp_token or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
        return headers
    jwt = str(config.get("yyds_jwt") or "").strip()
    if jwt:
        headers["Authorization"] = f"Bearer {jwt}"
        return headers
    key = str(
        config.get("yyds_api_key")
        or config.get("cloudflare_api_key")
        or config.get("mail_admin_key")
        or ""
    ).strip()
    if key:
        headers["X-API-Key"] = key
    return headers


def _yyds_unwrap(data: Any) -> Any:
    if isinstance(data, dict) and data.get("success") is False:
        err = data.get("error") or data.get("errorCode") or data
        raise RuntimeError(f"YYDS API error: {err}")
    if isinstance(data, dict) and "data" in data:
        return data.get("data")
    return data


def _yyds_list_domains(config: dict) -> list[dict[str, Any]]:
    api_base = _yyds_api_base(config)
    try:
        response = _mail_session(config).get(
            f"{api_base}/domains",
            headers=_yyds_auth_headers(config),
            timeout=20,
        )
        response.raise_for_status()
        raw = _yyds_unwrap(response.json())
        items = raw if isinstance(raw, list) else []
        if isinstance(raw, dict):
            items = raw.get("domains") or raw.get("items") or []
        return [d for d in items if isinstance(d, dict)]
    except Exception:
        return []


def _yyds_domain_candidates(config: dict, emit: Callable[[str], None] | None = None) -> list[str]:
    """User-hosted domains first (blacklist-safe). Public shared domains are last resort only."""
    preferred = _split_domains(
        config.get("defaultDomains")
        or config.get("yyds_domain")
        or config.get("mail_domains")
        or config.get("mailDomains")
    )
    allow_public = _truthy(config.get("yyds_allow_public_domains"), default=False)
    items = _yyds_list_domains(config)

    owned: list[str] = []
    public: list[str] = []
    for item in items:
        domain = str(item.get("domain") or "").strip().lower().lstrip("@")
        if not domain:
            continue
        verified = bool(item.get("isVerified") if item.get("isVerified") is not None else item.get("verified", True))
        if not verified:
            continue
        is_public = bool(item.get("isPublic") if item.get("isPublic") is not None else item.get("public"))
        if is_public:
            public.append(domain)
        else:
            owned.append(domain)

    # Prefer explicit user selection; keep only those that appear owned when list is available.
    ordered: list[str] = []
    seen: set[str] = set()

    def add(domain: str) -> None:
        d = domain.strip().lower().lstrip("@")
        if d and d not in seen:
            seen.add(d)
            ordered.append(d)

    if preferred:
        owned_set = set(owned)
        public_set = set(public)
        for domain in preferred:
            if owned_set and domain in owned_set:
                add(domain)
            elif not owned_set and not public_set:
                # API list failed — trust user input (self-hosted domain they manage on YYDS)
                add(domain)
            elif domain in owned_set or (allow_public and domain in public_set):
                add(domain)
            else:
                # User typed a domain not in list; still try (custom host may lag API)
                add(domain)
        _phase(
            emit,
            "resolve_domains",
            f"yyds preferred={preferred} owned={owned} public={public} allow_public={allow_public} → try={ordered}",
        )
    else:
        for domain in owned:
            add(domain)
        if allow_public:
            for domain in public:
                add(domain)
        _phase(
            emit,
            "resolve_domains",
            f"yyds auto owned={owned} public={public if allow_public else 'skipped'} → try={ordered}",
        )

    if not ordered and allow_public:
        for domain in public:
            add(domain)
    return ordered


def _yyds_pick_domain(config: dict, emit: Callable[[str], None] | None = None) -> str:
    """Pick first candidate; empty means use prefer_owned strategy on create."""
    candidates = _yyds_domain_candidates(config, emit=emit)
    if not candidates:
        return ""
    strategy = str(config.get("mail_domain_strategy") or "rotate").strip().lower()
    if strategy == "random":
        return secrets.choice(candidates)
    return candidates[0]


def _yyds_message_text(item: dict) -> tuple[str, str]:
    subject = str(item.get("subject") or "")
    parts: list[str] = []
    # Server-side OTP extraction (docs: verificationCode field)
    server_code = str(item.get("verificationCode") or item.get("verification_code") or "").strip()
    if server_code:
        parts.append(server_code)
    for field in ("text", "raw", "content", "intro", "body", "snippet", "source"):
        value = item.get(field)
        if isinstance(value, str) and value.strip():
            parts.append(value)
    html = item.get("html") or []
    if isinstance(html, str):
        html = [html]
    for chunk in html:
        if isinstance(chunk, str):
            parts.append(re.sub(r"<[^>]+>", " ", chunk))
    return subject, "\n".join(parts)


class YydsMailReceiver:
    """Poll YYDS Mail for xAI verification codes.

    Prefers GET /v1/messages/next?address=&wait= (long-poll + atomic mark-read).
    Falls back to list + detail when next is unavailable.
    """

    def __init__(self, email: str, temp_token: str, config: dict) -> None:
        self.email = email
        self.temp_token = temp_token
        self.config = config
        self.api_base = _yyds_api_base(config)

    def wait_for_code(self, timeout: float = 120, emit: Callable[[str], None] | None = None) -> str:
        deadline = time.time() + timeout
        poll = max(0.5, min(float(self.config.get("mail_poll_interval") or 2), 5.0))
        seen: set[str] = set()
        last_report = 0.0
        while time.time() < deadline:
            remaining = max(1.0, deadline - time.time())
            wait_sec = int(min(30, remaining))
            if emit and time.time() - last_report >= 15:
                _phase(emit, "wait_email_code", f"yyds polling remaining={int(remaining)}s email={self.email}")
                last_report = time.time()
            # Preferred: one-call next unread (docs)
            try:
                response = _mail_session(self.config).get(
                    f"{self.api_base}/messages/next",
                    headers=_yyds_auth_headers(self.config, temp_token=self.temp_token),
                    params={"address": self.email, "wait": wait_sec},
                    timeout=wait_sec + 15,
                )
                if response.status_code == 204:
                    continue
                if response.ok:
                    payload = _yyds_unwrap(response.json())
                    msg = payload
                    if isinstance(payload, dict):
                        msg = payload.get("message") or payload
                    if isinstance(msg, dict):
                        subject, text = _yyds_message_text(msg)
                        code = _extract_xai_code(text, subject) or str(
                            msg.get("verificationCode") or ""
                        ).strip().upper().replace("-", "")
                        if code and len(code) == 6:
                            return code
            except Exception:
                pass

            # Fallback: list messages + detail
            try:
                response = _mail_session(self.config).get(
                    f"{self.api_base}/messages",
                    headers=_yyds_auth_headers(self.config, temp_token=self.temp_token),
                    params={"address": self.email},
                    timeout=20,
                )
                if not response.ok:
                    time.sleep(poll)
                    continue
                data = _yyds_unwrap(response.json())
                messages: list = []
                if isinstance(data, list):
                    messages = data
                elif isinstance(data, dict):
                    messages = data.get("messages") or data.get("items") or []
                for raw in messages:
                    item = dict(raw or {})
                    msg_id = str(item.get("id") or "")
                    if msg_id and msg_id in seen:
                        continue
                    if msg_id:
                        seen.add(msg_id)
                    subject, text = _yyds_message_text(item)
                    server_code = str(item.get("verificationCode") or "").strip()
                    if server_code and len(server_code.replace("-", "")) == 6:
                        return server_code.upper().replace("-", "")
                    if msg_id and len(text) < 20:
                        try:
                            detail_resp = _mail_session(self.config).get(
                                f"{self.api_base}/messages/{msg_id}",
                                headers=_yyds_auth_headers(self.config, temp_token=self.temp_token),
                                params={"address": self.email},
                                timeout=20,
                            )
                            if detail_resp.ok:
                                detail = _yyds_unwrap(detail_resp.json())
                                if isinstance(detail, dict):
                                    d_subject, d_text = _yyds_message_text(detail)
                                    subject = subject or d_subject
                                    text = (text + "\n" + d_text).strip()
                                    sc = str(detail.get("verificationCode") or "").strip()
                                    if sc and len(sc.replace("-", "")) == 6:
                                        return sc.upper().replace("-", "")
                        except Exception:
                            pass
                    code = _extract_xai_code(text, subject)
                    if code and len(code) == 6:
                        return code
            except Exception:
                pass
            time.sleep(poll)
        raise RuntimeError(
            "timeout waiting for xAI email verification code (YYDS). "
            "Public shared domains are often blocked by xAI — set your self-hosted domain in mailDomains."
        )

    def wait_for_verification_link(
        self,
        timeout: float = 180,
        emit: Callable[[str], None] | None = None,
        allowed_hosts: list[str] | tuple[str, ...] | set[str] = (),
    ) -> str:
        from elevenlabs_assisted.mailbox_link import wait_for_yyds_verification_link

        return wait_for_yyds_verification_link(
            self,
            timeout=timeout,
            allowed_hosts=allowed_hosts,
            emit=emit,
        )


def create_yyds_mailbox(
    config: dict,
    emit: Callable[[str], None] | None = None,
) -> tuple[str, YydsMailReceiver]:
    """Create a temporary YYDS inbox. Docs: POST /v1/accounts.

    Prefer user self-hosted domains (mailDomains). Shared public domains are
    often blacklisted by xAI and should stay off unless yyds_allow_public_domains.
    """
    api_key = str(
        config.get("yyds_api_key")
        or config.get("cloudflare_api_key")
        or config.get("mail_admin_key")
        or ""
    ).strip()
    jwt = str(config.get("yyds_jwt") or "").strip()
    if not api_key and not jwt:
        raise ValueError("YYDS requires yyds_api_key (X-API-Key) or yyds_jwt")

    api_base = _yyds_api_base(config)
    candidates = _yyds_domain_candidates(config, emit=emit)
    allow_public = _truthy(config.get("yyds_allow_public_domains"), default=False)
    if not candidates and not allow_public:
        # Still try prefer_owned so accounts with custom domains work without listing.
        candidates = [""]

    errors: list[str] = []
    strategy = str(config.get("mail_domain_strategy") or "rotate").strip().lower()
    if strategy == "random" and len(candidates) > 1:
        secrets.SystemRandom().shuffle(candidates)

    for idx, domain in enumerate(candidates):
        local_part = secrets.token_hex(5)
        payload: dict[str, Any] = {"localPart": local_part}
        if domain:
            payload["domain"] = domain
            _phase(emit, "create_mailbox", f"yyds try domain={domain} ({idx + 1}/{len(candidates)})")
        else:
            # Prefer owned custom domains when key has them
            payload["autoDomainStrategy"] = "prefer_owned"
            _phase(emit, "create_mailbox", "yyds autoDomainStrategy=prefer_owned (no explicit domain)")

        try:
            response = _mail_session(config).post(
                f"{api_base}/accounts",
                json=payload,
                headers=_yyds_auth_headers(config, content_type=True),
                timeout=30,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{domain or 'prefer_owned'}: {exc}")
            continue
        if response.status_code >= 400:
            errors.append(f"{domain or 'prefer_owned'}: HTTP {response.status_code} {response.text[:160]}")
            continue
        data = _yyds_unwrap(response.json())
        if not isinstance(data, dict):
            errors.append(f"{domain or 'prefer_owned'}: unexpected response")
            continue
        address = str(data.get("address") or "").strip().lower()
        temp_token = str(data.get("token") or "").strip()
        if not address:
            errors.append(f"{domain or 'prefer_owned'}: missing address")
            continue
        if not temp_token:
            try:
                tok_resp = _mail_session(config).post(
                    f"{api_base}/token",
                    json={"address": address},
                    headers=_yyds_auth_headers(config, content_type=True),
                    timeout=20,
                )
                if tok_resp.ok:
                    tok_data = _yyds_unwrap(tok_resp.json())
                    if isinstance(tok_data, dict):
                        temp_token = str(tok_data.get("token") or "").strip()
            except Exception:
                pass
        _phase(emit, "create_mailbox", f"yyds ok address={address}")
        return address, YydsMailReceiver(address, temp_token, config)

    raise RuntimeError(
        "YYDS create inbox failed. Configure your self-hosted domain in mailDomains "
        f"(public domains are blocked by xAI). details: {'; '.join(errors[:6])}"
    )


def create_mailbox(config: dict, emit: Callable[[str], None] | None = None) -> tuple[str, Any]:
    """Create mailbox via configured provider: cloudflare | yyds."""
    provider = str(
        config.get("email_provider")
        or config.get("mail_provider")
        or config.get("protocol_email_provider")
        or "cloudflare"
    ).strip().lower()
    if provider in {"yyds", "yyds_mail", "215", "215.im"}:
        return create_yyds_mailbox(config, emit=emit)
    return create_cloudflare_mailbox(config, emit=emit)


def _fresh_turnstile(solver: Any, website_url: str, sitekey: str) -> str:
    return solver.solve_turnstile(
        website_url=website_url,
        website_key=sitekey,
        premium=True,
        fallback_non_premium=True,
    )


def _set_cookie_names(set_cookies: list[str]) -> list[str]:
    names: list[str] = []
    for raw_cookie in set_cookies:
        match = re.match(r"\s*([^=;,\s]+)=", str(raw_cookie or ""))
        if match and match.group(1) not in names:
            names.append(match.group(1))
    return names


def _normalize_proxy(proxy: str) -> str:
    """Prefer socks5h (remote DNS). curl_cffi accepts socks5:// with auth."""
    proxy = str(proxy or "").strip()
    if not proxy or proxy.lower() in {"direct", "none", "off", "disabled"}:
        return ""
    # curl_cffi often works better with socks5:// than socks5h://
    if proxy.startswith("socks5h://"):
        return "socks5://" + proxy[len("socks5h://") :]
    return proxy


def _safe_proxy_label(proxy: str) -> str:
    """Return a log-safe proxy endpoint without userinfo or query secrets."""
    proxy = str(proxy or "").strip()
    if not proxy:
        return "direct"
    try:
        from urllib.parse import urlsplit, urlunsplit

        parsed = urlsplit(proxy)
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        return urlunsplit((parsed.scheme, host, parsed.path, "", "")) or "proxy"
    except (TypeError, ValueError):
        return "proxy"


def register_one(
    config: dict,
    *,
    proxy: str = "",
    log: Callable[[str], None] | None = None,
    index: int = 1,
) -> dict[str, Any]:
    """Register one Grok Web account; return email/password/sso.

    Captcha is optional: when captcha key is empty (or skip_captcha=true),
    signup is attempted without Turnstile. Clean residential IPs sometimes pass.
    """
    emit = log or (lambda message: print(message, flush=True))
    _sanitize_proxy_env()
    _load_engine()
    from xconsole_client import XConsoleAuthClient, YesCaptchaSolver
    from xconsole_client import config as engine_config

    skip_captcha = _truthy(config.get("skip_captcha"), default=False)
    yescaptcha_key = str(config.get("protocol_yescaptcha_key") or config.get("captcha_key") or "").strip()
    if skip_captcha:
        yescaptcha_key = ""
    endpoint = (
        str(config.get("protocol_yescaptcha_endpoint") or config.get("captcha_endpoint") or "").strip()
        or "https://api.ez-captcha.com"
    )
    # Proxy is selected by the Go gateway from the unified Grok Web egress pool
    # (or auto-register fallbackProxyURL). Do not silently fall back to proxies.txt —
    # that hid nodes from the admin UI and split proxy management into two places.
    proxy = _normalize_proxy(proxy or config.get("protocol_proxy") or config.get("proxy") or "")
    if not proxy:
        _phase(emit, "pick_proxy", f"#{index} no proxy from gateway (direct)")
    else:
        _phase(emit, "pick_proxy", f"#{index} proxy={_safe_proxy_label(proxy)}")

    _phase(emit, "create_mailbox", f"#{index} creating mailbox")
    email, receiver = create_mailbox(config, emit=emit)
    password = f"Aa{os.urandom(5).hex()}9!xZ"
    client = XConsoleAuthClient(debug=False, proxy=proxy or None, signup_url=SIGNUP_URL)
    try:
        _phase(
            emit,
            "create_mailbox",
            f"#{index} mailbox={email} captcha={'off' if not yescaptcha_key else 'on'}",
        )
        _phase(emit, "visit_home", f"#{index} open accounts.x.ai")
        client.visit_home()
        _phase(emit, "load_signup", f"#{index} load signup page")
        client.load_signup_page()
        sitekey = str(
            getattr(client, "turnstile_sitekey", None)
            or getattr(engine_config, "TURNSTILE_SITEKEY", None)
            or ""
        ).strip()
        website_url = str(getattr(client, "signup_url", None) or SIGNUP_URL).strip()
        if yescaptcha_key and not sitekey:
            raise RuntimeError("Turnstile sitekey was not discovered")

        solver = None
        if yescaptcha_key:
            solver = YesCaptchaSolver(
                yescaptcha_key,
                endpoint=endpoint,
                timeout=float(config.get("protocol_yescaptcha_timeout_sec") or 180),
                debug=False,
                auto_fallback_endpoint=False,
            )
        else:
            _phase(emit, "skip_captcha", f"#{index} captcha skipped (empty turnstile)")

        _phase(emit, "validate_password", f"#{index} validate password")
        client.validate_password(email, password)
        _phase(emit, "send_email_code", f"#{index} request verification code")
        client.create_email_validation_code(email)
        _phase(emit, "wait_email_code", f"#{index} waiting for email code")
        code = receiver.wait_for_code(
            timeout=float(config.get("protocol_mail_timeout_sec") or 120),
            emit=emit,
        )
        _phase(emit, "got_email_code", f"#{index} code received")

        signup_error = ""
        mail_timeout = float(config.get("protocol_mail_timeout_sec") or 120)
        for attempt in range(1, 3):
            if attempt > 1:
                _phase(emit, "send_email_code", f"#{index} resend code attempt={attempt}")
                client.create_email_validation_code(email)
                code = receiver.wait_for_code(timeout=mail_timeout, emit=emit)
                _phase(emit, "got_email_code", f"#{index} code received (retry)")
            _phase(emit, "verify_email_code", f"#{index} verify code")
            client.verify_email_validation_code(email, code)
            turnstile = ""
            if solver is not None:
                _phase(emit, "solve_turnstile", f"#{index} solving Turnstile")
                try:
                    turnstile = _fresh_turnstile(solver, website_url, sitekey)
                    _phase(emit, "solve_turnstile", f"#{index} turnstile ok")
                except Exception as exc:  # noqa: BLE001
                    _phase(emit, "solve_turnstile", f"#{index} turnstile failed: {str(exc)[:120]}")
            else:
                _phase(emit, "create_account", f"#{index} without Turnstile token")
            _phase(emit, "create_account", f"#{index} submit signup attempt={attempt}")
            response = client.create_account(
                email=email,
                given_name="User",
                family_name="Grok",
                password=password,
                email_validation_code=code,
                turnstile_token=turnstile,
                castle_request_token="",
                conversion_id=str(uuid.uuid4()),
            )
            body = str(getattr(response, "rsc_body", "") or "")
            try:
                signup_error = str(client.extract_signup_error(body) or "")
            except Exception:
                signup_error = ""
            status = int(getattr(response, "http_status", 0) or 0)
            digest = hashlib.sha256(body.encode("utf-8", "replace")).hexdigest()[:12]
            _phase(
                emit,
                "create_account",
                f"#{index} signup HTTP {status} ok={bool(getattr(response, 'ok', False))} "
                f"rsc={digest} cookies={_set_cookie_names(list(getattr(response, 'set_cookies', None) or []))}",
            )
            if status == 200 and not signup_error:
                break
            captcha_needed = any(
                m in (signup_error or "").lower() for m in ("turnstile", "captcha", "challenge")
            )
            if captcha_needed and solver is None:
                raise RuntimeError(
                    "xAI requires Turnstile for this IP/session; set captcha key or use a cleaner residential IP. "
                    f"detail={signup_error or status}"
                )
            if attempt >= 2:
                raise RuntimeError(f"create_account rejected: HTTP {status}; {signup_error or 'unknown'}")

        _phase(emit, "extract_sso", f"#{index} extracting SSO")
        sso = client.fetch_sso_token(email=email, password=password, save=False, retries=4)
        if not sso and solver is not None:
            time.sleep(1.5)
            signin_url = "https://accounts.x.ai/sign-in?redirect=grok-com"
            try:
                _phase(emit, "extract_sso", f"#{index} SSO recovery via password+turnstile")
                signin_turnstile = _fresh_turnstile(solver, signin_url, sitekey)
                sso = client.obtain_session_via_password(
                    email=email,
                    password=password,
                    turnstile_token=signin_turnstile,
                    referer=signin_url,
                    retries=4,
                )
            except Exception as exc:  # noqa: BLE001
                _phase(emit, "extract_sso", f"#{index} SSO recovery failed: {str(exc)[:160]}")

        if not sso:
            raise RuntimeError("registration completed without SSO token")

        _phase(emit, "done", f"#{index} success email={email}")
        return {
            "ok": True,
            "email": email,
            "password": password,
            "sso": sso,
            "proxy": proxy or "direct",
            "phase": "done",
        }
    finally:
        client.close()
