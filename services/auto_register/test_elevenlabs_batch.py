import unittest
from datetime import datetime, timedelta, timezone

from elevenlabs_assisted.batch import (
    MAX_BATCH_CONCURRENCY,
    MAX_BATCH_COUNT,
    concurrency_for,
    parse_count,
    unused_exits,
    used_exit_keys,
)
from elevenlabs_assisted.dynamic_proxy import (
    dynamic_proxy_api_url,
    is_dynamic_proxy_api,
    parse_dynamic_proxy_payload,
)


class BatchCountTest(unittest.TestCase):
    def test_defaults_to_one(self) -> None:
        self.assertEqual(parse_count({}), 1)
        self.assertEqual(parse_count(None), 1)
        self.assertEqual(parse_count({"count": ""}), 1)

    def test_accepts_valid_count(self) -> None:
        self.assertEqual(parse_count({"count": 3}), 3)
        self.assertEqual(parse_count({"count": "20"}), MAX_BATCH_COUNT)

    def test_rejects_out_of_range(self) -> None:
        with self.assertRaisesRegex(ValueError, "between"):
            parse_count({"count": 0})
        with self.assertRaisesRegex(ValueError, "between"):
            parse_count({"count": MAX_BATCH_COUNT + 1})
        with self.assertRaisesRegex(ValueError, "integer"):
            parse_count({"count": "many"})

    def test_concurrency_caps_at_three_and_available_exits(self) -> None:
        self.assertEqual(concurrency_for(1, 10), 1)
        self.assertEqual(concurrency_for(8, 10), MAX_BATCH_CONCURRENCY)
        self.assertEqual(concurrency_for(0, 5), 0)

    def test_skips_exits_already_used_for_free_accounts(self) -> None:
        now = datetime(2026, 8, 25, tzinfo=timezone.utc)
        used = used_exit_keys(
            [
                {"status": "active", "exit_label": "direct", "updated_at": now.isoformat()},
                {"status": "failed", "exit_label": "http://1.1.1.1:8080", "updated_at": now.isoformat()},
                {"status": "active", "exit_label": "http://2.2.2.2:8080", "updated_at": (now - timedelta(days=2)).isoformat()},
            ],
            now=now,
        )
        unused = unused_exits(["", "http://1.1.1.1:8080", "http://2.2.2.2:8080"], used)
        self.assertEqual(unused, ["http://1.1.1.1:8080", "http://2.2.2.2:8080"])


class DynamicProxyTest(unittest.TestCase):
    def test_parses_txt_and_json_payloads(self) -> None:
        self.assertEqual(
            parse_dynamic_proxy_payload("105.33.13.23:8080"),
            ["http://105.33.13.23:8080"],
        )
        self.assertEqual(
            parse_dynamic_proxy_payload([{"host": "165.1.2.3", "port": "7098"}]),
            ["http://165.1.2.3:7098"],
        )

    def test_builds_api_url_with_requested_count(self) -> None:
        url = dynamic_proxy_api_url(
            "https://white.1024proxy.com/white/api?region=Rand&num=1&time=10&format=1&type=txt",
            count=3,
        )
        self.assertTrue(is_dynamic_proxy_api(url))
        self.assertIn("num=3", url)
        self.assertNotIn("num=1", url)


if __name__ == "__main__":
    unittest.main()

