import unittest
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


if __name__ == "__main__":
    unittest.main()
