#!/usr/bin/env python3
"""Check LLM API key remaining balance via /v1/usage endpoint."""

import argparse
import json
import os
import sys
from decimal import Decimal, InvalidOperation
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


def check_balance(base_url: str, api_key: str) -> dict:
    url = f"{base_url.rstrip('/')}/v1/usage"
    req = Request(url, headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except HTTPError as e:
        body = e.read().decode(errors="replace")
        return {"valid": False, "error": f"HTTP {e.code}: {body[:200]}"}
    except URLError as e:
        return {"valid": False, "error": str(e.reason)}

    remaining = first_present(data, "remaining", "quota.remaining", "balance")
    total = first_present(data, "total", "quota.total", "limit")
    unit = first_present(data, "unit", "quota.unit") or "USD"
    is_valid = data.get("is_active", data.get("isValid", True))

    return {"valid": is_valid, "remaining": remaining, "total": total, "unit": unit, "raw": data}


def first_present(d: dict, *paths: str):
    """Return the first non-None value for top-level or dot-separated paths."""
    for path in paths:
        value = deep_get(d, path) if "." in path else d.get(path)
        if value is not None:
            return value
    return None


def deep_get(d: dict, path: str):
    """Get nested dict value by dot-separated path."""
    keys = path.split(".")
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def mask_key(key: str, show: int = 6) -> str:
    if len(key) <= show:
        return key
    return key[:show] + "*" * min(len(key) - show, 8)


def to_decimal(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite():
        return None
    return amount


def format_decimal(amount: Decimal) -> str:
    if amount == amount.to_integral_value():
        return str(amount.quantize(Decimal("1")))
    return format(amount.normalize().quantize(Decimal("0.001")), "f")


def summarize_totals(results):
    totals = {}
    for r in results:
        if r.get("error") or not r.get("valid", True):
            continue
        amount = to_decimal(r.get("remaining"))
        if amount is None:
            continue
        unit = r.get("unit") or "USD"
        totals[unit] = totals.get(unit, Decimal("0")) + amount
    return {unit: format_decimal(amount) for unit, amount in totals.items()}


def format_totals(totals):
    if not totals:
        return "N/A"
    return ", ".join(f"{amount} {unit}" for unit, amount in totals.items())


def has_invalid_or_depleted_balance(results):
    for r in results:
        if not r.get("valid"):
            return True
        amount = to_decimal(r.get("remaining"))
        if amount is not None and amount <= 0:
            return True
    return False


def main():
    parser = argparse.ArgumentParser(description="Check LLM API key balance")
    parser.add_argument("-u", "--url", required=True, help="API base URL, e.g. https://api.example.com")
    parser.add_argument("-k", "--keys", nargs="+", help="API key(s), or set LLM_API_KEY env")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    keys = args.keys or [os.environ.get("LLM_API_KEY")]
    if not keys or not keys[0]:
        parser.error("No API key provided. Use -k or set LLM_API_KEY env var.")

    results = []
    for key in keys:
        r = check_balance(args.url, key)
        r["key"] = mask_key(key)
        results.append(r)

    total_remaining = summarize_totals(results)

    if args.json:
        print(
            json.dumps(
                {"results": results, "total_remaining": total_remaining},
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    for r in results:
        if r.get("error"):
            print(f"[{r['key']}] ERROR: {r['error']}")
        else:
            status = "ACTIVE" if r["valid"] else "INACTIVE"
            remain = format_decimal(to_decimal(r["remaining"])) if r["remaining"] is not None else "N/A"
            total = format_decimal(to_decimal(r["total"])) if r.get("total") is not None else None
            unit = r["unit"]
            if total is not None:
                print(f"[{r['key']}] {status} | {remain} / {total} {unit} (Remain/Total)")
            else:
                print(f"[{r['key']}] {status} | {remain} {unit} (Remain)")

    print(f"Total Remaining: {format_totals(total_remaining)}")

    ## exit 1 if any key is invalid or has no remaining balance
    if has_invalid_or_depleted_balance(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
