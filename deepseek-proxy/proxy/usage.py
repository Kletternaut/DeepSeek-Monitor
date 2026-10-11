"""Token usage data model and merging of usage objects.

The Anthropic Messages API delivers token usage spread over several SSE events
(``message_start`` already contains e.g. the input tokens, ``message_delta`` the
final output tokens). ``merge_usage`` merges these partial pieces of information
into a :class:`TokenUsage`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger("deepseek_proxy.usage")

_BERLIN = ZoneInfo("Europe/Berlin")

# Tolerated field names in the raw ``usage`` object, in priority order.
_INPUT_KEYS = ("input_tokens", "prompt_tokens")
_OUTPUT_KEYS = ("output_tokens", "completion_tokens")
_CACHE_CREATION_KEYS = ("cache_creation_input_tokens", "prompt_cache_miss_tokens")
_CACHE_READ_KEYS = ("cache_read_input_tokens", "prompt_cache_hit_tokens")
_TOTAL_KEYS = ("total_tokens",)


def now_iso_berlin() -> str:
    """Current time as ISO-8601 string with Europe/Berlin offset."""
    return datetime.now(_BERLIN).isoformat(timespec="seconds")


@dataclass
class TokenUsage:
    """Merged token usage of a request."""

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
    """Convert ``obj[key]`` safely to ``int``; ``0`` on error."""
    value = obj.get(key)
    if value is None:
        return 0
    if isinstance(value, bool):  # bool is a subclass of int -> reject explicitly
        logger.warning("Unexpected boolean token value for %r: %r", key, value)
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning("Unexpected token value for %r: %r", key, value)
        return 0


def _first_present(usage: dict, keys: tuple[str, ...]) -> str | None:
    """First key from ``keys`` that is present (not None) in ``usage``."""
    for key in keys:
        if usage.get(key) is not None:
            return key
    return None


def _apply(usage: dict, keys: tuple[str, ...], target: TokenUsage, attr: str) -> None:
    """Transfer the first present field from ``keys`` to ``target.attr``."""
    key = _first_present(usage, keys)
    if key is not None:
        setattr(target, attr, _int(usage, key))


def merge_usage(obj: dict, existing: TokenUsage) -> TokenUsage:
    """Merge the usage information from ``obj`` into ``existing``.

    Only fields that are actually present in the event are overwritten.
    ``existing`` is mutated in place and returned.
    """
    if not isinstance(obj, dict):
        return existing

    # Anthropic Messages API puts initial usage under message_start.message.usage;
    # later message_delta events provide usage at the top level. Prefer the
    # top-level object when both exist, but accept both documented shapes.
    message = obj.get("message")
    message = message if isinstance(message, dict) else {}
    usage = obj.get("usage")
    if not isinstance(usage, dict):
        nested_usage = message.get("usage")
        usage = nested_usage if isinstance(nested_usage, dict) else None

    if isinstance(usage, dict):
        _apply(usage, _INPUT_KEYS, existing, "input_tokens")
        _apply(usage, _OUTPUT_KEYS, existing, "output_tokens")
        _apply(usage, _CACHE_CREATION_KEYS, existing, "cache_creation_input_tokens")
        _apply(usage, _CACHE_READ_KEYS, existing, "cache_read_input_tokens")
        _apply(usage, _TOTAL_KEYS, existing, "total_tokens")
        existing.raw_usage = usage

    model = obj.get("model") or message.get("model")
    if model:
        existing.model = str(model)

    request_id = obj.get("request_id") or message.get("id")
    if request_id:
        existing.request_id = str(request_id)

    return existing


def extract_usage_from_event(event: dict) -> TokenUsage | None:
    """Create a :class:`TokenUsage` from a single event.

    Returns ``None`` if the event contains no ``usage`` object.
    """
    if not isinstance(event, dict):
        return None
    usage: Any = event.get("usage")
    if not isinstance(usage, dict):
        message = event.get("message")
        usage = message.get("usage") if isinstance(message, dict) else None
    if not isinstance(usage, dict):
        return None
    return merge_usage(event, TokenUsage())


def finalize_usage(usage: TokenUsage) -> TokenUsage:
    """Compute ``total_tokens`` as a fallback if it was not in the stream.

        DeepSeek does not deliver ``total_tokens`` in the Anthropic stream. Since
        ``input_tokens`` reports the cache-MISS share and ``cache_read_input_tokens``
        the cache-HIT share for DeepSeek (additive), the following holds:
        ``total_tokens = input_tokens + cache_read_input_tokens + output_tokens``.

        It is only overwritten when ``total_tokens`` is still ``0`` -- if DeepSeek
    delivers its own value in the future, it is preserved.
    """
    if usage.total_tokens == 0:
        usage.total_tokens = (
            usage.input_tokens + usage.cache_read_input_tokens + usage.output_tokens
        )
    return usage
