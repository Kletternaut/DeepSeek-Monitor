"""Tests für die Kern-Logik der Monitor-App (deepseek_monitor).

Deckt die Tkinter-freien Module ab: Tariflogik, API-Parsing, Währungs-
Formatierung und Farb-Helfer.
"""

from __future__ import annotations

import datetime as dt

from deepseek_monitor import config
from deepseek_monitor.analytics import (
    _shade_hex,
    analyze_history,
    analyze_proxy_usage,
    format_money,
)
from deepseek_monitor.api import currency_symbol, parse_balance
from deepseek_monitor.tariff import calculate_status, get_rate_periods, status_at

UTC = dt.timezone.utc


# ---------------------------------------------------------------------------
# Tariflogik (status_at)
# ---------------------------------------------------------------------------

def test_status_at_peak_window() -> None:
    # 02:00 UTC liegt im Peak-Fenster 01:00–04:00 UTC.
    # Datum liegt VOR dem Wochenend-Regel-Start (2026-08-22), daher greift die
    # Wochenend-Logik nicht und das Ergebnis ist unabhängig vom Wochentag.
    assert status_at(dt.datetime(2026, 1, 15, 2, 0, tzinfo=UTC)) == "PEAK"


def test_status_at_standard_outside_peak() -> None:
    # 12:00 UTC liegt außerhalb aller Peak-Fenster.
    assert status_at(dt.datetime(2026, 1, 15, 12, 0, tzinfo=UTC)) == "STANDARD"


def test_chinese_public_holiday_overrides_weekday_peak() -> None:
    # 10:00 Beijing time on the Mid-Autumn holiday is off-peak, despite a peak hour.
    assert status_at(dt.datetime(2026, 9, 25, 2, 0, tzinfo=UTC)) == "HOLIDAY"


def test_holiday_status_has_its_own_color() -> None:
    status_text, seconds_left, color, key = calculate_status(
        dt.datetime(2026, 9, 25, 2, 0, tzinfo=UTC)
    )
    assert key == "HOLIDAY"
    assert color == config.COLOR_HOLIDAY
    assert "HOLIDAY" in status_text


def test_status_at_naive_datetime_assumes_utc() -> None:
    # Naive Datetimes werden intern als UTC behandelt (kein Fehler).
    assert status_at(dt.datetime(2026, 1, 15, 2, 0)) == "PEAK"


def test_calculate_status_returns_4_tuple() -> None:
    text, seconds_left, color, key = calculate_status(
        dt.datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    )
    assert text == "STANDARD"
    assert seconds_left >= 0.0
    assert color == config.STATUS_COLORS["STANDARD"]
    assert key == "STANDARD"


def test_get_rate_periods_covers_interval() -> None:
    start = dt.datetime(2026, 1, 15, 0, 0, tzinfo=UTC)
    end = dt.datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    periods = get_rate_periods(start, end)
    assert periods
    # Erste Periode beginnt am Intervallstart, letzte endet am Intervallende.
    assert periods[0][0] == start
    assert periods[-1][1] == end


def test_get_rate_periods_empty_for_inverted_interval() -> None:
    start = dt.datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    end = dt.datetime(2026, 1, 15, 0, 0, tzinfo=UTC)
    assert get_rate_periods(start, end) == []


def test_long_rate_interval_splits_across_chinese_holidays() -> None:
    start = dt.datetime(2026, 9, 24, 0, 0, tzinfo=UTC)
    end = dt.datetime(2026, 10, 2, 0, 0, tzinfo=UTC)

    periods = get_rate_periods(start, end)

    assert periods[0][0] == start
    assert periods[-1][1] == end
    assert any(status == "HOLIDAY" for _, _, status in periods)
    assert len(periods) > 10


def test_balance_analytics_reports_chinese_holiday_consumption() -> None:
    records = [
        {"ts": "2026-09-24T00:00:00+00:00", "total": 100.0, "currency": "USD"},
        {"ts": "2026-10-02T00:00:00+00:00", "total": 90.0, "currency": "USD"},
    ]

    analytics = analyze_history(
        records,
        tz_name="America/Los_Angeles",
        days=60,
        max_gap_s=1_000_000,
    )

    assert analytics["holiday_usage"] > 0
    assert any(row["holiday_usage"] > 0 for row in analytics["daily"])


def test_real_proxy_analytics_reports_chinese_holiday_costs() -> None:
    events = [
        {
            "timestamp": "2026-09-25T02:00:00+00:00",
            "cost_usd": 0.25,
            "model": "deepseek-flash",
        }
    ]

    analytics = analyze_proxy_usage(
        events,
        tz_name="America/Los_Angeles",
        days=60,
    )

    assert analytics["holiday_usage"] == 0.25
    assert any(row["holiday_usage"] == 0.25 for row in analytics["daily"])
    assert analytics["peak_usage"] == 0.0


# ---------------------------------------------------------------------------
# API (parse_balance, currency_symbol)
# ---------------------------------------------------------------------------

def test_parse_balance_with_balance_infos() -> None:
    data = {
        "is_available": True,
        "balance_infos": [
            {
                "total_balance": "12.50",
                "topped_up_balance": "10.00",
                "granted_balance": "2.50",
                "currency": "USD",
            }
        ],
    }
    result = parse_balance(data)
    assert result["total"] == 12.50
    assert result["topped"] == 10.00
    assert result["granted"] == 2.50
    assert result["available"] is True
    assert result["currency"] == "USD"


def test_parse_balance_flat_structure() -> None:
    data = {
        "total_balance": "5.0",
        "topped_up_balance": "5.0",
        "granted_balance": "0",
        "currency": "CNY",
    }
    result = parse_balance(data)
    assert result["total"] == 5.0
    assert result["currency"] == "CNY"


def test_parse_balance_invalid_values_default_to_zero() -> None:
    result = parse_balance({"total_balance": "not-a-number"})
    assert result["total"] == 0.0


def test_currency_symbol_known() -> None:
    assert currency_symbol("USD") == "$"
    assert currency_symbol("eur") == "€"
    assert currency_symbol("CNY") == "¥"


def test_currency_symbol_unknown_returns_currency() -> None:
    assert currency_symbol("XYZ") == "XYZ"


# ---------------------------------------------------------------------------
# Formatierung (format_money, _shade_hex)
# ---------------------------------------------------------------------------

def test_format_money_english() -> None:
    assert format_money(12.3456, "USD") == "$12.3456"


def test_format_money_german_uses_comma() -> None:
    assert format_money(12.3456, "USD", "de") == "$12,3456"


def test_format_money_no_currency_symbol() -> None:
    assert format_money(12.3456, "") == "12.3456"


def test_shade_hex_darken() -> None:
    assert _shade_hex("#808080", 0.5) == "#404040"


def test_shade_hex_identity() -> None:
    assert _shade_hex("#123456", 1.0) == "#123456"


def test_shade_hex_clamps_at_bounds() -> None:
    assert _shade_hex("#ffffff", 2.0) == "#ffffff"
    assert _shade_hex("#000000", 0.0) == "#000000"


def test_shade_hex_invalid_returns_input() -> None:
    assert _shade_hex("not-a-color", 0.5) == "#not-a-color"
