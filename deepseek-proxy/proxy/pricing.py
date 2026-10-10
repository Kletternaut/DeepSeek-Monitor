"""Price model and peak/off-peak logic for DeepSeek/Anthropic models.

Prices are given in USD per 1 million tokens. The default table in
:data:`DEFAULT_PRICING` is the verified starting value (DeepSeek pricing page)
and can be fully adjusted via persistence (:func:`load_pricing` /
:func:`save_pricing`).

Peak/off-peak windows (UTC): 01:00–04:00 and 06:00–10:00. Weekends in Beijing
time (``Asia/Shanghai``) count entirely as off-peak.

Chinese public holidays are off-peak according to DeepSeek's published pricing
rules. The holiday dates for 2026 are maintained in this module and must be
updated when DeepSeek publishes the next annual calendar.

Public symbols:
    * :data:`DEFAULT_PRICING`
    * :func:`load_pricing`
    * :func:`save_pricing`
    * :func:`normalize_model`
    * :func:`is_peak`
    * :func:`get_tariff`
    * :func:`fetch_latest_pricing`
"""

from __future__ import annotations

import copy
import json
import logging
import re
from html.parser import HTMLParser
from math import isfinite
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:  # pragma: no cover - only for type hints
    from .storage import Storage

logger = logging.getLogger("deepseek_proxy.pricing")

__all__ = [
    "DEFAULT_PRICING",
    "load_pricing",
    "save_pricing",
    "normalize_model",
    "is_peak",
    "get_tariff",
    "fetch_latest_pricing",
]

#: Settings key under which the price table is persisted as JSON.
PRICING_SETTING_KEY = "pricing"

#: Verified default prices in USD per 1 million tokens.
DEFAULT_PRICING: dict = {
    "deepseek-flash": {
        "off_peak": {"cache_hit": 0.003, "cache_miss": 0.15, "output": 0.60},
        "peak": {"cache_hit": 0.006, "cache_miss": 0.30, "output": 1.20},
    },
    "deepseek-v4-pro": {
        "off_peak": {"cache_hit": 0.022, "cache_miss": 0.66, "output": 1.98},
        "peak": {"cache_hit": 0.044, "cache_miss": 1.32, "output": 3.96},
    },
}

#: Fallback tariff if model or zone does not exist in the price table.
_FALLBACK_TARIFF = {"cache_hit": 0.003, "cache_miss": 0.15, "output": 0.60}

#: Peak windows as ``(start_minute, end_minute)`` in UTC; ``start`` inclusive,
#: ``end`` exclusive (minutes since midnight).
_PEAK_WINDOWS_UTC: tuple[tuple[int, int], ...] = ((1 * 60, 4 * 60), (6 * 60, 10 * 60))

_SHANGHAI = ZoneInfo("Asia/Shanghai")

# DeepSeek's current pricing docs exclude Chinese public holidays from peak
# billing. 2026 dates are based on the State Council's published holiday notice;
# update this calendar when the next year's schedule is announced.
_CHINESE_PUBLIC_HOLIDAY_RANGES_2026 = (
    (date(2026, 1, 1), date(2026, 1, 3)),
    (date(2026, 2, 15), date(2026, 2, 23)),
    (date(2026, 4, 4), date(2026, 4, 6)),
    (date(2026, 5, 1), date(2026, 5, 5)),
    (date(2026, 6, 19), date(2026, 6, 21)),
    (date(2026, 9, 25), date(2026, 9, 27)),
    (date(2026, 10, 1), date(2026, 10, 7)),
)
_CHINESE_PUBLIC_HOLIDAYS = frozenset(
    start + timedelta(days=offset)
    for first, last in _CHINESE_PUBLIC_HOLIDAY_RANGES_2026
    for start in (first,)
    for offset in range((last - start).days + 1)
)

#: Price fields in the order of the DeepSeek table (off-peak row, then peak
#: row): cache_hit, cache_miss, output.
_ROW_FIELDS = ("cache_hit", "cache_miss", "output")


def _deep_copy_default() -> dict:
    """Deep copy of the default price table."""
    return copy.deepcopy(DEFAULT_PRICING)


def validate_pricing(pricing: dict) -> dict:
    """Validate and normalize a persisted or user-supplied price table."""
    if not isinstance(pricing, dict) or not pricing:
        raise ValueError("Pricing must be a non-empty object.")

    validated: dict[str, dict] = {}
    for model, model_data in pricing.items():
        if model not in DEFAULT_PRICING:
            raise ValueError(f"Unsupported pricing model: {model!r}.")
        if not isinstance(model_data, dict):
            raise ValueError(f"Pricing for {model!r} must be an object.")

        validated_model: dict[str, dict[str, float]] = {}
        for period in ("off_peak", "peak"):
            period_data = model_data.get(period)
            if not isinstance(period_data, dict):
                raise ValueError(f"Pricing for {model!r} is missing {period!r}.")
            validated_period: dict[str, float] = {}
            for field in _ROW_FIELDS:
                value = period_data.get(field)
                if isinstance(value, bool):
                    raise ValueError(f"{model}.{period}.{field} must be a number.")
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    raise ValueError(f"{model}.{period}.{field} must be a number.") from None
                if not isfinite(number) or number < 0:
                    raise ValueError(
                        f"{model}.{period}.{field} must be finite and non-negative."
                    )
                validated_period[field] = number
            validated_model[period] = validated_period

        for field in _ROW_FIELDS:
            if validated_model["peak"][field] < validated_model["off_peak"][field]:
                raise ValueError(
                    f"{model}.{field}: peak price cannot be lower than off-peak."
                )
        validated[model] = validated_model

    return validated


def load_pricing(storage: Storage | None = None) -> dict:
    """Load the price table from ``storage`` (JSON) or return the defaults.

    Parameters:
        storage: Optional :class:`~proxy.storage.Storage`. For ``None`` or a
            missing/invalid entry, a deep copy of :data:`DEFAULT_PRICING` is
            returned.
    """
    if storage is None:
        return _deep_copy_default()

    raw = storage.get_setting(PRICING_SETTING_KEY)
    if not raw:
        return _deep_copy_default()

    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError) as exc:
        logger.warning("Invalid pricing JSON in settings: %s", exc)
        return _deep_copy_default()

    if not isinstance(parsed, dict) or not parsed:
        logger.warning("Pricing setting is not a non-empty dict, using defaults.")
        return _deep_copy_default()

    try:
        return validate_pricing(parsed)
    except ValueError as exc:
        logger.warning("Invalid pricing setting: %s; using defaults.", exc)
        return _deep_copy_default()


def save_pricing(storage: Storage, pricing: dict) -> None:
    """Validate then persist the price table under the ``pricing`` setting."""
    storage.set_setting(PRICING_SETTING_KEY, json.dumps(validate_pricing(pricing)))


def normalize_model(model: str) -> str:
    """Map a model name to a price key.

    Rules:
        * ``pro`` / ``v4-pro`` / ``opus`` → ``"deepseek-v4-pro"``
        * ``flash`` / ``sonnet`` / ``haiku`` → ``"deepseek-flash"``
        * unknown/empty → ``"deepseek-flash"`` (DeepSeek maps unknown models
          to the cheap ``flash`` model too).
    """
    name = (model or "").strip().lower()

    if "pro" in name or "opus" in name:
        return "deepseek-v4-pro"
    if "flash" in name or "sonnet" in name or "haiku" in name:
        return "deepseek-flash"
    return "deepseek-flash"


def is_peak(timestamp_iso: str) -> bool:
    """Check whether the point in time lies in the peak zone.

        The timestamp is parsed via :func:`datetime.fromisoformat` and converted to
        UTC. A timestamp without offset is assumed to be UTC. On a parse error or
        ``None``, ``False`` (off-peak) is returned.

        A point in time that falls on Saturday or Sunday in Beijing time is always
    off-peak. Chinese public holidays are not considered.
    """
    if not timestamp_iso:
        return False

    try:
        dt = datetime.fromisoformat(timestamp_iso)
    except (TypeError, ValueError):
        logger.debug("is_peak: unparseable timestamp %r", timestamp_iso)
        return False

    if dt.tzinfo is None:
        # Naive timestamp: conservatively interpret as UTC (documented
        # assumption, since the expected format contains an offset).
        dt = dt.replace(tzinfo=timezone.utc)

    utc_dt = dt.astimezone(timezone.utc)

    # Weekends and officially announced Chinese public holidays are off-peak.
    shanghai_dt = utc_dt.astimezone(_SHANGHAI)
    if shanghai_dt.weekday() >= 5 or shanghai_dt.date() in _CHINESE_PUBLIC_HOLIDAYS:
        return False

    minute_of_day = utc_dt.hour * 60 + utc_dt.minute
    return any(start <= minute_of_day < end for start, end in _PEAK_WINDOWS_UTC)


def _zone_key(timestamp_iso: str) -> str:
    """Return ``"peak"`` or ``"off_peak"`` for the timestamp."""
    return "peak" if is_peak(timestamp_iso) else "off_peak"


def get_tariff(
    model: str,
    timestamp_iso: str,
    pricing: dict | None = None,
) -> dict:
    """Return ``{cache_hit, cache_miss, output}`` for the model and zone.

    The fallback is always ``deepseek-flash``/``off_peak`` from the loaded table
    if the model or zone is missing or invalid.
    """
    table = pricing if pricing is not None else load_pricing()
    model_key = normalize_model(model)
    zone_key = _zone_key(timestamp_iso)

    model_entry = table.get(model_key)
    if not isinstance(model_entry, dict):
        model_entry = table.get("deepseek-flash")
    if not isinstance(model_entry, dict):
        return dict(_FALLBACK_TARIFF)

    tariff = model_entry.get(zone_key)
    if not isinstance(tariff, dict):
        tariff = model_entry.get("off_peak")
    if not isinstance(tariff, dict):
        fallback_entry = table.get("deepseek-flash")
        if isinstance(fallback_entry, dict) and isinstance(fallback_entry.get("off_peak"), dict):
            return dict(fallback_entry["off_peak"])
        return dict(_FALLBACK_TARIFF)

    return dict(tariff)


# --------------------------------------------------------------------- fetch
_PRICING_URL = "https://api-docs.deepseek.com/quick_start/pricing/"
_AMOUNT_RE = re.compile(r"\$\s*([0-9]+(?:\.[0-9]+)?)")


class _PricingTableParser(HTMLParser):
    """Collect top-level HTML tables as rows of normalized cell text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table_depth = 0
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "table":
            self._table_depth += 1
            if self._table_depth == 1:
                self._table = []
                self.tables.append(self._table)
            return
        if self._table_depth != 1:
            return
        if tag == "tr":
            self._row = []
            if self._table is not None:
                self._table.append(self._row)
        elif tag in ("th", "td") and self._row is not None:
            self._cell_parts = []

    def handle_data(self, data: str) -> None:
        if self._cell_parts is not None:
            self._cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._table_depth == 1:
            if tag in ("th", "td") and self._cell_parts is not None:
                value = " ".join(" ".join(self._cell_parts).split())
                if self._row is not None:
                    self._row.append(value)
                self._cell_parts = None
            elif tag == "tr":
                self._row = None
        if tag == "table" and self._table_depth:
            self._table_depth -= 1
            if self._table_depth == 0:
                self._table = None
                self._row = None
                self._cell_parts = None


def parse_pricing_html(html: str) -> dict | None:
    """Parse DeepSeek's pricing matrix; return None if the layout is ambiguous."""
    parser = _PricingTableParser()
    try:
        parser.feed(html)
        parser.close()
    except (ValueError, TypeError):
        return None

    expected_categories = {"cache_hit", "cache_miss", "output"}
    expected_periods = {"off_peak", "peak"}

    for table in parser.tables:
        table_text = " ".join(cell for row in table for cell in row).lower()
        if "deepseek-flash" not in table_text or "deepseek-v4-pro" not in table_text:
            continue

        current_category: str | None = None
        prices: dict[tuple[str, str], list[float]] = {}

        for row in table:
            row_text = " ".join(row).lower()
            if re.search(r"1m\s+input\s+tokens?\s*\(\s*cache\s+hit\s*\)", row_text):
                current_category = "cache_hit"
            elif re.search(r"1m\s+input\s+tokens?\s*\(\s*cache\s+miss\s*\)", row_text):
                current_category = "cache_miss"
            elif re.search(r"1m\s+output\s+tokens?", row_text):
                current_category = "output"

            if not current_category:
                continue

            if re.search(r"\boff[\s-]?peak\b", row_text):
                period = "off_peak"
            elif re.search(r"\bpeak\b", row_text):
                period = "peak"
            else:
                continue

            row_prices = [
                float(match)
                for cell in row
                for match in _AMOUNT_RE.findall(cell)
            ]
            if len(row_prices) != 2 or any(
                not isfinite(value) or value < 0 for value in row_prices
            ):
                continue
            prices[(current_category, period)] = row_prices

        required = {
            (category, period)
            for category in expected_categories
            for period in expected_periods
        }
        if set(prices) != required:
            continue

        result = copy.deepcopy(DEFAULT_PRICING)
        for category in expected_categories:
            for period in expected_periods:
                flash_price, pro_price = prices[(category, period)]
                result["deepseek-flash"][period][category] = flash_price
                result["deepseek-v4-pro"][period][category] = pro_price

        try:
            return validate_pricing(result)
        except ValueError:
            continue

    return None


def fetch_latest_pricing() -> dict | None:
    """Fetch official prices. Never overwrite stored prices when parsing fails."""
    from urllib.request import Request, urlopen  # locally: only load on demand

    try:
        request = Request(
            _PRICING_URL,
            headers={"User-Agent": "deepseek-api-proxy/1.0"},
        )
        with urlopen(request, timeout=10) as response:
            raw_bytes = response.read()
    except Exception as exc:  # noqa: BLE001 - network errors are best-effort
        logger.warning("fetch_latest_pricing: fetch failed: %s", exc)
        return None

    result = parse_pricing_html(raw_bytes.decode("utf-8", errors="replace"))
    if result is None:
        logger.warning(
            "fetch_latest_pricing: official pricing table could not be parsed; "
            "stored prices remain unchanged."
        )
    return result
