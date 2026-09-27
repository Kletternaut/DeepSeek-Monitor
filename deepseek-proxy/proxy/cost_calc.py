"""Cost calculation from token usage and the price table.

Computes the cost in USD for individual :class:`~proxy.usage.TokenUsage` events
and aggregates lists of events.

Note: :mod:`proxy.storage` is deliberately NOT imported here to avoid a
circular import. Prices come from :mod:`proxy.pricing`.

Public symbols:
    * :func:`calc_usage_cost`
    * :func:`calc_costs`
"""

from __future__ import annotations

import logging

from .pricing import get_tariff
from .usage import TokenUsage

logger = logging.getLogger("deepseek_proxy.cost_calc")

__all__ = ["calc_usage_cost", "calc_costs"]

#: Rounding of the USD cost (decimal places).
_COST_DECIMALS = 8


def _cache_token_split(usage: TokenUsage) -> tuple[int, int]:
    """Split into ``(cache_miss_tokens, cache_hit_tokens)``.

    IMPORTANT (DeepSeek-Anthropic peculiarity): unlike standard Anthropic,
    ``input_tokens`` here is NOT the total input, but only the NON-cached share
    (cache MISS). ``cache_read_input_tokens`` is the cached share (cache HIT).
    Both are additive, not nested.

    Evidence (real data): ``cache_read_input_tokens`` (≈181k) can clearly
exceed ``input_tokens`` (≈8k) -- only possible if ``input_tokens`` is not the
total. ``cache_creation_input_tokens`` is observed to always be 0 for DeepSeek
(included in ``input_tokens``).
    """
    miss_tokens = max(0, int(usage.input_tokens))
    hit_tokens = max(0, int(usage.cache_read_input_tokens))
    return miss_tokens, hit_tokens


def calc_usage_cost(usage: TokenUsage, pricing: dict | None = None) -> float:
    """Compute the cost of a request in USD.

    ``cost = (miss * cache_miss + hit * cache_hit + output * output) / 1e6``.
    """
    tariff = get_tariff(usage.model, usage.timestamp, pricing)
    miss_tokens, hit_tokens = _cache_token_split(usage)

    cost = (
        miss_tokens * tariff["cache_miss"]
        + hit_tokens * tariff["cache_hit"]
        + max(0, int(usage.output_tokens)) * tariff["output"]
    ) / 1_000_000

    return round(cost, _COST_DECIMALS)


def calc_costs(usages: list[TokenUsage], pricing: dict | None = None) -> dict:
    """Aggregate the cost over a list of usage events.

    Returns:
        Dict with ``total_cost``, ``total_input_tokens``, ``total_output_tokens``,
        ``total_cache_read_tokens``, ``total_cache_creation_tokens``,
        ``requests`` and ``per_model`` (mapping model name → ``cost``,
        ``input_tokens``, ``output_tokens``, ``requests``).
    """
    total_cost = 0.0
    total_input = 0
    total_output = 0
    total_cache_read = 0
    total_cache_creation = 0
    per_model: dict[str, dict] = {}

    for usage in usages:
        cost = calc_usage_cost(usage, pricing)
        total_cost += cost
        total_input += int(usage.input_tokens)
        total_output += int(usage.output_tokens)
        total_cache_read += int(usage.cache_read_input_tokens)
        total_cache_creation += int(usage.cache_creation_input_tokens)

        key = (usage.model or "").strip() or "unknown"
        entry = per_model.setdefault(
            key,
            {"cost": 0.0, "input_tokens": 0, "output_tokens": 0, "requests": 0},
        )
        entry["cost"] = round(entry["cost"] + cost, _COST_DECIMALS)
        entry["input_tokens"] += int(usage.input_tokens)
        entry["output_tokens"] += int(usage.output_tokens)
        entry["requests"] += 1

    return {
        "total_cost": round(total_cost, _COST_DECIMALS),
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_cache_read_tokens": total_cache_read,
        "total_cache_creation_tokens": total_cache_creation,
        "requests": len(usages),
        "per_model": per_model,
    }
