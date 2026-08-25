from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from runtime_config import RuntimeConfig, RuntimeConfigStore


class RuntimeConfigTests(unittest.TestCase):
    def test_defaults_to_direct_connection(self) -> None:
        config = RuntimeConfig.from_mapping({})
        self.assertEqual(config.proxy_url, "")
        self.assertEqual(config.captcha_provider, "yescaptcha")
        self.assertEqual(config.public()["proxy_label"], "direct")

    def test_store_redacts_and_preserves_write_only_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RuntimeConfigStore(Path(directory) / "runtime.json")
            updated = store.update(
                {
                    "api_key": "api-secret",
                    "captcha_provider": "captcha_gateway",
                    "yescaptcha_api_key": "captcha-secret",
                    "captcha_gateway_api_key": "gateway-secret",
                    "yyds_api_key": "mail-secret",
                    "mail_domains": "Mail.Example.com, mail.example.com",
                }
            )
            self.assertEqual(updated.mail_domains, "mail.example.com")
            public = updated.public()
            self.assertTrue(public["api_key_configured"])
            self.assertEqual(public["captcha_provider"], "captcha_gateway")
            self.assertTrue(public["captcha_gateway_key_configured"])
            self.assertNotIn("api_key", public)
            self.assertNotIn("captcha_gateway_api_key", public)

            preserved = store.update({"api_key": "", "proxy_url": ""})
            self.assertEqual(preserved.api_key, "api-secret")
            self.assertEqual(preserved.captcha_gateway_api_key, "gateway-secret")
            self.assertEqual(preserved.proxy_url, "")

            cleared = store.update({"clear_api_key": True})
            self.assertEqual(cleared.api_key, "")

            cleared_gateway = store.update({"clear_captcha_gateway_api_key": True})
            self.assertEqual(cleared_gateway.captcha_gateway_api_key, "")

    def test_rejects_invalid_proxy_and_domain(self) -> None:
        with self.assertRaises(ValueError):
            RuntimeConfig.from_mapping({"proxy_url": "127.0.0.1:7890"})
        with self.assertRaises(ValueError):
            RuntimeConfig.from_mapping({"mail_domains": "not a domain"})
        with self.assertRaises(ValueError):
            RuntimeConfig.from_mapping({"captcha_provider": "unknown"})

    def test_captcha_gateway_requires_proxy_for_registration(self) -> None:
        direct = RuntimeConfig.from_mapping(
            {
                "captcha_provider": "captcha_gateway",
                "captcha_gateway_api_key": "gateway-secret",
            }
        )
        self.assertIn("requires a task proxy", direct.captcha_registration_error())

        proxied = RuntimeConfig.from_mapping(
            {
                "captcha_provider": "captcha_gateway",
                "captcha_gateway_api_key": "gateway-secret",
                "proxy_url": "http://proxy.example:8080",
            }
        )
        self.assertEqual(proxied.captcha_registration_error(), "")


if __name__ == "__main__":
    unittest.main()
