"""Incremental Server-Sent Events (SSE) parser.

Pure and without I/O: only the raw byte stream (e.g. from an Anthropic Messages
response) is taken in and converted into events.

Supports:
  * events spread over multiple chunks
  * multiple events per chunk
  * UTF-8 multibyte characters spread over chunk boundaries
  * CRLF and LF line endings
  * comment lines (``: keep-alive``) and ``id:``/``retry:`` (ignored)
  * incomplete events at stream end (no final ``\n\n``)

NO JSON decoding takes place -- the parser only delivers the event name and the
concatenated ``data`` string.
"""

from __future__ import annotations

import codecs
import logging
from collections.abc import Callable

logger = logging.getLogger("deepseek_proxy.sse")

OnEvent = Callable[[str | None, str | None], None]


class SSEParser:
    """Stateful, incremental SSE parser.

    Parameters:
        on_event: Callback ``(event_name, data)``. ``event_name`` is ``None``
            if the event had no ``event:`` block. ``data`` is ``None`` if no
            ``data:`` line was present.
    """

    def __init__(self, on_event: OnEvent | None = None) -> None:
        self._on_event: OnEvent | None = on_event
        self._new_decoder()
        self._pending_line: str = ""
        self._event_name: str | None = None
        self._data_lines: list[str] = []

    # ------------------------------------------------------------------ helpers
    def _new_decoder(self) -> None:
        # ``replace`` instead of ``strict``: invalid bytes must not abort the
        # stream. The incremental decoder buffers broken multibyte sequences
        # itself and only emits them with the next chunk.
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def _reset_event(self) -> None:
        self._event_name = None
        self._data_lines = []

    # --------------------------------------------------------------------- API
    def reset(self) -> None:
        """Reset the entire internal state."""
        self._new_decoder()
        self._pending_line = ""
        self._reset_event()

    def feed(self, chunk: bytes | bytearray | memoryview | str) -> None:
        """Feed raw bytes; process all lines that became complete."""
        if not chunk:
            return
        if isinstance(chunk, str):  # defensively: accept text too
            chunk = chunk.encode("utf-8")

        text = self._decoder.decode(bytes(chunk))

        # Process line by line. ``\n`` cannot be part of a multibyte sequence
        # in UTF-8, so splitting here is safe.
        pending = self._pending_line + text
        start = 0
        while True:
            idx = pending.find("\n", start)
            if idx == -1:
                break
            self._handle_line(pending[start:idx])
            start = idx + 1
        self._pending_line = pending[start:]

    # ------------------------------------------------------------ line handling
    def _handle_line(self, line: str) -> None:
        # Tolerate CRLF: remove a possible trailing '\r'.
        if line.endswith("\r"):
            line = line[:-1]

        # Empty line -> event is complete.
        if line == "":
            self._dispatch()
            return

        # Comment (e.g. ": keep-alive") -> ignore entirely.
        if line.startswith(":"):
            logger.debug("SSE comment ignored: %r", line)
            return

        if ":" in line:
            field, value = line.split(":", 1)
            if value.startswith(" "):
                value = value[1:]
        else:
            field, value = line, ""

        if field == "event":
            self._event_name = value
        elif field == "data":
            self._data_lines.append(value)
        elif field in ("id", "retry"):
            logger.debug("SSE field %r ignored: %r", field, value)
        else:
            logger.debug("Unknown SSE field %r ignored", field)

    def _dispatch(self) -> None:
        event_name = self._event_name
        data_lines = self._data_lines
        self._reset_event()

        # Reines Kommentar-/Leer-Event: nichts zu melden.
        if event_name is None and not data_lines:
            return

        data: str | None = "\n".join(data_lines) if data_lines else None

        if self._on_event is not None:
            try:
                self._on_event(event_name, data)
            except Exception:  # Callback errors must not kill the parser
                logger.exception("Fehler im SSE on_event-Callback")
