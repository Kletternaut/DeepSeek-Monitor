"""Streaming-Proxy zwischen VS-Code (Anthropic Messages API) und DeepSeek.

Der Proxy stellt die beiden Endpunkte ``POST /anthropic/v1/messages`` und
``POST /v1/messages`` bereit, leitet die Requests **bytegenau und unverändert**
an ``https://api.deepseek.com/anthropic/v1/messages`` weiter und streamt die
Antwort (Server-Sent-Events) unverändert zum Client zurück.

Transparenz ist die Kernfunktion:

* Der Request-Body wird als rohe Bytes empfangen und 1:1 weitergegeben -- es
  findet keine Dekodierung/Neukodierung und keine Feldmanipulation statt.
* Request-Header werden durchgereicht (insbesondere ``x-api-key``,
  ``anthropic-version``, ``anthropic-beta``). Es wird **kein** eigener
  API-Key erzeugt oder eingesetzt.
* Nur Hop-by-Hop-Header werden entfernt; ``Host`` wird auf den Upstream
  gesetzt, ``Content-Length`` übernimmt httpx.
* Antwort-Bytes werden unverändert gestreamt; der Antwort-Status und die
  Antwort-Header (inkl. Content-Type) werden übernommen.

Neben dem Transport läuft eine vollständig entkoppelte Token-Überwachung: eine
Kopie jedes Upstream-Chunks wird in einen inkrementellen SSE-Parser gefüttert,
die ``usage``-Objekte werden zusammengeführt und am Stream-Ende als **ein**
:class:`~proxy.usage.TokenUsage` an ``usage_callback`` übergeben. Ein Fehler im
Monitoring kann den Datenstrom niemals beschädigen.

Der HTTP-Server läuft in einem eigenen Daemon-Thread mit eigener asyncio-
Eventloop, damit der Proxy später aus einer Tkinter-Anwendung heraus gestartet
und gestoppt werden kann.

Öffentliche Symbole:
    * :class:`DeepSeekProxyServer`
    * :data:`DEFAULT_UPSTREAM_URL`
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
import threading
from typing import Any, Callable, Iterable, Sequence
from urllib.parse import urlsplit

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .labels import fingerprint_key
from .sse import SSEParser
from .usage import TokenUsage, finalize_usage, merge_usage

logger = logging.getLogger("deepseek_proxy")
usage_logger = logging.getLogger("deepseek_proxy.usage")

__all__ = ["DeepSeekProxyServer", "DEFAULT_UPSTREAM_URL"]

DEFAULT_UPSTREAM_URL = "https://api.deepseek.com/anthropic/v1/messages"

#: Hop-by-Hop-Header laut RFC 7230 -- dürfen nicht weitergegeben werden.
_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)

#: Request-Header, die zusätzlich verworfen werden (Werte setzt httpx/Host selbst).
_DROP_REQUEST_HEADERS = _HOP_BY_HOP | {"content-length", "host"}

#: Response-Header, die zusätzlich verworfen werden. ``content-length`` wird
#: entfernt, weil die Antwort erneut (chunked) gestreamt wird.
_DROP_RESPONSE_HEADERS = _HOP_BY_HOP | {"content-length"}

#: Obergrenze für das Puffern einer Nicht-SSE-/JSON-Antwort zur Usage-Ermittlung.
_MAX_JSON_BUFFER = 1 << 20  # 1 MiB

UsageCallback = Callable[[TokenUsage], Any]


def _default_usage_callback(usage: TokenUsage) -> None:
    """Default-``usage_callback``: schreibt die Usage nur ins Log."""
    usage_logger.info(
        "usage in=%d out=%d cache_read=%d cache_write=%d total=%d model=%s "
        "request_id=%s key=%s",
        usage.input_tokens,
        usage.output_tokens,
        usage.cache_read_input_tokens,
        usage.cache_creation_input_tokens,
        usage.total_tokens,
        usage.model or "-",
        usage.request_id or "-",
        usage.api_key_fingerprint or "-",
    )


def _mask_secret(value: str) -> str:
    """Maskiert einen Secret-Wert für Logausgaben (nur Präfix bleibt sichtbar)."""
    if not value:
        return "<leer>"
    if len(value) <= 4:
        return "****"
    return f"{value[:4]}…"


def _format_headers_for_log(pairs: Iterable[tuple[str, str]]) -> list[str]:
    """Bereitet Header für Debug-Logs auf und maskiert sensible Werte."""
    return [
        f"{name}: {_mask_secret(value) if name.lower() == 'x-api-key' else value}"
        for name, value in pairs
    ]


class _InterruptibleServer(uvicorn.Server):
    """Uvicorn-Server, der in einem Thread läuft und Startbereitschaft meldet."""

    def __init__(
        self,
        config: uvicorn.Config,
        on_started: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(config)
        self._on_started = on_started

    def install_signal_handlers(self) -> None:  # noqa: D102 - absichtlich no-op
        # Signal-Handler dürfen nur im Main-Thread registriert werden.
        # Der Server läuft in einem eigenen Thread -> nichts tun.
        return

    async def startup(self, sockets: Sequence[Any] | None = None) -> None:  # type: ignore[override]
        await super().startup(sockets=sockets)
        callback = self._on_started
        if callback is not None:
            try:
                callback()
            except Exception:  # pragma: no cover - rein defensiv
                logger.exception("on_started-Callback fehlgeschlagen")


class DeepSeekProxyServer:
    """Streaming-Proxy-Server mit Token-Überwachung.

    Parameter:
        usage_callback: Callable ``(TokenUsage) -> None`` oder Coroutine-Funktion.
            Default: :func:`_default_usage_callback` (loggt nur).
        host: Bind-Adresse (Default ``127.0.0.1``).
        port: Bind-Port (Default ``8000``).
        upstream_url: Ziel-URL der DeepSeek-Anthropic-API.
        timeout: httpx-``Timeout``. Default: connect 10s / read 600s /
            write 60s / pool 10s (kein Read-Abbruch bei langen SSE-Streams).
        log_level: Uvicorn-Log-Level.
    """

    def __init__(
        self,
        usage_callback: UsageCallback | None = None,
        host: str = "127.0.0.1",
        port: int = 8000,
        upstream_url: str = DEFAULT_UPSTREAM_URL,
        *,
        timeout: httpx.Timeout | None = None,
        log_level: str = "info",
    ) -> None:
        self.host = host
        self.port = port
        self.upstream_url = upstream_url
        self.log_level = log_level
        self.timeout = timeout or httpx.Timeout(
            connect=10.0,
            read=600.0,
            write=60.0,
            pool=10.0,
        )
        self._usage_callback: UsageCallback = usage_callback or _default_usage_callback

        parsed = urlsplit(upstream_url)
        self._upstream_host = parsed.netloc or parsed.path

        self._app = FastAPI(
            title="DeepSeek Anthropic Proxy",
            docs_url=None,
            redoc_url=None,
            openapi_url=None,
        )
        self._register_routes()

        self._server: _InterruptibleServer | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    # ------------------------------------------------------------------ setup
    def _register_routes(self) -> None:
        @self._app.post("/anthropic/v1/messages")
        async def anthropic_messages(request: Request) -> Response:
            return await self._handle_messages(request)

        @self._app.post("/v1/messages")
        async def messages_alias(request: Request) -> Response:
            return await self._handle_messages(request)

    # -------------------------------------------------------------- lifecycle
    @property
    def app(self) -> FastAPI:
        """Die zugrunde liegende FastAPI-Anwendung."""
        return self._app

    @property
    def base_url(self) -> str:
        """Basis-URL des laufenden Proxy-Servers."""
        return f"http://{self.host}:{self.port}"

    @property
    def is_running(self) -> bool:
        """``True``, wenn der Server-Thread läuft."""
        return self._thread is not None and self._thread.is_alive()

    def start(self, wait: bool = True, timeout: float = 15.0) -> None:
        """Startet den Uvicorn-Server in einem eigenen Daemon-Thread.

        Parameter:
            wait: Auf die Startbereitschaft warten (Default ``True``).
            timeout: Maximale Wartezeit auf die Startbereitschaft in Sekunden.

        Raises:
            RuntimeError: Wenn der Server bereits läuft oder nicht rechtzeitig
                bereit wird.
        """
        if self.is_running:
            raise RuntimeError("Proxy-Server läuft bereits")

        self._ready.clear()
        config = uvicorn.Config(
            self._app,
            host=self.host,
            port=self.port,
            loop="asyncio",
            lifespan="on",
            log_level=self.log_level,
            log_config=None,  # Host-Anwendung nicht die Logging-Config zerschießen
        )
        self._server = _InterruptibleServer(config, on_started=self._ready.set)

        self._thread = threading.Thread(
            target=self._run,
            name="deepseek-proxy-server",
            daemon=True,
        )
        self._thread.start()

        if wait and not self._ready.wait(timeout=timeout):
            raise RuntimeError(
                f"Proxy-Server wurde nicht innerhalb von {timeout:.1f}s bereit"
            )

        logger.info("Proxy läuft auf %s -> %s", self.base_url, self.upstream_url)

    def _run(self) -> None:
        """Thread-Target: betreibt Uvicorn mit eigener Eventloop."""
        server = self._server
        if server is None:  # pragma: no cover - rein defensiv
            return
        try:
            server.run()
        except Exception:  # pragma: no cover - Thread darf nicht lautlos sterben
            logger.exception("Uvicorn-Server im Proxy-Thread abgestürzt")
        finally:
            self._ready.set()

    def stop(self, timeout: float = 10.0) -> None:
        """Fährt den Server kontrolliert herunter.

        Setzt ``should_exit`` und wartet auf das Ende des Server-Threads. Reagiert
        der Thread nicht innerhalb von ``timeout``, wird der Shutdown erzwungen.
        """
        server = self._server
        thread = self._thread
        if server is None and thread is None:
            return

        if server is not None:
            server.should_exit = True

        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
            if thread.is_alive():
                logger.warning("Proxy-Thread reagierte nicht -- erzwinge Shutdown")
                if server is not None:
                    server.should_force_exit = True
                thread.join(timeout=timeout)
            if thread.is_alive():  # pragma: no cover - sehr unwahrscheinlich
                logger.error("Proxy-Thread konnte nicht beendet werden")

        self._server = None
        self._thread = None
        logger.info("Proxy gestoppt")

    # ---------------------------------------------------------------- handling
    async def _handle_messages(self, request: Request) -> Response:
        """Reicht einen Messages-Request transparent an DeepSeek weiter."""
        # Bytegenauer Body -- keine Dekodierung, keine Rekonstruktion.
        body = await request.body()

        # Einweg-Hash des API-Keys zur Zuordnung der Usage (kein Klartext).
        api_key_fingerprint = fingerprint_key(request.headers.get("x-api-key", ""))

        upstream_headers = self._build_upstream_headers(request)
        logger.debug(
            "Proxy-Request %s %s (%d Bytes) Header: %s",
            request.method,
            request.url.path,
            len(body),
            _format_headers_for_log(upstream_headers),
        )

        client = httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=False,
            # Eigene Verbindung pro Request -> sauberes Aufräumen bei Abbruch.
        )
        try:
            upstream_request = client.build_request(
                "POST",
                self.upstream_url,
                headers=upstream_headers,
                content=body,
            )
            try:
                upstream = await client.send(upstream_request, stream=True)
            except httpx.HTTPError as exc:
                logger.warning(
                    "Upstream-Verbindung fehlgeschlagen (%s): %s",
                    exc.__class__.__name__,
                    exc,
                )
                with contextlib.suppress(Exception):
                    await client.aclose()
                return JSONResponse(
                    status_code=502,
                    content={
                        "type": "error",
                        "error": {
                            "type": "proxy_upstream_error",
                            "message": (
                                "Proxy konnte die Upstream-Verbindung nicht "
                                f"aufbauen ({exc.__class__.__name__})."
                            ),
                        },
                    },
                )
        except Exception:
            with contextlib.suppress(Exception):
                await client.aclose()
            raise

        response_headers = self._build_response_headers(upstream)
        logger.debug(
            "Upstream-Antwort %d Header: %s",
            upstream.status_code,
            list(response_headers.keys()),
        )

        return StreamingResponse(
            self._stream_upstream(upstream, client, api_key_fingerprint),
            status_code=upstream.status_code,
            headers=response_headers,
            media_type=None,  # Content-Type kommt 1:1 vom Upstream
        )

    # ---------------------------------------------------------------- headers
    def _build_upstream_headers(self, request: Request) -> list[tuple[str, str]]:
        """Filtert die eingehenden Header und setzt den Upstream-``Host``."""
        headers: list[tuple[str, str]] = []
        for raw_name, raw_value in request.headers.raw:
            name = raw_name.decode("latin-1")
            if name.lower() in _DROP_REQUEST_HEADERS:
                continue
            headers.append((name, raw_value.decode("latin-1")))
        headers.append(("host", self._upstream_host))
        return headers

    @staticmethod
    def _build_response_headers(upstream: httpx.Response) -> httpx.Headers:
        """Filtert die Antwort-Header (Hop-by-Hop und ``content-length`` raus)."""
        headers = [
            (name.decode("latin-1"), value.decode("latin-1"))
            for name, value in upstream.headers.raw
            if name.decode("latin-1").lower() not in _DROP_RESPONSE_HEADERS
        ]
        return httpx.Headers(headers)

    # --------------------------------------------------------------- streaming
    async def _stream_upstream(
        self,
        upstream: httpx.Response,
        client: httpx.AsyncClient,
        api_key_fingerprint: str = "",
    ) -> Any:
        """Generator: liefert Original-Bytes und überwacht parallel die Usage.

        Der Transport steht immer an erster Stelle: Bytes werden unverändert
        weitergereicht, Monitoring läuft isoliert in ``try/except``.
        """
        accumulator = TokenUsage(api_key_fingerprint=api_key_fingerprint)
        state = {"seen": False, "emitted": False}

        content_type = (upstream.headers.get("content-type") or "").lower()
        is_sse = "text/event-stream" in content_type
        json_buffer = bytearray()

        def on_sse_event(_event_name: str | None, data: str | None) -> None:
            if not data:
                return
            try:
                obj = json.loads(data)
            except (ValueError, TypeError):
                # Ungültiges JSON (z. B. Keep-alive) darf niemals crashen.
                return
            if not isinstance(obj, dict):
                return
            if isinstance(obj.get("usage"), dict):
                state["seen"] = True
            try:
                merge_usage(obj, accumulator)
            except Exception:
                logger.exception("Fehler beim Zusammenführen der Usage-Daten")

        parser = SSEParser(on_event=on_sse_event)

        try:
            async for chunk in upstream.aiter_raw():
                if not chunk:
                    continue
                # Monitoring zuerst, aber vollständig isoliert.
                try:
                    if is_sse:
                        parser.feed(chunk)
                    elif len(json_buffer) < _MAX_JSON_BUFFER:
                        json_buffer.extend(chunk)
                except Exception:
                    logger.exception("Fehler im Usage-Monitoring (Stream läuft weiter)")
                yield chunk
        except asyncio.CancelledError:
            logger.debug("Stream durch Client-Abbruch beendet")
            raise
        except httpx.HTTPError as exc:
            logger.warning("Upstream-Stream abgebrochen: %s", exc)
        finally:
            if not is_sse and json_buffer:
                self._merge_json_body(bytes(json_buffer), accumulator, state)

            with contextlib.suppress(Exception):
                await upstream.aclose()
            with contextlib.suppress(Exception):
                await client.aclose()

            if state["seen"] and not state["emitted"]:
                state["emitted"] = True
                try:
                    await self._emit_usage(accumulator)
                except asyncio.CancelledError:
                    logger.debug("Usage-Callback während Abbruch verworfen")
                except Exception:
                    logger.exception("Usage-Callback fehlgeschlagen (Stream bleibt intakt)")

    @staticmethod
    def _merge_json_body(
        body: bytes,
        accumulator: TokenUsage,
        state: dict[str, bool],
    ) -> None:
        """Wertet eine (nicht gestreamte) JSON-Antwort auf ``usage`` aus."""
        try:
            obj = json.loads(body.decode("utf-8", errors="replace"))
        except (ValueError, TypeError, UnicodeError):
            return
        if not isinstance(obj, dict):
            return
        if isinstance(obj.get("usage"), dict):
            state["seen"] = True
        try:
            merge_usage(obj, accumulator)
        except Exception:
            logger.exception("Fehler beim Zusammenführen der JSON-Usage-Daten")

    # ---------------------------------------------------------------- callback
    async def _emit_usage(self, usage: TokenUsage) -> None:
        """Übergibt die konsolidierte Usage genau einmal an den Callback.

        Ein Coroutine-Rückgabewert wird ``await``-ed. Fehler im Callback werden
        abgefangen und geloggt -- der Stream darf dadurch nicht abbrechen.
        """
        # total_tokens-Fallback (input_tokens enthält bereits die Cache-Token).
        finalize_usage(usage)

        callback = self._usage_callback
        if callback is None:
            return
        try:
            result: Any = callback(usage)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception("Fehler im usage_callback")
