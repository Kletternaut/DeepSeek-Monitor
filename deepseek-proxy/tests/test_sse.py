"""Tests für den inkrementellen SSE-Parser (Anthropic Messages SSE).

Diese Tests dürfen den echten Parser NICHT umgehen — es wird ausschließlich
:class:`proxy.sse.SSEParser` benutzt und über dessen Callback beobachtet.
"""

import json

import pytest

from proxy.sse import SSEParser


def _feed_collect(chunks, on_event):
    """Füttert ``chunks`` (bytes) in einen Parser und sammelt Events."""
    parser = SSEParser(on_event=on_event)
    for c in chunks:
        parser.feed(c)
    return parser


def _chunks_bytewise(data: bytes):
    return [data[i:i + 1] for i in range(len(data))]


class TestParser:
    def test_single_event_single_chunk(self):
        events = []
        payload = (
            b'event: message_start\n'
            b'data: {"type":"message_start"}\n\n'
        )
        _feed_collect([payload], lambda n, d: events.append((n, d)))
        assert events == [("message_start", '{"type":"message_start"}')]

    def test_event_split_across_chunks(self):
        """Fall 4: Ein SSE-Event über mehrere HTTP-Chunks verteilt."""
        events = []
        full = b'event: content_block_delta\ndata: {"delta":"hi"}\n\n'
        chunks = [full[:5], full[5:20], full[20:]]
        _feed_collect(chunks, lambda n, d: events.append((n, d)))
        assert len(events) == 1
        assert events[0][0] == "content_block_delta"
        assert events[0][1] == '{"delta":"hi"}'

    def test_multiple_events_in_one_chunk(self):
        """Fall 8: Mehrere SSE-Events in einem einzigen Chunk."""
        events = []
        payload = (
            b'event: message_start\ndata: {"type":"message_start"}\n\n'
            b'event: content_block_delta\ndata: {"delta":"a"}\n\n'
            b'event: content_block_delta\ndata: {"delta":"b"}\n\n'
        )
        _feed_collect([payload], lambda n, d: events.append((n, d)))
        assert [n for n, _ in events] == [
            "message_start", "content_block_delta", "content_block_delta"
        ]

    def test_utf8_char_split_across_chunks(self):
        """Fall 6: UTF-8-Zeichen über mehrere Chunks verteilt."""
        events = []
        # "Grüße" — das "ü" (0xC3 0xBC) wird bewusst an der Chunk-Grenze geteilt.
        text = 'event: message_delta\ndata: {"text":"Grüße"}\n\n'
        raw = text.encode("utf-8")
        # Grenze mitten in "ü" finden
        u_pos = raw.index("ü".encode("utf-8"))
        chunks = [raw[:u_pos + 1], raw[u_pos + 1:]]
        _feed_collect(chunks, lambda n, d: events.append((n, d)))
        assert len(events) == 1
        assert 'Grüße' in events[0][1]

    def test_bytewise_feed(self):
        """Härtetest: jedes Byte einzeln füttern."""
        events = []
        payload = b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
        _feed_collect(_chunks_bytewise(payload), lambda n, d: events.append((n, d)))
        assert events == [("message_stop", '{"type":"message_stop"}')]

    def test_keep_alive_comment_ignored(self):
        """Fall 7: `: keep-alive`-Kommentar stört den Parser nicht."""
        events = []
        payload = (
            b': keep-alive\n\n'
            b'event: content_block_delta\ndata: {"delta":"x"}\n\n'
            b': keep-alive\n\n'
        )
        _feed_collect([payload], lambda n, d: events.append((n, d)))
        assert events == [("content_block_delta", '{"delta":"x"}')]

    def test_unknown_event_type(self):
        events = []
        payload = b'event: strange_event\ndata: {"foo":1}\n\n'
        _feed_collect([payload], lambda n, d: events.append((n, d)))
        assert events == [("strange_event", '{"foo":1}')]

    def test_crlf_line_endings(self):
        events = []
        payload = b'event: message_start\r\ndata: {"x":1}\r\n\r\n'
        _feed_collect([payload], lambda n, d: events.append((n, d)))
        assert events == [("message_start", '{"x":1}')]

    def test_multiline_data_joined(self):
        events = []
        payload = b'event: msg\ndata: line1\ndata: line2\n\n'
        _feed_collect([payload], lambda n, d: events.append((n, d)))
        assert events == [("msg", "line1\nline2")]

    def test_event_without_data(self):
        events = []
        payload = b'event: ping\n\n'
        _feed_collect([payload], lambda n, d: events.append((n, d)))
        assert events == [("ping", None)]

    def test_premature_stream_end_no_crash(self):
        """Fall 10: vorzeitiger Abbruch (kein finales \\n\\n) — kein Crash."""
        events = []
        payload = b'event: message_delta\ndata: {"unfinished"'
        parser = _feed_collect([payload], lambda n, d: events.append((n, d)))
        assert events == []  # unvollständiges Event wird nicht dispatcht
        # kein Exception — Test erreicht diese Zeile nur, wenn nichts gecrasht ist

    def test_callback_error_does_not_crash(self):
        """Fall 12: Callback-Fehler brechen den Parser nicht ab."""
        payload = b'event: a\ndata: {}\n\nevent: b\ndata: {}\n\n'
        seen = []
        state = {"n": 0}

        def flaky_cb(n, d):
            state["n"] += 1
            if state["n"] == 1:
                raise RuntimeError("boom")
            seen.append((n, d))

        parser = SSEParser(on_event=flaky_cb)
        parser.feed(payload)
        # Erstes Event wirft, zweites Event wird dennoch zugestellt.
        assert seen == [("b", "{}")]

    def test_reset_clears_state(self):
        events = []
        parser = SSEParser(on_event=lambda n, d: events.append((n, d)))
        parser.feed(b'event: a\ndata: partial')
        parser.reset()
        parser.feed(b'event: b\ndata: {}\n\n')
        assert events == [("b", "{}")]
