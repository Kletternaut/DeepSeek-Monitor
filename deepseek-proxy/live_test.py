#!/usr/bin/env python3
"""One-off live test: inspect a real DeepSeek-Anthropic response.

Reads the API key from ~/.config/deepseek/api_key (NEVER printed) and sends a
small streaming request. Prints exclusively the usage field names/values of the
stream so we can see the actual format.
"""

import json
import os
import sys
import urllib.request

# Make the proxy modules importable (script lives in the project root).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from proxy.paths import API_KEY_FILE  # noqa: E402

KEY_FILE = API_KEY_FILE
URL = "https://api.deepseek.com/anthropic/v1/messages"


def main() -> int:
    with open(KEY_FILE, encoding="utf-8") as f:
        api_key = f.read().strip()

    body = {
        "model": "deepseek-flash",
        "max_tokens": 32,
        "stream": True,
        "messages": [{"role": "user", "content": "Say in one word: Hello"}],
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

    print("== Request sent, reading stream ... ==")
    with urllib.request.urlopen(req, timeout=60) as resp:
        print(f"HTTP {resp.status}, Content-Type: {resp.headers.get('Content-Type')}")
        # Print raw (first ~2000 chars) so we can see the real format.
        raw = resp.read().decode("utf-8", errors="replace")
        print("== RAW SSE RESPONSE ==")
        print(raw[:2000])
        print("== END RAW RESPONSE ==")

        # Extract usage objects (only field names + values, no key).
        print("== USAGE OBJECTS IN STREAM ==")
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
                print(
                    f"  type={obj.get('type')!r}  usage={json.dumps(obj['usage'], sort_keys=True)}"
                )
        if not found:
            print("  (kein 'usage'-Objekt gefunden)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
