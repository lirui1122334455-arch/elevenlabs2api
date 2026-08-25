import unittest
from unittest.mock import patch

from engine.xconsole_client.client import XConsoleAuthClient
from engine.xconsole_client.fingerprint import FingerprintTransport


class SignupPageScraperTest(unittest.TestCase):
    def test_reads_action_id_from_chunk_url_with_deployment_query(self) -> None:
        action_id = "7f" + "a" * 40
        html = (
            '<script src="/_next/static/chunks/signup.js?dpl=current-build"></script>'
        )
        chunk = (
            f'createUserAndSessionRequest emailValidationCode "{action_id}"'
        ).encode()
        client = XConsoleAuthClient.__new__(XConsoleAuthClient)
        client.debug = False
        client._request = lambda *_args, **_kwargs: (200, {}, [], chunk)
        client._base_headers = lambda: {}

        self.assertEqual(client._scrape_action_id(html), action_id)

    def test_reports_missing_script_chunks_without_executor_error(self) -> None:
        client = XConsoleAuthClient.__new__(XConsoleAuthClient)
        client.debug = False

        with self.assertRaisesRegex(RuntimeError, "Next.js JavaScript chunks"):
            client._scrape_action_id("<html></html>")


class FingerprintTransportTest(unittest.TestCase):
    def test_does_not_inherit_process_proxy_environment(self) -> None:
        class Session:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs
                self.headers = {}

        session = Session()
        with patch(
            "engine.xconsole_client.fingerprint.cc_requests.Session",
            return_value=session,
        ) as session_factory:
            FingerprintTransport()

        self.assertIs(session_factory.call_args.kwargs["trust_env"], False)


if __name__ == "__main__":
    unittest.main()
