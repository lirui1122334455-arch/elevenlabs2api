import tempfile
import unittest
from pathlib import Path

from elevenlabs_assisted.mailbox_link import MailContent, extract_verification_link
from elevenlabs_assisted.outlook_mail import (
    claim_outlook_account,
    finish_outlook_account,
    import_outlook_accounts,
    outlook_pool_stats,
    parse_outlook_lines,
)


HOSTS = ("elevenlabs.io", "*.elevenlabs.io")


class OutlookPoolTest(unittest.TestCase):
    def test_parses_four_field_lines(self) -> None:
        rows = parse_outlook_lines(
            "one@outlook.com----pass----client-id----" + ("r" * 24) + "\n"
            "bad-line\n"
            "two@outlook.com----pass----client-id----" + ("s" * 24)
        )
        self.assertEqual([row["email"] for row in rows], ["one@outlook.com", "two@outlook.com"])

    def test_claims_imported_accounts_once(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "outlook.db"
        result = import_outlook_accounts(
            path,
            "one@outlook.com----pass----client-id----" + ("r" * 24),
        )
        self.assertEqual(result["imported"], 1)
        self.assertEqual(outlook_pool_stats(path)["available"], 1)
        claimed = claim_outlook_account(path)
        self.assertEqual(claimed["email"], "one@outlook.com")
        self.assertEqual(outlook_pool_stats(path)["in_use"], 1)
        with self.assertRaisesRegex(RuntimeError, "no available Outlook mailbox"):
            claim_outlook_account(path)
        finish_outlook_account(path, claimed["email"], status="done")
        self.assertEqual(outlook_pool_stats(path)["done"], 1)

    def test_extracts_elevenlabs_link_from_outlook_html(self) -> None:
        content = MailContent(
            "1",
            "Verify your email",
            "",
            ('<a href="https://elevenlabs.io/app/action?mode=verifyEmail&amp;oobCode=outlook-code">Verify</a>',),
        )
        self.assertIn("oobCode=outlook-code", extract_verification_link(content, HOSTS))


if __name__ == "__main__":
    unittest.main()
