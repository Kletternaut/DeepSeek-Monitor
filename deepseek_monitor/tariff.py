"""DeepSeek Monitor — tariff logic (peak / standard / weekend).

Computes the current tariff status from the UTC time and the Beijing weekend
rule, plus tariff periods/boundaries. No Tkinter dependency.

Public symbols:
    * :func:`status_at`
    * :func:`_all_tariff_boundaries_around`
    * :func:`next_status_change`
    * :func:`get_rate_periods`
    * :func:`weekend_segments_utc`
    * :func:`calculate_status`
"""

from __future__ import annotations

import datetime as dt

from deepseek_monitor.config import (
    BJ_TZ,
    PEAK_WINDOWS_UTC,
    STATUS_COLORS,
    UTC,
    WEEKEND_RULE_START_UTC,
)


def status_at(now_utc: dt.datetime) -> str:
    """
    Return STANDARD, PEAK or WEEKEND.
    The status is derivable from UTC time + the Beijing weekend rule alone.
    """
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=UTC)

    bj_now = now_utc.astimezone(BJ_TZ)

    if (
        now_utc >= WEEKEND_RULE_START_UTC
        and bj_now.weekday() >= 5
    ):
        return "WEEKEND"

    current_minute = now_utc.hour * 60 + now_utc.minute

    for start_minute, end_minute in PEAK_WINDOWS_UTC:
        if start_minute <= current_minute < end_minute:
            return "PEAK"

    return "STANDARD"


def _all_tariff_boundaries_around(
    now_utc: dt.datetime,
    days: int = 3,
) -> list[dt.datetime]:
    """
    Create possible tariff boundaries in a small window.
    This list is small and is not fully analyzed every second.
    """
    boundaries: set[dt.datetime] = set()

    start_date = now_utc.date() - dt.timedelta(days=days)
    end_date = now_utc.date() + dt.timedelta(days=days)

    cursor = start_date
    while cursor <= end_date:
        for hour in (1, 4, 6, 10):
            boundaries.add(
                dt.datetime(
                    cursor.year,
                    cursor.month,
                    cursor.day,
                    hour,
                    tzinfo=UTC,
                )
            )
        cursor += dt.timedelta(days=1)

    bj_date = now_utc.astimezone(BJ_TZ).date() - dt.timedelta(days=days)

    while bj_date <= now_utc.astimezone(BJ_TZ).date() + dt.timedelta(days=days):
        local_midnight = dt.datetime(
            bj_date.year,
            bj_date.month,
            bj_date.day,
            tzinfo=BJ_TZ,
        )
        boundaries.add(local_midnight.astimezone(UTC))
        bj_date += dt.timedelta(days=1)

    boundaries.add(WEEKEND_RULE_START_UTC)

    return sorted(boundaries)


def next_status_change(now_utc: dt.datetime) -> dt.datetime:
    """
    Find the next boundary at which the actually visible status changes.
    This avoids false warnings at peak edges on weekends, which are already
    masked by WEEKEND.
    """
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=UTC)

    before_delta = dt.timedelta(milliseconds=10)

    for boundary in _all_tariff_boundaries_around(now_utc):
        if boundary <= now_utc:
            continue

        before = status_at(boundary - before_delta)
        after = status_at(boundary + before_delta)

        if before != after:
            return boundary

    return now_utc + dt.timedelta(days=3)


def get_rate_periods(
    start_utc: dt.datetime,
    end_utc: dt.datetime,
) -> list[tuple[dt.datetime, dt.datetime, str]]:
    """
    Gap-free tariff periods within [start_utc, end_utc].
    Primarily used by analytics, not by the 1-second GUI loop.
    """
    if start_utc.tzinfo is None:
        start_utc = start_utc.replace(tzinfo=UTC)
    if end_utc.tzinfo is None:
        end_utc = end_utc.replace(tzinfo=UTC)

    if end_utc <= start_utc:
        return []

    boundaries = {
        start_utc,
        end_utc,
    }

    for boundary in _all_tariff_boundaries_around(start_utc, days=2):
        if start_utc < boundary < end_utc:
            boundaries.add(boundary)

    points = sorted(boundaries)
    periods: list[tuple[dt.datetime, dt.datetime, str]] = []

    for a, b in zip(points, points[1:]):
        if b <= a:
            continue

        mid = a + (b - a) / 2
        periods.append((a, b, status_at(mid)))

    if not periods:
        return [(start_utc, end_utc, status_at(start_utc))]

    merged: list[tuple[dt.datetime, dt.datetime, str]] = []
    for a, b, status in periods:
        if merged and merged[-1][2] == status:
            merged[-1] = (merged[-1][0], b, status)
        else:
            merged.append((a, b, status))

    return merged


def weekend_segments_utc(
    day_start_utc: dt.datetime,
    day_end_utc: dt.datetime,
) -> list[tuple[dt.datetime, dt.datetime]]:
    return [
        (a, b)
        for a, b, status in get_rate_periods(
            day_start_utc,
            day_end_utc,
        )
        if status == "WEEKEND"
    ]


def calculate_status(
    now_utc: dt.datetime,
) -> tuple[str, float, str, str]:
    key = status_at(now_utc)
    next_change = next_status_change(now_utc)
    seconds_left = max(
        0.0,
        (next_change - now_utc).total_seconds(),
    )

    text = (
        "WEEKEND OFF-PEAK"
        if key == "WEEKEND"
        else key
    )

    return text, seconds_left, STATUS_COLORS[key], key
