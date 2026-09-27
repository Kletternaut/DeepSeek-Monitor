"""Token-Usage-Datenmodell und Zusammenführen von Usage-Objekten.

Die Anthropic Messages API liefert die Token-Nutzung verteilt über mehrere
SSE-Events (``message_start`` enthält z. B. bereits die Input-Token,
``message_delta`` die finalen Output-Token). ``merge_usage`` führt diese
Teilinformationen in einem :class:`TokenUsage` zusammen.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger("deepseek_proxy.usage")

_BERLIN = ZoneInfo("Europe/Berlin")

# Tolerierte Feldnamen im rohen ``usage``-Objekt, in Prioritätsreihenfolge.
_INPUT_KEYS = ("input_tokens", "prompt_tokens")
_OUTPUT_KEYS = ("output_tokens", "completion_tokens")
_CACHE_CREATION_KEYS = ("cache_creation_input_tokens", "prompt_cache_miss_tokens")
_CACHE_READ_KEYS = ("cache_read_input_tokens", "prompt_cache_hit_tokens")
_TOTAL_KEYS = ("total_tokens",)


def now_iso_berlin() -> str:
    """Aktuelle Zeit als ISO-8601-String mit Europe/Berlin-Offset."""
    return datetime.now(_BERLIN).isoformat(timespec="seconds")


@dataclass
class TokenUsage:
    """Zusammengeführte Token-Nutzung eines Requests."""

    timestamp: str = field(default_factory=now_iso_berlin)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    total_tokens: int = 0
    model: str = ""
    request_id: str = ""
    api_key_fingerprint: str = ""
    raw_usage: dict = field(default_factory=dict)


def _int(obj: dict, key: str) -> int:
    """Konvertiert ``obj[key]`` sicher zu ``int``; bei Fehler ``0``."""
    value = obj.get(key)
    if value is None:
        return 0
    if isinstance(value, bool):  # bool ist subklasse von int -> explizit ablehnen
        logger.warning("Unerwarteter boolescher Token-Wert für %r: %r", key, value)
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning("Unerwarteter Token-Wert für %r: %r", key, value)
        return 0


def _first_present(usage: dict, keys: tuple[str, ...]) -> str | None:
    """Erster Schlüssel aus ``keys``, der in ``usage`` vorhanden (nicht None) ist."""
    for key in keys:
        if usage.get(key) is not None:
            return key
    return None


def _apply(usage: dict, keys: tuple[str, ...], target: TokenUsage, attr: str) -> None:
    """Überträgt das erste vorhandene Feld aus ``keys`` auf ``target.attr``."""
    key = _first_present(usage, keys)
    if key is not None:
        setattr(target, attr, _int(usage, key))


def merge_usage(obj: dict, existing: TokenUsage) -> TokenUsage:
    """Führt die Usage-Informationen aus ``obj`` in ``existing`` ein.

    Nur Felder, die im Event tatsächlich vorhanden sind, werden überschrieben.
    ``existing`` wird in-place verändert und zurückgegeben.
    """
    if not isinstance(obj, dict):
        return existing

    usage = obj.get("usage")
    if isinstance(usage, dict):
        _apply(usage, _INPUT_KEYS, existing, "input_tokens")
        _apply(usage, _OUTPUT_KEYS, existing, "output_tokens")
        _apply(usage, _CACHE_CREATION_KEYS, existing, "cache_creation_input_tokens")
        _apply(usage, _CACHE_READ_KEYS, existing, "cache_read_input_tokens")
        _apply(usage, _TOTAL_KEYS, existing, "total_tokens")
        existing.raw_usage = usage
    else:
        usage = None

    # ``model`` kann top-level oder im verschachtelten ``message``-Objekt stehen.
    message = obj.get("message")
    message = message if isinstance(message, dict) else {}

    model = obj.get("model") or message.get("model")
    if model:
        existing.model = str(model)

    request_id = obj.get("request_id") or message.get("id")
    if request_id:
        existing.request_id = str(request_id)

    return existing


def extract_usage_from_event(event: dict) -> TokenUsage | None:
    """Erzeugt aus einem einzelnen Event ein :class:`TokenUsage`.

    Liefert ``None``, wenn das Event kein ``usage``-Objekt enthält.
    """
    if not isinstance(event, dict):
        return None
    usage: Any = event.get("usage")
    if not isinstance(usage, dict):
        return None
    return merge_usage(event, TokenUsage())


def finalize_usage(usage: TokenUsage) -> TokenUsage:
    """Berechnet ``total_tokens`` als Fallback, falls es nicht im Stream war.

    DeepSeek liefert im Anthropic-Stream kein ``total_tokens``. Da ``input_tokens``
    bei DeepSeek den Cache-MISS-Anteil und ``cache_read_input_tokens`` den
    Cache-HIT-Anteil melden (additiv), gilt:
    ``total_tokens = input_tokens + cache_read_input_tokens + output_tokens``.

    Es wird nur überschrieben, wenn ``total_tokens`` noch ``0`` ist -- liefert
    DeepSeek künftig einen eigenen Wert, bleibt dieser erhalten.
    """
    if usage.total_tokens == 0:
        usage.total_tokens = (
            usage.input_tokens
            + usage.cache_read_input_tokens
            + usage.output_tokens
        )
    return usage
