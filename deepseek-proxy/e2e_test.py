#!/usr/bin/env python3
"""End-to-end test: real request THROUGH the local proxy.

Reads the key from ~/.config/deepseek/api_key (never printed), sends the
request to http://127.0.0.1:18999/anthropic/v1/messages and compares the
streamed response with the direct upstream response. Then the SQLite DB is
checked for the stored usage entry.
"""

import json
import os
import sqlite3
import sys
import tempfile
import urllib.request

# Make the proxy modules importable (script lives in the project root).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from proxy.paths import API_KEY_FILE  # noqa: E402

KEY_FILE = API_KEY_FILE
PROXY_URL = "http://127.0.0.1:18999/anthropic/v1/messages"
# Deterministic temp path (works on Linux and Windows, unlike /tmp/ds_e2e.db),
# so the separately-started proxy can be pointed at the same DB via --db.
DB_PATH = os.path.join(tempfile.gettempdir(), "ds_e2e.db")


def _read_key() -> str:
    with open(KEY_FILE, encoding="utf-8") as f:
        return f.read().strip()


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


def _usage_from_sse(raw: bytes) -> dict:
    text = raw.decode("utf-8", errors="replace")
    # The last usage object wins (message_delta).
    last = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        try:
            obj = json.loads(line[5:].strip())
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict) and isinstance(obj.get("usage"), dict):
            last = obj["usage"]
    return last


def main() -> int:
    print("== 1) Direct (upstream) ==")
    ct_up, raw_up = _request("https://api.deepseek.com/anthropic/v1/messages")
    print(f"    Content-Type: {ct_up}, Bytes: {len(raw_up)}")
    usage_up = _usage_from_sse(raw_up)
    print(f"    usage: {json.dumps(usage_up, sort_keys=True)}")

    print("== 2) Through the proxy ==")
    ct_px, raw_px = _request(PROXY_URL)
    print(f"    Content-Type: {ct_px}, Bytes: {len(raw_px)}")
    usage_px = _usage_from_sse(raw_px)
    print(f"    usage: {json.dumps(usage_px, sort_keys=True)}")

    # 3) Comparison: the response structure (usage) must be identical.
    #    The raw stream contains random IDs -> we only compare usage.
    ok = usage_up == usage_px
    print(f"== 3) usage identical: {ok} ==")

    # 4) DB check: did the proxy save the usage entry?
    print("== 4) SQLite check ==")
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT api_key_fingerprint, model, input_tokens, output_tokens, "
        "cache_creation_tokens, cache_read_tokens, total_tokens "
        "FROM usage_events ORDER BY id DESC LIMIT 1"
    ).fetchall()
    conn.close()
    if rows:
        fp, model, it, ot, cc, cr, tt = rows[0]
        print(f"    fingerprint={fp[:16]}… model={model}")
        print(f"    input={it} output={ot} cache_create={cc} cache_read={cr} total={tt}")
        match = it == usage_px.get("input_tokens") and ot == usage_px.get("output_tokens")
        print(f"    Token values match the stream: {match}")
    else:
        print("    NO entry found (proxy saved nothing!)")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
