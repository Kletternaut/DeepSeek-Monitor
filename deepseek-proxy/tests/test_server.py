"""Integration test for the proxy server (transparency + usage extraction).

The upstream is simulated via ``httpx.MockTransport``; the FastAPI app of the
proxy is called via ``ASGITransport`` without a real network socket.
"""

import asyncio
from unittest.mock import patch

import httpx

from proxy.server import DeepSeekProxyServer

# Beispielhafter Anthropic-SSE-Stream (Antwort des Upstream).
SSE_BODY = (
    b"event: message_start\n"
    b'data: {"type":"message_start","message":{"id":"m1","model":"deepseek-flash",'
    b'"usage":{"input_tokens":55,"output_tokens":1}}}\n\n'
    b": keep-alive\n\n"
    b"event: content_block_delta\n"
    b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hi"}}\n\n'
    b"event: message_delta\n"
    b'data: {"type":"message_delta","usage":{"output_tokens":12}}\n\n'
    b"event: message_stop\n"
    b'data: {"type":"message_stop"}\n\n'
)

REQUEST_BODY = (
    b'{"model":"deepseek-flash","max_tokens":100,"stream":true,'
    b'"messages":[{"role":"user","content":"Hi"}]}'
)


class _SSEStream(httpx.AsyncByteStream):
    """Liefert den SSE-Body als echten (streaming) Byte-Stream."""

    def __init__(self, body: bytes):
        self._body = body

    async def __aiter__(self):
        yield self._body

    async def aclose(self):
        pass


def _make_mock_transport(captured):
    """Erzeugt einen MockTransport, der die SSE-Antwort liefert und den Request aufzeichnet."""

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        captured["headers"] = {k.decode(): v.decode() for k, v in request.headers.raw}
        return httpx.Response(
            200,
            stream=_SSEStream(SSE_BODY),
            headers={"content-type": "text/event-stream; charset=utf-8"},
        )

    return httpx.MockTransport(handler)


def _fake_async_client_factory(mock_transport):
    """Patch factory: server client gets the mock, test client runs normally."""
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        if "transport" in kwargs:
            # own test client -> unchanged
            return real(*args, **kwargs)
        # Server-interner Client -> Mock-Transport injizieren
        return real(*args, transport=mock_transport, **kwargs)

    return factory


async def _post(server, path, body=b"{}", headers=None):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app),
        base_url="http://test",
    ) as client:
        return await client.post(path, content=body, headers=headers or {})


def test_transparent_passthrough_and_usage():
    async def run():
        captured: dict = {}
        mock_transport = _make_mock_transport(captured)
        received = []

        server = DeepSeekProxyServer(usage_callback=received.append)

        with patch(
            "proxy.server.httpx.AsyncClient",
            side_effect=_fake_async_client_factory(mock_transport),
        ):
            resp = await _post(
                server,
                "/anthropic/v1/messages",
                body=REQUEST_BODY,
                headers={
                    "x-api-key": "sk-test-123",
                    "anthropic-version": "2023-06-01",
                    "anthropic-beta": "messages-2023-12-15",
                    "content-type": "application/json",
                },
            )
            streamed = await resp.aread()

        # 1) Body passed through byte-exact and unchanged.
        assert streamed == SSE_BODY
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

        # 2) Request-Body bytegenau an den Upstream weitergegeben.
        assert captured["body"] == REQUEST_BODY

        # 3) Header-Transparenz.
        assert captured["headers"]["x-api-key"] == "sk-test-123"
        assert captured["headers"]["anthropic-version"] == "2023-06-01"
        assert captured["headers"]["anthropic-beta"] == "messages-2023-12-15"

        # 4) Host wird auf den Upstream-Host gesetzt.
        assert captured["headers"]["host"] == "api.deepseek.com"

        # 5) Genau EIN konsolidierter Usage-Datensatz an den Callback.
        assert len(received) == 1
        usage = received[0]
        assert usage.input_tokens == 55
        assert usage.output_tokens == 12
        assert usage.model == "deepseek-flash"
        assert usage.request_id == "m1"
        # total_tokens-Fallback: input + output
        assert usage.total_tokens == 67

        # 6) Der API-Key-Fingerprint wird aus x-api-key abgeleitet (kein Klartext).
        from proxy.labels import fingerprint_key

        assert usage.api_key_fingerprint == fingerprint_key("sk-test-123")
        assert "sk-test-123" not in usage.api_key_fingerprint

    asyncio.run(run())


def test_upstream_error_maps_to_502():
    async def run():
        def failing_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        mock_transport = httpx.MockTransport(failing_handler)
        server = DeepSeekProxyServer()

        with patch(
            "proxy.server.httpx.AsyncClient",
            side_effect=_fake_async_client_factory(mock_transport),
        ):
            resp = await _post(server, "/v1/messages", body=b"{}")
            assert resp.status_code == 502

    asyncio.run(run())


def test_gzip_encoded_response_is_decompressed_for_monitoring():
    """Regression: DeepSeek compresses responses; usage must still be captured.

    The bytes forwarded to the client stay byte-exact (still gzip), but the
    monitoring copy is decompressed so the SSE parser sees the usage object.
    """
    import gzip

    gzip_body = gzip.compress(SSE_BODY)

    async def run():
        received = []

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                stream=_SSEStream(gzip_body),
                headers={
                    "content-type": "text/event-stream; charset=utf-8",
                    "content-encoding": "gzip",
                },
            )

        mock_transport = httpx.MockTransport(handler)
        server = DeepSeekProxyServer(usage_callback=received.append)

        with patch(
            "proxy.server.httpx.AsyncClient",
            side_effect=_fake_async_client_factory(mock_transport),
        ):
            resp = await _post(
                server,
                "/anthropic/v1/messages",
                body=REQUEST_BODY,
                headers={
                    "x-api-key": "sk-test-123",
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
            )
            # httpx decodes the passthrough body; the decoded result is the
            # original SSE stream, proving the gzip bytes were forwarded intact.
            streamed = await resp.aread()

        assert streamed == SSE_BODY
        assert resp.headers["content-encoding"] == "gzip"

        # Usage extraction must still work despite the compression.
        assert len(received) == 1
        usage = received[0]
        assert usage.input_tokens == 55
        assert usage.output_tokens == 12
        assert usage.total_tokens == 67

    asyncio.run(run())


def test_zstd_encoded_response_is_decompressed_for_monitoring():
    """Regression: DeepSeek answers VS Code with ``zstd``; usage must be captured."""
    import zstandard

    zstd_body = zstandard.ZstdCompressor().compress(SSE_BODY)

    async def run():
        received = []

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                stream=_SSEStream(zstd_body),
                headers={
                    "content-type": "text/event-stream; charset=utf-8",
                    "content-encoding": "zstd",
                },
            )

        mock_transport = httpx.MockTransport(handler)
        server = DeepSeekProxyServer(usage_callback=received.append)

        with patch(
            "proxy.server.httpx.AsyncClient",
            side_effect=_fake_async_client_factory(mock_transport),
        ):
            resp = await _post(
                server,
                "/anthropic/v1/messages",
                body=REQUEST_BODY,
                headers={
                    "x-api-key": "sk-test-123",
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
            )
            streamed = await resp.aread()

        assert streamed == SSE_BODY
        assert resp.headers["content-encoding"] == "zstd"

        assert len(received) == 1
        usage = received[0]
        assert usage.input_tokens == 55
        assert usage.output_tokens == 12
        assert usage.total_tokens == 67

    asyncio.run(run())
