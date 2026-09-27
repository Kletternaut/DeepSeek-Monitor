#!/usr/bin/env python3
"""Einmaliger Live-Test: echte DeepSeek-Anthropic-Antwort inspizieren.

Liest den API-Key aus ~/.config/deepseek/api_key (wird NIE ausgegeben) und
sendet einen kleinen Streaming-Request. Gibt ausschließlich die Usage-
Feldnamen/Werte des Streams aus, damit wir das tatsächliche Format sehen.
"""

import json
import sys
import urllib.request

KEY_FILE = "/home/admin/.config/deepseek/api_key"
URL = "https://api.deepseek.com/anthropic/v1/messages"


def main() -> int:
    with open(KEY_FILE, "r", encoding="utf-8") as f:
        api_key = f.read().strip()

    body = {
        "model": "deepseek-flash",
        "max_tokens": 32,
        "stream": True,
        "messages": [{"role": "user", "content": "Sag in einem Wort: Hallo"}],
    }
    req = urllib.request.Request(
        URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )

    print("== Request gesendet, lese Stream ... ==")
    with urllib.request.urlopen(req, timeout=60) as resp:
        print(f"HTTP {resp.status}, Content-Type: {resp.headers.get('Content-Type')}")
        # Roh ausgeben (erste ~2000 Zeichen), damit wir das echte Format sehen.
        raw = resp.read().decode("utf-8", errors="replace")
        print("== ROHE SSE-ANTWORT ==")
        print(raw[:2000])
        print("== ENDE ROHE ANTWORT ==")

        # Usage-Objekte extrahieren (nur Feldnamen + Werte, kein Key).
        print("== USAGE-OBJEKTE IM STREAM ==")
        found = False
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            try:
                obj = json.loads(payload)
            except (ValueError, TypeError):
                continue
            if isinstance(obj, dict) and isinstance(obj.get("usage"), dict):
                found = True
                print(f"  type={obj.get('type')!r}  usage={json.dumps(obj['usage'], sort_keys=True)}")
        if not found:
            print("  (kein 'usage'-Objekt gefunden)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
