#!/usr/bin/env python3
"""
Validierungs-Skript: vergleicht die vom Proxy erfassten Token/Kosten mit den
offiziellen DeepSeek-Ground-Truth-Quellen.

Zwei Ground-Truth-Quellen:
1. Usage-Seite  (https://platform.deepseek.com/usage)  -> exakte Token-Zahl
2. Balance-API  (/user/balance)                        -> exaktes Guthaben

Aufruf:
  .venv/bin/python validation_test.py --balance   # aktuellen Stand ausgeben
  .venv/bin/python validation_test.py --request   # 1 kontrollierten Request senden

Der API-Key wird aus ~/.config/deepseek/api_key gelesen und NIE ausgegeben.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.request

KEY_FILE = "/home/admin/.config/deepseek/api_key"
PROXY_URL = "http://127.0.0.1:8000/anthropic/v1/messages"
BALANCE_URL = "https://api.deepseek.com/user/balance"
DB_PATH = "/home/admin/.local/state/deepseek/usage.db"


def read_key() -> str:
    with open(KEY_FILE, "r", encoding="utf-8") as f:
        return f.read().strip()


def get_balance() -> tuple[float, str]:
    """Liefert (total_balance, currency) aus der Balance-API."""
    req = urllib.request.Request(
        BALANCE_URL,
        headers={"Authorization": f"Bearer {read_key()}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    info = data["balance_infos"][0]
    return float(info["total_balance"]), str(info.get("currency", "CNY"))


def db_totals() -> dict:
    """Summiert alle erfassten Usage-Events (Token + daraus Kosten)."""
    import sys as _sys

    _sys.path.insert(0, "/home/admin/deepseek/deepseek-proxy")
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
    """Sendet EINEN eindeutigen Request durch den Proxy (kein Cache-Hit)."""
    import sys as _sys

    _sys.path.insert(0, "/home/admin/deepseek/deepseek-proxy")
    from proxy.cost_calc import calc_usage_cost
    from proxy.pricing import load_pricing
    from proxy.storage import Storage, row_to_usage

    # Eindeutiger Prompt -> garantiert Cache-Miss = input, kein Cache-Hit.
    prompt = (
        f"Validierungstest um {time.strftime('%H:%M:%S')}. "
        "Beantworte nur mit dem Wort: OK"
    )
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

    # Kurz warten, damit der Callback den Eintrag geschrieben hat.
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
    group.add_argument("--balance", action="store_true", help="aktuellen Stand ausgeben")
    group.add_argument("--request", action="store_true", help="1 kontrollierten Request senden")
    args = parser.parse_args()

    if args.balance:
        bal, cur = get_balance()
        t = db_totals()
        print("=== AKTUELLER STAND ===")
        print(f"Guthaben (DeepSeek):   {bal:.2f} {cur}")
        print(f"Proxy erfasste Events: {t['requests']}  (id {t['first_id']}..{t['last_id']})")
        print(f"  Input (Cache-Miss):  {t['input']:,}")
        print(f"  Cache-Read (Hit):    {t['cache_read']:,}")
        print(f"  Output:              {t['output']:,}")
        print(f"  Token gesamt:        {t['input'] + t['cache_read'] + t['output']:,}")
        print(f"  Kosten (berechnet):  ${t['cost_usd']:.6f}")
        print()
        print("=> Vergleiche 'Token gesamt' mit der Delta-Zahl auf "
              "platform.deepseek.com/usage")
        print("=> Vergleiche 'Kosten' mit dem Guthaben-Delta (beachte USD vs. Währung)")

    elif args.request:
        print("=== KONTROLLIERTER REQUEST (vorher) ===")
        bal_before, cur = get_balance()
        t_before = db_totals()
        print(f"Guthaben: {bal_before:.2f} {cur}")
        print(f"Proxy-Events vorher: {t_before['requests']}")

        print("=== Sende 1 eindeutigen Request durch den Proxy ... ===")
        r = send_controlled_request()

        print("=== NACH dem Request ===")
        bal_after, _ = get_balance()
        t_after = db_totals()
        print(f"Erfasster Request ({r['timestamp']}):")
        print(f"  Input (Miss):  {r['input']:,}")
        print(f"  Cache-Read:    {r['cache_read']:,}")
        print(f"  Output:        {r['output']:,}")
        print(f"  Token gesamt:  {r['input'] + r['cache_read'] + r['output']:,}")
        print(f"  Kosten:        ${r['cost_usd']:.8f}")
        print(f"Proxy-Events nachher: {t_after['requests']} "
              f"(+{t_after['requests'] - t_before['requests']})")
        print(f"Guthaben: {bal_after:.2f} {cur} "
              f"(Delta: {bal_before - bal_after:.2f} {cur})")
        print()
        print("=> Token-Delta auf der Usage-Seite mit 'Token gesamt' vergleichen.")
        print("=> Einzel-Request-Kosten sind im Guthaben (2 Dezimalen) i.d.R. unsichtbar.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
