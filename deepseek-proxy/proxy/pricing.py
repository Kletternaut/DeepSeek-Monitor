"""Price model and peak/off-peak logic for DeepSeek/Anthropic models.

Prices are given in USD per 1 million tokens. The default table in
:data:`DEFAULT_PRICING` is the verified starting value (DeepSeek pricing page)
and can be fully adjusted via persistence (:func:`load_pricing` /
:func:`save_pricing`).

Peak/off-peak windows (UTC): 01:00–04:00 and 06:00–10:00. Weekends in Beijing
time (``Asia/Shanghai``) count entirely as off-peak.

Known limitation: Chinese public holidays are NOT handled specially (they are
therefore incorrectly treated as peak when they fall on a weekday).

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
from datetime import datetime, timezone
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

#: Price fields in the order of the DeepSeek table (off-peak row, then peak
#: row): cache_hit, cache_miss, output.
_ROW_FIELDS = ("cache_hit", "cache_miss", "output")


def _deep_copy_default() -> dict:
    """Deep copy of the default price table."""
    return copy.deepcopy(DEFAULT_PRICING)


def load_pricing(storage: "Storage | None" = None) -> dict:
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

    return parsed


def save_pricing(storage: "Storage", pricing: dict) -> None:
    """Persist ``pricing`` as a JSON string under the settings key ``pricing``."""
    storage.set_setting(PRICING_SETTING_KEY, json.dumps(pricing))


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

    # Weekend in Beijing time → always off-peak.
    if dt.astimezone(_SHANGHAI).weekday() >= 5:
        return False

    minute_of_day = utc_dt.hour * 60 + utc_dt.minute
    for start, end in _PEAK_WINDOWS_UTC:
        if start <= minute_of_day < end:
            return True
    return False


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
        if isinstance(fallback_entry, dict) and isinstance(
            fallback_entry.get("off_peak"), dict
        ):
            return dict(fallback_entry["off_peak"])
        return dict(_FALLBACK_TARIFF)

    return dict(tariff)


# --------------------------------------------------------------------- fetch
_PRICING_URL = "https://api-docs.deepseek.com/quick_start/pricing/"
_FLASH_ROW_RE = re.compile(r"deepseek[-\s]?flash", re.IGNORECASE)
_PRO_ROW_RE = re.compile(r"deepseek[-\s]?(v4[-\s]?pro|pro)", re.IGNORECASE)
_AMOUNT_RE = re.compile(r"\$\s*([0-9]+(?:\.[0-9]+)?)")


def _extract_row_amounts(text: str, start: int, end: int) -> list[float]:
    """Extract all ``$`` amounts in the text range ``[start, end)``."""
    chunk = text[start:end]
    return [float(m) for m in _AMOUNT_RE.findall(chunk)]


def fetch_latest_pricing() -> dict | None:
    """Attempt to load the current price table from the DeepSeek docs.

    Best effort: the docs page is HTML and its layout can change. It is assumed
    that six ``$`` amounts appear per model in the order ``cache_hit(off),
    cache_miss(off), output(off), cache_hit(peak), cache_miss(peak),
    output(peak)``. Additionally, ``peak >= off_peak`` must hold per category.

    Returns:
        The parsed table in the :data:`DEFAULT_PRICING` format, or ``None`` if
        no safe result could be extracted. Values are NEVER guessed. The caller
decides (with confirmation) whether to adopt the result.
    """
    from urllib.request import Request, urlopen  # locally: only load on demand

    try:
        request = Request(_PRICING_URL, headers={"User-Agent": "deepseek-proxy/1.0"})
        with urlopen(request, timeout=10) as response:  # noqa: S310 (feste URL)
            raw_bytes = response.read()
    except Exception as exc:  # noqa: BLE001 - any network error → None
        logger.warning("fetch_latest_pricing: fetch failed: %s", exc)
        return None

    try:
        html = raw_bytes.decode("utf-8", errors="replace")
    except Exception:  # pragma: no cover - decode with replace practically never fails
        return None

    # Roughly remove tags to keep positional distances.
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;?", " ", text)

    flash_match = _FLASH_ROW_RE.search(text)
    pro_match = _PRO_ROW_RE.search(text)
    if not flash_match or not pro_match:
        logger.info("fetch_latest_pricing: model rows not found.")
        return None

    boundaries = sorted({flash_match.start(), pro_match.start(), len(text)})

    def _row_amounts(pos: int) -> list[float] | None:
        # Determine the next line boundary.
        next_pos = len(text)
        for b in boundaries:
            if b > pos:
                next_pos = b
                break
        amounts = _extract_row_amounts(text, pos, next_pos)
        return amounts if len(amounts) == 6 else None

    flash_amounts = _row_amounts(flash_match.start())
    pro_amounts = _row_amounts(pro_match.start())
    if flash_amounts is None or pro_amounts is None:
        logger.info("fetch_latest_pricing: unexpected number of amounts per row.")
        return None

    def _build(amounts: list[float]) -> dict | None:
        off = dict(zip(_ROW_FIELDS, amounts[:3]))
        peak = dict(zip(_ROW_FIELDS, amounts[3:]))
        for field in _ROW_FIELDS:
            if peak[field] < off[field]:
                return None
        return {"off_peak": off, "peak": peak}

    flash_entry = _build(flash_amounts)
    pro_entry = _build(pro_amounts)
    if flash_entry is None or pro_entry is None:
        logger.info("fetch_latest_pricing: values not plausible (peak < off_peak).")
        return None

    return {"deepseek-flash": flash_entry, "deepseek-v4-pro": pro_entry}
