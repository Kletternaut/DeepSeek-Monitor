#!/usr/bin/env python3
"""End-to-End-Test: echter Request DURCH den lokalen Proxy.

Liest den Key aus ~/.config/deepseek/api_key (nie ausgegeben), sendet den
Request an http://127.0.0.1:18999/anthropic/v1/messages und vergleicht die
gestreamte Antwort mit der direkten Upstream-Antwort. Anschließend wird die
SQLite-DB auf den gespeicherten Usage-Eintrag geprüft.
"""

import json
import sqlite3
import sys
import urllib.request

KEY_FILE = "/home/admin/.config/deepseek/api_key"
PROXY_URL = "http://127.0.0.1:18999/anthropic/v1/messages"
DB_PATH = "/tmp/ds_e2e.db"


def _read_key() -> str:
    with open(KEY_FILE, "r", encoding="utf-8") as f:
        return f.read().strip()


def _request(url: str) -> tuple[str, bytes]:
    body = {
        "model": "deepseek-flash",
        "max_tokens": 16,
        "stream": True,
        "messages": [{"role": "user", "content": "Antworte mit: OK"}],
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
    # Letztes usage-Objekt gewinnt (message_delta).
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
    print("== 1) Direkt (Upstream) ==")
    ct_up, raw_up = _request("https://api.deepseek.com/anthropic/v1/messages")
    print(f"    Content-Type: {ct_up}, Bytes: {len(raw_up)}")
    usage_up = _usage_from_sse(raw_up)
    print(f"    usage: {json.dumps(usage_up, sort_keys=True)}")

    print("== 2) Durch den Proxy ==")
    ct_px, raw_px = _request(PROXY_URL)
    print(f"    Content-Type: {ct_px}, Bytes: {len(raw_px)}")
    usage_px = _usage_from_sse(raw_px)
    print(f"    usage: {json.dumps(usage_px, sort_keys=True)}")

    # 3) Vergleich: Antwort-Struktur (usage) muss identisch sein.
    #    Der rohe Stream enthält zufällige IDs -> wir vergleichen nur usage.
    ok = usage_up == usage_px
    print(f"== 3) usage identisch: {ok} ==")

    # 4) DB-Check: hat der Proxy den Usage-Eintrag gespeichert?
    print("== 4) SQLite-Check ==")
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
        match = (it == usage_px.get("input_tokens") and ot == usage_px.get("output_tokens"))
        print(f"    Token-Werte stimmen mit Stream überein: {match}")
    else:
        print("    KEIN Eintrag gefunden (Proxy hat nichts gespeichert!)")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
