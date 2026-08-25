import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import elevenlabs_assisted.browser_flow as browser_flow
from elevenlabs_assisted.captcha import captcha_proxy_fields, solve_hcaptcha_token
from elevenlabs_assisted.credentials import (
    account_id,
    load_credentials,
    public_account,
    save_credentials,
    subscription_snapshot,
)
from elevenlabs_assisted.browser_flow import (
    HCAPTCHA_INIT_SCRIPT,
    _email_verified,
    _install_presignup_token_route,
    _raise_if_signup_failed,
    _request_json_payload,
    _sitekey_from_url,
    _submit_signup,
    _submission_error_message,
    _verification_required,
)


class _FakeLocator:
    def __init__(self, visible: bool = False, text: str = "") -> None:
        self._visible = visible
        self._text = text

    def count(self) -> int:
        return 1 if self._visible or self._text else 0

    @property
    def first(self) -> "_FakeLocator":
        return self

    def is_visible(self) -> bool:
        return self._visible

    def inner_text(self, timeout: int = 1000) -> str:
        return self._text


class _FakePage:
    def __init__(self, url: str, body: str = "", authed: bool = False) -> None:
        self.url = url
        self.body = body
        self.authed = authed

    def locator(self, selector: str) -> _FakeLocator:
        if "authenticated-root" in selector:
            return _FakeLocator(self.authed)
        if selector == "body":
            return _FakeLocator(text=self.body)
        return _FakeLocator()


class _FakeSigninPage:
    def __init__(self) -> None:
        self.url = "https://elevenlabs.io/app/sign-in"
        self.gotos: list[str] = []
        self.listeners: list[tuple[str, object]] = []

    def on(self, event: str, callback: object) -> None:
        self.listeners.append((event, callback))

    def goto(self, url: str, **_kwargs: object) -> None:
        self.url = url
        self.gotos.append(url)


class CaptchaProxyTest(unittest.TestCase):
    def test_maps_socks5h_to_solver_fields_without_credentials(self) -> None:
        fields = captcha_proxy_fields("socks5h://127.0.0.1:1080")
        self.assertEqual(fields["proxyType"], "socks5")
        self.assertEqual(fields["proxyAddress"], "127.0.0.1")
        self.assertEqual(fields["proxyPort"], 1080)
        self.assertNotIn("proxyPassword", fields)

    def test_keeps_http_proxy_login(self) -> None:
        fields = captcha_proxy_fields("http://user:pass@proxy.example:8080")
        self.assertEqual(fields["proxyType"], "http")
        self.assertEqual(fields["proxyLogin"], "user")
        self.assertEqual(fields["proxyPassword"], "pass")

    def test_reads_sitekey_from_widget_url(self) -> None:
        self.assertEqual(
            _sitekey_from_url("https://hcaptcha.com/captcha?sitekey=abc-123&size=invisible"),
            "abc-123",
        )

    def test_reads_sitekey_from_challenge_fragment(self) -> None:
        self.assertEqual(
            _sitekey_from_url(
                "https://newassets.hcaptcha.com/captcha/v1/hcaptcha.html"
                "#frame=challenge&host=elevenlabs.io&sitekey=fragment-key&size=invisible"
            ),
            "fragment-key",
        )

    def test_solve_hcaptcha_token_forwards_proxy_and_hides_key_from_logs(self) -> None:
        created = []

        class Solver:
            def __init__(self, api_key, **kwargs) -> None:
                self.api_key = api_key
                self.kwargs = kwargs
                self.calls = []
                created.append(self)

            def solve_hcaptcha(self, website_url, website_key, **kwargs):
                self.calls.append((website_url, website_key, kwargs))
                return "token-value"

        with patch("elevenlabs_assisted.captcha._solver_class", return_value=Solver):
            token = solve_hcaptcha_token(
                api_key="secret-key",
                website_url="https://elevenlabs.io/app/sign-up",
                website_key="site-key",
                proxy_url="socks5://127.0.0.1:1080",
                invisible=True,
            )
        self.assertEqual(token, "token-value")
        self.assertEqual(created[0].calls[0][2]["proxy"]["proxyType"], "socks5")
        self.assertNotIn("secret-key", str(created[0].calls[0]))

    def test_captcha_gateway_uses_bearer_sync_api_and_returns_token(self) -> None:
        calls = []
        logs = []

        class Response:
            ok = True
            status_code = 200

            @staticmethod
            def json():
                return {
                    "errorId": 0,
                    "status": "ready",
                    "solution": {"token": "gateway-token"},
                }

        class Session:
            trust_env = True

            def post(self, url, **kwargs):
                calls.append((url, kwargs))
                return Response()

        session = Session()
        with patch("elevenlabs_assisted.captcha.requests.Session", return_value=session):
            token = solve_hcaptcha_token(
                provider="captcha_gateway",
                api_key="gateway-secret",
                endpoint="https://sub.aixiangshu.com",
                website_url="https://elevenlabs.io/app/sign-up",
                website_key="site-key",
                proxy_url="http://user:pass@proxy.example:8080",
                user_agent="test-agent",
                rqdata="dynamic-data",
                emit=logs.append,
            )

        self.assertEqual(token, "gateway-token")
        self.assertFalse(session.trust_env)
        self.assertEqual(calls[0][0], "https://sub.aixiangshu.com/captcha/solve")
        self.assertEqual(calls[0][1]["headers"]["Authorization"], "Bearer gateway-secret")
        self.assertNotIn("clientKey", calls[0][1]["json"])
        self.assertEqual(calls[0][1]["json"]["type"], "hcaptcha")
        self.assertEqual(calls[0][1]["json"]["websiteKey"], "site-key")
        self.assertEqual(
            calls[0][1]["json"]["proxy"],
            "http://user:pass@proxy.example:8080",
        )
        self.assertNotIn("gateway-secret", " ".join(logs))

    def test_captcha_gateway_error_does_not_expose_key(self) -> None:
        class Response:
            ok = False
            status_code = 400

            @staticmethod
            def json():
                return {
                    "errorId": 1,
                    "errorCode": "BAD_REQUEST",
                    "errorDescription": (
                        "request requires proxy http://user:pass@proxy.example:8080 "
                        "for gateway-secret"
                    ),
                }

        class Session:
            trust_env = True

            @staticmethod
            def post(_url, **_kwargs):
                return Response()

        with patch("elevenlabs_assisted.captcha.requests.Session", return_value=Session()):
            with self.assertRaisesRegex(RuntimeError, "request requires proxy") as caught:
                solve_hcaptcha_token(
                    provider="captcha_gateway",
                    api_key="gateway-secret",
                    website_url="https://elevenlabs.io/app/sign-up",
                    website_key="site-key",
                    proxy_url="http://user:pass@proxy.example:8080",
                )
        self.assertNotIn("gateway-secret", str(caught.exception))
        self.assertNotIn("user:pass", str(caught.exception))

    def test_bridge_returns_the_official_async_execute_shape(self) -> None:
        self.assertIn("Promise.resolve({ response: token, key })", HCAPTCHA_INIT_SCRIPT)
        self.assertIn("__elevenlabsSeedHcaptchaToken", HCAPTCHA_INIT_SCRIPT)
        self.assertIn("__elevenlabsHcaptchaCallbacks", HCAPTCHA_INIT_SCRIPT)
        async_branch = HCAPTCHA_INIT_SCRIPT.index("if (options && options.async === true)")
        callback_branch = HCAPTCHA_INIT_SCRIPT.index("invokeCallback(widgetId, token, key, true)", async_branch)
        self.assertLess(async_branch, callback_branch)

    def test_presignup_route_replaces_a_stale_token_and_records_response(self) -> None:
        class Request:
            url = "https://api.us.elevenlabs.io/v1/user/pre-sign-up"
            method = "POST"
            headers = {"content-type": "application/json"}

            @staticmethod
            def post_data_json():
                return {"email": "one@example.com", "recaptcha_token": "stale-token"}

        class Response:
            status = 200

            @staticmethod
            def json():
                return {"ok": True}

        class Route:
            request = Request()

            def __init__(self) -> None:
                self.fetch_args = {}
                self.fulfilled = False

            def fetch(self, **kwargs):
                self.fetch_args = kwargs
                return Response()

            def fulfill(self, **_kwargs) -> None:
                self.fulfilled = True

        class Page:
            def __init__(self) -> None:
                self.handler = None

            def unroute(self, _pattern: str) -> None:
                return

            def route(self, _pattern: str, handler) -> None:
                self.handler = handler

        page = Page()
        logs = []
        state = _install_presignup_token_route(page, "fresh-token", logs.append)
        route = Route()
        page.handler(route)
        payload = json.loads(route.fetch_args["post_data"])
        self.assertEqual(payload["recaptcha_token"], "fresh-token")
        self.assertTrue(route.fulfilled)
        self.assertEqual(state["requests"], 1)
        self.assertEqual(state["status"], 200)

    def test_reads_playwright_request_json_property(self) -> None:
        class Request:
            post_data_json = {"email": "property@example.com", "recaptcha_token": "old"}
            post_data = '{"email":"fallback@example.com"}'

        self.assertEqual(
            _request_json_payload(Request()),
            {"email": "property@example.com", "recaptcha_token": "old"},
        )

    def test_reads_nested_submission_error(self) -> None:
        self.assertEqual(
            _submission_error_message({"detail": {"status": "captcha_invalid", "message": "try again"}}),
            "captcha_invalid: try again",
        )

    def test_signup_solves_once_while_stripe_risk_check_runs(self) -> None:
        logs = []
        config = SimpleNamespace(confirmation_timeout=600)
        with (
            patch("elevenlabs_assisted.browser_flow._accept_terms"),
            patch(
                "elevenlabs_assisted.browser_flow._wait_for_hcaptcha_bridge",
                return_value={"callbacks": 2, "widgetId": "widget", "executeCalls": 0},
            ),
            patch(
                "elevenlabs_assisted.browser_flow._solve_hcaptcha",
                return_value="token",
            ) as solve,
            patch(
                "elevenlabs_assisted.browser_flow._install_presignup_token_route",
                return_value={"requests": 0, "status": None, "error": ""},
            ),
            patch("elevenlabs_assisted.browser_flow._stage_hcaptcha_token"),
            patch("elevenlabs_assisted.browser_flow._click_signup"),
            patch("elevenlabs_assisted.browser_flow._wait_for_hcaptcha_execute"),
            patch("elevenlabs_assisted.browser_flow._wait_for_signup_outcome") as outcome,
        ):
            _submit_signup(object(), config, logs.append)

        solve.assert_called_once()
        self.assertTrue(solve.call_args.kwargs["invisible"])
        self.assertEqual(outcome.call_args.kwargs["timeout"], 120)
        self.assertTrue(any("Stripe risk check" in line for line in logs))


class CredentialsTest(unittest.TestCase):
    def test_appends_records_without_logging_password(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "creds.json"
        save_credentials(path, email="one@example.com", password="Aa9!secret", extra={"final_url": "https://elevenlabs.io/app/home"})
        save_credentials(path, email="two@example.com", password="Aa9!other")
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(payload), 2)
        self.assertEqual(payload[0]["email"], "one@example.com")
        self.assertEqual(payload[0]["password"], "Aa9!secret")
        self.assertEqual(payload[1]["email"], "two@example.com")

    def test_upserts_by_email_and_exposes_quota_without_api_key(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "creds.json"
        save_credentials(path, email="one@example.com", password="old")
        save_credentials(
            path,
            email="ONE@example.com",
            password="new",
            extra={
                "api_key": "secret-api-key",
                "authenticated": True,
                "subscription": {
                    "tier": "free",
                    "status": "active",
                    "character_count": 120,
                    "character_limit": 1000,
                    "next_character_count_reset_unix": 1_800_000_000,
                },
            },
        )
        records = load_credentials(path)
        self.assertEqual(len(records), 1)
        account = public_account(records[0])
        self.assertEqual(account["id"], account_id("one@example.com"))
        self.assertEqual(account["password"], "new")
        self.assertEqual(account["quota_remaining"], 880)
        self.assertNotIn("api_key", account)

    def test_normalizes_subscription_from_user_payload(self) -> None:
        snapshot = subscription_snapshot(
            {"xi_api_key": "hidden", "subscription": {"tier": "starter", "character_count": 5, "character_limit": 10}}
        )
        self.assertEqual(snapshot, {"tier": "starter", "character_count": 5, "character_limit": 10})


class SignupStateTest(unittest.TestCase):
    def test_detects_verification_required_from_copy(self) -> None:
        page = _FakePage("https://elevenlabs.io/app/sign-up", "we have sent a verification email")
        self.assertTrue(_verification_required(page))

    def test_detects_email_in_use(self) -> None:
        page = _FakePage("https://elevenlabs.io/app/sign-up", "This email is already in use")
        with self.assertRaisesRegex(RuntimeError, "email_in_use"):
            _raise_if_signup_failed(page)

    def test_verified_state_from_signin_url(self) -> None:
        page = _FakePage("https://elevenlabs.io/app/sign-in")
        self.assertTrue(_email_verified(page))

    def test_automated_signin_reloads_after_a_silent_first_attempt(self) -> None:
        page = _FakeSigninPage()
        logs: list[str] = []
        with (
            patch.object(browser_flow, "_fill_and_submit_signin") as submit,
            patch.object(browser_flow, "_wait_for_authenticated", side_effect=[False, True]),
            patch.object(browser_flow, "_signin_bridge_status", return_value={"patched": False, "callbacks": 0}),
        ):
            browser_flow._automated_signin(
                page,
                SimpleNamespace(),
                email="one@example.com",
                password="Password1!",
                emit=logs.append,
            )
        self.assertEqual(submit.call_count, 2)
        self.assertEqual(page.gotos, [browser_flow.SIGNIN_URL, browser_flow.SIGNIN_URL])
        self.assertTrue(any("reloading before retry" in line for line in logs))


if __name__ == "__main__":
    unittest.main()
