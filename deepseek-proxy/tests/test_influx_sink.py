"""Tests for the InfluxDB sink (writing) and the Influx read helpers.

The read helpers (Flux CSV parser + event mapping) are pure functions and are
tested without network. The sink is tested with a mocked
``influxdb_client`` so no real connection is needed.
"""

from __future__ import annotations

import os
import sys
from types import ModuleType
from unittest import mock

from proxy.usage import TokenUsage


# ---------------------------------------------------------------------------
# Flux-annotated-CSV-Parser + Event-Mapping
# ---------------------------------------------------------------------------

def _load_read_helpers() -> tuple:
    """Load the pure read functions from deepseek_monitor.analytics."""
    repo_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..")
    )
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    from deepseek_monitor.analytics import (
        _flux_row_to_event,
        _parse_flux_annotated_csv,
    )

    return _parse_flux_annotated_csv, _flux_row_to_event


def _parse_csv():
    parse, _map = _load_read_helpers()
    return parse


def _row_to_event():
    _parse, mapper = _load_read_helpers()
    return mapper


def test_parse_flux_csv_skips_annotations():
    parse = _parse_csv()
    raw = (
        "#datatype,string,long,string\n"
        "#group,false,false,false\n"
        "_time,input_tokens,cost_usd\n"
        "2026-09-25T16:47:30.000000000Z,10,0.000123\n"
        "2026-09-25T17:00:00.000000000Z,20,0.000456\n"
    )
    rows = parse(raw)
    assert len(rows) == 2
    assert rows[0]["_time"] == "2026-09-25T16:47:30.000000000Z"
    assert rows[0]["input_tokens"] == "10"


def test_parse_flux_csv_empty_and_blanks():
    parse = _parse_csv()
    assert parse("") == []
    assert parse("#datatype,string\n") == []


def test_flux_row_to_event_maps_fields():
    mapper = _row_to_event()
    row = {
        "_time": "2026-09-25T16:47:30.000000000Z",
        "host": "raspi5",
        "model": "deepseek-flash",
        "fingerprint": "abc",
        "tariff": "off_peak",
        "input_tokens": "10",
        "output_tokens": "20",
        "cache_read_tokens": "0",
        "cache_creation_tokens": "0",
        "total_tokens": "30",
        "cost_usd": "0.000123",
    }
    event = mapper(row)
    assert event["timestamp"] == "2026-09-25T16:47:30.000000000+00:00"
    assert event["host"] == "raspi5"
    assert event["model"] == "deepseek-flash"
    assert event["input_tokens"] == 10
    assert event["output_tokens"] == 20
    assert event["total_tokens"] == 30
    assert event["cost_usd"] == 0.000123


def test_flux_row_to_event_handles_missing_numeric():
    mapper = _row_to_event()
    row = {"_time": "2026-09-25T16:47:30Z", "cost_usd": "", "input_tokens": ""}
    event = mapper(row)
    assert event["cost_usd"] == 0.0
    assert event["input_tokens"] == 0
    assert event["timestamp"].endswith("+00:00")


# ---------------------------------------------------------------------------
# InfluxSink (Schreiben) -- gemockter Client
# ---------------------------------------------------------------------------

def _mk_usage() -> TokenUsage:
    return TokenUsage(
        timestamp="2026-09-25T16:47:30+02:00",
        input_tokens=10,
        output_tokens=20,
        cache_read_input_tokens=2,
        cache_creation_input_tokens=3,
        total_tokens=32,
        model="deepseek-flash",
        request_id="r1",
        api_key_fingerprint="abc123",
        raw_usage={"input_tokens": 10},
    )


def test_influx_sink_disabled_without_config():
    from proxy.influx_sink import InfluxSink

    sink = InfluxSink(url="", token="", org="", bucket="")
    assert sink._enabled is False


def test_influx_sink_connect_lazy_import_missing():
    from proxy.influx_sink import InfluxSink

    sink = InfluxSink(
        url="http://x:8086", token="t", org="o", bucket="b", host_name="h"
    )
    # influxdb-client ist im Test ggf. nicht installiert -> sink deaktiviert,
    # write darf trotzdem nicht werfen.
    sink.write(_mk_usage())  # best effort, keine Exception


def test_influx_sink_to_point_with_mocked_client():
    from proxy.influx_sink import InfluxSink

    sink = InfluxSink(
        url="http://x:8086", token="t", org="o", bucket="b", host_name="raspi5"
    )
    sink._enabled = False  # no real client needed

    # ``_to_point`` importiert ``Point`` lazy aus ``influxdb_client`` und
    # chains calls (`.tag().tag()...field().time()`). A simple recorder
    # spiegelt diese Kette und sammelt die Aufrufe.
    class Recorder:
        def __init__(self, measurement=None):
            self.measurement = measurement
            self.tags: list[tuple] = []
            self.fields: list[tuple] = []
            self.time_arg = None

        def tag(self, key, value):
            self.tags.append((key, value))
            return self

        def field(self, key, value):
            self.fields.append((key, value))
            return self

        def time(self, value):
            self.time_arg = value
            return self

    fake_influx = ModuleType("influxdb_client")
    fake_influx.Point = Recorder

    with mock.patch.dict(sys.modules, {"influxdb_client": fake_influx}):
        point = sink._to_point(_mk_usage())

    assert point.measurement == "usage"
    assert ("host", "raspi5") in point.tags
    assert ("model", "deepseek-flash") in point.tags
    assert ("cost_usd", mock.ANY) in point.fields
    assert ("requests", 1) in point.fields
    assert point.time_arg is not None
