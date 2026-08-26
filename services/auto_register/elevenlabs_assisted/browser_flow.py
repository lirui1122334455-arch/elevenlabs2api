from __future__ import annotations

import json
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlsplit

from .captcha import solve_hcaptcha_token
from .config import ElevenLabsConfig
from .credentials import api_key_from_user_payload, subscription_snapshot
from .mailbox_link import redacted_link_label
from .proxy_preflight import safe_proxy_label


SIGNUP_URL = "https://elevenlabs.io/app/sign-up"
SIGNIN_URL = "https://elevenlabs.io/app/sign-in"
API_KEY_NAME = "ElevenLabs Console Gateway"
API_KEY_PERMISSIONS = [
    "sound_generation",
    "image_video_generation",
    "flows",
    "models_read",
    "user_read",
]
SIGNUP_EMAIL = '[data-testid="sign-up-email-input"], input[type="email"][name="email"]'
SIGNUP_PASSWORD = '[data-testid="sign-up-password-input"], input[type="password"][name="password"]'
SIGNUP_SUBMIT = '[data-testid="signup-signup-button-div"] button, button:text-is("Sign up")'
SIGNIN_EMAIL = '[data-testid="sign-in-email-input"], input[type="email"][name="email"]'
SIGNIN_PASSWORD = '[data-testid="sign-in-password-input"], input[type="password"][name="password"]'
SIGNIN_SUBMIT = '[data-testid="sign-in-submit-button"], button[type="submit"]:text-is("Sign in")'
TERMS_INPUT = 'input[name="terms"]'
AUTHENTICATED_ROOT = '#authenticated-root[data-authed="true"]'
SITEKEY_RE = re.compile(r"(?:hCaptchaSiteKey|hcaptchaSiteKey|sitekey)['\"]?\s*[:=]\s*['\"]([0-9a-f-]{8,})['\"]", re.I)
RQDATA_RE = re.compile(r"rqdata['\"]?\s*[:=]\s*['\"]([^'\"]+)['\"]", re.I)
HCAPTCHA_INIT_SCRIPT = """
(() => {
  const callbacks = window.__elevenlabsHcaptchaCallbacks || {};
  window.__elevenlabsHcaptchaCallbacks = callbacks;
  window.__elevenlabsHcaptchaLastWidgetId = window.__elevenlabsHcaptchaLastWidgetId || '';
  window.__elevenlabsHcaptchaExecuteCalls = 0;
  window.__elevenlabsHcaptchaLastExecuteAsync = false;

  const resolveCallback = (value) => {
    if (typeof value === 'function') return value;
    if (typeof value === 'string' && typeof window[value] === 'function') return window[value];
    return null;
  };

  const setResponseFields = (token) => {
    for (const node of document.querySelectorAll(
      'textarea[name="h-captcha-response"], textarea[name="g-recaptcha-response"], input[name="h-captcha-response"], input[name="g-recaptcha-response"]'
    )) {
      const prototype = node instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
      if (setter) setter.call(node, token);
      else node.value = token;
      node.dispatchEvent(new Event('input', { bubbles: true }));
      node.dispatchEvent(new Event('change', { bubbles: true }));
    }
  };

  const invokeCallback = (widgetId, token, key, force = false) => {
    const normalized = String(widgetId ?? window.__elevenlabsHcaptchaLastWidgetId ?? '');
    const entry = callbacks[normalized] || callbacks.__last;
    const callback = entry && resolveCallback(entry.callback);
    if (!callback || (!force && entry.lastToken === token)) return 0;
    entry.lastToken = token;
    queueMicrotask(() => callback(token, key || ''));
    return 1;
  };

  window.__elevenlabsSeedHcaptchaToken = (token, key = '') => {
    window.__elevenlabsHcaptchaToken = token;
    window.__elevenlabsHcaptchaResponseKey = key;
    setResponseFields(token);
    const widgetIds = Object.keys(callbacks).filter((value) => value !== '__last');
    return {
      callbacks: widgetIds.length,
      invoked: 0,
      widgetId: widgetIds.join(','),
    };
  };

  window.__elevenlabsApplyHcaptchaToken = (token, key = '') => {
    const seeded = window.__elevenlabsSeedHcaptchaToken(token, key);
    const widgetIds = Object.keys(callbacks).filter((value) => value !== '__last');
    let invoked = 0;
    for (const widgetId of widgetIds) invoked += invokeCallback(widgetId, token, key);
    return {
      callbacks: seeded.callbacks,
      invoked,
      widgetId: seeded.widgetId,
    };
  };

  const patch = (api) => {
    if (!api) return api;
    api.__elevenlabsPatched = true;

    const wrapRender = (value) => {
      if (typeof value !== 'function' || value.__elevenlabsPatched) return value;
      const originalRender = value.bind(api);
      const wrapped = function (container, params) {
        let widgetId = '';
        const options = params && typeof params === 'object' ? { ...params } : {};
        const originalCallback = options.callback;
        options.callback = function (token, key) {
          const entry = callbacks[String(widgetId)] || callbacks.__last;
          if (entry) entry.lastToken = token;
          const callback = resolveCallback(originalCallback);
          return callback ? callback(token, key) : undefined;
        };
        widgetId = originalRender(container, options);
        const entry = { callback: options.callback, lastToken: '' };
        callbacks[String(widgetId)] = entry;
        callbacks.__last = entry;
        window.__elevenlabsHcaptchaLastWidgetId = String(widgetId);
        return widgetId;
      };
      wrapped.__elevenlabsPatched = true;
      return wrapped;
    };

    const wrapExecute = (value) => {
      if (typeof value !== 'function' || value.__elevenlabsPatched) return value;
      const originalExecute = value.bind(api);
      const wrapped = function (widgetId, options) {
        window.__elevenlabsHcaptchaExecuteCalls += 1;
        window.__elevenlabsHcaptchaLastExecuteAsync = !!(options && options.async === true);
        const token = window.__elevenlabsHcaptchaToken || '';
        if (!token) return originalExecute.apply(this, arguments);
        const key = window.__elevenlabsHcaptchaResponseKey || '';
        if (options && options.async === true) {
          return Promise.resolve({ response: token, key });
        }
        invokeCallback(widgetId, token, key, true);
        return undefined;
      };
      wrapped.__elevenlabsPatched = true;
      return wrapped;
    };

    const trap = (name, wrap) => {
      const marker = `__elevenlabs${name}Trap`;
      if (api[marker]) return;
      let value = wrap(api[name]);
      try {
        Object.defineProperty(api, name, {
          configurable: true,
          enumerable: true,
          get: () => value,
          set: (next) => { value = wrap(next); },
        });
        api[marker] = true;
      } catch (_) {
        if (value) api[name] = value;
      }
    };

    trap('render', wrapRender);
    trap('execute', wrapExecute);
    return api;
  };

  let current = window.hcaptcha;
  try {
    Object.defineProperty(window, 'hcaptcha', {
      configurable: true,
      enumerable: true,
      get: () => current,
      set: (value) => { current = patch(value); },
    });
    current = patch(current);
  } catch (_) {
    current = window.hcaptcha;
  }

  const timer = setInterval(() => {
    const value = patch(window.hcaptcha);
    if (value && current !== value) current = value;
  }, 10);
  setTimeout(() => clearInterval(timer), 60000);

  window.__elevenlabsHcaptchaBridgeStatus = () => ({
    patched: !!(window.hcaptcha && window.hcaptcha.__elevenlabsPatched),
    callbacks: Object.keys(callbacks).filter((value) => value !== '__last').length,
    widgetId: String(window.__elevenlabsHcaptchaLastWidgetId || ''),
    executeCalls: Number(window.__elevenlabsHcaptchaExecuteCalls || 0),
    lastExecuteAsync: !!window.__elevenlabsHcaptchaLastExecuteAsync,
  });
})();
"""


@dataclass(frozen=True)
class BrowserResult:
    email: str
    final_url: str
    authenticated: bool
    api_key: str = ""
    subscription: dict[str, Any] | None = None


def _playwright_proxy(proxy_url: str) -> dict[str, str]:
    parsed = urlsplit(proxy_url)
    scheme = "socks5" if parsed.scheme.lower() == "socks5h" else parsed.scheme
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    value = {"server": f"{scheme}://{host}:{parsed.port}"}
    if parsed.username:
        value["username"] = unquote(parsed.username)
    if parsed.password:
        value["password"] = unquote(parsed.password)
    return value


def _emit(emit: Callable[[str], None], phase: str, message: str) -> None:
    emit(f"[phase:{phase}] {message}")


def _new_context(config: ElevenLabsConfig) -> tuple[Any, Any, str | None]:
    from playwright.sync_api import sync_playwright

    profile_dir = config.profile_dir
    temporary_profile = None
    if config.unique_profile:
        config.profile_dir.mkdir(parents=True, exist_ok=True)
        profile_dir = tempfile.mkdtemp(prefix="run-", dir=config.profile_dir)
        temporary_profile = profile_dir
    else:
        profile_dir.mkdir(parents=True, exist_ok=True)
    manager = sync_playwright().start()
    try:
        launch_options: dict[str, Any] = {
            "user_data_dir": str(profile_dir),
            "headless": config.headless,
            "args": [
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        }
        if config.browser_channel:
            launch_options["channel"] = config.browser_channel
        if config.proxy_url:
            launch_options["proxy"] = _playwright_proxy(config.proxy_url)
        context = manager.chromium.launch_persistent_context(
            **launch_options,
        )
    except Exception:
        manager.stop()
        if temporary_profile:
            shutil.rmtree(temporary_profile, ignore_errors=True)
        raise
    context.set_default_timeout(config.navigation_timeout * 1000)
    context.set_default_navigation_timeout(config.navigation_timeout * 1000)
    context.add_init_script(HCAPTCHA_INIT_SCRIPT)
    return manager, context, temporary_profile


def _close_context(manager: Any, context: Any, temporary_profile: str | None) -> None:
    try:
        context.close()
    finally:
        manager.stop()
        if temporary_profile:
            shutil.rmtree(temporary_profile, ignore_errors=True)


def _page(context: Any) -> Any:
    return context.pages[0] if context.pages else context.new_page()


def _capture_account_response(response: Any, state: dict[str, Any]) -> None:
    try:
        path = urlsplit(str(response.url or "")).path.rstrip("/").lower()
        if path not in {"/v1/user", "/v1/user/internal", "/v1/user/subscription"} or int(response.status) != 200:
            return
        request_headers = response.request.all_headers()
        auth_headers = {
            key: value
            for key, value in request_headers.items()
            if key.lower() in {"authorization", "x-xi-workspace-id", "xi-workspace-id"}
        }
        if auth_headers:
            parsed = urlsplit(str(response.url or ""))
            state["auth_headers"] = auth_headers
            state["api_base_url"] = f"{parsed.scheme}://{parsed.netloc}"
        payload = response.json()
    except Exception:
        return
    snapshot = subscription_snapshot(payload)
    if snapshot:
        state["subscription"] = snapshot
    api_key = api_key_from_user_payload(payload)
    if api_key:
        state["api_key"] = api_key


def _wait_for_account_snapshot(
    page: Any,
    state: dict[str, Any],
    emit: Callable[[str], None],
    timeout: float = 15,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not state.get("subscription"):
        page.wait_for_timeout(250)
    subscription = state.get("subscription")
    if isinstance(subscription, dict) and subscription:
        _emit(
            emit,
            "sync_account",
            f"subscription captured tier={subscription.get('tier') or 'unknown'} "
            f"used={subscription.get('character_count', 'unknown')} "
            f"limit={subscription.get('character_limit', 'unknown')} "
            f"api_key={'available' if state.get('api_key') else 'unavailable'}",
        )
    else:
        _emit(emit, "sync_account", "subscription was not observed; account will be saved with quota pending")


def _api_error(response: Any, operation: str) -> RuntimeError:
    message = f"ElevenLabs {operation} returned HTTP {int(response.status)}"
    try:
        payload = response.json()
        detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
        if isinstance(detail, dict):
            value = detail.get("message") or detail.get("detail") or detail.get("code")
            if value:
                message += f": {str(value)[:300]}"
        elif isinstance(detail, str):
            message += f": {detail[:300]}"
    except Exception:
        pass
    return RuntimeError(message)


def _provision_gateway_api_key(
    page: Any,
    state: dict[str, Any],
    emit: Callable[[str], None],
) -> str:
    existing = str(state.get("api_key") or "").strip()
    if existing:
        return existing
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not state.get("auth_headers"):
        page.wait_for_timeout(250)
    headers = state.get("auth_headers")
    api_base_url = str(state.get("api_base_url") or "").rstrip("/")
    if not isinstance(headers, dict) or not headers or not api_base_url:
        raise RuntimeError("ElevenLabs authenticated API session was not captured")
    _emit(emit, "provision_api_key", "creating a restricted API key for the generation gateway")
    response = page.context.request.post(
        api_base_url + "/v1/user/create-api-key",
        headers={**headers, "Content-Type": "application/json"},
        data={"name": API_KEY_NAME, "permissions": API_KEY_PERMISSIONS},
        timeout=30_000,
    )
    if int(response.status) != 200:
        raise _api_error(response, "API key creation")
    try:
        payload = response.json()
    except Exception as exc:
        raise RuntimeError("ElevenLabs API key creation returned invalid JSON") from exc
    api_key = ""
    if isinstance(payload, dict):
        api_key = str(payload.get("xi-api-key") or payload.get("xi_api_key") or "").strip()
    if not api_key:
        raise RuntimeError("ElevenLabs API key creation did not return the one-time key")
    state["api_key"] = api_key
    _emit(emit, "provision_api_key", "restricted gateway API key created")
    return api_key


def connect_existing_account(
    config: ElevenLabsConfig,
    *,
    email: str,
    password: str,
    emit: Callable[[str], None] = print,
) -> BrowserResult:
    manager, context, temporary_profile = _new_context(config)
    account_state: dict[str, Any] = {"api_key": "", "subscription": None}
    try:
        page = _page(context)
        page.on("response", lambda response: _capture_account_response(response, account_state))
        _emit(emit, "load_signin", "opening ElevenLabs sign-in for gateway connection")
        _automated_signin(page, config, email=email, password=password, emit=emit)
        _complete_onboarding(page, emit)
        _raise_if_account_restricted(page)
        _wait_for_account_snapshot(page, account_state, emit)
        api_key = _provision_gateway_api_key(page, account_state, emit)
        return BrowserResult(
            email=email,
            final_url=page.url,
            authenticated=True,
            api_key=api_key,
            subscription=(
                dict(account_state["subscription"])
                if isinstance(account_state.get("subscription"), dict)
                else None
            ),
        )
    finally:
        _close_context(manager, context, temporary_profile)


def _wait_for_form(page: Any, selectors: tuple[str, ...], label: str) -> None:
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            for selector in selectors:
                page.locator(selector).first.wait_for(state="visible", timeout=45_000)
            return
        except Exception as exc:  # Playwright exposes version-specific timeout classes.
            last_error = exc
            if attempt == 0:
                page.reload(wait_until="domcontentloaded")
    raise RuntimeError(f"ElevenLabs {label} form did not become ready after one reload") from last_error


def _assert_signup_page(page: Any) -> None:
    if "/app/home" in page.url:
        raise RuntimeError("the dedicated Chrome profile is already authenticated")
    _wait_for_form(page, (SIGNUP_EMAIL, SIGNUP_PASSWORD, SIGNUP_SUBMIT), "sign-up")


def _wait_for_hcaptcha_bridge(page: Any, timeout: float = 15) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_status: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            status = page.evaluate(
                """() => window.__elevenlabsHcaptchaBridgeStatus
                    ? window.__elevenlabsHcaptchaBridgeStatus()
                    : { patched: false, callbacks: 0, widgetId: '' }"""
            )
            if isinstance(status, dict):
                last_status = status
                if bool(status.get("patched")) and int(status.get("callbacks") or 0) > 0:
                    return status
        except Exception:
            pass
        time.sleep(0.1)
    raise RuntimeError(
        "hcaptcha_bridge_unavailable: the ElevenLabs hCaptcha callback was not captured "
        f"(patched={bool(last_status.get('patched'))}, callbacks={int(last_status.get('callbacks') or 0)})"
    )


def dry_run_browser(config: ElevenLabsConfig, emit: Callable[[str], None] = print) -> None:
    manager, context, temporary_profile = _new_context(config)
    try:
        page = _page(context)
        _emit(emit, "load_signup", f"opening ElevenLabs sign-up page via {safe_proxy_label(config.proxy_url)}")
        page.goto(SIGNUP_URL, wait_until="domcontentloaded")
        _assert_signup_page(page)
        page.locator(SIGNUP_EMAIL).fill("dry-run@example.invalid")
        page.locator(SIGNUP_PASSWORD).fill("DryRun9!NoSubmit")
        bridge = _wait_for_hcaptcha_bridge(page)
        _emit(
            emit,
            "dry_run_done",
            f"selectors and hCaptcha bridge verified callbacks={int(bridge.get('callbacks') or 0)}; no form was submitted",
        )
    finally:
        _close_context(manager, context, temporary_profile)


def _authenticated(page: Any) -> bool:
    try:
        if page.locator(AUTHENTICATED_ROOT).count() and page.locator(AUTHENTICATED_ROOT).first.is_visible():
            return True
    except Exception:
        pass
    path = urlsplit(page.url).path.rstrip("/")
    return path == "/app/home" or path.startswith("/app/onboarding")


def _raise_if_account_restricted(page: Any) -> None:
    body = _page_text(page)
    checks = (
        ("unusual activity", "detected_unusual_activity"),
        ("too many free accounts", "free_account_limit"),
        ("one free account", "free_account_limit"),
        ("phone verification", "phone_verification_required"),
        ("verify your phone", "phone_verification_required"),
        ("account has been disabled", "account_disabled"),
        ("account suspended", "account_disabled"),
        ("restricted country", "geo_restricted"),
    )
    for needle, code in checks:
        if needle in body:
            raise RuntimeError(f"{code}: ElevenLabs blocked this account ({needle})")


def _complete_onboarding(page: Any, emit: Callable[[str], None], timeout: float = 45) -> None:
    path = urlsplit(page.url).path.lower()
    if not path.startswith("/app/onboarding"):
        return
    _emit(emit, "onboarding", "completing ElevenLabs first-run onboarding")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _raise_if_account_restricted(page)
        path = urlsplit(page.url).path.rstrip("/")
        if path == "/app/home" or path.startswith("/app/home"):
            _emit(emit, "onboarding", "onboarding finished")
            return
        clicked = False
        for selector in (
            'button:has-text("Continue")',
            'button:has-text("Next")',
            'button:has-text("Get started")',
            'button:has-text("Skip")',
            'button[type="submit"]',
            '[data-testid*="continue"] button',
            '[data-testid*="next"] button',
        ):
            locator = page.locator(selector)
            if locator.count() == 0:
                continue
            try:
                button = locator.first
                if button.is_visible() and button.is_enabled():
                    button.click()
                    clicked = True
                    page.wait_for_timeout(800)
                    break
            except Exception:
                continue
        if not clicked:
            page.wait_for_timeout(1000)
    _raise_if_account_restricted(page)
    path = urlsplit(page.url).path
    if path.startswith("/app/onboarding"):
        _emit(emit, "onboarding", f"left onboarding unfinished path={path}")


def _capture_signin_response(response: Any, state: dict[str, Any]) -> None:
    try:
        parsed = urlsplit(str(response.url or ""))
        if parsed.hostname != "identitytoolkit.googleapis.com" or not parsed.path.endswith(
            "/accounts:signInWithPassword"
        ):
            return
        state["status"] = int(response.status)
        state["error"] = ""
        if int(response.status) >= 400:
            payload = response.json()
            error = payload.get("error", payload) if isinstance(payload, dict) else payload
            if isinstance(error, dict):
                state["error"] = str(error.get("message") or error.get("status") or "")[:240]
            elif isinstance(error, str):
                state["error"] = error[:240]
    except Exception:
        return


def _capture_verification_response(response: Any, state: dict[str, Any]) -> None:
    try:
        parsed = urlsplit(str(response.url or ""))
        if parsed.hostname != "identitytoolkit.googleapis.com" or not parsed.path.endswith(
            "/accounts:update"
        ):
            return
        request_payload = _request_json_payload(response.request)
        if not str(request_payload.get("oobCode") or "").strip():
            return
        status = int(response.status)
        state["requests"] = int(state.get("requests") or 0) + 1
        state["last_response_at"] = time.monotonic()
        if state.get("verified"):
            return
        if 200 <= status < 300:
            state.update({"verified": True, "status": status, "error": ""})
            return
        try:
            payload = response.json()
        except Exception:
            payload = None
        state.update(
            {
                "status": status,
                "error": _submission_error_message(payload) or "identity provider rejected the action code",
            }
        )
    except Exception:
        return


def _wait_for_authenticated(
    page: Any,
    timeout: float,
    *,
    signin_state: dict[str, Any] | None = None,
    emit: Callable[[str], None] | None = None,
    attempt: int = 1,
) -> bool:
    started = time.monotonic()
    deadline = time.monotonic() + timeout
    last_report = -10.0
    while time.monotonic() < deadline:
        if _authenticated(page):
            return True
        if signin_state and isinstance(signin_state.get("status"), int):
            status = int(signin_state["status"])
            if status >= 400:
                detail = str(signin_state.get("error") or "identity provider rejected the request")
                raise RuntimeError(f"ElevenLabs sign-in HTTP {status}: {detail}")
        body = _page_text(page)
        if "invalid credential" in body or "incorrect password" in body:
            raise RuntimeError("ElevenLabs rejected the sign-in credentials")
        if "network-request-failed" in body or "auth/network-request-failed" in body:
            raise RuntimeError("ElevenLabs reported auth/network-request-failed; check the proxy")
        elapsed = time.monotonic() - started
        if emit and elapsed - last_report >= 10:
            path = urlsplit(str(page.url or "")).path or "/"
            status = signin_state.get("status") if signin_state else None
            _emit(
                emit,
                "fill_signin",
                f"waiting for authenticated page attempt={attempt} elapsed={int(elapsed)}s "
                f"path={path} identity_status={status or 'pending'}",
            )
            last_report = elapsed
        time.sleep(1)
    return False


def _signin_bridge_status(page: Any, timeout: float = 3) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            value = page.evaluate(
                """() => window.__elevenlabsHcaptchaBridgeStatus
                    ? window.__elevenlabsHcaptchaBridgeStatus()
                    : { patched: false, callbacks: 0, widgetId: '' }"""
            )
            if isinstance(value, dict):
                last = value
                if bool(value.get("patched")) and int(value.get("callbacks") or 0) > 0:
                    return value
        except Exception:
            pass
        time.sleep(0.1)
    return last


def _fill_and_submit_signin(
    page: Any,
    email: str,
    password: str,
    *,
    emit: Callable[[str], None] | None = None,
) -> None:
    for attempt in range(2):
        _wait_for_form(page, (SIGNIN_EMAIL, SIGNIN_PASSWORD, SIGNIN_SUBMIT), "sign-in")
        page.locator(SIGNIN_EMAIL).fill(email)
        page.locator(SIGNIN_PASSWORD).fill(password)
        try:
            _click_signin(page)
            return
        except RuntimeError as exc:
            if "sign-in submit button remained disabled" not in str(exc) or attempt > 0:
                raise
            if emit:
                _emit(
                    emit,
                    "fill_signin",
                    "sign-in form remained disabled after email verification; reloading before submit",
                )
            page.goto(SIGNIN_URL, wait_until="domcontentloaded")


def _automated_signin(
    page: Any,
    config: ElevenLabsConfig,
    *,
    email: str,
    password: str,
    emit: Callable[[str], None],
) -> None:
    signin_state: dict[str, Any] = {"status": None, "error": ""}
    page.on("response", lambda response: _capture_signin_response(response, signin_state))
    page.goto(SIGNIN_URL, wait_until="domcontentloaded")
    _emit(emit, "fill_signin", "submitting ElevenLabs sign-in attempt 1/2")
    _fill_and_submit_signin(page, email, password, emit=emit)
    if _wait_for_authenticated(
        page,
        30,
        signin_state=signin_state,
        emit=emit,
        attempt=1,
    ):
        return

    bridge = _signin_bridge_status(page)
    signin_state.update({"status": None, "error": ""})
    if bool(bridge.get("patched")) and int(bridge.get("callbacks") or 0) > 0:
        _emit(emit, "fill_signin", "sign-in hCaptcha detected; solving before retry")
        token = _solve_hcaptcha(page, config, emit, invisible=True)
        _stage_hcaptcha_token(page, token, emit)
    else:
        _emit(emit, "fill_signin", "first sign-in attempt did not authenticate; reloading before retry")
        page.goto(SIGNIN_URL, wait_until="domcontentloaded")
    _emit(emit, "fill_signin", "submitting ElevenLabs sign-in attempt 2/2")
    _fill_and_submit_signin(page, email, password, emit=emit)
    if _wait_for_authenticated(
        page,
        45,
        signin_state=signin_state,
        emit=emit,
        attempt=2,
    ):
        return
    path = urlsplit(str(page.url or "")).path or "/"
    raise RuntimeError(
        "timed out waiting for an authenticated ElevenLabs page after two sign-in attempts "
        f"(path={path}, identity_status={signin_state.get('status') or 'pending'})"
    )


def _page_text(page: Any) -> str:
    try:
        return page.locator("body").inner_text(timeout=1000).lower()
    except Exception:
        return ""


def _raise_if_signup_failed(page: Any) -> None:
    body = _page_text(page)
    checks = (
        ("email already", "email_in_use"),
        ("already in use", "email_in_use"),
        ("invalid recaptcha", "captcha_invalid"),
        ("restricted country", "geo_restricted"),
        ("email not allowlisted", "email_not_allowlisted"),
        ("no available seats", "no_seats"),
        ("too many requests", "too_many_requests"),
        ("weak password", "weak_password"),
        ("invalid email", "invalid_email"),
        ("network-request-failed", "proxy_unavailable"),
    )
    for needle, code in checks:
        if needle in body:
            raise RuntimeError(f"{code}: ElevenLabs rejected signup ({needle})")


def _verification_required(page: Any) -> bool:
    path = urlsplit(page.url).path.lower()
    if any(part in path for part in ("verify", "check-email", "confirm-email", "email-verification")):
        return True
    body = _page_text(page)
    markers = (
        "verify your email",
        "verification email",
        "check your email",
        "we've sent",
        "we have sent",
        "sent a verification",
        "confirm your email",
        "has not been verified",
        "resend",
    )
    return sum(1 for marker in markers if marker in body) >= 1


def _wait_for_verification_required(page: Any, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _raise_if_signup_failed(page)
        if _verification_required(page):
            return
        time.sleep(1)
    raise RuntimeError("timed out waiting for the ElevenLabs verification-email state")


def _email_verified(page: Any) -> bool:
    body = _page_text(page)
    markers = (
        "email verified",
        "successfully verified",
        "verification successful",
        "verified your email",
    )
    return any(marker in body for marker in markers) or _authenticated(page)


def _verification_response_error(state: dict[str, Any]) -> RuntimeError | None:
    status = state.get("status")
    if not isinstance(status, int) or status < 400 or state.get("verified"):
        return None
    detail = str(state.get("error") or "identity provider rejected the action code")[:240]
    return RuntimeError(f"verification_link_rejected: Firebase HTTP {status}: {detail}")


def _wait_for_email_verified(
    page: Any,
    timeout: float,
    *,
    verification_state: dict[str, Any] | None = None,
) -> None:
    state = verification_state if verification_state is not None else {}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = _page_text(page)
        if "expired" in body or "invalid verification" in body or "link is invalid" in body:
            raise RuntimeError("verification_link_expired: ElevenLabs reported an expired or invalid link")
        if state.get("verified") or _email_verified(page):
            return
        response_error = _verification_response_error(state)
        last_response_at = float(state.get("last_response_at") or 0)
        if response_error and time.monotonic() - last_response_at >= 2:
            raise response_error
        time.sleep(1)
    if state.get("verified") or _email_verified(page):
        return
    response_error = _verification_response_error(state)
    if response_error:
        raise response_error
    path = urlsplit(str(page.url or "")).path or "/"
    raise RuntimeError(
        "timed out waiting for ElevenLabs to confirm the email verification action "
        f"(path={path}, identity_status={state.get('status') or 'not_observed'})"
    )


def _accept_terms(page: Any) -> None:
    terms = page.locator(TERMS_INPUT)
    if terms.count() == 0:
        return
    box = terms.first
    control = page.locator('button[role="checkbox"][aria-required="true"]').first

    def accepted() -> bool:
        try:
            return box.is_checked() and control.get_attribute("aria-checked") == "true"
        except Exception:
            return False

    if accepted():
        return
    try:
        control.wait_for(state="visible", timeout=5_000)
        control.click(force=True, timeout=5_000)
        page.wait_for_timeout(100)
    except Exception:
        pass
    if not accepted():
        raise RuntimeError("terms_not_accepted: ElevenLabs terms checkbox did not enter the checked state")


def _click_signup(page: Any) -> None:
    button = page.locator(SIGNUP_SUBMIT).first
    button.wait_for(state="visible")
    try:
        button.click()
        return
    except Exception:
        pass
    page.evaluate(
        """() => {
            const node = document.querySelector('[data-testid="signup-signup-button-div"]');
            const button = node && node.querySelector('button');
            if (button instanceof HTMLButtonElement) button.click();
            else if (node instanceof HTMLElement) node.click();
        }"""
    )


def _click_signin(page: Any) -> None:
    button = page.locator(SIGNIN_SUBMIT).first
    button.wait_for(state="visible")
    try:
        button.click(timeout=10_000, no_wait_after=True)
        return
    except Exception:
        if _authenticated(page):
            return
    try:
        if button.is_enabled():
            button.click(force=True, timeout=5_000, no_wait_after=True)
            return
    except Exception:
        pass
    submitted = button.evaluate(
        """(node) => {
            if (!(node instanceof HTMLButtonElement) || node.disabled) return false;
            const form = node.form;
            if (form) form.requestSubmit(node);
            else node.click();
            return true;
        }"""
    )
    if not submitted:
        raise RuntimeError("ElevenLabs sign-in submit button remained disabled")


def _sitekey_from_url(url: str) -> str:
    parsed = urlsplit(url)
    for component in (parsed.query, parsed.fragment.lstrip("?")):
        values = parse_qs(component).get("sitekey") or []
        if values:
            return str(values[0])
    return ""


def _extract_hcaptcha_sitekey(page: Any) -> str:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            key = page.evaluate(
                """() => {
                    const sitekeyFromUrl = (value) => {
                        if (!value) return '';
                        try {
                            const url = new URL(value, window.location.href);
                            const queryValue = url.searchParams.get('sitekey');
                            if (queryValue) return queryValue;
                            return new URLSearchParams(url.hash.replace(/^#/, '')).get('sitekey') || '';
                        } catch (_) {
                            return '';
                        }
                    };
                    const fromEnv = (window.getEnv && window.getEnv().hCaptchaSiteKey)
                        || window.hCaptchaSiteKey
                        || (window.__NEXT_DATA__ && window.__NEXT_DATA__.runtimeConfig && window.__NEXT_DATA__.runtimeConfig.hCaptchaSiteKey)
                        || '';
                    if (fromEnv) return fromEnv;
                    for (const node of document.querySelectorAll('[data-sitekey]')) {
                        const direct = node.getAttribute('data-sitekey');
                        if (direct) return direct;
                    }
                    for (const iframe of document.querySelectorAll('iframe[src*="hcaptcha.com"]')) {
                        const value = sitekeyFromUrl(iframe.src);
                        if (value) return value;
                    }
                    if (window.performance && window.performance.getEntriesByType) {
                        for (const entry of window.performance.getEntriesByType('resource')) {
                            if (!String(entry.name || '').includes('hcaptcha')) continue;
                            const value = sitekeyFromUrl(entry.name);
                            if (value) return value;
                        }
                    }
                    return '';
                }"""
            )
            if key:
                return str(key)
        except Exception:
            pass
        try:
            for frame in page.frames:
                key = _sitekey_from_url(str(getattr(frame, "url", "") or ""))
                if key:
                    return key
        except Exception:
            pass
        time.sleep(0.5)
    try:
        html = page.content()
    except Exception:
        html = ""
    match = SITEKEY_RE.search(html)
    if match:
        return match.group(1)
    raise RuntimeError("hCaptcha sitekey was not found on the ElevenLabs sign-up page")


def _extract_hcaptcha_rqdata(page: Any) -> str:
    try:
        html = page.content()
    except Exception:
        return ""
    match = RQDATA_RE.search(html)
    return match.group(1) if match else ""


def _submission_error_message(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("detail", "error", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:240]
        if isinstance(value, dict):
            status = str(value.get("status") or value.get("code") or "").strip()
            message = str(value.get("message") or value.get("detail") or "").strip()
            combined = ": ".join(part for part in (status, message) if part)
            if combined:
                return combined[:240]
    return ""


def _request_json_payload(request: Any) -> dict[str, Any]:
    try:
        value = getattr(request, "post_data_json", None)
        payload = value() if callable(value) else value
        if isinstance(payload, dict):
            return dict(payload)
    except Exception:
        pass
    try:
        value = getattr(request, "post_data", None)
        raw = value() if callable(value) else value
        payload = json.loads(raw or "{}")
        return dict(payload) if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _install_presignup_token_route(
    page: Any,
    token: str,
    emit: Callable[[str], None],
) -> dict[str, Any]:
    state: dict[str, Any] = {"requests": 0, "status": None, "error": ""}

    def handle(route: Any) -> None:
        request = route.request
        url = str(getattr(request, "url", "") or "")
        if "/v1/user/pre-sign-up" not in url or str(getattr(request, "method", "")).upper() != "POST":
            route.continue_()
            return
        payload = _request_json_payload(request)
        if not payload:
            state["error"] = "pre-sign-up request body was empty or unreadable"
            _emit(emit, "submit_signup", state["error"])
            route.continue_()
            return
        payload["recaptcha_token"] = token
        headers = {
            key: value
            for key, value in dict(request.headers).items()
            if key.lower() not in {"content-length", "content-encoding"}
        }
        headers["content-type"] = "application/json"
        state["requests"] = int(state["requests"]) + 1
        try:
            response = route.fetch(post_data=json.dumps(payload), headers=headers)
            state["status"] = int(response.status)
            try:
                response_payload = response.json()
            except Exception:
                response_payload = None
            state["error"] = _submission_error_message(response_payload)
            detail = f" detail={state['error']}" if state["error"] else ""
            _emit(
                emit,
                "submit_signup",
                f"pre-sign-up HTTP {state['status']} request={state['requests']}{detail}",
            )
            route.fulfill(response=response)
        except Exception as exc:
            state["error"] = f"request interception failed: {str(exc)[:180]}"
            _emit(emit, "submit_signup", state["error"])
            route.continue_(post_data=json.dumps(payload), headers=headers)

    try:
        page.unroute("**/v1/user/pre-sign-up*")
    except Exception:
        pass
    page.route("**/v1/user/pre-sign-up*", handle)
    return state


def _stage_hcaptcha_token(
    page: Any,
    token: str,
    emit: Callable[[str], None],
) -> dict[str, Any]:
    deadline = time.monotonic() + 5
    last_result: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            result = page.evaluate(
                """(token) => window.__elevenlabsSeedHcaptchaToken
                    ? window.__elevenlabsSeedHcaptchaToken(token)
                    : { callbacks: 0, invoked: 0, widgetId: '' }""",
                token,
            )
            if isinstance(result, dict):
                last_result = result
                if int(result.get("callbacks") or 0) > 0:
                    _emit(
                        emit,
                        "await_hcaptcha",
                        f"token staged for React async execute widget={str(result.get('widgetId') or 'unknown')[:32]}",
                    )
                    return result
        except Exception:
            pass
        time.sleep(0.1)
    raise RuntimeError(
        "hcaptcha_token_not_staged: solver returned a token but the React bridge rejected it "
        f"(callbacks={int(last_result.get('callbacks') or 0)})"
    )


def _wait_for_hcaptcha_execute(page: Any, previous_calls: int, timeout: float = 5) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_status: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            status = page.evaluate(
                """() => window.__elevenlabsHcaptchaBridgeStatus
                    ? window.__elevenlabsHcaptchaBridgeStatus()
                    : { executeCalls: 0, lastExecuteAsync: false }"""
            )
            if isinstance(status, dict):
                last_status = status
                if int(status.get("executeCalls") or 0) > previous_calls:
                    if not bool(status.get("lastExecuteAsync")):
                        raise RuntimeError(
                            "hcaptcha_execute_contract_changed: ElevenLabs no longer uses the async execute contract"
                        )
                    return status
        except RuntimeError:
            raise
        except Exception:
            pass
        time.sleep(0.1)
    raise RuntimeError(
        "signup_submit_not_started: clicking Sign up did not call the ElevenLabs hCaptcha widget "
        f"(executeCalls={int(last_status.get('executeCalls') or 0)})"
    )


def _solve_hcaptcha(page: Any, config: ElevenLabsConfig, emit: Callable[[str], None], *, invisible: bool) -> str:
    mode = "invisible" if invisible else "interactive"
    _emit(emit, "await_hcaptcha", f"solving {mode} hCaptcha")
    return solve_hcaptcha_token(
        provider=config.captcha_provider,
        api_key=config.captcha_api_key,
        website_url=page.url or SIGNUP_URL,
        website_key=_extract_hcaptcha_sitekey(page),
        endpoint=config.captcha_endpoint,
        timeout=config.captcha_timeout,
        invisible=invisible,
        user_agent=page.evaluate("() => navigator.userAgent"),
        proxy_url=config.proxy_url,
        rqdata=_extract_hcaptcha_rqdata(page),
        emit=lambda message: _emit(emit, "await_hcaptcha", message),
    )


def _wait_for_signup_outcome(
    page: Any,
    state: dict[str, Any],
    *,
    timeout: float,
    emit: Callable[[str], None],
) -> None:
    started = time.monotonic()
    deadline = started + timeout
    last_report = -10.0
    while time.monotonic() < deadline:
        if _verification_required(page):
            return
        _raise_if_signup_failed(page)
        status = state.get("status")
        if isinstance(status, int) and not 200 <= status < 300:
            detail = str(state.get("error") or "ElevenLabs rejected the pre-sign-up request")
            raise RuntimeError(f"pre_signup_rejected: HTTP {status}: {detail[:240]}")
        elapsed = time.monotonic() - started
        if elapsed - last_report >= 10:
            waiting_for = (
                "ElevenLabs pre-sign-up (Stripe risk check in progress)"
                if int(state.get("requests") or 0) == 0
                else "verification-email state"
            )
            _emit(
                emit,
                "submit_signup",
                f"waiting for {waiting_for} requests={int(state.get('requests') or 0)} "
                f"status={status or 'pending'}",
            )
            last_report = elapsed
        time.sleep(0.5)
    if int(state.get("requests") or 0) == 0:
        raise RuntimeError(
            "pre_signup_not_emitted: the ElevenLabs form accepted hCaptcha, but the Stripe risk check "
            "did not emit a pre-sign-up request before timeout"
        )
    raise RuntimeError(
        "signup_state_timeout: ElevenLabs did not enter the verification-email state "
        f"after pre-sign-up HTTP {state.get('status') or 'unknown'}"
    )


def _submit_signup(page: Any, config: ElevenLabsConfig, emit: Callable[[str], None]) -> None:
    _accept_terms(page)
    _emit(emit, "fill_signup", "solving hCaptcha and submitting the ElevenLabs sign-up form")
    bridge = _wait_for_hcaptcha_bridge(page)
    _emit(
        emit,
        "await_hcaptcha",
        f"React bridge ready callbacks={int(bridge.get('callbacks') or 0)} widget={str(bridge.get('widgetId') or 'unknown')[:32]}",
    )
    token = _solve_hcaptcha(page, config, emit, invisible=True)
    state = _install_presignup_token_route(page, token, emit)
    _stage_hcaptcha_token(page, token, emit)
    execute_calls = int(bridge.get("executeCalls") or 0)
    _click_signup(page)
    _wait_for_hcaptcha_execute(page, execute_calls)
    _emit(emit, "submit_signup", "hCaptcha accepted by the form; waiting for the Stripe risk check")
    signup_timeout = min(max(config.confirmation_timeout, 60), 120)
    _wait_for_signup_outcome(
        page,
        state,
        timeout=signup_timeout,
        emit=emit,
    )


def _maybe_resend_verification(page: Any) -> None:
    candidates = (
        'button:has-text("Resend")',
        'button:has-text("resend")',
        '[data-testid*="resend"]',
    )
    for selector in candidates:
        locator = page.locator(selector)
        if locator.count() == 0:
            continue
        try:
            button = locator.first
            if button.is_enabled() and button.is_visible():
                button.click()
                return
        except Exception:
            continue


def run_assisted_registration(
    config: ElevenLabsConfig,
    *,
    email: str,
    password: str,
    receiver: Any,
    prompt: Callable[[str], str] = input,
    emit: Callable[[str], None] = print,
) -> BrowserResult:
    return _run_registration(
        config,
        email=email,
        password=password,
        receiver=receiver,
        automated=False,
        prompt=prompt,
        emit=emit,
    )


def run_automated_registration(
    config: ElevenLabsConfig,
    *,
    email: str,
    password: str,
    receiver: Any,
    emit: Callable[[str], None] = print,
) -> BrowserResult:
    return _run_registration(
        config,
        email=email,
        password=password,
        receiver=receiver,
        automated=True,
        emit=emit,
    )


def _run_registration(
    config: ElevenLabsConfig,
    *,
    email: str,
    password: str,
    receiver: Any,
    automated: bool,
    prompt: Callable[[str], str] = input,
    emit: Callable[[str], None] = print,
) -> BrowserResult:
    manager, context, temporary_profile = _new_context(config)
    account_state: dict[str, Any] = {"api_key": "", "subscription": None}
    verification_state: dict[str, Any] = {
        "verified": False,
        "status": None,
        "error": "",
        "requests": 0,
    }
    try:
        page = _page(context)
        page.on("response", lambda response: _capture_account_response(response, account_state))
        page.on("response", lambda response: _capture_verification_response(response, verification_state))
        _emit(emit, "load_signup", "opening a clean ElevenLabs sign-up profile")
        page.goto(SIGNUP_URL, wait_until="domcontentloaded")
        _assert_signup_page(page)
        page.locator(SIGNUP_EMAIL).fill(email)
        page.locator(SIGNUP_PASSWORD).fill(password)
        if automated:
            _submit_signup(page, config, emit)
        else:
            _emit(emit, "await_signup_confirmation", "email and password filled; browser is waiting for user submission")
            prompt(
                "In Chrome, review the linked terms, click Sign up, and complete hCaptcha if shown. "
                "Press Enter here only after ElevenLabs says it sent a verification email: "
            )

        _emit(emit, "wait_email_link", "waiting for the verification email")
        try:
            link = receiver.wait_for_verification_link(
                timeout=config.mail_timeout,
                emit=emit,
                allowed_hosts=config.allowed_verification_hosts,
            )
        except Exception:
            if automated:
                _emit(emit, "wait_email_link", "first poll timed out; requesting one resend")
                _maybe_resend_verification(page)
                time.sleep(60)
                link = receiver.wait_for_verification_link(
                    timeout=config.mail_timeout,
                    emit=emit,
                    allowed_hosts=config.allowed_verification_hosts,
                )
            else:
                raise
        _emit(emit, "got_email_link", f"verification link received: {redacted_link_label(link)}")
        if not automated:
            decision = prompt("Type OPEN to verify this email in the same proxy browser: ").strip().upper()
            if decision != "OPEN":
                raise RuntimeError("email verification was not confirmed")

        _emit(emit, "verify_email", "opening the verification link")
        page.goto(link, wait_until="domcontentloaded")
        _wait_for_email_verified(
            page,
            min(config.confirmation_timeout, 60),
            verification_state=verification_state,
        )
        _emit(emit, "verify_email", "email verification action confirmed")
        page.goto("about:blank", wait_until="load")

        _emit(emit, "load_signin", "opening ElevenLabs sign-in")
        if automated:
            _automated_signin(page, config, email=email, password=password, emit=emit)
        else:
            page.goto(SIGNIN_URL, wait_until="domcontentloaded")
            _wait_for_form(page, (SIGNIN_EMAIL, SIGNIN_PASSWORD, SIGNIN_SUBMIT), "sign-in")
            page.locator(SIGNIN_EMAIL).fill(email)
            page.locator(SIGNIN_PASSWORD).fill(password)
            _emit(emit, "await_signin_confirmation", "credentials filled; browser is waiting for user sign-in")
            prompt("In Chrome, click Sign in. Press Enter here after the page starts signing in: ")
            if not _wait_for_authenticated(page, min(config.confirmation_timeout, 120)):
                raise RuntimeError("timed out waiting for an authenticated ElevenLabs page")
        _complete_onboarding(page, emit)
        _raise_if_account_restricted(page)
        _wait_for_account_snapshot(page, account_state, emit)
        _provision_gateway_api_key(page, account_state, emit)
        _emit(emit, "done", "ElevenLabs authenticated page confirmed")
        if not automated:
            prompt("Registration is complete. Press Enter to close the dedicated Chrome window: ")
        return BrowserResult(
            email=email,
            final_url=page.url,
            authenticated=True,
            api_key=str(account_state.get("api_key") or ""),
            subscription=(
                dict(account_state["subscription"])
                if isinstance(account_state.get("subscription"), dict)
                else None
            ),
        )
    finally:
        _close_context(manager, context, temporary_profile)
