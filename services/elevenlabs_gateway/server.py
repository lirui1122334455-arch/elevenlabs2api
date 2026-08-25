#!/usr/bin/env python3
"""Local-only HTTP gateway for ElevenLabs sound effects and GPT Image 2."""

from __future__ import annotations

import hmac
import json
import mimetypes
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from client import ClientConfig, ElevenLabsClient, GatewayError, MediaStore, public_models
from runtime_config import RuntimeConfig, RuntimeConfigStore


MAX_BODY_BYTES = 16 << 20


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


class GatewayServer(ThreadingHTTPServer):
    runtime_store: RuntimeConfigStore
    media_store: MediaStore
    gateway_key: str
    generation_slots: threading.BoundedSemaphore


class Handler(BaseHTTPRequestHandler):
    server_version = "grok2api-elevenlabs-gateway/0.1"

    @property
    def gateway(self) -> GatewayServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[elevenlabs-gateway] {self.address_string()} {fmt % args}", flush=True)

    def _authorized(self) -> bool:
        expected = self.gateway.gateway_key
        if not expected:
            return True
        value = self.headers.get("Authorization", "")
        supplied = value[7:] if value.lower().startswith("bearer ") else ""
        return hmac.compare_digest(supplied, expected)

    def _require_auth(self) -> bool:
        if self._authorized():
            return True
        json_response(
            self,
            401,
            {"error": {"type": "authentication_error", "message": "invalid gateway key"}},
        )
        return False

    def _payload(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise GatewayError(400, "invalid_body", "invalid Content-Length") from exc
        if length <= 0 or length > MAX_BODY_BYTES:
            raise GatewayError(400, "invalid_body", "invalid JSON body size")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GatewayError(400, "invalid_json", "request body must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise GatewayError(400, "invalid_json", "request body must be a JSON object")
        return payload

    def _gateway_error(self, exc: GatewayError) -> None:
        json_response(self, exc.status, exc.payload())

    def _client(self) -> ElevenLabsClient:
        config = self.gateway.runtime_store.get()
        return ElevenLabsClient(
            ClientConfig(
                api_key=config.api_key,
                api_base_url=config.api_base_url,
                proxy_url=config.proxy_url,
                request_timeout=config.request_timeout,
                generation_timeout=config.generation_timeout,
            ),
            self.gateway.media_store,
        )

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in {"/", "/health", "/healthz"}:
            config = self.gateway.runtime_store.get()
            json_response(
                self,
                200,
                {
                    "ok": True,
                    "service": "elevenlabs-gateway",
                    "configured": bool(config.api_key),
                    "proxy_configured": bool(config.proxy_url),
                    "runtime_revision": config.revision,
                },
            )
            return
        if path.startswith("/media/"):
            name = path.removeprefix("/media/")
            target = self.gateway.media_store.resolve(name)
            if target is None:
                json_response(self, 404, {"error": {"message": "media not found"}})
                return
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
            self.end_headers()
            self.wfile.write(data)
            return
        if not self._require_auth():
            return
        try:
            if path == "/v1/runtime-config":
                json_response(self, 200, self.gateway.runtime_store.get().public())
                return
            if path == "/v1/models":
                json_response(self, 200, {"object": "list", "data": public_models()})
                return
            json_response(self, 404, {"error": {"message": "not found"}})
        except GatewayError as exc:
            self._gateway_error(exc)

    def do_POST(self) -> None:  # noqa: N802
        if not self._require_auth():
            return
        path = urlparse(self.path).path
        try:
            payload = self._payload()
            if path == "/v1/runtime-config/preflight":
                json_response(self, 200, runtime_preflight(self.gateway.runtime_store.get()))
                return
            if path in {"/v1/sound-generation", "/v1/audio/sound-effects"}:
                if not self.gateway.generation_slots.acquire(blocking=False):
                    raise GatewayError(429, "gateway_busy", "another generation is already running")
                try:
                    data, content_type, metadata = self._client().generate_sound(payload)
                finally:
                    self.gateway.generation_slots.release()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                for key, value in metadata.items():
                    self.send_header(f"x-elevenlabs-{key}", value)
                self.end_headers()
                self.wfile.write(data)
                return
            if path == "/v1/images/generations":
                if not self.gateway.generation_slots.acquire(blocking=False):
                    raise GatewayError(429, "gateway_busy", "another generation is already running")
                try:
                    result = self._client().generate_image(payload)
                finally:
                    self.gateway.generation_slots.release()
                json_response(self, 200, result)
                return
            json_response(self, 404, {"error": {"message": "not found"}})
        except GatewayError as exc:
            self._gateway_error(exc)

    def do_PUT(self) -> None:  # noqa: N802
        if not self._require_auth():
            return
        path = urlparse(self.path).path
        if path != "/v1/runtime-config":
            json_response(self, 404, {"error": {"message": "not found"}})
            return
        try:
            updated = self.gateway.runtime_store.update(self._payload())
            json_response(self, 200, updated.public())
        except ValueError as exc:
            json_response(
                self,
                400,
                {"error": {"type": "invalid_runtime_config", "message": str(exc)[:500]}},
            )


def runtime_preflight(config: RuntimeConfig) -> dict[str, Any]:
    connection = requests.Session()
    connection.trust_env = False
    if config.proxy_url:
        connection.proxies.update({"http": config.proxy_url, "https": config.proxy_url})
    selected_captcha_key = config.selected_captcha_api_key()
    checks: dict[str, Any] = {
        "connection": {"ok": False, "mode": config.public()["proxy_label"]},
        "api_key": {"configured": bool(config.api_key), "valid": False},
        "captcha": {
            "provider": config.captcha_provider,
            "configured": bool(selected_captcha_key),
            "valid": False,
        },
        "yescaptcha": {"configured": bool(config.yescaptcha_api_key), "valid": False},
        "yyds": {"configured": bool(config.yyds_api_key), "valid": False, "owned_domains": []},
    }
    try:
        response = connection.get(
            "https://elevenlabs.io/app/sign-up",
            timeout=config.request_timeout,
            allow_redirects=True,
        )
        checks["connection"].update(
            {"ok": 200 <= response.status_code < 400, "status": response.status_code}
        )
    except requests.RequestException as exc:
        checks["connection"]["error"] = str(exc)[:300]

    direct = requests.Session()
    direct.trust_env = False
    if config.api_key:
        try:
            response = direct.get(
                config.api_base_url.rstrip("/") + "/v1/user/subscription",
                headers={"xi-api-key": config.api_key, "Accept": "application/json"},
                timeout=config.request_timeout,
            )
            checks["api_key"].update(
                {"valid": response.status_code == 200, "status": response.status_code}
            )
        except requests.RequestException as exc:
            checks["api_key"]["error"] = str(exc)[:300]
    if config.captcha_provider == "yescaptcha" and config.yescaptcha_api_key:
        try:
            response = direct.post(
                config.yescaptcha_endpoint.rstrip("/") + "/getBalance",
                json={"clientKey": config.yescaptcha_api_key},
                timeout=config.request_timeout,
            )
            payload = response.json()
            valid = response.ok and isinstance(payload, dict) and payload.get("errorId", 0) == 0
            checks["yescaptcha"].update({"valid": valid, "status": response.status_code})
            checks["captcha"].update({"valid": valid, "status": response.status_code})
            if valid and payload.get("balance") is not None:
                checks["yescaptcha"]["balance"] = payload.get("balance")
                checks["captcha"]["balance"] = payload.get("balance")
        except (requests.RequestException, ValueError) as exc:
            checks["yescaptcha"]["error"] = str(exc)[:300]
            checks["captcha"]["error"] = str(exc)[:300]
    elif config.captcha_provider == "captcha_gateway":
        # The Gateway documentation exposes no free key-validation endpoint.
        # Avoid creating a billable solve task during a configuration preflight.
        captcha_error = config.captcha_registration_error()
        checks["captcha"].update(
            {
                "valid": not captcha_error,
                "validation": "deferred_until_solve" if not captcha_error else "configuration_error",
                "endpoint": config.captcha_gateway_endpoint,
            }
        )
        if captcha_error:
            checks["captcha"]["error"] = captcha_error
    if config.yyds_api_key:
        try:
            response = direct.get(
                config.yyds_api_base.rstrip("/") + "/domains",
                headers={"X-API-Key": config.yyds_api_key},
                timeout=config.request_timeout,
            )
            payload = response.json()
            items = payload.get("data", []) if isinstance(payload, dict) else []
            owned = [
                str(item.get("domain") or "")
                for item in items
                if isinstance(item, dict) and item.get("isVerified") and not item.get("isPublic")
            ]
            valid = response.ok and isinstance(payload, dict) and payload.get("success") is not False
            checks["yyds"].update(
                {
                    "valid": valid,
                    "status": response.status_code,
                    "domain_count": len(items),
                    "owned_domains": [item for item in owned if item],
                }
            )
        except (requests.RequestException, ValueError) as exc:
            checks["yyds"]["error"] = str(exc)[:300]
    checks["registration_ready"] = bool(
        checks["connection"]["ok"]
        and checks["captcha"]["valid"]
        and checks["yyds"]["valid"]
        and config.mail_domains
    )
    checks["gateway_ready"] = bool(
        checks["connection"]["ok"] and checks["api_key"]["valid"]
    )
    checks["ready"] = bool(checks["registration_ready"] and checks["gateway_ready"])
    return checks


def build_server() -> GatewayServer:
    runtime_store = RuntimeConfigStore(
        os.environ.get("ELEVENLABS_RUNTIME_CONFIG_PATH", "/app/data/runtime-config.json"),
        RuntimeConfig.from_env(),
    )
    public_base_url = os.environ.get(
        "ELEVENLABS_PUBLIC_BASE_URL", "http://127.0.0.1:8092"
    ).strip()
    media_store = MediaStore(
        Path(os.environ.get("ELEVENLABS_MEDIA_DIR", "./data")),
        public_base_url,
        int(os.environ.get("ELEVENLABS_MAX_IMAGE_BYTES", str(32 << 20))),
    )
    host = os.environ.get("ELEVENLABS_GATEWAY_HOST", "0.0.0.0")
    port = int(os.environ.get("ELEVENLABS_GATEWAY_PORT", "8092"))
    server = GatewayServer((host, port), Handler)
    server.runtime_store = runtime_store
    server.media_store = media_store
    server.gateway_key = os.environ.get("ELEVENLABS_GATEWAY_KEY", "").strip()
    concurrent = max(1, min(int(os.environ.get("ELEVENLABS_MAX_CONCURRENT", "1")), 8))
    server.generation_slots = threading.BoundedSemaphore(concurrent)
    return server


def main() -> None:
    server = build_server()
    host, port = server.server_address[:2]
    print(
        f"[elevenlabs-gateway] listening on {host}:{port}; "
        f"configured={bool(server.runtime_store.get().api_key)}; "
        f"proxy={bool(server.runtime_store.get().proxy_url)}",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
