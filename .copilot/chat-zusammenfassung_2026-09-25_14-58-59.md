# Chat-Zusammenfassung

> **Erstellt am:** 2026-09-25 · 14:35 Uhr

## Ziel

Ein lokaler HTTP-Proxy für den DeepSeek-Kostenmonitor, der die **echten** Token-Usage-Daten aus dem Anthropic-SSE-Stream abgreift (statt Schätzungen aus Guthaben-Deltas), mit genauer Detailseite und API-Key-Filter.

## Umgesetzt (Projekt `/home/admin/deepseek/deepseek-proxy/`)

- `proxy/sse.py` — inkrementeller SSE-Parser
- `proxy/usage.py` — `TokenUsage` + Merge + `api_key_fingerprint`
- `proxy/server.py` — `DeepSeekProxyServer` (bytegenauer Streaming-Proxy, Header-transparent, Uvicorn im Thread)
- `proxy/storage.py` — SQLite (`~/.local/state/deepseek/usage.db`)
- `proxy/labels.py` — SHA-256-Fingerprint + Labels
- `proxy/pricing.py` — anpassbare Preise + Peak/Off-Peak
- `proxy/cost_calc.py` — Kostenrechnung
- `proxy/dashboard.py` — Web-Dashboard (`/` + `/api/*`)
- `proxy/tkinter_view.py` — Tkinter-Detailseite
- `main.py`, `deepseek-proxy.service`, `README.md`
- **74 Tests grün** (pytest)

## Orchestrierung

- Coding → `deepseek-flash`-Subagenten
- Tests + Review → `deepseek-v4-pro`

## Verifizierte Fakten

- Anthropic-Endpunkt `https://api.deepseek.com/anthropic/v1/messages`, Header `x-api-key`/`anthropic-version`/`anthropic-beta`.
- Preise (USD/1M): flash/pro off-peak & peak, Cache-Hit/Miss/Output.
- **Cache-Semantik (wichtig, live belegt):** `input_tokens` = Cache-MISS, `cache_read_input_tokens` = Cache-HIT, additiv (nicht verschachtelt). Formel entsprechend korrigiert.
- `total_tokens`-Fallback = input + cache_read + output.

## Wichtige Korrekturen im Verlauf

1. `https://` → `http://` (SSL-Fehler behoben).
2. Aktive Config `chatLanguageModels.json` auf Proxy umgestellt.
3. Cache-Kostenformel korrigiert (Miss vs. Hit).
4. Cache-Test-Prompt mit Random-Nonce einzigartig gemacht.

## Offener Punkt (Validierung gegen `platform.deepseek.com/usage`)

- Live-Test läuft (Chat über Proxy, Daten in DB).
- Kontrollpunkt `baseline` gesetzt (`checkpoint.py`).
- **Ungeklärte Diskrepanz:** Usage-Seiten-Delta (Gesamtzahl) vs. Proxy-DB-Delta weichen ab (~537k Tokens). Nutzer bestätigte: Zähler ist echtzeit, keine Filter, Chat war während des Tests still, und beim Cache-Test änderte sich die Online-Gesamtzahl **nicht**.
- Meine Erklärungsversuche (Zähler-Lag, Thinking-Tokens, zwei API-Keys) waren nach Nutzeraussage **falsch** und wurden zurückgewiesen. Die tatsächliche Ursache ist **noch nicht geklärt**.

## Aktueller Zustand

Nutzer stark verärgert über wiederholte falsche Annahmen. Anweisung war, keine weiteren Vermutungen anzustellen. Die Diskrepanz-Ursache ist offen und bedarf eines faktenbasierten (keines spekulativen) Vorgehens.
