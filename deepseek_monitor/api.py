"""DeepSeek Monitor — balance API.

Fetches the balance via ``/user/balance`` and parses the response. No Tkinter
dependency.

Public symbols:
    * :func:`fetch_balance`
    * :func:`parse_balance`
    * :func:`currency_symbol`
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any

from deepseek_monitor.config import BALANCE_URL
from deepseek_monitor.i18n import CURRENCY_SYMBOLS


def fetch_balance(api_key: str) -> dict[str, Any]:
    request = urllib.request.Request(
        BALANCE_URL,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="GET",
    )

    with urllib.request.urlopen(request, timeout=15) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw)


def parse_balance(data: dict[str, Any]) -> dict[str, Any]:
    def as_float(obj: dict[str, Any], field: str) -> float:
        try:
            return float(obj.get(field, 0))
        except (TypeError, ValueError):
            return 0.0

    is_available = bool(data.get("is_available", True))

    infos = data.get("balance_infos") or []
    if infos and isinstance(infos[0], dict):
        info = infos[0]
        return {
            "total": as_float(info, "total_balance"),
            "topped": as_float(info, "topped_up_balance"),
            "granted": as_float(info, "granted_balance"),
            "available": is_available,
            "currency": str(info.get("currency", "") or ""),
        }

    return {
        "total": as_float(data, "total_balance"),
        "topped": as_float(data, "topped_up_balance"),
        "granted": as_float(data, "granted_balance"),
        "available": is_available,
        "currency": str(data.get("currency", "") or ""),
    }


def currency_symbol(currency: str) -> str:
    return CURRENCY_SYMBOLS.get(
        (currency or "").upper(),
        currency or "",
    )
