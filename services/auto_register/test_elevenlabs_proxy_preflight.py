import unittest

from elevenlabs_assisted.proxy_preflight import (
    normalize_proxy_url,
    preflight_proxy,
    safe_proxy_label,
)


class _Response:
    status_code = 200
    url = "https://elevenlabs.io/app/sign-up"


class ProxyPreflightTest(unittest.TestCase):
    def test_allows_direct_connection(self) -> None:
        self.assertEqual(normalize_proxy_url(""), "")
        self.assertEqual(safe_proxy_label(""), "direct")

    def test_direct_request_omits_proxy_option(self) -> None:
        calls = []

        def request_get(url, **kwargs):
            calls.append((url, kwargs))
            return _Response()

        result = preflight_proxy("", request_get=request_get)
        self.assertEqual(result.proxy_label, "direct")
        self.assertNotIn("proxy", calls[0][1])

    def test_redacts_proxy_credentials(self) -> None:
        self.assertEqual(
            safe_proxy_label("socks5://proxy-user:proxy-pass@proxy.example:1080"),
            "socks5://proxy.example:1080",
        )

    def test_https_request_receives_proxy_and_never_falls_back(self) -> None:
        calls = []

        def request_get(url, **kwargs):
            calls.append((url, kwargs))
            return _Response()

        result = preflight_proxy(
            "socks5://proxy.example:1080",
            request_get=request_get,
        )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1]["proxy"], "socks5h://proxy.example:1080")

    def test_rejects_unsuccessful_elevenlabs_response(self) -> None:
        class Forbidden:
            status_code = 403
            url = "https://elevenlabs.io/app/sign-up"

        with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
            preflight_proxy(
                "socks5://proxy.example:1080",
                request_get=lambda *_args, **_kwargs: Forbidden(),
            )


if __name__ == "__main__":
    unittest.main()
