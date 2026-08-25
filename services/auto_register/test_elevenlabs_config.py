import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from elevenlabs_assisted.config import ElevenLabsConfig


class ConfigTest(unittest.TestCase):
    def _write(self, payload) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "config.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_loads_proxy_and_resolves_isolated_profile(self) -> None:
        path = self._write(
            {
                "provider": "elevenlabs",
                "proxy": {"url": "socks5://proxy.example:1080", "require_proxy": True},
                "browser": {"headless": False, "profile_dir": "./profile"},
                "mail": {
                    "provider": "yyds",
                    "yyds_api_key": "test-only-key",
                    "email_proxy": "same_as_browser",
                    "allowed_verification_hosts": ["elevenlabs.io", "*.elevenlabs.io"],
                },
                "account": {"email": "auto", "password": "auto"},
            }
        )
        config = ElevenLabsConfig.load(path)
        self.assertEqual(config.proxy_url, "socks5://proxy.example:1080")
        self.assertEqual(config.mail["email_proxy"], config.proxy_url)
        self.assertEqual(config.mail["email_provider"], "yyds")
        self.assertEqual(config.profile_dir, path.parent / "profile")
        self.assertEqual(config.allowed_verification_hosts, ("elevenlabs.io", "*.elevenlabs.io"))
        config.validate_for_run()
        self.assertTrue(config.unique_profile)
        with self.assertRaisesRegex(ValueError, "YesCaptcha"):
            config.validate_for_automated_run()

    def test_allows_direct_mode(self) -> None:
        config = ElevenLabsConfig.load(
            self._write(
                {
                    "proxy": {"url": "", "require_proxy": False},
                    "browser": {"headless": False},
                    "mail": {"email_proxy": "same_as_browser"},
                }
            )
        )
        self.assertEqual(config.proxy_url, "")
        self.assertEqual(config.mail["email_proxy"], "direct")

    def test_requires_url_when_proxy_is_mandatory(self) -> None:
        with self.assertRaisesRegex(ValueError, "proxy.url is required"):
            ElevenLabsConfig.load(
                self._write({"proxy": {"url": "", "require_proxy": True}})
            )

    def test_loads_headless_bundled_browser_mode(self) -> None:
        config = ElevenLabsConfig.load(
            self._write(
                {
                    "proxy": {"url": ""},
                    "browser": {"headless": True, "channel": ""},
                }
            )
        )
        self.assertTrue(config.headless)
        self.assertEqual(config.browser_channel, "")

    def test_run_rejects_placeholder_mail_credentials(self) -> None:
        path = self._write(
            {
                "proxy": {"url": "socks5://proxy.example:1080"},
                "mail": {
                    "provider": "yyds",
                    "yyds_api_key": "replace-with-local-key",
                    "mailDomains": "replace-with-owned-domain",
                },
                "account": {"email": "auto"},
            }
        )
        with self.assertRaisesRegex(ValueError, "YYDS"):
            ElevenLabsConfig.load(path).validate_for_run()

    def test_expands_mail_secrets_from_environment(self) -> None:
        path = self._write(
            {
                "proxy": {"url": "socks5://proxy.example:1080"},
                "mail": {
                    "provider": "yyds",
                    "yyds_api_key": "${YYDS_API_KEY}",
                    "mailDomains": "${ELEVENLABS_MAIL_DOMAINS}",
                },
            }
        )
        with patch.dict(
            "os.environ",
            {"YYDS_API_KEY": "local-secret", "ELEVENLABS_MAIL_DOMAINS": "mail.example.com"},
        ):
            config = ElevenLabsConfig.load(path)
        self.assertEqual(config.mail["yyds_api_key"], "local-secret")
        self.assertEqual(config.mail["mailDomains"], "mail.example.com")
        config.validate_for_run()

    def test_expands_captcha_key_for_automated_run(self) -> None:
        path = self._write(
            {
                "proxy": {"url": "socks5://proxy.example:1080"},
                "mail": {
                    "provider": "yyds",
                    "yyds_api_key": "test-only-key",
                    "mailDomains": "mail.example.com",
                },
                "captcha": {"api_key": "${YESCAPTCHA_API_KEY}"},
                "account": {"email": "auto", "save_credentials": True},
            }
        )
        with patch.dict("os.environ", {"YESCAPTCHA_API_KEY": "captcha-secret"}):
            config = ElevenLabsConfig.load(path)
        self.assertEqual(config.captcha_api_key, "captcha-secret")
        self.assertTrue(config.save_credentials)
        self.assertEqual(config.credentials_file.name, "elevenlabs-credentials.json")
        config.validate_for_automated_run()

    def test_loads_captcha_gateway_provider_from_environment(self) -> None:
        path = self._write(
            {
                "proxy": {"url": "http://proxy.example:8080"},
                "mail": {
                    "provider": "yyds",
                    "yyds_api_key": "test-only-key",
                    "mailDomains": "mail.example.com",
                },
                "captcha": {
                    "provider": "captcha_gateway",
                    "api_key": "${CAPTCHA_GATEWAY_API_KEY}",
                },
            }
        )
        with patch.dict("os.environ", {"CAPTCHA_GATEWAY_API_KEY": "gateway-secret"}):
            config = ElevenLabsConfig.load(path)
        self.assertEqual(config.captcha_provider, "captcha_gateway")
        self.assertEqual(config.captcha_api_key, "gateway-secret")
        self.assertEqual(config.captcha_endpoint, "https://sub.aixiangshu.com")
        config.validate_for_automated_run()

    def test_rejects_direct_captcha_gateway_before_creating_mailbox(self) -> None:
        path = self._write(
            {
                "proxy": {"url": ""},
                "mail": {
                    "provider": "yyds",
                    "yyds_api_key": "test-only-key",
                    "mailDomains": "mail.example.com",
                },
                "captcha": {
                    "provider": "captcha_gateway",
                    "api_key": "gateway-secret",
                },
            }
        )
        with self.assertRaisesRegex(ValueError, "requires proxy.url"):
            ElevenLabsConfig.load(path).validate_for_automated_run()


if __name__ == "__main__":
    unittest.main()
