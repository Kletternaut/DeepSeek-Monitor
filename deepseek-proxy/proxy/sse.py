"""Inkrementeller Server-Sent-Events (SSE)-Parser.

Rein und ohne I/O: Es wird nur der rohe Bytestrom (z. B. aus einer
Anthropic-Messages-Antwort) entgegengenommen und in Events umgewandelt.

Unterstützt:
  * Events über mehrere Chunks verteilt
  * Mehrere Events pro Chunk
  * UTF-8-Multibyte-Zeichen über Chunk-Grenzen verteilt
  * CRLF- und LF-Zeilenenden
  * Kommentar-Zeilen (``: keep-alive``) und ``id:``/``retry:`` (werden ignoriert)
  * Unvollständige Events am Streamende (kein finales ``\\n\\n``)

Es findet KEINE JSON-Dekodierung statt -- der Parser liefert lediglich
Event-Name und den zusammengefügten ``data``-String.
"""

from __future__ import annotations

import codecs
import logging
from typing import Callable

logger = logging.getLogger("deepseek_proxy.sse")

OnEvent = Callable[[str | None, str | None], None]


class SSEParser:
    """Zustandsbehafteter, inkrementeller SSE-Parser.

    Parameter:
        on_event: Callback ``(event_name, data)``. ``event_name`` ist ``None``,
            wenn das Event keinen ``event:``-Block hatte. ``data`` ist ``None``,
            wenn keine ``data:``-Zeile vorhanden war.
    """

    def __init__(self, on_event: OnEvent | None = None) -> None:
        self._on_event: OnEvent | None = on_event
        self._new_decoder()
        self._pending_line: str = ""
        self._event_name: str | None = None
        self._data_lines: list[str] = []

    # ------------------------------------------------------------------ helpers
    def _new_decoder(self) -> None:
        # ``replace`` statt ``strict``: ungültige Bytes dürfen den Stream nicht
        # abbrechen. Der inkrementelle Decoder puffert angebrochene Multibyte-
        # Sequenzen selbst und gibt sie erst beim nächsten Chunk aus.
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def _reset_event(self) -> None:
        self._event_name = None
        self._data_lines = []

    # --------------------------------------------------------------------- API
    def reset(self) -> None:
        """Setzt den gesamten internen Zustand zurück."""
        self._new_decoder()
        self._pending_line = ""
        self._reset_event()

    def feed(self, chunk: bytes | bytearray | memoryview | str) -> None:
        """Füttert rohe Bytes; verarbeitet alle dadurch vollständig gewordenen Zeilen."""
        if not chunk:
            return
        if isinstance(chunk, str):  # defensiv: Text ebenfalls akzeptieren
            chunk = chunk.encode("utf-8")

        text = self._decoder.decode(bytes(chunk))

        # Zeilenweise verarbeiten. ``\n`` kann in UTF-8 nicht Teil einer
        # Multibyte-Sequenz sein, daher ist das Aufteilen hier sicher.
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
        # CRLF tolerieren: ein eventuell vorhandenes '\r' am Zeilenende entfernen.
        if line.endswith("\r"):
            line = line[:-1]

        # Leerzeile -> Event ist abgeschlossen.
        if line == "":
            self._dispatch()
            return

        # Kommentar (z. B. ": keep-alive") -> vollständig ignorieren.
        if line.startswith(":"):
            logger.debug("SSE-Kommentar ignoriert: %r", line)
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
            logger.debug("SSE-Feld %r ignoriert: %r", field, value)
        else:
            logger.debug("Unbekanntes SSE-Feld %r ignoriert", field)

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
            except Exception:  # Callback-Fehler dürfen den Parser nicht killen
                logger.exception("Fehler im SSE on_event-Callback")
