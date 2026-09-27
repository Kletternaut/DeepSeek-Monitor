# Changelog

## 2.4.0 — 2026-09-25

- Optionale **zentrale InfluxDB 2.x** als Datenquelle für die Detailseite „Kosten & Verbrauch" (multi-host).
- **Konfigurations-Schalter über Env-Variablen** (keine Hardcodes): `DEEPSEEK_INFLUX_URL`, `DEEPSEEK_INFLUX_TOKEN`, `DEEPSEEK_INFLUX_ORG`, `DEEPSEEK_INFLUX_BUCKET`. Sind diese gesetzt, liest die App direkt aus InfluxDB (HTTP-Query-API, Flux); sonst weiterhin über den lokalen Proxy `/api/usage` (unverändertes Verhalten).
- **Proxy-Schreibpfad**: neues Modul `deepseek-proxy/proxy/influx_sink.py`. Der Proxy schreibt die Usage zusätzlich in InfluxDB, wenn `--influx-url` (bzw. `DEEPSEEK_INFLUX_URL`) gesetzt ist. SQLite bleibt immer aktiv; Influx ist rein additiv und best effort.
- Neues `host`-Tag (Hostname via `--host-name` bzw. `DEEPSEEK_INFLUX_HOST_NAME`), damit mehrere Entwicklungs-Hosts unterscheidbar sind.
- Neues Feld `requests` (Wert 1) pro Event für spätere Tagesaggregation.
- Dependency `influxdb-client` ergänzt.
- Downsampling-Konzept vorbereitet: tägliche Aggregation über einen Flux-Task.
- **Host-Filter** in der Detailseite (Dropdown „Host"), sichtbar nur im InfluxDB-Modus; listet alle Hosts und filtert die Daten je Host („Alle Hosts" = aggregiert über alle).
- **GUI-Schalter „Datenquelle"** in den Einstellungen: umschalten zwischen „Lokal (Proxy/SQLite)" und „InfluxDB (zentral)" inkl. Eingabefeldern für URL/Token/Org/Bucket. Die Werte werden lokal in `~/.config/deepseek/influx_config` (Datei `0600`) persistiert; ohne GUI-Eintrag greifen weiterhin die Env-Variablen `DEEPSEEK_INFLUX_*`.

## 2.3.0 — 2026-09-25

- Die Detailseite „Kosten & Verbrauch" nutzt jetzt die **echten** Token-Usage-Daten des Proxys (`deepseek-proxy/`, HTTP `GET /api/usage`) statt der ungenauen Guthaben-Delta-Schätzung. Dadurch bleiben die Werte auch bei Tageswechsel korrekt, unabhängig davon, ob die App läuft.
- Proxy-Basis-URL über Umgebungsvariable `DEEPSEEK_PROXY_URL` überschreibbar (Default `http://127.0.0.1:8000`).
- Realtime-Balance-Kopf (Gesamt/Aufgeladen/Geschenkt) kommt weiterhin aus der `/user/balance`-API.
- Intervalle-Tabelle zeigt jetzt einen Eintrag pro Request (statt zeitanteilig geschätzter Intervalle).
- Bei nicht erreichbarem Proxy erscheint ein Hinweis statt der alten Schätzung.
- Währung der Detailseite wechselt auf USD (Proxy rechnet USD pro 1M Token).

## 2.2.9 — 2026-08-30

- Version number is now part of the file name (`deepseek_rates_v2.2.9.py`); `deepseek_rates_latest.py` symlink always points to the newest version, and `~/Desktop/deepseek.desktop` runs the symlink (no update needed on version bumps).
- Old versioned files are kept as version history and fallback from now on.

## 2.2.8 — 2026-08-30

- Replaced the colored 📌 pin emoji on the "always on top" button with the monochrome ⚲ symbol to match the style of the other action buttons.

## 2.2.7 — 2026-08-30

- Costs & usage: Days without consumption now appear as an empty slot with date in the daily breakdown (previously they were skipped entirely).
- Analytics chart: No "0.0000" label is drawn above the axis for days without consumption.
- Version information (`APP_VERSION`) introduced in the source code and shown in the settings header.
- Introduced changelog.md; will be updated with every change from now on.
