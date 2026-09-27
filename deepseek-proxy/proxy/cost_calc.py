"""Kostenberechnung aus Token-Nutzung und Preis-Tabelle.

Berechnet für einzelne :class:`~proxy.usage.TokenUsage`-Events die Kosten in USD
und aggregiert Listen von Events.

Hinweis: :mod:`proxy.storage` wird hier absichtlich NICHT importiert, um einen
Zirkularimport zu vermeiden. Preise kommen aus :mod:`proxy.pricing`.

Öffentliche Symbole:
    * :func:`calc_usage_cost`
    * :func:`calc_costs`
"""

from __future__ import annotations

import logging

from .pricing import get_tariff
from .usage import TokenUsage

logger = logging.getLogger("deepseek_proxy.cost_calc")

__all__ = ["calc_usage_cost", "calc_costs"]

#: Rundung der USD-Kosten (Nachkommastellen).
_COST_DECIMALS = 8


def _cache_token_split(usage: TokenUsage) -> tuple[int, int]:
    """Aufteilung in ``(cache_miss_tokens, cache_hit_tokens)``.

    WICHTIG (DeepSeek-Anthropic-Eigenheit): Anders als bei Standard-Anthropic ist
    ``input_tokens`` hier NICHT die Gesamt-Eingabe, sondern nur der NICHT
    gecachte Anteil (Cache MISS). ``cache_read_input_tokens`` ist der gecachte
    Anteil (Cache HIT). Beide sind additiv, nicht verschachtelt.

    Beleg (reale Daten): ``cache_read_input_tokens`` (≈181k) kann ``input_tokens``
    (≈8k) deutlich übersteigen -- nur möglich, wenn ``input_tokens`` nicht die
    Gesamtsumme ist. ``cache_creation_input_tokens`` ist bei DeepSeek beobachtet
    immer 0 (wird in ``input_tokens`` eingerechnet).
    """
    miss_tokens = max(0, int(usage.input_tokens))
    hit_tokens = max(0, int(usage.cache_read_input_tokens))
    return miss_tokens, hit_tokens


def calc_usage_cost(usage: TokenUsage, pricing: dict | None = None) -> float:
    """Berechnet die Kosten eines Requests in USD.

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
    """Aggregiert die Kosten über eine Liste von Usage-Events.

    Returns:
        Dict mit ``total_cost``, ``total_input_tokens``, ``total_output_tokens``,
        ``total_cache_read_tokens``, ``total_cache_creation_tokens``,
        ``requests`` und ``per_model`` (Mapping Modellname → ``cost``,
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
