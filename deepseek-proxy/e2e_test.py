#!/usr/bin/env python3
"""End-to-end test: capture and persist token usage through the local proxy.

The proxy must be started on port 18999 with --db pointing at the same
temporary database used by this script. The API key is read from the local
configuration file and is never printed.
"""

import json
import os
import sqlite3
import sys
import tempfile
import urllib.request

# Make the proxy modules importable (script lives inside deepseek-proxy/).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from proxy.paths import API_KEY_FILE  # noqa: E402

KEY_FILE = API_KEY_FILE
PROXY_URL = "http://127.0.0.1:18999/anthropic/v1/messages"
DB_PATH = os.path.join(tempfile.gettempdir(), "ds_e2e.db")


def _read_key() -> str:
    with open(KEY_FILE, encoding="utf-8") as f:
        key = f.read().strip()
    if not key:
        raise RuntimeError(f"No API key found in {KEY_FILE}")
    return key


def _request(url: str) -> tuple[str, bytes]:
    body = {
        "model": "deepseek-flash",
        "max_tokens": 16,
        "stream": True,
        "messages": [{"role": "user", "content": "Reply with: OK"}],
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "x-api-key": _read_key(),
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.headers.get("Content-Type", ""), resp.read()


def _merged_usage_from_sse(raw: bytes) -> dict:
    """Merge usage found in either documented message_start or delta shape."""
    text = raw.decode("utf-8", errors="replace")
    merged: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        try:
            obj = json.loads(line[5:].strip())
        except (ValueError, TypeError):
            continue
        if not isinstance(obj, dict):
            continue

        usage = obj.get("usage")
        if not isinstance(usage, dict):
            message = obj.get("message")
            usage = message.get("usage") if isinstance(message, dict) else None
        if isinstance(usage, dict):
            merged.update(usage)
    return merged


def _latest_database_id() -> int | None:
    if not os.path.exists(DB_PATH):
        return None
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT MAX(id) FROM usage_events").fetchone()
    return row[0] if row and row[0] is not None else None


def main() -> int:
    print("== 1) Direct request to verify upstream usage format ==")
    ct_up, raw_up = _request("https://api.deepseek.com/anthropic/v1/messages")
    usage_up = _merged_usage_from_sse(raw_up)
    print(f"    Content-Type: {ct_up}, Bytes: {len(raw_up)}")
    print(f"    Usage fields: {sorted(usage_up)}")

    before_id = _latest_database_id()

    print("== 2) Request through the proxy ==")
    ct_px, raw_px = _request(PROXY_URL)
    usage_px = _merged_usage_from_sse(raw_px)
    print(f"    Content-Type: {ct_px}, Bytes: {len(raw_px)}")
    print(f"    Usage fields: {sorted(usage_px)}")

    format_ok = (
        "text/event-stream" in ct_up.lower()
        and "text/event-stream" in ct_px.lower()
        and "input_tokens" in usage_up
        and "output_tokens" in usage_up
        and "input_tokens" in usage_px
        and "output_tokens" in usage_px
    )
    print(f"== 3) Both streams contain merged input/output usage: {format_ok} ==")

    print("== 4) SQLite persistence check ==")
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT id, api_key_fingerprint, model, input_tokens, output_tokens, "
            "cache_creation_tokens, cache_read_tokens, total_tokens "
            "FROM usage_events ORDER BY id DESC LIMIT 1"
        ).fetchone()

    db_ok = False
    if row:
        entry_id, fp, model, input_tokens, output_tokens, cache_create, cache_read, total = row
        is_new = before_id is None or entry_id > before_id
        print(f"    New usage entry: {is_new}")
        print(f"    fingerprint={fp[:16]}… model={model}")
        print(
            f"    input={input_tokens} output={output_tokens} "
            f"cache_create={cache_create} cache_read={cache_read} total={total}"
        )
        db_ok = (
            is_new
            and input_tokens == usage_px.get("input_tokens", 0)
            and output_tokens == usage_px.get("output_tokens", 0)
            and cache_create == usage_px.get("cache_creation_input_tokens", 0)
            and cache_read == usage_px.get("cache_read_input_tokens", 0)
        )
        print(f"    Stored counts match the proxied stream: {db_ok}")
    else:
        print("    NO entry found (proxy saved nothing!)")

    success = format_ok and db_ok
    print(f"== E2E result: {'PASS' if success else 'FAIL'} ==")
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
