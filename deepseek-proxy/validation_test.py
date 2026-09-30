#!/usr/bin/env python3
"""
Validation script: compares the tokens/cost recorded by the proxy with the
official DeepSeek ground-truth sources.

Two ground-truth sources:
1. Usage page  (https://platform.deepseek.com/usage)  -> exact token count
2. Balance API  (/user/balance)                        -> exact credit

Invocation:
  .venv/bin/python validation_test.py --balance   # print current state
  .venv/bin/python validation_test.py --request   # send 1 controlled request

The API key is read from ~/.config/deepseek/api_key and NEVER printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

# Make the proxy modules importable (script lives in the project root).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from proxy.paths import API_KEY_FILE, DB_FILE  # noqa: E402

KEY_FILE = API_KEY_FILE
PROXY_URL = "http://127.0.0.1:8000/anthropic/v1/messages"
BALANCE_URL = "https://api.deepseek.com/user/balance"
DB_PATH = DB_FILE


def read_key() -> str:
    with open(KEY_FILE, encoding="utf-8") as f:
        return f.read().strip()


def get_balance() -> tuple[float, str]:
    """Return (total_balance, currency) from the balance API."""
    req = urllib.request.Request(
        BALANCE_URL,
        headers={"Authorization": f"Bearer {read_key()}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    info = data["balance_infos"][0]
    return float(info["total_balance"]), str(info.get("currency", "CNY"))


def db_totals() -> dict:
    """Sum all recorded usage events (tokens + costs derived from them)."""
    import sys as _sys

    _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from proxy.cost_calc import calc_usage_cost
    from proxy.pricing import load_pricing
    from proxy.storage import Storage, row_to_usage

    storage = Storage(DB_PATH)
    pricing = load_pricing(storage)
    rows = storage.get_usage(limit=None)

    totals = {
        "requests": len(rows),
        "input": 0,
        "cache_read": 0,
        "output": 0,
        "cost_usd": 0.0,
        "first_id": rows[0]["id"] if rows else None,
        "last_id": rows[-1]["id"] if rows else None,
    }
    for r in rows:
        u = row_to_usage(r)
        totals["input"] += u.input_tokens
        totals["cache_read"] += u.cache_read_input_tokens
        totals["output"] += u.output_tokens
        totals["cost_usd"] += calc_usage_cost(u, pricing)
    totals["cost_usd"] = round(totals["cost_usd"], 8)
    return totals


def send_controlled_request() -> dict:
    """Send ONE unique request through the proxy (no cache hit)."""
    import sys as _sys

    _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from proxy.cost_calc import calc_usage_cost
    from proxy.pricing import load_pricing
    from proxy.storage import Storage, row_to_usage

    # Unique prompt -> guarantees cache miss = input, no cache hit.
    prompt = f"Validation test at {time.strftime('%H:%M:%S')}. " "Reply only with the word: OK"
    body = {
        "model": "deepseek-flash",
        "max_tokens": 16,
        "stream": True,
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        PROXY_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "x-api-key": read_key(),
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        resp.read()

    # Wait briefly so the callback writes the entry.
    time.sleep(1.0)

    storage = Storage(DB_PATH)
    pricing = load_pricing(storage)
    rows = storage.get_usage(limit=1)
    u = row_to_usage(rows[0])
    return {
        "input": u.input_tokens,
        "cache_read": u.cache_read_input_tokens,
        "output": u.output_tokens,
        "cost_usd": calc_usage_cost(u, pricing),
        "timestamp": u.timestamp,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--balance", action="store_true", help="print current state")
    group.add_argument("--request", action="store_true", help="send 1 controlled request")
    args = parser.parse_args()

    if args.balance:
        bal, cur = get_balance()
        t = db_totals()
        print("=== CURRENT STATE ===")
        print(f"Balance (DeepSeek):    {bal:.2f} {cur}")
        print(f"Proxy recorded events: {t['requests']}  (id {t['first_id']}..{t['last_id']})")
        print(f"  Input (cache miss):  {t['input']:,}")
        print(f"  Cache-read (hit):    {t['cache_read']:,}")
        print(f"  Output:              {t['output']:,}")
        print(f"  Total tokens:        {t['input'] + t['cache_read'] + t['output']:,}")
        print(f"  Cost (calculated):   ${t['cost_usd']:.6f}")
        print()
        print("=> Compare 'total tokens' with the delta number on " "platform.deepseek.com/usage")
        print("=> Compare 'cost' with the balance delta (note USD vs. currency)")

    elif args.request:
        print("=== CONTROLLED REQUEST (before) ===")
        bal_before, cur = get_balance()
        t_before = db_totals()
        print(f"Balance: {bal_before:.2f} {cur}")
        print(f"Proxy events before: {t_before['requests']}")

        print("=== Sending 1 unique request through the proxy ... ===")
        r = send_controlled_request()

        print("=== AFTER the request ===")
        bal_after, _ = get_balance()
        t_after = db_totals()
        print(f"Recorded request ({r['timestamp']}):")
        print(f"  Input (miss):  {r['input']:,}")
        print(f"  Cache-read:    {r['cache_read']:,}")
        print(f"  Output:        {r['output']:,}")
        print(f"  Total tokens:  {r['input'] + r['cache_read'] + r['output']:,}")
        print(f"  Cost:          ${r['cost_usd']:.8f}")
        print(
            f"Proxy events after: {t_after['requests']} "
            f"(+{t_after['requests'] - t_before['requests']})"
        )
        print(f"Balance: {bal_after:.2f} {cur} " f"(delta: {bal_before - bal_after:.2f} {cur})")
        print()
        print("=> Compare the token delta on the usage page with 'total tokens'.")
        print("=> Single-request costs are usually invisible in the balance (2 decimals).")

    return 0


if __name__ == "__main__":
    sys.exit(main())
