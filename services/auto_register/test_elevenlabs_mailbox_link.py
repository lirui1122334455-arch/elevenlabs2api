import unittest
from unittest.mock import patch

from elevenlabs_assisted.mailbox_link import (
    MailContent,
    extract_verification_link,
    is_elevenlabs_verification_url,
    redacted_link_label,
)
from protocol_register import CloudTempMailReceiver, YydsMailReceiver


HOSTS = ("elevenlabs.io", "*.elevenlabs.io", "elevenlabs.firebaseapp.com")


class VerificationLinkTest(unittest.TestCase):
    def test_extracts_html_href_and_unescapes_query(self) -> None:
        content = MailContent(
            "message-1",
            "Verify your email",
            "",
            ('<a href="https://elevenlabs.io/app/auth?mode=verifyEmail&amp;oobCode=abc123">Verify</a>',),
        )
        self.assertEqual(
            extract_verification_link(content, HOSTS),
            "https://elevenlabs.io/app/auth?mode=verifyEmail&oobCode=abc123",
        )

    def test_extracts_plain_text_link(self) -> None:
        content = MailContent(
            "message-1",
            "",
            "Open https://auth.elevenlabs.io/action?mode=verifyEmail&verificationCode=code-2.",
            (),
        )
        self.assertIn("verificationCode=code-2", extract_verification_link(content, HOSTS))

    def test_rejects_untrusted_or_incomplete_links(self) -> None:
        self.assertFalse(
            is_elevenlabs_verification_url(
                "https://attacker.example/action?mode=verifyEmail&oobCode=secret",
                HOSTS,
            )
        )
        self.assertFalse(
            is_elevenlabs_verification_url(
                "http://elevenlabs.io/action?mode=verifyEmail&oobCode=secret",
                HOSTS,
            )
        )
        self.assertFalse(
            is_elevenlabs_verification_url("https://elevenlabs.io/action?mode=verifyEmail", HOSTS)
        )

    def test_redacted_label_drops_all_query_values(self) -> None:
        value = redacted_link_label(
            "https://elevenlabs.io/action?mode=verifyEmail&oobCode=do-not-log"
        )
        self.assertEqual(value, "https://elevenlabs.io/action")
        self.assertNotIn("do-not-log", value)


class _Response:
    def __init__(self, payload, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 300

    def raise_for_status(self) -> None:
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class CloudReceiverLinkTest(unittest.TestCase):
    def test_does_not_consume_the_same_message_twice(self) -> None:
        class Session:
            messages = [
                {
                    "id": "old",
                    "html": '<a href="https://elevenlabs.io/action?mode=verifyEmail&amp;oobCode=old-code">Verify</a>',
                }
            ]

            def get(self, *_args, **_kwargs):
                return _Response({"messages": self.messages})

        session = Session()
        receiver = CloudTempMailReceiver(
            "generated@example.com",
            "mail-token",
            {"cloudflare_api_base": "https://mail.example.com", "mail_poll_interval": 0.01},
        )
        with patch("protocol_register._mail_session", return_value=session):
            self.assertIn(
                "oobCode=old-code",
                receiver.wait_for_verification_link(timeout=1, allowed_hosts=HOSTS),
            )
            session.messages.append(
                {
                    "id": "new",
                    "text": "https://elevenlabs.io/action?mode=verifyEmail&oobCode=new-code",
                }
            )
            self.assertIn(
                "oobCode=new-code",
                receiver.wait_for_verification_link(timeout=1, allowed_hosts=HOSTS),
            )


class YydsReceiverLinkTest(unittest.TestCase):
    def test_reads_link_from_long_poll_message(self) -> None:
        class Session:
            def get(self, url, **_kwargs):
                if url.endswith("/messages/next"):
                    return _Response(
                        {
                            "data": {
                                "message": {
                                    "id": "message-1",
                                    "html": '<a href="https://elevenlabs.firebaseapp.com/__/auth/action?mode=verifyEmail&amp;oobCode=yyds-code">Verify</a>',
                                }
                            }
                        }
                    )
                return _Response({}, 404)

        receiver = YydsMailReceiver(
            "generated@example.com",
            "mail-token",
            {"yyds_api_base": "https://mail.example.com/v1", "mail_poll_interval": 0.01},
        )
        with patch("protocol_register._mail_session", return_value=Session()):
            self.assertIn(
                "oobCode=yyds-code",
                receiver.wait_for_verification_link(timeout=1, allowed_hosts=HOSTS),
            )


if __name__ == "__main__":
    unittest.main()
