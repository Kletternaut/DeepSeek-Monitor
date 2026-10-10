"""DeepSeek Monitor — configuration and colors.

This module holds all module-level constants (tariff assumptions, poll
intervals, URLs, InfluxDB defaults) plus the GUI color palette. It has no
Tkinter dependency and is imported by the other logic modules.

Public symbols:
    * :data:`UTC`, :data:`APP_NAME`, :data:`APP_VERSION`
    * tariff/poll/analytics constants
    * :data:`BALANCE_URL`, :data:`PROXY_BASE_URL`, :data:`PROXY_USAGE_URL`
    * InfluxDB globals (:data:`INFLUX_URL` … :data:`INFLUX_ENABLED`)
    * :func:`_apply_influx_config`
    * color constants (:data:`COLOR_BG` … :data:`STATUS_COLORS`)
"""

from __future__ import annotations

import datetime as dt
import os
import zoneinfo

# ============================================================
# CONFIGURATION
# ============================================================

UTC = dt.timezone.utc
APP_NAME = "DeepSeek API Proxy Monitor"

# The project version lives in a single place: ``deepseek_monitor._version``.
from deepseek_monitor._version import __version__ as APP_VERSION

# Tariff assumption from the original version
PEAK_WINDOWS_UTC = (
    (1 * 60, 4 * 60),   # 01:00–04:00 UTC
    (6 * 60, 10 * 60),  # 06:00–10:00 UTC
)

PEAK_MULTIPLIER = 2.0

# Weekend off-peak rule:
# entire Saturday + Sunday in Beijing time
WEEKEND_RULE_START_UTC = dt.datetime(
    2026, 8, 22, 16, 0, tzinfo=UTC
)
BJ_TZ = zoneinfo.ZoneInfo("Asia/Shanghai")

# Official Chinese public-holiday periods for 2026 (State Council notice,
# published 2025-11-04). Update this calendar when the next annual notice is
# published. Dates are evaluated in Beijing time, not the UI display timezone.
CHINESE_PUBLIC_HOLIDAY_RANGES_2026 = (
    (dt.date(2026, 1, 1), dt.date(2026, 1, 3)),    # New Year
    (dt.date(2026, 2, 15), dt.date(2026, 2, 23)),  # Spring Festival
    (dt.date(2026, 4, 4), dt.date(2026, 4, 6)),    # Qingming
    (dt.date(2026, 5, 1), dt.date(2026, 5, 5)),    # Labor Day
    (dt.date(2026, 6, 19), dt.date(2026, 6, 21)),  # Dragon Boat
    (dt.date(2026, 9, 25), dt.date(2026, 9, 27)),  # Mid-Autumn
    (dt.date(2026, 10, 1), dt.date(2026, 10, 7)),  # National Day
)
CHINESE_PUBLIC_HOLIDAYS = frozenset(
    first + dt.timedelta(days=offset)
    for first, last in CHINESE_PUBLIC_HOLIDAY_RANGES_2026
    for offset in range((last - first).days + 1)
)

DEFAULT_TZ = "Europe/Berlin"

DEFAULT_POLL_INTERVAL_S = 20
MIN_POLL_INTERVAL_S = 20
MAX_POLL_INTERVAL_S = 3600
POLL_INTERVAL_STEP_S = 10
POLL_INTERVAL_S = DEFAULT_POLL_INTERVAL_S
WARNING_BEFORE_S = 60
ANALYTICS_DAYS = 14
ANALYTICS_INTERVAL_LIMIT = 150

BALANCE_URL = "https://api.deepseek.com/user/balance"

# Proxy (deepseek-proxy/) provides real token-usage data as the source for the
# "Cost & Usage" detail page instead of the balance-delta estimate.
PROXY_BASE_URL = os.environ.get(
    "DEEPSEEK_PROXY_URL",
    "http://127.0.0.1:8000",
).rstrip("/")
PROXY_USAGE_URL = f"{PROXY_BASE_URL}/api/usage"

# Optional central InfluxDB 2.x as an alternative (multi-host) data source.
# These values can be changed at runtime via the GUI settings
# (``_apply_influx_config``) and are persisted in ``influx_config``.
INFLUX_URL = ""
INFLUX_TOKEN = ""
INFLUX_ORG = ""
INFLUX_BUCKET = ""
INFLUX_ENABLED = False


def _apply_influx_config(
    url: str,
    token: str,
    org: str,
    bucket: str,
) -> None:
    """Set the InfluxDB configuration at runtime (module globals)."""
    global INFLUX_URL, INFLUX_TOKEN, INFLUX_ORG, INFLUX_BUCKET, INFLUX_ENABLED

    INFLUX_URL = (url or "").strip().rstrip("/")
    INFLUX_TOKEN = (token or "").strip()
    INFLUX_ORG = (org or "").strip()
    INFLUX_BUCKET = (bucket or "").strip()
    INFLUX_ENABLED = bool(
        INFLUX_URL and INFLUX_TOKEN and INFLUX_ORG and INFLUX_BUCKET
    )


MAX_HISTORY = 50_000
MAX_ANALYTICS_GAP_S = POLL_INTERVAL_S * 2.5


# ============================================================
# COLORS
# ============================================================

COLOR_BG = "#121212"
COLOR_CARD = "#1a1a1a"
COLOR_CARD_BORDER = "#2d2d2d"

COLOR_STANDARD = "#2e7d32"
COLOR_PEAK = "#c62828"
COLOR_WEEKEND = "#f57c00"\nCOLOR_HOLIDAY = "#7e57c2"

COLOR_TEXT_MAIN = "#ffffff"
COLOR_TEXT_DIM = "#78909c"
COLOR_TICK_MINOR = "#404040"
COLOR_ACCENT = "#64b5f6"
COLOR_NEEDLE = "#ffffff"

COLOR_WARNING = "#ffb300"


STATUS_COLORS = {
    "STANDARD": COLOR_STANDARD,
    "PEAK": COLOR_PEAK,
    "WEEKEND": COLOR_WEEKEND,
}
