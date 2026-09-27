"""Tests for Storage (SQLite) and labels (fingerprint + label resolution)."""

import hashlib

from proxy.labels import fingerprint_key, resolve_label
from proxy.storage import Storage
from proxy.usage import TokenUsage


def test_fingerprint_key_deterministic_and_full_hex():
    fp = fingerprint_key("sk-abc")
    assert fp == hashlib.sha256(b"sk-abc").hexdigest()
    assert len(fp) == 64
    # deterministisch
    assert fp == fingerprint_key("sk-abc")


def test_fingerprint_key_empty():
    assert fingerprint_key("") == ""
    assert fingerprint_key(None) == ""  # type: ignore


def test_fingerprint_distinguishes_keys():
    assert fingerprint_key("sk-a") != fingerprint_key("sk-b")


def _mk_usage(input_tokens=10, output_tokens=5, fingerprint="", model="m", request_id="r"):
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=2,
        cache_creation_input_tokens=3,
        total_tokens=input_tokens + output_tokens,
        model=model,
        request_id=request_id,
        api_key_fingerprint=fingerprint,
        raw_usage={"input_tokens": input_tokens},
    )


class TestStorage:
    def test_save_and_get_usage(self, tmp_path):
        s = Storage(str(tmp_path / "test.db"))
        s.save_usage(_mk_usage(fingerprint="fp1", model="deepseek-flash"))
        s.save_usage(_mk_usage(fingerprint="fp2", output_tokens=99))

        rows = s.get_usage()
        assert len(rows) == 2

        fp1 = s.get_usage(fingerprint="fp1")
        assert len(fp1) == 1
        assert fp1[0]["api_key_fingerprint"] == "fp1"
        assert fp1[0]["model"] == "deepseek-flash"
        assert fp1[0]["input_tokens"] == 10
        assert fp1[0]["output_tokens"] == 5
        assert fp1[0]["cache_creation_tokens"] == 3
        assert fp1[0]["cache_read_tokens"] == 2

    def test_get_usage_limit_and_order(self, tmp_path):
        s = Storage(str(tmp_path / "test.db"))
        for i in range(5):
            s.save_usage(_mk_usage(request_id=f"r{i}"))
        rows = s.get_usage(limit=2)
        assert len(rows) == 2
        # neueste zuerst
        assert rows[0]["request_id"] == "r4"
        assert rows[1]["request_id"] == "r3"

    def test_get_daily_usage_aggregates(self, tmp_path):
        s = Storage(str(tmp_path / "test.db"))
        # Zwei Events mit unterschiedlichen Tokens am selben Tag.
        s.save_usage(_mk_usage(input_tokens=10, output_tokens=5))
        s.save_usage(_mk_usage(input_tokens=20, output_tokens=15))
        daily = s.get_daily_usage(days=7)
        assert len(daily) == 1
        d = daily[0]
        assert d["input_tokens"] == 30
        assert d["output_tokens"] == 20
        assert d["requests"] == 2

    def test_get_daily_usage_fingerprint_filter(self, tmp_path):
        s = Storage(str(tmp_path / "test.db"))
        s.save_usage(_mk_usage(input_tokens=10, fingerprint="fpA"))
        s.save_usage(_mk_usage(input_tokens=100, fingerprint="fpB"))
        daily = s.get_daily_usage(fingerprint="fpA")
        assert len(daily) == 1
        assert daily[0]["input_tokens"] == 10

    def test_distinct_fingerprints(self, tmp_path):
        s = Storage(str(tmp_path / "test.db"))
        s.save_usage(_mk_usage(fingerprint="fpA"))
        s.save_usage(_mk_usage(fingerprint="fpB"))
        s.save_usage(_mk_usage(fingerprint="fpA"))
        assert sorted(s.get_distinct_fingerprints()) == ["fpA", "fpB"]


class TestLabels:
    def test_set_get_labels(self, tmp_path):
        s = Storage(str(tmp_path / "test.db"))
        fp = fingerprint_key("sk-secret")
        assert s.get_label(fp) is None
        s.set_label(fp, "Mein Label")
        assert s.get_label(fp) == "Mein Label"
        assert s.get_labels() == {fp: "Mein Label"}

    def test_resolve_label_fallback(self, tmp_path):
        s = Storage(str(tmp_path / "test.db"))
        fp = fingerprint_key("sk-xyz")
        # kein Label gesetzt -> Fallback key-<erste 8 Zeichen>
        assert resolve_label(s, fp) == f"key-{fp[:8]}"
        s.set_label(fp, "Custom")
        assert resolve_label(s, fp) == "Custom"

    def test_resolve_label_unknown_fingerprint(self, tmp_path):
        s = Storage(str(tmp_path / "test.db"))
        assert resolve_label(s, "") == "key-unknown"


class TestSettings:
    def test_settings_roundtrip(self, tmp_path):
        s = Storage(str(tmp_path / "test.db"))
        assert s.get_setting("nope", "dflt") == "dflt"
        s.set_setting("currency", "CNY")
        assert s.get_setting("currency") == "CNY"
