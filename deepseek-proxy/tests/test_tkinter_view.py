"""Tests for the pure aggregation logic build_summary (Tkinter detail page)."""

from proxy.labels import fingerprint_key
from proxy.storage import Storage
from proxy.tkinter_view import build_summary
from proxy.usage import TokenUsage, now_iso_berlin


def _seed(storage):
    ts = now_iso_berlin()
    fp_a = fingerprint_key("sk-a")
    fp_b = fingerprint_key("sk-b")
    storage.set_label(fp_a, "MeinKey")
    storage.save_usage(
        TokenUsage(
            timestamp=ts,
            input_tokens=1000,
            output_tokens=500,
            cache_read_input_tokens=400,
            model="deepseek-flash",
            request_id="r1",
            api_key_fingerprint=fp_a,
        )
    )
    storage.save_usage(
        TokenUsage(
            timestamp=ts,
            input_tokens=200,
            output_tokens=100,
            model="deepseek-v4-pro",
            request_id="r2",
            api_key_fingerprint=fp_b,
        )
    )


def test_build_summary_totals(tmp_path):
    s = Storage(str(tmp_path / "test.db"))
    _seed(s)

    summary = build_summary(s, days=365)
    assert summary["requests"] == 2
    assert summary["total_input_tokens"] == 1200
    assert summary["total_output_tokens"] == 600
    assert summary["total_cache_read_tokens"] == 400
    assert summary["total_cost"] > 0
    # Peak + Off-Peak zusammen = alle Requests
    assert summary["peak"]["requests"] + summary["off_peak"]["requests"] == 2
    assert len(summary["daily"]) == 1
    assert len(summary["per_label"]) == 2  # MeinKey + Fallback-Label


def test_build_summary_fingerprint_filter(tmp_path):
    s = Storage(str(tmp_path / "test.db"))
    _seed(s)
    fp_a = fingerprint_key("sk-a")

    summary = build_summary(s, days=365, fingerprint=fp_a)
    assert summary["requests"] == 1
    assert summary["total_input_tokens"] == 1000


def test_build_summary_empty(tmp_path):
    s = Storage(str(tmp_path / "test.db"))
    summary = build_summary(s, days=7)
    assert summary["requests"] == 0
    assert summary["total_cost"] == 0.0
    assert summary["daily"] == []
    assert summary["per_label"] == {}


def test_build_summary_consistent_with_cost_calc(tmp_path):
    """Kosten aus build_summary == Summe aus calc_usage_cost pro Event."""
    from proxy.cost_calc import calc_usage_cost
    from proxy.pricing import load_pricing
    from proxy.storage import row_to_usage

    s = Storage(str(tmp_path / "test.db"))
    _seed(s)
    summary = build_summary(s, days=365)

    pricing = load_pricing(s)
    rows = s.get_usage()
    expected = sum(calc_usage_cost(row_to_usage(r), pricing) for r in rows)

    assert summary["total_cost"] == round(expected, 8)
