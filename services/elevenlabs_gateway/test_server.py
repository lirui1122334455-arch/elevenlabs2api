from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from client import GatewayError
from server import AccountCandidate, load_account_candidates, run_with_account_failover


class AccountFailoverTests(unittest.TestCase):
    def test_active_key_is_first_and_keys_are_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.json"
            path.write_text(
                json.dumps(
                    [
                        {"id": "account-one", "api_key": "key-one"},
                        {"id": "account-two", "api_key": "key-two"},
                        {"id": "duplicate", "api_key": "key-one"},
                    ]
                ),
                encoding="utf-8",
            )
            candidates = load_account_candidates(path, "key-two")

        self.assertEqual(
            [(item.identifier, item.api_key) for item in candidates],
            [("account-two", "key-two"), ("account-one", "key-one")],
        )

    def test_retries_safe_account_rejection(self) -> None:
        candidates = [
            AccountCandidate("one", "key-one"),
            AccountCandidate("two", "key-two"),
        ]
        attempted: list[str] = []

        def operation(candidate: AccountCandidate) -> str:
            attempted.append(candidate.identifier)
            if candidate.identifier == "one":
                raise GatewayError(
                    402,
                    "paid_plan_required",
                    "Pro plan required",
                    retry_safe=True,
                )
            return "generated"

        result, selected, attempts = run_with_account_failover(candidates, operation)

        self.assertEqual(result, "generated")
        self.assertEqual(selected.identifier, "two")
        self.assertEqual(attempts, 2)
        self.assertEqual(attempted, ["one", "two"])

    def test_aggregates_when_every_account_is_rejected(self) -> None:
        candidates = [
            AccountCandidate("one", "key-one"),
            AccountCandidate("two", "key-two"),
        ]

        def operation(_: AccountCandidate) -> str:
            raise GatewayError(
                402,
                "paid_plan_required",
                "This endpoint requires a Pro plan or above.",
                retry_safe=True,
            )

        with self.assertRaises(GatewayError) as raised:
            run_with_account_failover(candidates, operation)

        self.assertEqual(raised.exception.status, 402)
        self.assertEqual(raised.exception.code, "account_pool_exhausted")
        self.assertIn("All 2 configured", raised.exception.message)
        self.assertIn("paid_plan_required x2", raised.exception.message)

    def test_does_not_retry_unsafe_failure(self) -> None:
        candidates = [
            AccountCandidate("one", "key-one"),
            AccountCandidate("two", "key-two"),
        ]
        attempts = 0

        def operation(_: AccountCandidate) -> str:
            nonlocal attempts
            attempts += 1
            raise GatewayError(504, "upstream_timeout", "request timed out")

        with self.assertRaises(GatewayError) as raised:
            run_with_account_failover(candidates, operation)

        self.assertEqual(raised.exception.code, "upstream_timeout")
        self.assertEqual(attempts, 1)

    def test_preserves_unsafe_failure_after_one_safe_retry(self) -> None:
        candidates = [
            AccountCandidate("one", "key-one"),
            AccountCandidate("two", "key-two"),
        ]

        def operation(candidate: AccountCandidate) -> str:
            if candidate.identifier == "one":
                raise GatewayError(
                    402,
                    "paid_plan_required",
                    "Pro plan required",
                    retry_safe=True,
                )
            raise GatewayError(504, "upstream_timeout", "request timed out")

        with self.assertRaises(GatewayError) as raised:
            run_with_account_failover(candidates, operation)

        self.assertEqual(raised.exception.code, "upstream_timeout")


if __name__ == "__main__":
    unittest.main()
