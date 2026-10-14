"""Streaming proxy between VS Code (Anthropic Messages API) and DeepSeek.

The proxy exposes the two endpoints ``POST /anthropic/v1/messages`` and
``POST /v1/messages``, forwards the requests **byte-exact and unchanged** to
``https://api.deepseek.com/anthropic/v1/messages`` and streams the response
(Server-Sent Events) back to the client unchanged.

Transparency is the core feature:

* The request body is received as raw bytes and passed through 1:1 -- no
decoding/re-encoding and no field manipulation takes place.
* Request headers are passed through (especially ``x-api-key``,
  ``anthropic-version``, ``anthropic-beta``). **No** own API key is created or
  inserted.
* Only hop-by-hop headers are removed; ``Host`` is set to the upstream,
  ``Content-Length`` is handled by httpx.
* Response bytes are streamed unchanged; the response status and the response
  headers (including Content-Type) are adopted.

Alongside the transport runs a fully decoupled token monitor: a copy of every
upstream chunk is fed into an incremental SSE parser, the ``usage`` objects are
merged and passed as **one** :class:`~proxy.usage.TokenUsage` to
``usage_callback`` at stream end. An error in the monitoring can never corrupt
the data stream.

The HTTP server runs in its own daemon thread with its own asyncio event loop,
so the proxy can later be started and stopped from within a Tkinter application.

Public symbols:
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
import zlib
from collections.abc import Callable, Iterable, Sequence
from typing import Any
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

#: Hop-by-hop headers per RFC 7230 -- must not be forwarded.
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

#: Request headers that are additionally dropped (httpx/Host set these itself).
_DROP_REQUEST_HEADERS = _HOP_BY_HOP | {"content-length", "host"}

#: Response headers that are additionally dropped. ``content-length`` is
#: removed because the response is streamed again (chunked).
_DROP_RESPONSE_HEADERS = _HOP_BY_HOP | {"content-length"}

#: Upper bound for buffering a non-SSE/JSON response for usage extraction.
_MAX_JSON_BUFFER = 1 << 20  # 1 MiB


def _make_decompressor(content_encoding: str | None):
    """Return an incremental decompressor for the response ``content-encoding``.

    ``None`` is returned for uncompressed (``identity``) responses. ``gzip``,
    ``deflate`` and ``zstd`` are supported (DeepSeek answers VS Code with
    ``zstd`` when the client advertises it); any other encoding raises
    ``ValueError`` and the caller falls back to raw passthrough. The client
    keeps receiving the original compressed bytes unchanged.
    """
    encoding = (content_encoding or "").strip().lower()
    if not encoding or encoding == "identity":
        return None
    if encoding == "gzip":
        return zlib.decompressobj(zlib.MAX_WBITS | 16)
    if encoding == "deflate":
        return zlib.decompressobj(zlib.MAX_WBITS)
    if encoding == "zstd":
        try:
            import zstandard
        except ImportError as exc:  # pragma: no cover - depends on install
            raise ValueError("zstandard not installed for zstd decoding") from exc
        return zstandard.ZstdDecompressor().decompressobj()
    raise ValueError(f"unsupported content-encoding: {encoding!r}")


UsageCallback = Callable[[TokenUsage], Any]


def _default_usage_callback(usage: TokenUsage) -> None:
    """Default ``usage_callback``: write the usage only to the log."""
    usage_logger.info(
        "usage in=%d out=%d cache_read=%d cache_write=%d total=%d model=%s " "request_id=%s key=%s",
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
    """Mask a secret value for log output (only the prefix stays visible)."""
    if not value:
        return "<empty>"
    if len(value) <= 4:
        return "****"
    return f"{value[:4]}…"


def _format_headers_for_log(pairs: Iterable[tuple[str, str]]) -> list[str]:
    """Prepare headers for debug logs and mask sensitive values."""
    return [
        f"{name}: {_mask_secret(value) if name.lower() == 'x-api-key' else value}"
        for name, value in pairs
    ]


class _InterruptibleServer(uvicorn.Server):
    """Uvicorn server that runs in a thread and reports start readiness."""

    def __init__(
        self,
        config: uvicorn.Config,
        on_started: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(config)
        self._on_started = on_started

    def install_signal_handlers(self) -> None:  # noqa: D102 - intentionally no-op
        # Signal handlers may only be registered in the main thread.
        # The server runs in its own thread -> do nothing.
        return

    async def startup(self, sockets: Sequence[Any] | None = None) -> None:  # type: ignore[override]
        await super().startup(sockets=sockets)
        callback = self._on_started
        if callback is not None:
            try:
                callback()
            except Exception:  # pragma: no cover - purely defensive
                logger.exception("on_started callback failed")


class DeepSeekProxyServer:
    """Streaming proxy server with token monitoring.

    Parameters:
        usage_callback: Callable ``(TokenUsage) -> None`` or coroutine function.
            Default: :func:`_default_usage_callback` (logs only).
        host: Bind address (default ``127.0.0.1``).
        port: Bind port (default ``8000``).
        upstream_url: Target URL of the DeepSeek-Anthropic API.
        timeout: httpx ``Timeout``. Default: connect 10s / read 600s /
            write 60s / pool 10s (no read abort for long SSE streams).
        log_level: Uvicorn log level.
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
        """The underlying FastAPI application."""
        return self._app

    @property
    def base_url(self) -> str:
        """Base URL of the running proxy server."""
        return f"http://{self.host}:{self.port}"

    @property
    def is_running(self) -> bool:
        """``True`` if the server thread is running."""
        return self._thread is not None and self._thread.is_alive()

    def start(self, wait: bool = True, timeout: float = 15.0) -> None:
        """Start the Uvicorn server in its own daemon thread.

        Parameters:
            wait: Wait for start readiness (default ``True``).
            timeout: Maximum wait time for start readiness in seconds.

        Raises:
            RuntimeError: If the server is already running or does not become
                ready in time.
        """
        if self.is_running:
            raise RuntimeError("Proxy server already running")

        self._ready.clear()
        config = uvicorn.Config(
            self._app,
            host=self.host,
            port=self.port,
            loop="asyncio",
            lifespan="on",
            log_level=self.log_level,
            log_config=None,  # do not let the host app destroy the logging config
        )
        self._server = _InterruptibleServer(config, on_started=self._ready.set)

        self._thread = threading.Thread(
            target=self._run,
            name="deepseek-proxy-server",
            daemon=True,
        )
        self._thread.start()

        if wait and not self._ready.wait(timeout=timeout):
            raise RuntimeError(f"Proxy server did not become ready within {timeout:.1f}s")

        logger.info("Proxy running at %s -> %s", self.base_url, self.upstream_url)

    def _run(self) -> None:
        """Thread target: run Uvicorn with its own event loop."""
        server = self._server
        if server is None:  # pragma: no cover - purely defensive
            return
        try:
            server.run()
        except Exception:  # pragma: no cover - thread must not die silently
            logger.exception("Uvicorn server in proxy thread crashed")
        finally:
            self._ready.set()

    def stop(self, timeout: float = 10.0) -> None:
        """Shut the server down in a controlled manner.

        Sets ``should_exit`` and waits for the server thread to end. If the
        thread does not react within ``timeout``, the shutdown is forced.
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
                logger.warning("Proxy thread did not react -- forcing shutdown")
                if server is not None:
                    server.should_force_exit = True
                thread.join(timeout=timeout)
            if thread.is_alive():  # pragma: no cover - very unlikely
                logger.error("Proxy thread could not be terminated")

        self._server = None
        self._thread = None
        logger.info("Proxy stopped")

    # ---------------------------------------------------------------- handling
    async def _handle_messages(self, request: Request) -> Response:
        """Forward a Messages request transparently to DeepSeek."""
        # Byte-exact body -- no decoding, no reconstruction.
        body = await request.body()

        # One-way hash of the API key to associate the usage (no plaintext).
        api_key_fingerprint = fingerprint_key(request.headers.get("x-api-key", ""))

        upstream_headers = self._build_upstream_headers(request)
        logger.debug(
            "Proxy request %s %s (%d bytes) headers: %s",
            request.method,
            request.url.path,
            len(body),
            _format_headers_for_log(upstream_headers),
        )

        client = httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=False,
            # Own connection per request -> clean cleanup on abort.
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
                    "Upstream connection failed (%s): %s",
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
                                "Proxy could not establish the upstream "
                                f"connection ({exc.__class__.__name__})."
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
            "Upstream response %d headers: %s",
            upstream.status_code,
            list(response_headers.keys()),
        )

        return StreamingResponse(
            self._stream_upstream(upstream, client, api_key_fingerprint),
            status_code=upstream.status_code,
            headers=response_headers,
            media_type=None,  # Content-Type comes 1:1 from upstream
        )

    # ---------------------------------------------------------------- headers
    def _build_upstream_headers(self, request: Request) -> list[tuple[str, str]]:
        """Filter the incoming headers and set the upstream ``Host``."""
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
        """Filter the response headers (drop hop-by-hop and ``content-length``)."""
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
        """Generator: deliver original bytes and monitor usage in parallel.

        The transport always comes first: bytes are passed through unchanged,
        monitoring runs isolated in ``try/except``.
        """
        accumulator = TokenUsage(api_key_fingerprint=api_key_fingerprint)
        state = {"seen": False, "emitted": False}

        content_type = (upstream.headers.get("content-type") or "").lower()
        is_sse = "text/event-stream" in content_type
        json_buffer = bytearray()

        # DeepSeek compresses responses (``content-encoding: gzip``) while the
        # bytes are forwarded to the client byte-exact. The monitoring copy must
        # be decompressed first so the SSE/JSON parser sees plain text.
        content_encoding = upstream.headers.get("content-encoding")
        try:
            decompressor = _make_decompressor(content_encoding)
        except ValueError as exc:
            logger.warning(
                "Monitoring cannot decompress %r: %s",
                content_encoding,
                exc,
            )
            decompressor = None

        def on_sse_event(_event_name: str | None, data: str | None) -> None:
            if not data:
                return
            try:
                obj = json.loads(data)
            except (ValueError, TypeError):
                # Invalid JSON (e.g. keep-alive) must never crash.
                return
            if not isinstance(obj, dict):
                return
            message = obj.get("message")
            nested_usage = message.get("usage") if isinstance(message, dict) else None
            if isinstance(obj.get("usage"), dict) or isinstance(nested_usage, dict):
                state["seen"] = True
            try:
                merge_usage(obj, accumulator)
            except Exception:
                logger.exception("Error merging usage data")

        parser = SSEParser(on_event=on_sse_event)

        try:
            async for chunk in upstream.aiter_raw():
                if not chunk:
                    continue
                # Monitoring first, but fully isolated.
                try:
                    plain = chunk
                    if decompressor is not None:
                        plain = decompressor.decompress(chunk)
                    if is_sse:
                        parser.feed(plain)
                    elif len(json_buffer) < _MAX_JSON_BUFFER:
                        json_buffer.extend(plain)
                except Exception:
                    logger.exception("Error in usage monitoring (stream continues)")
                yield chunk
        except asyncio.CancelledError:
            logger.debug("Stream ended by client abort")
            raise
        except httpx.HTTPError as exc:
            logger.warning("Upstream stream aborted: %s", exc)
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
                    logger.debug("Usage callback discarded during abort")
                except Exception:
                    logger.exception("Usage callback failed (stream stays intact)")

    @staticmethod
    def _merge_json_body(
        body: bytes,
        accumulator: TokenUsage,
        state: dict[str, bool],
    ) -> None:
        """Evaluate a (non-streamed) JSON response for ``usage``."""
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
            logger.exception("Error merging JSON usage data")

    # ---------------------------------------------------------------- callback
    async def _emit_usage(self, usage: TokenUsage) -> None:
        """Pass the consolidated usage exactly once to the callback.

        A coroutine return value is ``await``-ed. Errors in the callback are
        caught and logged -- the stream must not abort because of them.
        """
        # total_tokens fallback (input_tokens already contains the cache tokens).
        finalize_usage(usage)

        callback = self._usage_callback
        if callback is None:
            return
        try:
            result: Any = callback(usage)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception("Error in usage_callback")
