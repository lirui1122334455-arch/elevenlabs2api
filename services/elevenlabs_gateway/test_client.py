from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from client import ClientConfig, ElevenLabsClient, GatewayError, MediaStore


class FakeResponse:
    def __init__(
        self,
        status: int = 200,
        *,
        payload: Any = None,
        content: bytes = b"",
        content_type: str = "application/json",
    ) -> None:
        self.status_code = status
        self._payload = payload
        self.content = content
        self.headers = {"Content-Type": content_type}

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []
        self.proxies: dict[str, str] = {}
        self.trust_env = True

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError("unexpected request")
        return self.responses.pop(0)


class ClientTests(unittest.TestCase):
    def client(self, responses: list[FakeResponse]) -> tuple[ElevenLabsClient, FakeSession]:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        session = FakeSession(responses)
        client = ElevenLabsClient(
            ClientConfig(api_key="test-key", poll_interval=0, generation_timeout=5),
            MediaStore(Path(self.temp.name), "http://127.0.0.1:8092"),
            session=session,
            sleep=lambda _: None,
        )
        return client, session

    def test_sound_maps_official_request(self) -> None:
        client, session = self.client(
            [FakeResponse(content=b"audio", content_type="audio/mpeg")]
        )
        data, content_type, _ = client.generate_sound(
            {
                "text": "short documentary impact",
                "duration_seconds": 2,
                "prompt_influence": 0.3,
                "loop": False,
            }
        )
        self.assertEqual(data, b"audio")
        self.assertEqual(content_type, "audio/mpeg")
        call = session.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertIn("/v1/sound-generation?output_format=mp3_44100_128", call["url"])
        body = json.loads(call["data"])
        self.assertEqual(body["model_id"], "eleven_text_to_sound_v2")
        self.assertEqual(call["headers"]["xi-api-key"], "test-key")

    def test_sound_v3_is_forwarded_without_aliasing(self) -> None:
        client, session = self.client(
            [FakeResponse(content=b"audio-v3", content_type="audio/mpeg")]
        )
        data, _, _ = client.generate_sound(
            {"text": "short glass shimmer", "model": "eleven_text_to_sound_v3"}
        )
        self.assertEqual(data, b"audio-v3")
        self.assertEqual(json.loads(session.calls[0]["data"])["model_id"], "eleven_text_to_sound_v3")

    def test_image_generation_polls_and_saves_media(self) -> None:
        client, session = self.client(
            [
                FakeResponse(payload={"id": "flow-1", "status": "pending"}),
                FakeResponse(
                    payload={
                        "id": "flow-1",
                        "status": "completed",
                        "content_url": "https://media.example/generated.png",
                        "content_mime_type": "image/png",
                    }
                ),
                FakeResponse(content=b"png-bytes", content_type="image/png"),
            ]
        )
        result = client.generate_image(
            {
                "model": "gpt-image-2",
                "prompt": "a clean product photo",
                "aspect_ratio": "16:9",
                "resolution": "4K",
                "response_format": "url",
            }
        )
        self.assertEqual(len(result["data"]), 1)
        self.assertTrue(result["data"][0]["url"].startswith("http://127.0.0.1:8092/media/"))
        self.assertEqual([call["method"] for call in session.calls], ["POST", "GET", "GET"])
        create_body = json.loads(session.calls[0]["data"])
        self.assertEqual(create_body["model_id"], "gpt-image-2")
        self.assertEqual(create_body["resolution"], "4K")
        self.assertEqual(
            [call["url"].split("api.us.elevenlabs.io")[-1] for call in session.calls[:2]],
            ["/v1/flows/image", "/v1/flows/image/flow-1"],
        )

    def test_image_to_image_maps_inline_reference(self) -> None:
        client, session = self.client(
            [
                FakeResponse(
                    payload={
                        "id": "flow-2",
                        "status": "completed",
                        "content_url": "https://media.example/generated.webp",
                    }
                ),
                FakeResponse(content=b"webp-bytes", content_type="image/webp"),
            ]
        )
        result = client.generate_image(
            {
                "prompt": "preserve the composition and use studio lighting",
                "response_format": "b64_json",
                "images": [
                    {
                        "type": "inline_base64",
                        "mime_type": "image/png",
                        "content_base64": "aW1hZ2U=",
                    }
                ],
            }
        )
        self.assertIn("b64_json", result["data"][0])
        create_body = json.loads(session.calls[0]["data"])
        self.assertEqual(create_body["images"][0]["type"], "inline_base64")
        self.assertEqual(create_body["images"][0]["mime_type"], "image/png")

    def test_hcaptcha_error_is_explicit(self) -> None:
        client, _ = self.client(
            [
                FakeResponse(
                    status=403,
                    payload={"detail": {"code": "captcha_required", "message": "hCaptcha required"}},
                )
            ]
        )
        with self.assertRaisesRegex(GatewayError, "interactive hCaptcha") as raised:
            client.generate_sound({"text": "impact"})
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(raised.exception.code, "hcaptcha_required")

    def test_unusual_activity_error_is_preserved(self) -> None:
        client, _ = self.client(
            [
                FakeResponse(
                    status=401,
                    payload={
                        "detail": {
                            "status": "detected_unusual_activity",
                            "message": "Unusual activity has been detected on your account.",
                        }
                    },
                )
            ]
        )
        with self.assertRaisesRegex(GatewayError, "Unusual activity") as raised:
            client.generate_sound({"text": "impact"})
        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(raised.exception.code, "detected_unusual_activity")

    def test_rejects_unsupported_image_parameters_before_network(self) -> None:
        client, session = self.client([])
        with self.assertRaisesRegex(GatewayError, "unsupported resolution"):
            client.generate_image({"model": "gpt-image-2", "prompt": "test", "resolution": "8K"})
        with self.assertRaisesRegex(GatewayError, "only inline_base64"):
            client.generate_image(
                {
                    "model": "gpt-image-2",
                    "prompt": "test",
                    "images": [{"type": "asset", "asset_id": "asset-1"}],
                }
            )
        with self.assertRaisesRegex(GatewayError, "unsupported model"):
            client.generate_sound({"text": "impact", "model": "eleven_text_to_sound_v4"})
        self.assertEqual(session.calls, [])


if __name__ == "__main__":
    unittest.main()
