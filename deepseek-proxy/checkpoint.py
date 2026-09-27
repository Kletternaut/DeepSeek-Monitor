#!/usr/bin/env python3
"""
Validierungs-Skript: Kontrollpunkte + kontrollierter Cache-Test.

Dient zur Validierung der Proxy-Kostenrechnung gegen die Ground-Truth
(https://platform.deepseek.com/usage). Der API-Key wird aus
~/.config/deepseek/api_key gelesen und NIE ausgegeben.

Modi:
  .venv/bin/python checkpoint.py save <label> [--usage N]
      Speichert einen Kontrollpunkt (DB-Snapshot + optional die aktuell auf der
      Usage-Seite abgelesene Gesamt-Token-Zahl).

  .venv/bin/python checkpoint.py list
      Listet alle gespeicherten Kontrollpunkte.

  .venv/bin/python checkpoint.py compare <label> [--usage N]
      Zeigt das Delta (Requests/Token/Kosten) seit dem Kontrollpunkt. Mit
      --usage N wird zusätzlich das Usage-Seiten-Delta (N - usage_im_kontrollpunkt)
      ausgegeben und mit dem DB-Delta verglichen.

  .venv/bin/python checkpoint.py cachetest [--words 2000]
      Sendet ZWEI identische große Requests durch den Proxy:
      Request 1 = Cache-MISS (frisch), Request 2 = Cache-HIT (gecacht).
      Zeigt beide Messungen + den Kostenvergleich.

WICHTIG zur Isolation des laufenden Chats: Während der Testphase (Sekunden)
solltest du im Chat nichts Neues senden. Der Test misst sein Delta über die
Proxy-DB exakt; die Usage-Seite dient nur als unabhängige Gegenprobe.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
import urllib.request

KEY_FILE = "/home/admin/.config/deepseek/api_key"
PROXY_URL = "http://127.0.0.1:8000/anthropic/v1/messages"
DB_PATH = "/home/admin/.local/state/deepseek/usage.db"
CHECKPOINT_FILE = "/home/admin/.local/state/deepseek/checkpoints.json"

# Proxy-Module importierbar machen (Skript liegt im Projekt-Root).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def read_key() -> str:
    with open(KEY_FILE, "r", encoding="utf-8") as f:
        return f.read().strip()


def _db_snapshot() -> dict:
    """Liest den aktuellen DB-Stand (Totals + max_id)."""
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
    print(f"Kontrollpunkt '{label}' gespeichert:")
    print(f"  Zeit:        {cps[label]['timestamp']}")
    print(f"  DB-Events:   {snap['requests']} (max_id={snap['max_id']})")
    print(f"  Token gesamt:{_total_tokens(snap):,}")
    print(f"  Kosten:      ${snap['cost_usd']:.6f}")
    if usage_tokens is not None:
        print(f"  Usage-Seite: {usage_tokens:,} Tokens")


def cmd_list() -> None:
    cps = _load_checkpoints()
    if not cps:
        print("Keine Kontrollpunkte vorhanden.")
        return
    print("Kontrollpunkte:")
    for label, cp in cps.items():
        u = cp.get("usage_page_tokens")
        ustr = f"{u:,}" if u is not None else "-"
        print(f"  {label}: {cp['timestamp']} | DB-Tokens={cp['total_tokens']:,} "
              f"| Usage-Seite={ustr} | ${cp['cost_usd']:.6f}")


def cmd_compare(label: str, usage_tokens: int | None) -> None:
    cps = _load_checkpoints()
    if label not in cps:
        print(f"Kontrollpunkt '{label}' nicht gefunden. Verfügbar: {list(cps)}")
        return 1
    cp = cps[label]
    snap = _db_snapshot()

    # Delta nur für Events NACH dem Kontrollpunkt (id > max_id).
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

    print(f"Delta seit Kontrollpunkt '{label}' ({cp['timestamp']}):")
    print(f"  Neue Requests:    {d['requests']}")
    print(f"  Input (Miss):     {d['input']:,}")
    print(f"  Cache-Read (Hit): {d['cache_read']:,}")
    print(f"  Output:           {d['output']:,}")
    print(f"  Token gesamt:     {d_tokens:,}")
    print(f"  Kosten:           ${d['cost_usd']:.6f}")

    if usage_tokens is not None and cp.get("usage_page_tokens") is not None:
        usage_delta = usage_tokens - cp["usage_page_tokens"]
        print()
        print("  === Vergleich mit Usage-Seite (Ground-Truth) ===")
        print(f"  Usage-Seite vorher:  {cp['usage_page_tokens']:,}")
        print(f"  Usage-Seite nachher: {usage_tokens:,}")
        print(f"  Usage-Delta:         {usage_delta:,}")
        print(f"  Proxy-DB-Delta:      {d_tokens:,}")
        diff = usage_delta - d_tokens
        print(f"  Differenz:           {diff:,}  "
              f"({'Übereinstimmung' if diff == 0 else 'Abweichung'})")
    elif usage_tokens is not None and cp.get("usage_page_tokens") is None:
        print()
        print("  Hinweis: Kontrollpunkt hat keine Usage-Seiten-Tokens gespeichert.")


def _send_request(prompt: str) -> dict:
    """Sendet einen Request durch den Proxy und liest den zuletzt erfassten Eintrag."""
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
    """Erzeugt einen großen, GARANTIERT EINZIGARTIGEN Prompt (~words Wörter).

    Der Text enthält eine zufällige Nonce in JEDER Zeile, damit er sich über
    Testläufe hinweg NICHT per Präfix-Cache wiedertreffen kann. Nur so ist
    Request 1 eines Laufs ein echter Cache-MISS und Request 2 (identisch)
    ein echter Cache-HIT.
    """
    nonce = secrets.token_hex(8)
    parts = []
    i = 0
    while sum(len(p.split()) for p in parts) < words:
        parts.append(
            f"Absatz {i} nonce {nonce} zufallswort {secrets.token_hex(4)}: "
            f"Dies ist ein eindeutiger Testtext für die Cache-Validierung. "
            f"Er enthält eine zufällige Kennung {nonce}-{i}, damit jeder "
            f"Testlauf garantiert frisch ist und keinen Präfix-Cache trifft."
        )
        i += 1
    return " ".join(parts)


def cmd_cachetest(words: int) -> None:
    print(f"=== CACHE-TEST (Prompt ~{words} Wörter) ===")
    print("Erzeuge großen, eindeutigen Prompt ...")
    prompt = make_large_prompt(words)
    approx_tokens = len(prompt.split())  # grobe Schätzung
    print(f"Prompt: {len(prompt.split())} Wörter (~{approx_tokens} Tokens)")

    print("\n--- Request 1 (erwartet: Cache-MISS) ---")
    r1 = _send_request(prompt)
    print(f"  Input (Miss):   {r1['input']:,}")
    print(f"  Cache-Read:     {r1['cache_read']:,}")
    print(f"  Output:         {r1['output']:,}")
    print(f"  Kosten:         ${r1['cost_usd']:.8f}")

    print("\n--- Request 2 (identisch, erwartet: Cache-HIT) ---")
    r2 = _send_request(prompt)
    print(f"  Input (Miss):   {r2['input']:,}")
    print(f"  Cache-Read:     {r2['cache_read']:,}")
    print(f"  Output:         {r2['output']:,}")
    print(f"  Kosten:         ${r2['cost_usd']:.8f}")

    print("\n=== AUSWERTUNG ===")
    hit_detected = r2["cache_read"] > r1["cache_read"]
    print(f"Cache-HIT erkannt: {'JA' if hit_detected else 'NEIN'} "
          f"(Request 2 cache_read={r2['cache_read']:,} vs Request 1 "
          f"cache_read={r1['cache_read']:,})")
    print(f"Kostenersparnis durch Cache: ${r1['cost_usd'] - r2['cost_usd']:.8f}")
    print()
    print("=> Vergleiche: (Input+CacheRead+Output) der beiden Requests mit dem "
          "Usage-Seiten-Delta (vorher/nachher abgelesen).")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    def _usage_int(value: str) -> int:
        """Parst eine Token-Zahl; toleriert Kommas/Unterstriche/Leerzeichen."""
        cleaned = value.replace(",", "").replace("_", "").replace(" ", "")
        return int(cleaned)

    p_save = sub.add_parser("save", help="Kontrollpunkt speichern")
    p_save.add_argument("label")
    p_save.add_argument("--usage", type=_usage_int, default=None,
                        help="aktuelle Gesamt-Token-Zahl von der Usage-Seite")

    sub.add_parser("list", help="Kontrollpunkte auflisten")

    p_cmp = sub.add_parser("compare", help="Delta seit Kontrollpunkt")
    p_cmp.add_argument("label")
    p_cmp.add_argument("--usage", type=_usage_int, default=None,
                       help="aktuelle Gesamt-Token-Zahl von der Usage-Seite")

    p_ct = sub.add_parser("cachetest", help="Cache-MISS/HIT-Test")
    p_ct.add_argument("--words", type=int, default=2000,
                      help="Prompt-Größe in Wörtern (Default 2000)")

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
