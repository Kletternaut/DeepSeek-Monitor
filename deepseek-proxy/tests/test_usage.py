"""Tests for the TokenUsage model and usage extraction from the real SSE parser.

The tests must not bypass the real SSE parser: SSE bytes are fed through
:class:`proxy.sse.SSEParser`, the ``data`` lines are parsed with ``json.loads``
and then evaluated with ``proxy.usage`` — exactly as in a proxy run.
"""

import json

import pytest

from proxy.sse import SSEParser
from proxy.usage import (
    TokenUsage,
    extract_usage_from_event,
    finalize_usage,
    merge_usage,
    now_iso_berlin,
)


def _parse_stream(payload: bytes):
    """Run the full pipeline: SSE -> JSON events -> usage merge."""
    events = []
    parser = SSEParser(on_event=lambda n, d: events.append((n, d)))
    parser.feed(payload)
    result = TokenUsage()
    for _name, data in events:
        if data is None:
            continue
        obj = json.loads(data)  # invalid JSON can surface here
        merge_usage(obj, result)
    return result


class TestNowIso:
    def test_iso_format(self):
        s = now_iso_berlin()
        # z.B. 2026-09-25T11:30:42+02:00
        assert s.endswith("+02:00") or s.endswith("+01:00")
        assert "T" in s


class TestFinalizeUsage:
    def test_computes_total_from_input_plus_output(self):
        u = TokenUsage(input_tokens=36, output_tokens=16)
        finalize_usage(u)
        assert u.total_tokens == 52

    def test_includes_cache_read(self):
        # DeepSeek: input = miss, cache_read = hit (additiv)
        u = TokenUsage(input_tokens=8204, cache_read_input_tokens=181888, output_tokens=3180)
        finalize_usage(u)
        assert u.total_tokens == 8204 + 181888 + 3180

    def test_keeps_existing_total(self):
        u = TokenUsage(input_tokens=36, output_tokens=16, total_tokens=99)
        finalize_usage(u)
        assert u.total_tokens == 99

    def test_zero_input_output_gives_zero(self):
        u = TokenUsage()
        finalize_usage(u)
        assert u.total_tokens == 0


class TestMergeUsage:
    def test_message_start_event(self):
        """Fall 1: normales message_start-Event (Anthropic-Feldnamen)."""
        event = {
            "type": "message_start",
            "message": {"id": "msg_123", "model": "deepseek-v4-pro"},
            "usage": {"input_tokens": 42, "output_tokens": 1},
        }
        u = extract_usage_from_event(event)
        assert u is not None
        assert u.input_tokens == 42
        assert u.output_tokens == 1
        assert u.model == "deepseek-v4-pro"
        assert u.request_id == "msg_123"

    def test_message_delta_event(self):
        """Fall 2: normales message_delta-Event."""
        event = {
            "type": "message_delta",
            "usage": {"output_tokens": 128},
        }
        u = extract_usage_from_event(event)
        assert u is not None
        assert u.output_tokens == 128
        assert u.input_tokens == 0

    def test_usage_spread_across_events(self):
        """Case 3: usage spread over multiple events is merged."""
        events = [
            {"type": "message_start", "usage": {"input_tokens": 100, "output_tokens": 1}},
            {"type": "content_block_delta", "delta": {"type": "text_delta"}},
            {"type": "message_delta", "usage": {"output_tokens": 250}},
        ]
        acc = TokenUsage()
        for e in events:
            merge_usage(e, acc)
        assert acc.input_tokens == 100
        assert acc.output_tokens == 250  # overwritten by the last event

    def test_cache_fields_anthropic_names(self):
        event = {
            "usage": {
                "input_tokens": 1000,
                "cache_creation_input_tokens": 700,
                "cache_read_input_tokens": 300,
                "output_tokens": 50,
            }
        }
        u = extract_usage_from_event(event)
        assert u.cache_creation_input_tokens == 700
        assert u.cache_read_input_tokens == 300

    def test_cache_fields_openai_names(self):
        """Tolerance for OpenAI field names (prompt_cache_hit/miss)."""
        event = {
            "usage": {
                "prompt_tokens": 1000,
                "prompt_cache_miss_tokens": 700,
                "prompt_cache_hit_tokens": 300,
                "completion_tokens": 50,
                "total_tokens": 1050,
            }
        }
        u = extract_usage_from_event(event)
        assert u.input_tokens == 1000
        assert u.cache_creation_input_tokens == 700
        assert u.cache_read_input_tokens == 300
        assert u.output_tokens == 50
        assert u.total_tokens == 1050

    def test_missing_usage_returns_none(self):
        """Fall 11: fehlende Usage-Daten -> None."""
        event = {"type": "content_block_delta", "delta": {"type": "text_delta"}}
        assert extract_usage_from_event(event) is None

    def test_non_int_values_handled(self):
        event = {"usage": {"input_tokens": None, "output_tokens": "not a number"}}
        u = extract_usage_from_event(event)
        assert u is not None
        assert u.input_tokens == 0
        assert u.output_tokens == 0

    def test_bool_rejected(self):
        event = {"usage": {"input_tokens": True}}
        u = extract_usage_from_event(event)
        assert u is not None
        assert u.input_tokens == 0

    def test_non_dict_event_returns_none(self):
        assert extract_usage_from_event("not a dict") is None  # type: ignore


class TestFullPipeline:
    def test_stream_with_all_events(self):
        """Gesamter Anthropic-Stream, inkl. keep-alive, durch den echten Parser."""
        payload = (
            b"event: message_start\n"
            b'data: {"type":"message_start","message":{"id":"m1","model":"deepseek-flash"},'
            b'"usage":{"input_tokens":55,"output_tokens":1}}\n\n'
            b": keep-alive\n\n"
            b'event: content_block_start\ndata: {"type":"content_block_start"}\n\n'
            b'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hi"}}\n\n'
            b'event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":12}}\n\n'
            b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
        )
        u = _parse_stream(payload)
        assert u.input_tokens == 55
        assert u.output_tokens == 12
        assert u.model == "deepseek-flash"
        assert u.request_id == "m1"

    def test_json_split_across_chunks(self):
        """Case 5: JSON spread over several chunks — through the real parser."""
        full = (
            b'event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":99}}\n\n'
        )
        events = []
        parser = SSEParser(on_event=lambda n, d: events.append((n, d)))
        # beliebige Chunk-Grenzen, mitten im JSON
        for c in [full[:13], full[13:31], full[31:47], full[47:]]:
            parser.feed(c)
        data = events[0][1]
        obj = json.loads(data)
        u = merge_usage(obj, TokenUsage())
        assert u.output_tokens == 99

    def test_invalid_json_does_not_crash_parser(self):
        """Case 9: invalid JSON must not crash the parser (only the consumer)."""
        payload = b"event: message_delta\ndata: {invalid json!!!}\n\n"
        events = []
        parser = SSEParser(on_event=lambda n, d: events.append((n, d)))
        parser.feed(payload)  # parser returns the string unchanged
        assert events[0][1] == "{invalid json!!!}"
        # Consumer-seitig: json.loads wirft — das muss der Aufrufer abfangen.
        with pytest.raises(json.JSONDecodeError):
            json.loads(events[0][1])

    def test_stream_without_usage(self):
        """Stream ohne jegliche Usage-Daten -> alles bleibt 0."""
        payload = (
            b'event: content_block_delta\ndata: {"delta":{"text":"x"}}\n\n'
            b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
        )
        u = _parse_stream(payload)
        assert u.input_tokens == 0
        assert u.output_tokens == 0
