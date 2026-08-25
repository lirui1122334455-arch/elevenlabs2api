from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

import requests


CAPTCHA_GATEWAY_DEFAULT_ENDPOINT = "https://sub.aixiangshu.com"
_URL_CREDENTIALS = re.compile(r"(?i)\b(https?|socks4a?|socks5h?)://[^/@\s]+@")


def captcha_proxy_fields(proxy_url: str) -> dict[str, Any]:
    parsed = urlsplit((proxy_url or "").strip())
    scheme = parsed.scheme.lower()
    if scheme == "socks5h":
        scheme = "socks5"
    if scheme not in {"http", "https", "socks4", "socks5"} or not parsed.hostname or not parsed.port:
        return {}
    fields: dict[str, Any] = {
        "proxyType": scheme,
        "proxyAddress": parsed.hostname,
        "proxyPort": int(parsed.port),
    }
    if parsed.username:
        fields["proxyLogin"] = unquote(parsed.username)
    if parsed.password:
        fields["proxyPassword"] = unquote(parsed.password)
    return fields


def _solver_class() -> Any:
    engine = Path(__file__).resolve().parents[1] / "engine"
    engine_path = str(engine)
    if engine_path not in sys.path:
        sys.path.insert(0, engine_path)
    from xconsole_client.solver import YesCaptchaSolver

    return YesCaptchaSolver


def _gateway_solve_url(endpoint: str) -> str:
    base = (endpoint or CAPTCHA_GATEWAY_DEFAULT_ENDPOINT).strip().rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Captcha Gateway endpoint must be an HTTPS URL without credentials")
    if base.endswith("/captcha/solve"):
        return base
    if base.endswith("/captcha"):
        return f"{base}/solve"
    return f"{base}/captcha/solve"


def _gateway_error(
    payload: Any,
    status_code: int,
    *,
    api_key: str = "",
    proxy_url: str = "",
) -> RuntimeError:
    body = payload if isinstance(payload, dict) else {}
    code = str(body.get("errorCode") or body.get("code") or f"HTTP_{status_code}")
    description = str(
        body.get("errorDescription")
        or body.get("message")
        or "captcha solve request failed"
    )
    for secret in (api_key, proxy_url):
        if secret:
            description = description.replace(secret, "[redacted]")
    description = _URL_CREDENTIALS.sub(r"\1://[redacted]@", description)
    return RuntimeError(f"Captcha Gateway solve failed: {code}: {description[:500]}")


def _solve_with_captcha_gateway(
    *,
    api_key: str,
    website_url: str,
    website_key: str,
    endpoint: str,
    timeout: float,
    invisible: bool,
    user_agent: str,
    proxy_url: str,
    rqdata: str,
    emit: Callable[[str], None] | None,
) -> str:
    if not website_url or not website_key:
        raise ValueError("website_url and website_key are required for hCaptcha")
    payload: dict[str, Any] = {
        "type": "hcaptcha",
        "task_type": "hcaptcha",
        "websiteURL": website_url,
        "websiteKey": website_key,
        "target_url": website_url,
        "site_url": website_url,
        "site_key": website_key,
        "invisible": bool(invisible),
    }
    if proxy_url:
        payload["proxy"] = proxy_url
    if user_agent:
        payload["user_agent"] = user_agent
    if rqdata:
        payload["rqdata"] = rqdata

    if emit:
        emit("Captcha Gateway solve request submitted")
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.post(
            _gateway_solve_url(endpoint),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=(30, max(30.0, float(timeout))),
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Captcha Gateway request failed: {exc}") from exc
    try:
        body = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"Captcha Gateway returned non-JSON HTTP {response.status_code}"
        ) from exc
    if not response.ok or not isinstance(body, dict) or body.get("errorId", 0) != 0:
        raise _gateway_error(
            body,
            response.status_code,
            api_key=api_key,
            proxy_url=proxy_url,
        )
    if str(body.get("status") or "ready").lower() != "ready":
        raise RuntimeError(
            f"Captcha Gateway returned unexpected status: {str(body.get('status') or '')[:100]}"
        )
    solution = body.get("solution")
    if isinstance(solution, str):
        token = solution
    elif isinstance(solution, dict):
        token = (
            solution.get("token")
            or solution.get("hCaptchaResponse")
            or solution.get("gRecaptchaResponse")
            or solution.get("cf_turnstile_response")
        )
    else:
        token = None
    if not token:
        raise RuntimeError("Captcha Gateway returned no hCaptcha token")
    if emit:
        emit("Captcha Gateway solve completed")
    return str(token)


def solve_hcaptcha_token(
    *,
    provider: str = "yescaptcha",
    api_key: str,
    website_url: str,
    website_key: str,
    endpoint: str = "",
    timeout: float = 180,
    invisible: bool = True,
    user_agent: str = "",
    proxy_url: str = "",
    rqdata: str = "",
    emit: Callable[[str], None] | None = None,
) -> str:
    key = (api_key or "").strip()
    if not key:
        raise ValueError("captcha API key is required")
    selected = (provider or "yescaptcha").strip().lower().replace("-", "_")
    if selected == "gateway":
        selected = "captcha_gateway"
    if selected == "captcha_gateway":
        return _solve_with_captcha_gateway(
            api_key=key,
            website_url=(website_url or "").strip(),
            website_key=(website_key or "").strip(),
            endpoint=endpoint,
            timeout=timeout,
            invisible=invisible,
            user_agent=user_agent,
            proxy_url=proxy_url,
            rqdata=rqdata,
            emit=emit,
        )
    if selected != "yescaptcha":
        raise ValueError("captcha provider must be yescaptcha or captcha_gateway")
    solver = _solver_class()(
        key,
        endpoint=endpoint or None,
        timeout=timeout,
        on_progress=emit,
    )
    proxy = captcha_proxy_fields(proxy_url) if proxy_url else {}
    return solver.solve_hcaptcha(
        website_url,
        website_key,
        invisible=invisible,
        user_agent=user_agent or None,
        proxy=proxy or None,
        rqdata=rqdata or None,
    )
