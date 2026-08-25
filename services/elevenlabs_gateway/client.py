"""Small ElevenLabs client used by the local development gateway."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urljoin, urlsplit

import requests


SOUND_MODELS = {"eleven_text_to_sound_v2", "eleven_text_to_sound_v3"}
IMAGE_MODEL = "gpt-image-2"
IMAGE_ASPECT_RATIOS = {
    "auto",
    "3:1",
    "21:9",
    "2:1",
    "16:9",
    "3:2",
    "4:3",
    "5:4",
    "1:1",
    "4:5",
    "3:4",
    "2:3",
    "9:16",
    "1:2",
    "1:3",
}
IMAGE_RESOLUTIONS = {"1K", "2K", "4K"}
IMAGE_QUALITIES = {"low", "medium", "high"}
OUTPUT_FORMATS = {
    "mp3_22050_32",
    "mp3_24000_48",
    "mp3_44100_32",
    "mp3_44100_64",
    "mp3_44100_96",
    "mp3_44100_128",
    "mp3_44100_192",
    "pcm_8000",
    "pcm_16000",
    "pcm_22050",
    "pcm_24000",
    "pcm_32000",
    "pcm_44100",
    "pcm_48000",
    "ulaw_8000",
    "alaw_8000",
    "opus_48000_32",
    "opus_48000_64",
    "opus_48000_96",
    "opus_48000_128",
    "opus_48000_192",
}
SUCCESS_STATES = {"completed", "complete", "succeeded", "success", "ready"}
FAILURE_STATES = {"failed", "error", "cancelled", "canceled", "rejected"}


class GatewayError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

    def payload(self) -> dict[str, Any]:
        return {
            "error": {
                "type": "elevenlabs_gateway_error",
                "code": self.code,
                "message": self.message,
            }
        }


@dataclass(frozen=True)
class ClientConfig:
    api_key: str
    api_base_url: str = "https://api.us.elevenlabs.io"
    proxy_url: str = ""
    request_timeout: float = 60.0
    generation_timeout: float = 240.0
    poll_interval: float = 2.0

    @classmethod
    def from_env(cls) -> "ClientConfig":
        return cls(
            api_key=os.environ.get("ELEVENLABS_API_KEY", "").strip(),
            api_base_url=os.environ.get(
                "ELEVENLABS_API_BASE_URL", "https://api.us.elevenlabs.io"
            ).strip(),
            proxy_url=os.environ.get("ELEVENLABS_PROXY_URL", "").strip(),
            request_timeout=float(os.environ.get("ELEVENLABS_REQUEST_TIMEOUT", "60")),
            generation_timeout=float(
                os.environ.get("ELEVENLABS_GENERATION_TIMEOUT", "240")
            ),
            poll_interval=float(os.environ.get("ELEVENLABS_POLL_INTERVAL", "1.5")),
        )

    def validate(self) -> None:
        parsed = urlsplit(self.api_base_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username:
            raise GatewayError(503, "invalid_config", "invalid ElevenLabs API base URL")
        if self.proxy_url:
            proxy = urlsplit(self.proxy_url)
            if proxy.scheme not in {"http", "https", "socks5", "socks5h"} or not proxy.netloc:
                raise GatewayError(503, "invalid_config", "invalid ElevenLabs proxy URL")
        if self.request_timeout <= 0 or self.generation_timeout <= 0 or self.poll_interval < 0:
            raise GatewayError(503, "invalid_config", "invalid ElevenLabs timeout configuration")


class MediaStore:
    def __init__(self, root: Path, public_base_url: str, max_bytes: int = 32 << 20) -> None:
        self.root = root.resolve()
        self.public_base_url = public_base_url.rstrip("/")
        self.max_bytes = max_bytes
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def extension(content_type: str) -> str:
        normalized = content_type.split(";", 1)[0].strip().lower()
        return {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
            "image/gif": ".gif",
            "image/avif": ".avif",
        }.get(normalized, ".img")

    def save(self, data: bytes, content_type: str) -> tuple[str, str]:
        if not data:
            raise GatewayError(502, "empty_media", "ElevenLabs returned an empty image")
        if len(data) > self.max_bytes:
            raise GatewayError(502, "media_too_large", "generated image exceeds the local size limit")
        name = hashlib.sha256(data).hexdigest() + self.extension(content_type)
        target = self.root / name
        if not target.exists():
            temporary = self.root / f".{name}.{os.getpid()}.{threading.get_ident()}.tmp"
            temporary.write_bytes(data)
            os.replace(temporary, target)
        return name, f"{self.public_base_url}/media/{name}"

    def resolve(self, name: str) -> Path | None:
        if not re.fullmatch(r"[a-f0-9]{64}\.(?:png|jpg|webp|gif|avif|img)", name):
            return None
        target = (self.root / name).resolve()
        if target.parent != self.root or not target.is_file():
            return None
        return target


class ElevenLabsClient:
    def __init__(
        self,
        config: ClientConfig,
        media_store: MediaStore,
        *,
        session: requests.Session | Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        config.validate()
        self.config = config
        self.media_store = media_store
        self.session = session or requests.Session()
        self.sleep = sleep
        self.clock = clock
        if hasattr(self.session, "trust_env"):
            self.session.trust_env = False
        if config.proxy_url and hasattr(self.session, "proxies"):
            self.session.proxies.update(
                {"http": config.proxy_url, "https": config.proxy_url}
            )

    @property
    def configured(self) -> bool:
        return bool(self.config.api_key)

    def _require_key(self) -> None:
        if not self.config.api_key:
            raise GatewayError(
                503,
                "api_key_not_configured",
                "ELEVENLABS_API_KEY is not configured",
            )

    @staticmethod
    def _upstream_error(response: Any) -> GatewayError:
        code = "upstream_error"
        message = f"ElevenLabs returned HTTP {response.status_code}"
        try:
            payload = response.json()
            detail = payload.get("detail", payload) if isinstance(payload, dict) else {}
            if isinstance(detail, dict):
                code = str(detail.get("code") or detail.get("type") or detail.get("status") or code)
                message = str(detail.get("message") or detail.get("detail") or message)
            elif isinstance(detail, str):
                message = detail
        except (ValueError, TypeError):
            pass
        combined = f"{code} {message}".lower()
        if code == "detected_unusual_activity" or "unusual activity" in combined:
            return GatewayError(403, "detected_unusual_activity", message[:500])
        if "captcha" in combined:
            return GatewayError(
                409,
                "hcaptcha_required",
                "ElevenLabs requires an interactive hCaptcha challenge",
            )
        status = int(response.status_code)
        if status == 401:
            return GatewayError(401, "upstream_unauthorized", "ElevenLabs rejected the API key")
        if status == 403:
            return GatewayError(403, code, message[:500])
        if status == 429:
            return GatewayError(429, "upstream_rate_limited", message[:500])
        return GatewayError(status if 400 <= status < 600 else 502, code, message[:500])

    def _request(
        self,
        method: str,
        path_or_url: str,
        *,
        body: dict[str, Any] | None = None,
        timeout: float | None = None,
        authenticate: bool = True,
    ) -> Any:
        if authenticate:
            self._require_key()
        url = (
            path_or_url
            if path_or_url.startswith(("http://", "https://"))
            else urljoin(self.config.api_base_url.rstrip("/") + "/", path_or_url.lstrip("/"))
        )
        headers = {"Accept": "application/json", "User-Agent": "grok2api-elevenlabs-gateway/0.1"}
        if authenticate:
            headers["xi-api-key"] = self.config.api_key
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            response = self.session.request(
                method,
                url,
                headers=headers,
                data=None if body is None else json.dumps(body, separators=(",", ":")),
                timeout=(15, timeout or self.config.request_timeout),
            )
        except requests.Timeout as exc:
            raise GatewayError(504, "upstream_timeout", "ElevenLabs request timed out") from exc
        except requests.RequestException as exc:
            raise GatewayError(502, "upstream_unreachable", "ElevenLabs request failed") from exc
        if not 200 <= response.status_code < 300:
            raise self._upstream_error(response)
        return response

    @staticmethod
    def _json(response: Any) -> Any:
        try:
            return response.json()
        except (ValueError, TypeError) as exc:
            raise GatewayError(502, "invalid_upstream_json", "ElevenLabs returned invalid JSON") from exc

    @staticmethod
    def _text(payload: dict[str, Any]) -> str:
        value = payload.get("prompt", payload.get("text", ""))
        if not isinstance(value, str) or not value.strip():
            raise GatewayError(400, "invalid_prompt", "prompt/text must be a non-empty string")
        value = value.strip()
        if len(value) > 10_000:
            raise GatewayError(400, "invalid_prompt", "prompt/text exceeds 10000 characters")
        return value

    @staticmethod
    def _choice(value: Any, allowed: set[str], field: str, default: str) -> str:
        result = str(value or default)
        if result not in allowed:
            raise GatewayError(400, f"invalid_{field}", f"unsupported {field}: {result}")
        return result

    def generate_sound(self, payload: dict[str, Any]) -> tuple[bytes, str, dict[str, str]]:
        text = self._text(payload)
        model = self._choice(
            payload.get("model_id", payload.get("model")),
            SOUND_MODELS,
            "model",
            "eleven_text_to_sound_v2",
        )
        output_format = self._choice(
            payload.get("output_format"), OUTPUT_FORMATS, "output_format", "mp3_44100_128"
        )
        body: dict[str, Any] = {
            "text": text,
            "loop": bool(payload.get("loop", False)),
            "model_id": model,
        }
        if payload.get("duration_seconds") is not None:
            try:
                duration = float(payload["duration_seconds"])
            except (TypeError, ValueError) as exc:
                raise GatewayError(400, "invalid_duration", "duration_seconds must be numeric") from exc
            if duration < 0.5 or duration > 30:
                raise GatewayError(400, "invalid_duration", "duration_seconds must be between 0.5 and 30")
            body["duration_seconds"] = duration
        if payload.get("prompt_influence") is not None:
            try:
                influence = float(payload["prompt_influence"])
            except (TypeError, ValueError) as exc:
                raise GatewayError(400, "invalid_prompt_influence", "prompt_influence must be numeric") from exc
            if influence < 0 or influence > 1:
                raise GatewayError(400, "invalid_prompt_influence", "prompt_influence must be between 0 and 1")
            body["prompt_influence"] = influence
        # This billable POST is intentionally attempted exactly once.
        response = self._request(
            "POST", f"/v1/sound-generation?output_format={output_format}", body=body
        )
        content_type = response.headers.get("Content-Type", "audio/mpeg").split(";", 1)[0]
        if not content_type.startswith("audio/") or not response.content:
            raise GatewayError(502, "invalid_audio", "ElevenLabs did not return audio")
        metadata = {
            key: response.headers[key]
            for key in ("character-cost", "request-id", "x-trace-id")
            if key in response.headers
        }
        return bytes(response.content), content_type, metadata

    @staticmethod
    def _image_references(payload: dict[str, Any]) -> list[dict[str, str]]:
        raw = payload.get("images")
        if raw is None or raw == "":
            return []
        if not isinstance(raw, list) or len(raw) > 10:
            raise GatewayError(400, "invalid_images", "images must be an array with at most 10 items")
        references: list[dict[str, str]] = []
        for item in raw:
            if not isinstance(item, dict) or str(item.get("type") or "") != "inline_base64":
                raise GatewayError(400, "invalid_images", "only inline_base64 image references are supported")
            content = str(item.get("content_base64") or "").strip()
            mime_type = str(item.get("mime_type") or "").strip().lower()
            if mime_type not in {"image/png", "image/jpeg", "image/webp"}:
                raise GatewayError(400, "invalid_images", "reference images must be PNG, JPEG, or WebP")
            try:
                decoded = base64.b64decode(content, validate=True)
            except (ValueError, TypeError) as exc:
                raise GatewayError(400, "invalid_images", "reference image is not valid base64") from exc
            if not decoded or len(decoded) > 8 << 20:
                raise GatewayError(400, "invalid_images", "each reference image must be between 1 byte and 8 MiB")
            references.append(
                {"type": "inline_base64", "content_base64": content, "mime_type": mime_type}
            )
        return references

    def image_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        model = self._choice(payload.get("model"), {IMAGE_MODEL}, "model", IMAGE_MODEL)
        aspect_ratio = self._choice(
            payload.get("aspect_ratio"), IMAGE_ASPECT_RATIOS, "aspect_ratio", "16:9"
        )
        resolution = self._choice(
            payload.get("resolution"), IMAGE_RESOLUTIONS, "resolution", "4K"
        )
        quality = self._choice(
            payload.get("quality"), IMAGE_QUALITIES, "quality", "medium"
        )
        request: dict[str, Any] = {
            "model_id": model,
            "prompt": self._text(payload),
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
            "quality": quality,
        }
        images = self._image_references(payload)
        if images:
            request["images"] = images
        return request

    @staticmethod
    def _flow_failure(payload: dict[str, Any]) -> str:
        error = payload.get("error") or payload.get("failure_reason") or payload.get("message")
        if isinstance(error, dict):
            error = error.get("message") or error.get("detail") or error.get("code")
        return str(error or "image generation failed")[:500]

    def _poll_image_flow(self, initial: Any) -> dict[str, Any]:
        if not isinstance(initial, dict):
            raise GatewayError(502, "invalid_image_flow", "ElevenLabs returned an invalid image flow response")
        flow_id = str(initial.get("id") or "").strip()
        if not flow_id:
            raise GatewayError(502, "invalid_image_flow", "ElevenLabs image flow response did not include an id")
        current = initial
        deadline = self.clock() + self.config.generation_timeout
        while self.clock() < deadline:
            status = str(current.get("status") or "").lower()
            if status in SUCCESS_STATES:
                if not str(current.get("content_url") or "").strip():
                    raise GatewayError(502, "invalid_image_flow", "completed image flow has no content URL")
                return current
            if status in FAILURE_STATES:
                raise GatewayError(502, "image_generation_failed", self._flow_failure(current))
            self.sleep(self.config.poll_interval)
            response = self._request("GET", f"/v1/flows/image/{flow_id}", timeout=30)
            refreshed = self._json(response)
            if not isinstance(refreshed, dict):
                raise GatewayError(502, "invalid_image_flow", "ElevenLabs returned an invalid image flow response")
            current = refreshed
        raise GatewayError(504, "image_generation_timeout", "timed out waiting for ElevenLabs image flow")

    def _download_image(self, url: str) -> tuple[bytes, str]:
        response = self._request(
            "GET", url, timeout=self.config.request_timeout, authenticate=False
        )
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
        if not content_type.startswith("image/"):
            raise GatewayError(502, "invalid_image", "ElevenLabs returned a non-image asset")
        data = bytes(response.content)
        if len(data) > self.media_store.max_bytes:
            raise GatewayError(502, "media_too_large", "generated image exceeds the local size limit")
        return data, content_type

    def generate_image(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = self.image_request(payload)
        response_format = str(payload.get("response_format") or "url")
        if response_format not in {"url", "b64_json"}:
            raise GatewayError(400, "invalid_response_format", "response_format must be url or b64_json")
        # This billable POST is intentionally attempted exactly once.
        response = self._request("POST", "/v1/flows/image", body=request)
        result = self._poll_image_flow(self._json(response))
        media, content_type = self._download_image(str(result["content_url"]))
        item: dict[str, Any] = {"revised_prompt": request["prompt"]}
        if response_format == "b64_json":
            item["b64_json"] = base64.b64encode(media).decode("ascii")
        else:
            _, public_url = self.media_store.save(media, content_type)
            item["url"] = public_url
        return {"created": int(time.time()), "data": [item]}


def public_models() -> list[dict[str, Any]]:
    now = int(time.time())
    return [
        {"id": IMAGE_MODEL, "object": "model", "created": now, "owned_by": "elevenlabs-web"},
        *[
            {"id": model, "object": "model", "created": now, "owned_by": "elevenlabs"}
            for model in sorted(SOUND_MODELS)
        ],
    ]
