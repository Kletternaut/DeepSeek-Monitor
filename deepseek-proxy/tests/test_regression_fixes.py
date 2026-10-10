"""Regression tests for fixes to usage capture, date filtering and pricing validation."""

import asyncio

import httpx
from fastapi import FastAPI

from proxy.dashboard import add_dashboard_routes
from proxy.storage import Storage
from proxy.usage import TokenUsage, extract_usage_from_event


def test_extract_usage_from_nested_message_start_object():
    usage = extract_usage_from_event(
        {
            "type": "message_start",
            "message": {
                "id": "msg_nested",
                "model": "deepseek-flash",
                "usage": {
                    "input_tokens": 123,
                    "output_tokens": 1,
                    "cache_read_input_tokens": 50,
                },
            },
        }
    )

    assert usage is not None
    assert usage.input_tokens == 123
    assert usage.output_tokens == 1
    assert usage.cache_read_input_tokens == 50
    assert usage.request_id == "msg_nested"


def test_calendar_date_filters_include_the_whole_end_date(tmp_path):
    storage = Storage(str(tmp_path / "usage.db"))
    storage.save_usage(
        TokenUsage(timestamp="2026-09-25T00:01:00+02:00", input_tokens=10)
    )
    storage.save_usage(
        TokenUsage(timestamp="2026-09-25T23:59:59+02:00", input_tokens=20)
    )
    storage.save_usage(
        TokenUsage(timestamp="2026-09-26T00:00:00+02:00", input_tokens=30)
    )

    rows = storage.get_usage(start="2026-09-25", end="2026-09-25")

    assert len(rows) == 2
    assert {row["input_tokens"] for row in rows} == {10, 20}


def test_dashboard_summary_uses_explicit_date_range(tmp_path):
    async def run():
        storage = Storage(str(tmp_path / "usage.db"))
        storage.save_usage(
            TokenUsage(
                timestamp="2026-09-25T23:59:59+02:00",
                input_tokens=100,
                output_tokens=10,
                model="deepseek-flash",
            )
        )
        storage.save_usage(
            TokenUsage(
                timestamp="2026-09-26T00:00:00+02:00",
                input_tokens=999,
                output_tokens=99,
                model="deepseek-flash",
            )
        )
        app = FastAPI()
        add_dashboard_routes(app, storage)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/summary?start=2026-09-25&end=2026-09-25"
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["requests"] == 1
        assert payload["total_input_tokens"] == 100

    asyncio.run(run())


def test_dashboard_rejects_invalid_pricing(tmp_path):
    async def run():
        storage = Storage(str(tmp_path / "usage.db"))
        app = FastAPI()
        add_dashboard_routes(app, storage)
        invalid = {
            "pricing": {
                "deepseek-flash": {
                    "off_peak": {
                        "cache_hit": 0.003,
                        "cache_miss": 0.15,
                        "output": 0.6,
                    },
                    "peak": {
                        "cache_hit": 0.001,
                        "cache_miss": 0.30,
                        "output": 1.2,
                    },
                }
            }
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/api/pricing", json=invalid)

        assert response.status_code == 422

    asyncio.run(run())
