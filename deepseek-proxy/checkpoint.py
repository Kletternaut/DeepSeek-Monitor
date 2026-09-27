#!/usr/bin/env python3
"""
Validation script: checkpoints + controlled cache test.

Used to validate the proxy cost calculation against the ground truth
(https://platform.deepseek.com/usage). The API key is read from
~/.config/deepseek/api_key and NEVER printed.

Modes:
  .venv/bin/python checkpoint.py save <label> [--usage N]
      Save a checkpoint (DB snapshot + optionally the total token count
      currently read off the usage page).

  .venv/bin/python checkpoint.py list
      List all saved checkpoints.

  .venv/bin/python checkpoint.py compare <label> [--usage N]
      Show the delta (requests/tokens/cost) since the checkpoint. With
      --usage N the usage-page delta (N - usage_at_checkpoint) is additionally
      printed and compared to the DB delta.

  .venv/bin/python checkpoint.py cachetest [--words 2000]
      Send TWO identical large requests through the proxy:
      Request 1 = cache MISS (fresh), Request 2 = cache HIT (cached).
      Shows both measurements + the cost comparison.

IMPORTANT for isolating the running chat: during the test phase (seconds) you
should not send anything new in the chat. The test measures its delta exactly
via the proxy DB; the usage page only serves as an independent cross-check.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
import urllib.request

KEY_FILE = os.path.expanduser("~/.config/deepseek/api_key")
PROXY_URL = "http://127.0.0.1:8000/anthropic/v1/messages"
DB_PATH = os.path.expanduser("~/.local/state/deepseek/usage.db")
CHECKPOINT_FILE = os.path.expanduser("~/.local/state/deepseek/checkpoints.json")

# Make the proxy modules importable (script lives in the project root).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def read_key() -> str:
    with open(KEY_FILE, "r", encoding="utf-8") as f:
        return f.read().strip()


def _db_snapshot() -> dict:
    """Read the current DB state (totals + max_id)."""
    from proxy.cost_calc import calc_usage_cost
    from proxy.pricing import load_pricing
    from proxy.storage import Storage, row_to_usage

    storage = Storage(DB_PATH)
    pricing = load_pricing(storage)
    rows = storage.get_usage(limit=None)

    snap = {
        "max_id": rows[0]["id"] if rows else 0,
        "requests": len(rows),
        "input": 0,
        "cache_read": 0,
        "output": 0,
        "cost_usd": 0.0,
    }
    for r in rows:
        u = row_to_usage(r)
        snap["input"] += u.input_tokens
        snap["cache_read"] += u.cache_read_input_tokens
        snap["output"] += u.output_tokens
        snap["cost_usd"] += calc_usage_cost(u, pricing)
    snap["cost_usd"] = round(snap["cost_usd"], 8)
    return snap


def _load_checkpoints() -> dict:
    if not os.path.exists(CHECKPOINT_FILE):
        return {}
    try:
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_checkpoints(data: dict) -> None:
    os.makedirs(os.path.dirname(CHECKPOINT_FILE), exist_ok=True)
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _total_tokens(snap: dict) -> int:
    return snap["input"] + snap["cache_read"] + snap["output"]


def cmd_save(label: str, usage_tokens: int | None) -> None:
    snap = _db_snapshot()
    cps = _load_checkpoints()
    cps[label] = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "max_id": snap["max_id"],
        "requests": snap["requests"],
        "input": snap["input"],
        "cache_read": snap["cache_read"],
        "output": snap["output"],
        "total_tokens": _total_tokens(snap),
        "cost_usd": snap["cost_usd"],
        "usage_page_tokens": usage_tokens,
    }
    _save_checkpoints(cps)
    print(f"Checkpoint '{label}' saved:")
    print(f"  Time:        {cps[label]['timestamp']}")
    print(f"  DB events:   {snap['requests']} (max_id={snap['max_id']})")
    print(f"  Total tokens:{_total_tokens(snap):,}")
    print(f"  Cost:        ${snap['cost_usd']:.6f}")
    if usage_tokens is not None:
        print(f"  Usage page:  {usage_tokens:,} tokens")


def cmd_list() -> None:
    cps = _load_checkpoints()
    if not cps:
        print("No checkpoints present.")
        return
    print("Checkpoints:")
    for label, cp in cps.items():
        u = cp.get("usage_page_tokens")
        ustr = f"{u:,}" if u is not None else "-"
        print(f"  {label}: {cp['timestamp']} | DB-tokens={cp['total_tokens']:,} "
              f"| usage-page={ustr} | ${cp['cost_usd']:.6f}")


def cmd_compare(label: str, usage_tokens: int | None) -> None:
    cps = _load_checkpoints()
    if label not in cps:
        print(f"Checkpoint '{label}' not found. Available: {list(cps)}")
        return 1
    cp = cps[label]
    snap = _db_snapshot()

    # Delta only for events AFTER the checkpoint (id > max_id).
    from proxy.cost_calc import calc_usage_cost
    from proxy.pricing import load_pricing
    from proxy.storage import Storage, row_to_usage

    storage = Storage(DB_PATH)
    pricing = load_pricing(storage)
    rows = storage.get_usage(limit=None)
    new_rows = [r for r in rows if r["id"] > cp["max_id"]]

    d = {
        "requests": len(new_rows),
        "input": 0, "cache_read": 0, "output": 0, "cost_usd": 0.0,
    }
    for r in new_rows:
        u = row_to_usage(r)
        d["input"] += u.input_tokens
        d["cache_read"] += u.cache_read_input_tokens
        d["output"] += u.output_tokens
        d["cost_usd"] += calc_usage_cost(u, pricing)
    d["cost_usd"] = round(d["cost_usd"], 8)
    d_tokens = d["input"] + d["cache_read"] + d["output"]

    print(f"Delta since checkpoint '{label}' ({cp['timestamp']}):")
    print(f"  New requests:    {d['requests']}")
    print(f"  Input (miss):    {d['input']:,}")
    print(f"  Cache-read (hit):{d['cache_read']:,}")
    print(f"  Output:          {d['output']:,}")
    print(f"  Total tokens:    {d_tokens:,}")
    print(f"  Cost:            ${d['cost_usd']:.6f}")

    if usage_tokens is not None and cp.get("usage_page_tokens") is not None:
        usage_delta = usage_tokens - cp["usage_page_tokens"]
        print()
        print("  === Comparison with usage page (ground truth) ===")
        print(f"  Usage page before:  {cp['usage_page_tokens']:,}")
        print(f"  Usage page after:   {usage_tokens:,}")
        print(f"  Usage delta:        {usage_delta:,}")
        print(f"  Proxy DB delta:     {d_tokens:,}")
        diff = usage_delta - d_tokens
        print(f"  Difference:         {diff:,}  "
              f"({'match' if diff == 0 else 'mismatch'})")
    elif usage_tokens is not None and cp.get("usage_page_tokens") is None:
        print()
        print("  Note: checkpoint has no usage-page tokens stored.")


def _send_request(prompt: str) -> dict:
    """Send a request through the proxy and read the last recorded entry."""
    body = {
        "model": "deepseek-flash",
        "max_tokens": 32,
        "stream": True,
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        PROXY_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "x-api-key": read_key(),
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        resp.read()
    time.sleep(1.0)

    from proxy.cost_calc import calc_usage_cost
    from proxy.pricing import load_pricing
    from proxy.storage import Storage, row_to_usage

    storage = Storage(DB_PATH)
    pricing = load_pricing(storage)
    u = row_to_usage(storage.get_usage(limit=1)[0])
    return {
        "input": u.input_tokens,
        "cache_read": u.cache_read_input_tokens,
        "output": u.output_tokens,
        "cost_usd": calc_usage_cost(u, pricing),
    }


def make_large_prompt(words: int) -> str:
    """Create a large, GUARANTEED-UNIQUE prompt (~words words).

    The text contains a random nonce in EVERY line so that it cannot re-hit the
    prefix cache across test runs. Only this way is request 1 of a run a real
    cache MISS and request 2 (identical) a real cache HIT.
    """
    nonce = secrets.token_hex(8)
    parts = []
    i = 0
    while sum(len(p.split()) for p in parts) < words:
        parts.append(
            f"Paragraph {i} nonce {nonce} randomword {secrets.token_hex(4)}: "
            f"This is a unique test text for cache validation. "
            f"It contains a random identifier {nonce}-{i}, so every "
            f"test run is guaranteed fresh and does not hit a prefix cache."
        )
        i += 1
    return " ".join(parts)


def cmd_cachetest(words: int) -> None:
    print(f"=== CACHE TEST (prompt ~{words} words) ===")
    print("Generating large, unique prompt ...")
    prompt = make_large_prompt(words)
    approx_tokens = len(prompt.split())  # rough estimate
    print(f"Prompt: {len(prompt.split())} words (~{approx_tokens} tokens)")

    print("\n--- Request 1 (expected: cache MISS) ---")
    r1 = _send_request(prompt)
    print(f"  Input (miss):   {r1['input']:,}")
    print(f"  Cache-read:     {r1['cache_read']:,}")
    print(f"  Output:         {r1['output']:,}")
    print(f"  Cost:           ${r1['cost_usd']:.8f}")

    print("\n--- Request 2 (identical, expected: cache HIT) ---")
    r2 = _send_request(prompt)
    print(f"  Input (miss):   {r2['input']:,}")
    print(f"  Cache-read:     {r2['cache_read']:,}")
    print(f"  Output:         {r2['output']:,}")
    print(f"  Cost:           ${r2['cost_usd']:.8f}")

    print("\n=== EVALUATION ===")
    hit_detected = r2["cache_read"] > r1["cache_read"]
    print(f"Cache HIT detected: {'YES' if hit_detected else 'NO'} "
          f"(request 2 cache_read={r2['cache_read']:,} vs request 1 "
          f"cache_read={r1['cache_read']:,})")
    print(f"Cost saving via cache: ${r1['cost_usd'] - r2['cost_usd']:.8f}")
    print()
    print("=> Compare: (input+cache-read+output) of the two requests with the "
          "usage-page delta (read before/after).")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    def _usage_int(value: str) -> int:
        """Parse a token count; tolerate commas/underscores/spaces."""
        cleaned = value.replace(",", "").replace("_", "").replace(" ", "")
        return int(cleaned)

    p_save = sub.add_parser("save", help="Save a checkpoint")
    p_save.add_argument("label")
    p_save.add_argument("--usage", type=_usage_int, default=None,
                        help="current total token count from the usage page")

    sub.add_parser("list", help="List checkpoints")

    p_cmp = sub.add_parser("compare", help="Delta since checkpoint")
    p_cmp.add_argument("label")
    p_cmp.add_argument("--usage", type=_usage_int, default=None,
                       help="current total token count from the usage page")

    p_ct = sub.add_parser("cachetest", help="Cache MISS/HIT test")
    p_ct.add_argument("--words", type=int, default=2000,
                      help="prompt size in words (default 2000)")

    args = parser.parse_args()

    if args.cmd == "save":
        cmd_save(args.label, args.usage)
    elif args.cmd == "list":
        cmd_list()
    elif args.cmd == "compare":
        return cmd_compare(args.label, args.usage) or 0
    elif args.cmd == "cachetest":
        cmd_cachetest(args.words)
    return 0


if __name__ == "__main__":
    sys.exit(main())
