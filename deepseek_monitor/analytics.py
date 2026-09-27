"""DeepSeek Monitor — usage analysis.

Aggregates usage/cost data from the local proxy (``/api/usage``) or the central
InfluxDB into daily/zone breakdowns. No Tkinter dependency. The mutable InfluxDB
globals are read via ``config.<name>`` so runtime changes are preserved.

Public symbols:
    * :func:`_parse_history`, :func:`_split_at_local_midnights`
    * :func:`analyze_history`
    * :func:`fetch_proxy_usage`, :func:`fetch_influx_usage`,
      :func:`fetch_influx_hosts`, :func:`fetch_usage`
    * :func:`analyze_proxy_usage`
    * :func:`_parse_flux_annotated_csv`, :func:`_flux_row_to_event`
    * :func:`_influx_query`
    * :func:`_shade_hex`, :func:`format_money`
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import urllib.parse
import urllib.request
import zoneinfo
from collections import OrderedDict
from typing import Any

from deepseek_monitor import config
from deepseek_monitor.api import currency_symbol
from deepseek_monitor.tariff import get_rate_periods, status_at

UTC = config.UTC
ANALYTICS_DAYS = config.ANALYTICS_DAYS
ANALYTICS_INTERVAL_LIMIT = config.ANALYTICS_INTERVAL_LIMIT
PEAK_MULTIPLIER = config.PEAK_MULTIPLIER
PROXY_USAGE_URL = config.PROXY_USAGE_URL


def _parse_history(
    records: list[dict[str, Any]],
) -> list[tuple[dt.datetime, float, str]]:
    parsed: list[tuple[dt.datetime, float, str]] = []

    for record in records:
        try:
            timestamp = dt.datetime.fromisoformat(record["ts"])
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)

            total = float(record.get("total", 0.0))
            currency = str(record.get("currency", "") or "")

            parsed.append(
                (
                    timestamp.astimezone(UTC),
                    total,
                    currency,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue

    parsed.sort(key=lambda item: item[0])
    return parsed


def _split_at_local_midnights(
    start_utc: dt.datetime,
    end_utc: dt.datetime,
    tz: zoneinfo.ZoneInfo,
) -> list[tuple[dt.datetime, dt.datetime]]:
    """Split an interval additionally at local midnights."""
    if end_utc <= start_utc:
        return []

    boundaries = {start_utc, end_utc}

    local_start = start_utc.astimezone(tz).date() - dt.timedelta(days=1)
    local_end = end_utc.astimezone(tz).date() + dt.timedelta(days=1)

    cursor = local_start
    while cursor <= local_end:
        local_midnight = dt.datetime(
            cursor.year,
            cursor.month,
            cursor.day,
            tzinfo=tz,
        )
        boundary_utc = local_midnight.astimezone(UTC)

        if start_utc < boundary_utc < end_utc:
            boundaries.add(boundary_utc)

        cursor += dt.timedelta(days=1)

    points = sorted(boundaries)
    return list(zip(points, points[1:]))


def analyze_history(
    records: list[dict[str, Any]],
    tz_name: str,
    days: int = ANALYTICS_DAYS,
    max_gap_s: float | None = None,
) -> dict[str, Any]:
    """
    Analyze usage as balance loss.

    Assumption:
    Usage between two measurements is distributed proportionally over the
    tariff intervals between the measurement points.
    """
    tz = zoneinfo.ZoneInfo(tz_name)
    max_gap_s = float(max_gap_s) if max_gap_s is not None else config.MAX_ANALYTICS_GAP_S
    now_utc = dt.datetime.now(UTC)
    start_utc = now_utc - dt.timedelta(days=days)

    parsed = _parse_history(records)

    daily: OrderedDict[str, dict[str, float]] = OrderedDict()
    intervals: list[dict[str, Any]] = []

    total_usage = 0.0
    peak_usage = 0.0
    standard_usage = 0.0
    weekend_usage = 0.0

    currency = ""

    for prev, curr in zip(parsed, parsed[1:]):
        prev_ts, prev_total, prev_currency = prev
        curr_ts, curr_total, curr_currency = curr

        if curr_currency:
            currency = curr_currency

        if curr_ts <= prev_ts:
            continue

        raw_usage = prev_total - curr_total

        # Top-ups / positive account movements are not counted as usage.
        if raw_usage <= 0:
            continue

        interval_start = max(prev_ts, start_utc)
        interval_end = min(curr_ts, now_utc)

        if interval_end <= interval_start:
            continue

        raw_duration_s = (
            curr_ts - prev_ts
        ).total_seconds()

        clipped_duration_s = (
            interval_end - interval_start
        ).total_seconds()

        if raw_duration_s <= 0:
            continue

        # Proportional usage only for the visible analytics window.
        visible_usage = (
            raw_usage
            * clipped_duration_s
            / raw_duration_s
        )

        if visible_usage <= 0:
            continue

        segments = get_rate_periods(
            interval_start,
            interval_end,
        )

        segment_total_s = sum(
            (b - a).total_seconds()
            for a, b, _ in segments
        )

        if segment_total_s <= 0:
            continue

        interval_rows: list[dict[str, Any]] = []

        for seg_start, seg_end, status in segments:
            for day_start, day_end in _split_at_local_midnights(
                seg_start,
                seg_end,
                tz,
            ):
                duration_s = (
                    day_end - day_start
                ).total_seconds()

                amount = (
                    visible_usage
                    * duration_s
                    / segment_total_s
                )

                local_start = day_start.astimezone(tz)
                local_end = day_end.astimezone(tz)

                day_key = local_start.date().isoformat()

                row = daily.setdefault(
                    day_key,
                    {
                        "usage": 0.0,
                        "peak_usage": 0.0,
                        "standard_usage": 0.0,
                        "weekend_usage": 0.0,
                        "avoidable_savings": 0.0,
                    },
                )

                row["usage"] += amount

                if status == "PEAK":
                    peak_usage += amount
                    row["peak_usage"] += amount

                    # Balance loss = actual peak cost.
                    # At 2x the avoidable premium is 50% of that.
                    savings = (
                        amount
                        * (1.0 - 1.0 / PEAK_MULTIPLIER)
                        if PEAK_MULTIPLIER > 0
                        else 0.0
                    )

                    row["avoidable_savings"] += savings
                elif status == "WEEKEND":
                    weekend_usage += amount
                    row["weekend_usage"] += amount
                else:
                    standard_usage += amount
                    row["standard_usage"] += amount

                interval_rows.append(
                {
                    "start": local_start,
                    "end": local_end,
                    "status": status,
                    "amount": amount,
                }
            )

        gap = raw_duration_s > max_gap_s

        # For the detail view only real visible usage intervals.
        if interval_rows:
            intervals.append(
                {
                    "start": interval_rows[0]["start"],
                    "end": interval_rows[-1]["end"],
                    "duration_s": clipped_duration_s,
                    "amount": visible_usage,
                    "gap": gap,
                    "parts": interval_rows,
                }
            )

        total_usage += visible_usage

    daily_rows: list[dict[str, Any]] = []

    for date_key, row in daily.items():
        daily_rows.append(
            {
                "date": date_key,
                "usage": round(row["usage"], 6),
                "peak_usage": round(row["peak_usage"], 6),
                "standard_usage": round(row["standard_usage"], 6),
                "weekend_usage": round(row["weekend_usage"], 6),
                "avoidable_savings": round(
                    row["avoidable_savings"],
                    6,
                ),
            }
        )

    # Gap-free day series: days without usage also get an empty entry
    # (usage = 0), so they appear in the charts as an empty slot with a date.
    rows_by_date = {
        row["date"]: row
        for row in daily_rows
    }

    local_today = now_utc.astimezone(tz).date()
    daily_rows = []
    for offset in range(days, -1, -1):
        day_key = (
            local_today
            - dt.timedelta(days=offset)
        ).isoformat()

        row = rows_by_date.get(day_key)
        if row is None:
            row = {
                "date": day_key,
                "usage": 0.0,
                "peak_usage": 0.0,
                "standard_usage": 0.0,
                "weekend_usage": 0.0,
                "avoidable_savings": 0.0,
            }
        daily_rows.append(row)

    avoidable_savings = (
        peak_usage
        * (1.0 - 1.0 / PEAK_MULTIPLIER)
        if PEAK_MULTIPLIER > 0
        else 0.0
    )

    peak_share = (
        peak_usage / total_usage
        if total_usage > 0
        else 0.0
    )

    intervals.sort(
        key=lambda item: item["end"],
        reverse=True,
    )

    return {
        "days": days,
        "currency": currency,
        "usage": round(total_usage, 6),
        "peak_usage": round(peak_usage, 6),
        "standard_usage": round(standard_usage, 6),
        "weekend_usage": round(weekend_usage, 6),
        "peak_share": peak_share,
        "avoidable_savings": round(
            avoidable_savings,
            6,
        ),
        "daily": daily_rows,
        "intervals": intervals[:ANALYTICS_INTERVAL_LIMIT],
    }


def fetch_proxy_usage(days: int = ANALYTICS_DAYS) -> list[dict[str, Any]]:
    """Fetch the real recorded usage events of the proxy.

    The proxy persists one usage entry per request and exposes it under
    ``GET /api/usage``. The ``start`` parameter is formatted in Europe/Berlin
    because the storage timestamp comparison runs on Berlin ISO strings (same
    format as the database).
    """
    berlin = zoneinfo.ZoneInfo("Europe/Berlin")
    start = (
        dt.datetime.now(berlin)
        - dt.timedelta(days=max(0, int(days)))
    ).isoformat(timespec="seconds")

    url = f"{PROXY_USAGE_URL}?start={urllib.parse.quote(start)}"

    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json"},
        method="GET",
    )

    with urllib.request.urlopen(request, timeout=10) as response:
        raw = response.read().decode("utf-8")
        data = json.loads(raw)

    if not isinstance(data, list):
        return []

    return [item for item in data if isinstance(item, dict)]


def _parse_flux_annotated_csv(raw: str) -> list[dict[str, str]]:
    """Parse the annotated-CSV response format of the InfluxDB 2.x query API.

    Comment lines (``#``) are skipped; the first regular line is the header.
    Each data line becomes a ``column -> value`` dict.
    """
    rows: list[dict[str, str]] = []
    header: list[str] | None = None

    for line in raw.splitlines():
        if not line:
            continue
        if line.startswith("#"):
            continue
        fields = next(csv.reader([line]))
        if header is None:
            header = fields
            continue
        if len(fields) != len(header):
            continue
        rows.append(dict(zip(header, fields)))

    return rows


def _flux_row_to_event(row: dict[str, str]) -> dict[str, Any] | None:
    """Map a pivoted Flux row to an event dict analogous to ``/api/usage``.

    The format matches the proxy /api/usage structure so
    :func:`analyze_proxy_usage` can be reused unchanged.
    """
    def as_int(key: str) -> int:
        try:
            return int(float(row.get(key) or 0.0))
        except (TypeError, ValueError):
            return 0

    def as_float(key: str) -> float:
        try:
            return float(row.get(key) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    timestamp = row.get("_time", "")
    if timestamp.endswith("Z"):
        timestamp = timestamp[:-1] + "+00:00"

    return {
        "timestamp": timestamp,
        "model": row.get("model", "") or "unknown",
        "tariff": row.get("tariff", "") or "off_peak",
        "fingerprint": row.get("fingerprint", "") or "",
        "host": row.get("host", "") or "",
        "input_tokens": as_int("input_tokens"),
        "output_tokens": as_int("output_tokens"),
        "cache_read_tokens": as_int("cache_read_tokens"),
        "cache_creation_tokens": as_int("cache_creation_tokens"),
        "total_tokens": as_int("total_tokens"),
        "cost_usd": as_float("cost_usd"),
    }


def _influx_query(flux: str) -> list[dict[str, str]]:
    """Run a Flux query against the InfluxDB 2.x query API.

    Returns the parsed annotated-CSV lines as a list of dicts.
    """
    url = f"{config.INFLUX_URL}/api/v2/query?org={urllib.parse.quote(config.INFLUX_ORG)}"

    body = json.dumps(
        {
            "query": flux,
            "dialect": {
                "header": True,
                "delimiter": ",",
                "annotations": ["datatype"],
            },
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Accept": "application/csv",
            "Content-Type": "application/json",
            "Authorization": f"Token {config.INFLUX_TOKEN}",
        },
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=15) as response:
        raw = response.read().decode("utf-8")

    return _parse_flux_annotated_csv(raw)


def fetch_influx_usage(
    days: int = ANALYTICS_DAYS,
    host: str | None = None,
) -> list[dict[str, Any]]:
    """Read usage events directly from the central InfluxDB 2.x.

    Uses the HTTP query API (``POST /api/v2/query``) with Flux and ``pivot`` so
each event row comes back as one row. No extra dependency -- pure ``urllib`` +
stdlib CSV parsing.

    Parameters:
        host: Optional filter on the ``host`` tag (empty/None = all hosts).

    Returns an empty list if InfluxDB is not configured
    (``DEEPSEEK_INFLUX_*`` missing).
    """
    if not config.INFLUX_ENABLED:
        return []

    query = (
        f'from(bucket: "{config.INFLUX_BUCKET}")'
        f"  |> range(start: -{max(0, int(days))}d)"
        '  |> filter(fn: (r) => r._measurement == "usage")'
    )
    if host:
        query += f'\n  |> filter(fn: (r) => r.host == "{host}")'
    query += '  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")'

    rows = _influx_query(query)

    events: list[dict[str, Any]] = []
    for row in rows:
        event = _flux_row_to_event(row)
        if event:
            events.append(event)

    return events


def fetch_influx_hosts() -> list[str]:
    """Return all ``host`` tag values seen so far in InfluxDB."""
    if not config.INFLUX_ENABLED:
        return []

    flux = (
        'import "influxdata/influxdb/schema"'
        f'\nschema.tagValues(bucket: "{config.INFLUX_BUCKET}", tag: "host")'
    )

    rows = _influx_query(flux)

    hosts: list[str] = []
    for row in rows:
        value = (row.get("_value") or "").strip()
        if value and value not in hosts:
            hosts.append(value)

    return sorted(hosts)


def fetch_usage(
    days: int = ANALYTICS_DAYS,
    host: str | None = None,
) -> list[dict[str, Any]]:
    """Dispatcher: InfluxDB (if configured) otherwise local proxy ``/api/usage``."""
    if config.INFLUX_ENABLED:
        return fetch_influx_usage(days, host=host)
    return fetch_proxy_usage(days)


def analyze_proxy_usage(
    events: list[dict[str, Any]],
    tz_name: str,
    days: int = ANALYTICS_DAYS,
) -> dict[str, Any]:
    """Aggregate real proxy usage events into the same structure as
    :func:`analyze_history`, so the existing presentation can be reused
    unchanged.

    Unlike the balance-delta estimate, this uses the really calculated cost
    (``cost_usd``) per request. The zone is determined per event via the
existing :func:`status_at` (PEAK/STANDARD/WEEKEND).
    """
    tz = zoneinfo.ZoneInfo(tz_name)
    now_utc = dt.datetime.now(UTC)
    start_utc = now_utc - dt.timedelta(days=days)

    daily: OrderedDict[str, dict[str, float]] = OrderedDict()
    intervals: list[dict[str, Any]] = []

    total_usage = 0.0
    peak_usage = 0.0
    standard_usage = 0.0
    weekend_usage = 0.0

    for event in events:
        try:
            timestamp = dt.datetime.fromisoformat(
                str(event.get("timestamp") or "")
            )
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            ts_utc = timestamp.astimezone(UTC)
        except (TypeError, ValueError):
            continue

        # Filter exactly to the visible window (protects against DST fuzz of the
        # pure string comparison in the storage).
        if not (start_utc <= ts_utc <= now_utc):
            continue

        try:
            amount = float(event.get("cost_usd") or 0.0)
        except (TypeError, ValueError):
            amount = 0.0

        if amount <= 0:
            continue

        status = status_at(ts_utc)
        local = ts_utc.astimezone(tz)
        day_key = local.date().isoformat()
        host = str(event.get("host") or "").strip() or "unknown"

        row = daily.setdefault(
            day_key,
            {
                "usage": 0.0,
                "peak_usage": 0.0,
                "standard_usage": 0.0,
                "weekend_usage": 0.0,
                "avoidable_savings": 0.0,
            },
        )

        row["usage"] += amount
        row_zones = row.setdefault("_zone_hosts", {})
        zone_hosts = row_zones.setdefault(status, {})
        zone_hosts[host] = zone_hosts.get(host, 0.0) + amount

        if status == "PEAK":
            peak_usage += amount
            row["peak_usage"] += amount
            savings = (
                amount * (1.0 - 1.0 / PEAK_MULTIPLIER)
                if PEAK_MULTIPLIER > 0
                else 0.0
            )
            row["avoidable_savings"] += savings
        elif status == "WEEKEND":
            weekend_usage += amount
            row["weekend_usage"] += amount
        else:
            standard_usage += amount
            row["standard_usage"] += amount

        total_usage += amount

        intervals.append(
            {
                "start": local,
                "end": local,
                "duration_s": 0.0,
                "amount": amount,
                "gap": False,
                "parts": [
                    {
                        "start": local,
                        "end": local,
                        "status": status,
                        "amount": amount,
                    }
                ],
            }
        )

    daily_rows: list[dict[str, Any]] = []

    for date_key, row in daily.items():
        zone_hosts: dict[str, dict[str, float]] = {}
        for zone, hosts in row.get("_zone_hosts", {}).items():
            zone_hosts[zone] = {
                h: round(a, 6)
                for h, a in sorted(
                    hosts.items(), key=lambda kv: kv[1], reverse=True
                )
            }
        daily_rows.append(
            {
                "date": date_key,
                "usage": round(row["usage"], 6),
                "peak_usage": round(row["peak_usage"], 6),
                "standard_usage": round(row["standard_usage"], 6),
                "weekend_usage": round(row["weekend_usage"], 6),
                "avoidable_savings": round(row["avoidable_savings"], 6),
                "zone_hosts": zone_hosts,
            }
        )

    rows_by_date = {row["date"]: row for row in daily_rows}

    local_today = now_utc.astimezone(tz).date()
    daily_rows = []
    for offset in range(days, -1, -1):
        day_key = (local_today - dt.timedelta(days=offset)).isoformat()
        row = rows_by_date.get(day_key)
        if row is None:
            row = {
                "date": day_key,
                "usage": 0.0,
                "peak_usage": 0.0,
                "standard_usage": 0.0,
                "weekend_usage": 0.0,
                "avoidable_savings": 0.0,
                "zone_hosts": {},
            }
        daily_rows.append(row)

    avoidable_savings = (
        peak_usage * (1.0 - 1.0 / PEAK_MULTIPLIER)
        if PEAK_MULTIPLIER > 0
        else 0.0
    )

    peak_share = (
        peak_usage / total_usage
        if total_usage > 0
        else 0.0
    )

    intervals.sort(key=lambda item: item["end"], reverse=True)

    return {
        "days": days,
        "currency": "USD",
        "usage": round(total_usage, 6),
        "peak_usage": round(peak_usage, 6),
        "standard_usage": round(standard_usage, 6),
        "weekend_usage": round(weekend_usage, 6),
        "peak_share": peak_share,
        "avoidable_savings": round(avoidable_savings, 6),
        "daily": daily_rows,
        "intervals": intervals[:ANALYTICS_INTERVAL_LIMIT],
    }


def _shade_hex(color: str, factor: float) -> str:
    """Scale a ``#RRGGBB`` color by ``factor`` (1.0 = unchanged,
    < 1.0 = darker, > 1.0 = lighter). The result is clamped to the valid
    range and returned as ``#RRGGBB`` again."""
    color = color.lstrip("#")
    if len(color) != 6:
        return "#" + color
    try:
        r, g, b = (int(color[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return "#" + color
    r = max(0, min(255, int(r * factor)))
    g = max(0, min(255, int(g * factor)))
    b = max(0, min(255, int(b * factor)))
    return f"#{r:02x}{g:02x}{b:02x}"


def format_money(
    value: float,
    currency: str,
    language: str = "en",
    decimals: int = 4,
) -> str:
    symbol = currency_symbol(currency)

    if language in {"de", "fr", "es", "it", "pt", "nl", "pl", "ru"}:
        number = (
            f"{value:,.{decimals}f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )
    else:
        number = f"{value:,.{decimals}f}"

    return f"{symbol}{number}".strip() if symbol else number
