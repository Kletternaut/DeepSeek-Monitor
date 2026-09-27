"""Tests for the dashboard routes (usage/summary/labels/pricing)."""

import asyncio

import httpx
from fastapi import FastAPI

from proxy.dashboard import add_dashboard_routes
from proxy.labels import fingerprint_key
from proxy.storage import Storage
from proxy.usage import TokenUsage, now_iso_berlin


def _make_app(storage):
    app = FastAPI()
    add_dashboard_routes(app, storage)
    return app


async def _get(app, path):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(path)


async def _post(app, path, json_body=None):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(path, json=json_body)


def _seed(storage):
    """Insert two usage events with different keys/models."""
    ts = now_iso_berlin()
    storage.save_usage(
        TokenUsage(
            timestamp=ts,
            input_tokens=1000,
            output_tokens=500,
            cache_read_input_tokens=400,
            model="deepseek-flash",
            request_id="r1",
            api_key_fingerprint=fingerprint_key("sk-a"),
        )
    )
    storage.save_usage(
        TokenUsage(
            timestamp=ts,
            input_tokens=200,
            output_tokens=100,
            cache_read_input_tokens=0,
            model="deepseek-v4-pro",
            request_id="r2",
            api_key_fingerprint=fingerprint_key("sk-b"),
        )
    )


def test_index_returns_html():
    async def run():
        storage = Storage()  # temporary default path is fine for this test
        app = _make_app(storage)
        resp = await _get(app, "/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "DeepSeek" in resp.text

    asyncio.run(run())


def test_summary_and_usage(tmp_path):
    async def run():
        storage = Storage(str(tmp_path / "test.db"))
        _seed(storage)

        app = _make_app(storage)

        # Summary
        resp = await _get(app, "/api/summary?days=7")
        assert resp.status_code == 200
        s = resp.json()
        assert s["requests"] == 2
        assert s["total_input_tokens"] == 1200
        assert s["total_output_tokens"] == 600
        assert s["total_cost"] > 0
        assert s["peak"]["requests"] + s["off_peak"]["requests"] == 2
        assert len(s["daily"]) >= 1
        assert len(s["per_label"]) == 2  # zwei verschiedene Fingerprints

        # Usage (Einzelevents, angereichert)
        resp = await _get(app, "/api/usage?limit=10")
        events = resp.json()
        assert len(events) == 2
        e = events[0]
        assert "cost_usd" in e and isinstance(e["cost_usd"], float)
        assert "tariff" in e
        assert "label" in e

    asyncio.run(run())


def test_usage_filter_by_label(tmp_path):
    async def run():
        storage = Storage(str(tmp_path / "test.db"))
        _seed(storage)
        fp_a = fingerprint_key("sk-a")
        storage.set_label(fp_a, "MeinKey")

        app = _make_app(storage)

        resp = await _get(app, "/api/usage?label=MeinKey&limit=10")
        events = resp.json()
        assert len(events) == 1
        assert events[0]["label"] == "MeinKey"
        assert events[0]["fingerprint"] == fp_a

    asyncio.run(run())


def test_labels_crud(tmp_path):
    async def run():
        storage = Storage(str(tmp_path / "test.db"))
        _seed(storage)
        fp_a = fingerprint_key("sk-a")

        app = _make_app(storage)

        # GET /api/labels
        resp = await _get(app, "/api/labels")
        labels = resp.json()
        assert len(labels) == 2

        # POST /api/labels
        resp = await _post(app, "/api/labels", {"fingerprint": fp_a, "label": "Testlabel"})
        assert resp.json() == {"ok": True}
        assert storage.get_label(fp_a) == "Testlabel"

    asyncio.run(run())


def test_pricing_get_and_set(tmp_path):
    async def run():
        storage = Storage(str(tmp_path / "test.db"))
        app = _make_app(storage)

        resp = await _get(app, "/api/pricing")
        pricing = resp.json()
        assert "deepseek-flash" in pricing

        custom = {
            "deepseek-flash": {
                "off_peak": {"cache_hit": 0.001, "cache_miss": 0.1, "output": 0.5},
                "peak": {"cache_hit": 0.002, "cache_miss": 0.2, "output": 1.0},
            }
        }
        resp = await _post(app, "/api/pricing", {"pricing": custom})
        assert resp.json() == {"ok": True}

        resp = await _get(app, "/api/pricing")
        assert resp.json() == custom

    asyncio.run(run())
