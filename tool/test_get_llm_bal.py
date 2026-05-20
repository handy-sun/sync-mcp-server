import io
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import get_llm_bal


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return self.body


class BalanceSummaryTests(unittest.TestCase):
    def test_check_balance_preserves_zero_remaining(self):
        body = b'{"remaining": 0, "quota": {"remaining": 10}, "unit": "USD"}'

        with patch("get_llm_bal.urlopen", return_value=FakeResponse(body)):
            result = get_llm_bal.check_balance("https://api.example.com", "key")

        self.assertEqual(result["remaining"], 0)

    def test_sums_numeric_remaining_balances_by_unit(self):
        results = [
            {"valid": True, "remaining": 1.25, "unit": "USD"},
            {"valid": True, "remaining": "2.75", "unit": "USD"},
            {"valid": True, "remaining": 3, "unit": "CNY"},
            {"valid": False, "remaining": 100, "unit": "USD"},
            {"error": "timeout", "remaining": 200, "unit": "USD"},
            {"valid": True, "remaining": None, "unit": "USD"},
        ]

        self.assertEqual(
            get_llm_bal.summarize_totals(results),
            {"USD": "4", "CNY": "3"},
        )

    def test_detects_depleted_balance_from_numeric_string(self):
        results = [{"valid": True, "remaining": "0", "unit": "USD"}]

        self.assertTrue(get_llm_bal.has_invalid_or_depleted_balance(results))

    def test_main_outputs_total_remaining_on_same_line_for_single_key(self):
        result = {"valid": True, "remaining": "7.886", "total": "300", "unit": "USD"}

        with patch("get_llm_bal.check_balance", return_value=result), patch.object(
            sys,
            "argv",
            ["get_llm_bal.py", "-u", "https://api.example.com", "-k", "sk-55a123456789"],
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                get_llm_bal.main()

        self.assertEqual(
            stdout.getvalue(),
            "[sk-55a********] ACTIVE | 7.886 / 300 USD (Remain/Total) | Total Remaining: 7.886 USD\n",
        )

    def test_main_outputs_total_remaining_on_same_line_for_multiple_keys(self):
        results = [
            {"valid": True, "remaining": "1.25", "total": None, "unit": "USD"},
            {"valid": True, "remaining": "2.75", "total": None, "unit": "USD"},
        ]

        with patch("get_llm_bal.check_balance", side_effect=results), patch.object(
            sys,
            "argv",
            [
                "get_llm_bal.py",
                "-u",
                "https://api.example.com",
                "-k",
                "abcdef123456",
                "ghijkl123456",
            ],
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                get_llm_bal.main()

        self.assertEqual(
            stdout.getvalue(),
            "[abcdef******] ACTIVE | 1.250 USD (Remain) | "
            "[ghijkl******] ACTIVE | 2.750 USD (Remain) | "
            "Total Remaining: 4 USD\n",
        )

    def test_check_balance_reads_scalar_quota_as_total(self):
        body = b'{"remaining": "7.886", "quota": 300, "unit": "USD"}'

        with patch("get_llm_bal.urlopen", return_value=FakeResponse(body)):
            result = get_llm_bal.check_balance("https://api.example.com", "key")

        self.assertEqual(result["total"], 300)


if __name__ == "__main__":
    unittest.main()
