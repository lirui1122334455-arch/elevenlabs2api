#!/usr/bin/env python3
from __future__ import annotations

import hmac
import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import requests

from elevenlabs_assisted.browser_flow import (
    connect_existing_account,
    dry_run_browser,
    run_automated_registration,
)
from elevenlabs_assisted.config import ElevenLabsConfig
from elevenlabs_assisted.credentials import (
    account_id,
    api_key_from_user_payload,
    find_credentials,
    load_credentials,
    public_account,
    save_credentials,
    subscription_snapshot,
    update_credentials,
)
from elevenlabs_assisted.passwords import generate_password
from elevenlabs_assisted.proxy_preflight import preflight_proxy
from protocol_register import create_mailbox
from runtime_config import RuntimeConfig, RuntimeConfigStore


MAX_BODY_BYTES = 64 << 10
STREAM_HEARTBEAT_SECONDS = 15


def credentials_file_path() -> Path:
    return Path(
        os.environ.get(
            "ELEVENLABS_CREDENTIALS_PATH",
            "/app/data/elevenlabs-credentials.json",
        )
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


class ServerSentEventStream:
    def __init__(self, handler: BaseHTTPRequestHandler) -> None:
        self.handler = handler
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._connected = True
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
        handler.send_header("Cache-Control", "no-cache, no-transform")
        handler.send_header("Connection", "close")
        handler.send_header("X-Accel-Buffering", "no")
        handler.end_headers()
        self._heartbeat = threading.Thread(target=self._send_heartbeats, daemon=True)
        self._heartbeat.start()

    def _write(self, payload: bytes) -> bool:
        with self._lock:
            if not self._connected:
                return False
            try:
                self.handler.wfile.write(payload)
                self.handler.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionResetError, OSError):
                self._connected = False
                return False

    def event(self, name: str, payload: Any) -> bool:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return self._write(f"event: {name}\ndata: {body}\n\n".encode("utf-8"))

    def _send_heartbeats(self) -> None:
        while not self._stopped.wait(STREAM_HEARTBEAT_SECONDS):
            if not self._write(b": heartbeat\n\n"):
                return

    def close(self) -> None:
        self._stopped.set()
        self._heartbeat.join(timeout=1)
        self.handler.close_connection = True


def registration_payload(runtime: RuntimeConfig) -> dict[str, Any]:
    timeout = runtime.registration_timeout
    return {
        "provider": "elevenlabs",
        "proxy": {
            "url": runtime.proxy_url,
            "require_proxy": False,
            "preflight_url": "https://elevenlabs.io/app/sign-up",
            "connect_timeout_sec": min(runtime.request_timeout, 120),
        },
        "browser": {
            "headless": True,
            "channel": "",
            "unique_profile": True,
            "profile_dir": "/app/data/register-profile",
            "navigation_timeout_sec": min(max(runtime.request_timeout, 30), 300),
            "user_confirmation_timeout_sec": min(max(timeout, 60), 1800),
        },
        "captcha": {
            "provider": runtime.captcha_provider,
            "api_key": runtime.selected_captcha_api_key(),
            "endpoint": runtime.selected_captcha_endpoint(),
            "timeout_sec": min(max(timeout, 60), 600),
        },
        "mail": {
            "provider": "yyds",
            "yyds_api_base": runtime.yyds_api_base,
            "yyds_api_key": runtime.yyds_api_key,
            "mailDomains": runtime.mail_domains,
            "email_proxy": "same_as_browser",
            "timeout_sec": min(max(timeout, 60), 1800),
            "mail_poll_interval": 2,
            "allowed_verification_hosts": [
                "elevenlabs.io",
                "*.elevenlabs.io",
                "elevenlabs.firebaseapp.com",
            ],
        },
        "account": {
            "email": "auto",
            "password": "auto",
            "save_credentials": True,
            "credentials_file": str(credentials_file_path()),
        },
    }


def load_registration_config(runtime: RuntimeConfig) -> ElevenLabsConfig:
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".json", delete=False
    )
    try:
        json.dump(registration_payload(runtime), handle, ensure_ascii=False)
        handle.close()
        try:
            os.chmod(handle.name, 0o600)
        except OSError:
            pass
        return ElevenLabsConfig.load(handle.name)
    finally:
        try:
            handle.close()
        except Exception:
            pass
        try:
            os.unlink(handle.name)
        except OSError:
            pass


def run_registration_action(
    app: "RegisterServer", action: str, emit: Callable[[str], None]
) -> dict[str, Any]:
    runtime = app.runtime_store.get()
    config = load_registration_config(runtime)
    config.validate_for_automated_run()
    result = preflight_proxy(
        config.proxy_url,
        target_url=config.preflight_url,
        timeout=config.proxy_timeout,
    )
    emit(
        f"[phase:network_preflight] connection={result.proxy_label} "
        f"target={result.target_host} HTTP {result.status_code}"
    )
    if action == "preflight":
        return {
            "ok": True,
            "connection": result.proxy_label,
            "status": result.status_code,
        }
    if action == "dry-run":
        dry_run_browser(config, emit=emit)
        return {"ok": True}

    emit("[phase:create_mailbox] creating one YYDS mailbox")
    email, receiver = create_mailbox(config.mail, emit=emit)
    password = config.password or generate_password()
    if config.save_credentials:
        save_credentials(
            config.credentials_file,
            email=email,
            password=password,
            extra={"authenticated": False, "status": "registering"},
        )
    try:
        browser_result = run_automated_registration(
            config,
            email=email,
            password=password,
            receiver=receiver,
            emit=emit,
        )
    except Exception:
        if config.save_credentials:
            update_credentials(
                config.credentials_file,
                account_id(email),
                {"authenticated": False, "status": "failed"},
            )
        raise
    if config.save_credentials:
        extra: dict[str, Any] = {
            "final_url": browser_result.final_url,
            "authenticated": browser_result.authenticated,
            "status": "active",
        }
        if browser_result.api_key:
            extra["api_key"] = browser_result.api_key
        if browser_result.subscription:
            extra["subscription"] = browser_result.subscription
            extra["quota_updated_at"] = utc_now()
        save_credentials(
            config.credentials_file,
            email=browser_result.email,
            password=password,
            extra=extra,
        )
        if browser_result.api_key:
            app.runtime_store.update({"api_key": browser_result.api_key})
            emit("[phase:configure_gateway] registered account connected to the generation gateway")
    return {
        "ok": True,
        "email": browser_result.email,
        "password": password,
        "authenticated": browser_result.authenticated,
        "final_url": browser_result.final_url,
    }


def list_registration_accounts() -> list[dict[str, Any]]:
    records = load_credentials(credentials_file_path())
    accounts = [public_account(record) for record in records]
    return sorted(accounts, key=lambda item: str(item.get("created_at") or ""), reverse=True)


def refresh_registration_account(app: "RegisterServer", identifier: str) -> dict[str, Any]:
    path = credentials_file_path()
    record = find_credentials(path, identifier)
    if record is None:
        raise KeyError(identifier)
    api_key = str(record.get("api_key") or "").strip()
    if not api_key:
        runtime = app.runtime_store.get()
        config = load_registration_config(runtime)
        browser_result = connect_existing_account(
            config,
            email=str(record.get("email") or ""),
            password=str(record.get("password") or ""),
            emit=lambda message: print(message, flush=True),
        )
        api_key = browser_result.api_key
        updates: dict[str, Any] = {"api_key": api_key, "authenticated": True}
        if browser_result.subscription:
            updates["subscription"] = browser_result.subscription
            updates["quota_updated_at"] = utc_now()
        record = update_credentials(path, identifier, updates)
    runtime = app.runtime_store.get()
    if runtime.api_key != api_key:
        runtime = app.runtime_store.update({"api_key": api_key})
    session = requests.Session()
    session.trust_env = False
    if runtime.proxy_url:
        session.proxies.update({"http": runtime.proxy_url, "https": runtime.proxy_url})
    response = session.get(
        runtime.api_base_url.rstrip("/") + "/v1/user",
        headers={"xi-api-key": api_key, "Accept": "application/json"},
        timeout=runtime.request_timeout,
    )
    if response.status_code != 200:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        detail = ""
        if isinstance(payload, dict):
            value = payload.get("detail") or payload.get("error") or payload.get("message")
            if isinstance(value, str):
                detail = value
            elif isinstance(value, dict):
                detail = str(value.get("message") or value.get("status") or "")
        raise RuntimeError(
            f"ElevenLabs quota refresh returned HTTP {response.status_code}"
            + (f": {detail[:240]}" if detail else "")
        )
    payload = response.json()
    subscription = subscription_snapshot(payload)
    if not subscription:
        raise RuntimeError("ElevenLabs user response did not include subscription data")
    replacement_key = api_key_from_user_payload(payload)
    updated = update_credentials(
        path,
        identifier,
        {
            "api_key": replacement_key or api_key,
            "subscription": subscription,
            "quota_updated_at": utc_now(),
            "status": str(subscription.get("status") or record.get("status") or "active"),
        },
    )
    return public_account(updated)


class RegisterServer(ThreadingHTTPServer):
    runtime_store: RuntimeConfigStore
    service_key: str
    registration_lock: threading.Lock


class Handler(BaseHTTPRequestHandler):
    server_version = "elevenlabs-register/0.1"

    @property
    def app(self) -> RegisterServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[elevenlabs-register] {self.address_string()} {fmt % args}", flush=True)

    def _authorized(self) -> bool:
        if not self.app.service_key:
            return True
        value = self.headers.get("Authorization", "")
        supplied = value[7:] if value.lower().startswith("bearer ") else ""
        return hmac.compare_digest(supplied, self.app.service_key)

    def _require_auth(self) -> bool:
        if self._authorized():
            return True
        json_response(self, 401, {"error": {"message": "invalid registration service key"}})
        return False

    def _consume_body(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError("invalid request body size")
        if length:
            self.rfile.read(length)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/v1/accounts":
            if not self._require_auth():
                return
            json_response(self, 200, {"ok": True, "accounts": list_registration_accounts()})
            return
        if path not in {"/", "/health", "/healthz"}:
            json_response(self, 404, {"error": {"message": "not found"}})
            return
        runtime = self.app.runtime_store.get()
        captcha_error = runtime.captcha_registration_error()
        json_response(
            self,
            200,
            {
                "ok": True,
                "service": "elevenlabs-register",
                "running": self.app.registration_lock.locked(),
                "connection": runtime.public()["proxy_label"],
                "captcha_provider": runtime.captcha_provider,
                "captcha_configured": not captcha_error,
                "mail_configured": bool(runtime.yyds_api_key and runtime.mail_domains),
                "runtime_revision": runtime.revision,
                "error": captcha_error,
            },
        )

    def do_POST(self) -> None:  # noqa: N802
        if not self._require_auth():
            return
        try:
            self._consume_body()
        except ValueError as exc:
            json_response(self, 400, {"error": {"message": str(exc)}})
            return
        path = urlparse(self.path).path
        account_match = re.fullmatch(r"/v1/accounts/([0-9a-f]{20})/refresh", path)
        if account_match:
            try:
                account = refresh_registration_account(self.app, account_match.group(1))
                json_response(self, 200, {"ok": True, "account": account})
            except KeyError:
                json_response(
                    self,
                    404,
                    {"error": {"code": "account_not_found", "message": "ElevenLabs account was not found"}},
                )
            except ValueError as exc:
                json_response(
                    self,
                    409,
                    {"error": {"code": "account_quota_unavailable", "message": str(exc)[:500]}},
                )
            except Exception as exc:  # noqa: BLE001
                json_response(
                    self,
                    502,
                    {"error": {"code": "account_quota_refresh_failed", "message": str(exc)[:500]}},
                )
            return
        actions = {
            "/v1/preflight": ("preflight", False),
            "/v1/preflight/stream": ("preflight", True),
            "/v1/dry-run": ("dry-run", False),
            "/v1/dry-run/stream": ("dry-run", True),
            "/v1/register": ("register", False),
            "/v1/register/stream": ("register", True),
        }
        route = actions.get(path)
        if route is None:
            json_response(self, 404, {"error": {"message": "not found"}})
            return
        if not self.app.registration_lock.acquire(blocking=False):
            json_response(
                self,
                409,
                {
                    "error": {
                        "code": "registration_busy",
                        "message": "a registration task is already running",
                    }
                },
            )
            return
        action, streaming = route
        logs: list[str] = []
        stream: ServerSentEventStream | None = None

        def emit(message: str) -> None:
            value = str(message)[:500]
            logs.append(value)
            if len(logs) > 400:
                del logs[:-400]
            print(value, flush=True)
            if stream is not None:
                stream.event("log", {"message": value})

        try:
            if streaming:
                stream = ServerSentEventStream(self)
            result = run_registration_action(self.app, action, emit)
            if stream is not None:
                stream.event("complete", result)
            else:
                json_response(self, 200, {**result, "logs": logs[-40:]})
        except Exception as exc:  # noqa: BLE001
            emit(f"[phase:failed] {str(exc)[:400]}")
            error = {"code": "registration_failed", "message": str(exc)[:500]}
            if stream is not None:
                stream.event("error", error)
            elif not streaming:
                json_response(
                    self,
                    500,
                    {"ok": False, "error": error, "logs": logs[-40:]},
                )
        finally:
            if stream is not None:
                stream.close()
            self.app.registration_lock.release()


def build_server() -> RegisterServer:
    store = RuntimeConfigStore(
        os.environ.get("ELEVENLABS_RUNTIME_CONFIG_PATH", "/app/data/runtime-config.json"),
        RuntimeConfig.from_env(),
    )
    host = os.environ.get("ELEVENLABS_REGISTER_HOST", "0.0.0.0")
    port = int(os.environ.get("ELEVENLABS_REGISTER_PORT", "8093"))
    server = RegisterServer((host, port), Handler)
    server.runtime_store = store
    server.service_key = os.environ.get("ELEVENLABS_REGISTER_KEY", "").strip()
    server.registration_lock = threading.Lock()
    return server


def main() -> None:
    server = build_server()
    host, port = server.server_address[:2]
    print(f"[elevenlabs-register] listening on {host}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
