"""Preis-Modell und Peak/Off-Peak-Logik für DeepSeek/Anthropic-Modelle.

Die Preise sind in USD pro 1 Million Token angegeben. Die Default-Tabelle in
:data:`DEFAULT_PRICING` ist der verifizierte Startwert (DeepSeek-Pricing-Seite)
und kann vollständig über die Persistenz (:func:`load_pricing` /
:func:`save_pricing`) angepasst werden.

Peak/Off-Peak-Zeitfenster (UTC): 01:00–04:00 und 06:00–10:00. Wochenenden in
Beijing-Zeit (``Asia/Shanghai``) gelten durchgehend als Off-Peak.

Bekannte Einschränkung: Chinesische Feiertage werden NICHT speziell behandelt
(sie gelten daher fälschlich als Peak, sofern sie auf einen Werktag fallen).

Öffentliche Symbole:
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

if TYPE_CHECKING:  # pragma: no cover - nur für Type-Hints
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

#: Settings-Key, unter dem die Preis-Tabelle als JSON persistiert wird.
PRICING_SETTING_KEY = "pricing"

#: Verifizierte Default-Preise in USD pro 1 Million Token.
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

#: Fallback-Tarif, falls Modell oder Zone nicht in der Preis-Tabelle existiert.
_FALLBACK_TARIFF = {"cache_hit": 0.003, "cache_miss": 0.15, "output": 0.60}

#: Peak-Fenster als ``(start_minute, end_minute)`` in UTC; ``start`` inklusiv,
#: ``end`` exklusiv (Minuten seit Mitternacht).
_PEAK_WINDOWS_UTC: tuple[tuple[int, int], ...] = ((1 * 60, 4 * 60), (6 * 60, 10 * 60))

_SHANGHAI = ZoneInfo("Asia/Shanghai")

#: Preisfelder in der Reihenfolge der DeepSeek-Tabelle (off-peak-Zeile, dann
#: peak-Zeile): cache_hit, cache_miss, output.
_ROW_FIELDS = ("cache_hit", "cache_miss", "output")


def _deep_copy_default() -> dict:
    """Tiefe Kopie der Default-Preis-Tabelle."""
    return copy.deepcopy(DEFAULT_PRICING)


def load_pricing(storage: "Storage | None" = None) -> dict:
    """Lädt die Preis-Tabelle aus ``storage`` (JSON) oder liefert die Defaults.

    Parameter:
        storage: Optionale :class:`~proxy.storage.Storage`. Bei ``None`` oder
            fehlendem/ungültigem Eintrag wird eine tiefe Kopie von
            :data:`DEFAULT_PRICING` zurückgegeben.
    """
    if storage is None:
        return _deep_copy_default()

    raw = storage.get_setting(PRICING_SETTING_KEY)
    if not raw:
        return _deep_copy_default()

    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError) as exc:
        logger.warning("Ungültiges Pricing-JSON in Settings: %s", exc)
        return _deep_copy_default()

    if not isinstance(parsed, dict) or not parsed:
        logger.warning("Pricing-Setting ist kein nicht-leeres Dict, nutze Defaults.")
        return _deep_copy_default()

    return parsed


def save_pricing(storage: "Storage", pricing: dict) -> None:
    """Persistiert ``pricing`` als JSON-String unter dem Settings-Key ``pricing``."""
    storage.set_setting(PRICING_SETTING_KEY, json.dumps(pricing))


def normalize_model(model: str) -> str:
    """Mappt einen Modellnamen auf einen Preisschlüssel.

    Regeln:
        * ``pro`` / ``v4-pro`` / ``opus`` → ``"deepseek-v4-pro"``
        * ``flash`` / ``sonnet`` / ``haiku`` → ``"deepseek-flash"``
        * unbekannt/leer → ``"deepseek-flash"`` (DeepSeek mappt Unbekanntes
          ebenfalls auf das günstige ``flash``-Modell).
    """
    name = (model or "").strip().lower()

    if "pro" in name or "opus" in name:
        return "deepseek-v4-pro"
    if "flash" in name or "sonnet" in name or "haiku" in name:
        return "deepseek-flash"
    return "deepseek-flash"


def is_peak(timestamp_iso: str) -> bool:
    """Prüft, ob der Zeitpunkt in der Peak-Zone liegt.

    Der Zeitstempel wird per :func:`datetime.fromisoformat` geparst und nach UTC
    konvertiert. Ein Zeitstempel ohne Offset wird als UTC angenommen. Bei einem
    Parse-Fehler oder ``None`` wird ``False`` (Off-Peak) zurückgegeben.

    Ein Zeitpunkt, der in Beijing-Zeit auf Samstag oder Sonntag fällt, ist immer
    Off-Peak. Chinesische Feiertage werden nicht berücksichtigt.
    """
    if not timestamp_iso:
        return False

    try:
        dt = datetime.fromisoformat(timestamp_iso)
    except (TypeError, ValueError):
        logger.debug("is_peak: nicht parsebarer Zeitstempel %r", timestamp_iso)
        return False

    if dt.tzinfo is None:
        # Naive Zeitstempel: konservativ als UTC interpretieren (dokumentierte
        # Annahme, da das erwartete Format einen Offset enthält).
        dt = dt.replace(tzinfo=timezone.utc)

    utc_dt = dt.astimezone(timezone.utc)

    # Wochenende in Beijing-Zeit → immer Off-Peak.
    if dt.astimezone(_SHANGHAI).weekday() >= 5:
        return False

    minute_of_day = utc_dt.hour * 60 + utc_dt.minute
    for start, end in _PEAK_WINDOWS_UTC:
        if start <= minute_of_day < end:
            return True
    return False


def _zone_key(timestamp_iso: str) -> str:
    """Liefert ``"peak"`` oder ``"off_peak"`` zum Zeitstempel."""
    return "peak" if is_peak(timestamp_iso) else "off_peak"


def get_tariff(
    model: str,
    timestamp_iso: str,
    pricing: dict | None = None,
) -> dict:
    """Liefert ``{cache_hit, cache_miss, output}`` für Modell und Zone.

    Fallback ist immer ``deepseek-flash``/``off_peak`` aus der geladenen Tabelle,
    falls Modell oder Zone fehlen oder ungültig sind.
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
    """Extrahiert alle ``$``-Beträge im Textbereich ``[start, end)``."""
    chunk = text[start:end]
    return [float(m) for m in _AMOUNT_RE.findall(chunk)]


def fetch_latest_pricing() -> dict | None:
    """Versucht, die aktuelle Preis-Tabelle von der DeepSeek-Doku zu laden.

    Best-Effort: Die Doku-Seite ist HTML und ihr Layout kann sich ändern. Es wird
    davon ausgegangen, dass je Modell sechs ``$``-Beträge in der Reihenfolge
    ``cache_hit(off), cache_miss(off), output(off), cache_hit(peak),
    cache_miss(peak), output(peak)`` auftreten. Zusätzlich muss gelten
    ``peak >= off_peak`` pro Kategorie.

    Returns:
        Die geparste Tabelle im :data:`DEFAULT_PRICING`-Format oder ``None``,
        wenn kein sicheres Ergebnis extrahiert werden konnte. Es werden NIE
        Werte geraten. Der Aufrufer entscheidet (mit Bestätigung), ob er das
        Ergebnis übernimmt.
    """
    from urllib.request import Request, urlopen  # lokal: nur bei Bedarf laden

    try:
        request = Request(_PRICING_URL, headers={"User-Agent": "deepseek-proxy/1.0"})
        with urlopen(request, timeout=10) as response:  # noqa: S310 (feste URL)
            raw_bytes = response.read()
    except Exception as exc:  # noqa: BLE001 - jeder Netzfehler → None
        logger.warning("fetch_latest_pricing: Abruf fehlgeschlagen: %s", exc)
        return None

    try:
        html = raw_bytes.decode("utf-8", errors="replace")
    except Exception:  # pragma: no cover - decode mit replace schlägt praktisch nie fehl
        return None

    # Tags grob entfernen, um Positions-Abstände zu behalten.
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;?", " ", text)

    flash_match = _FLASH_ROW_RE.search(text)
    pro_match = _PRO_ROW_RE.search(text)
    if not flash_match or not pro_match:
        logger.info("fetch_latest_pricing: Modellzeilen nicht gefunden.")
        return None

    boundaries = sorted({flash_match.start(), pro_match.start(), len(text)})

    def _row_amounts(pos: int) -> list[float] | None:
        # Nächste Zeilengrenze bestimmen.
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
        logger.info("fetch_latest_pricing: unerwartete Anzahl Beträge pro Zeile.")
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
        logger.info("fetch_latest_pricing: Werte nicht plausibel (peak < off_peak).")
        return None

    return {"deepseek-flash": flash_entry, "deepseek-v4-pro": pro_entry}
