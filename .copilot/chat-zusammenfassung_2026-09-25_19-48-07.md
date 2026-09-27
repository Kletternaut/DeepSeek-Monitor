# Chat-Zusammenfassung

> **Erstellt am:** 2026-09-25 · 19:48 Uhr

## Ziel

Die Detailseite „Kosten & Verbrauch" von `deepseek_rates_latest.py` sollte auf
**echte Token-Usage-Daten** umgestellt werden (statt Guthaben-Delta-Schätzung),
und für ein **Multi-Host-Szenario** (mehrere Dev-Rechner, nicht immer an) eine
**zentrale InfluxDB 2.x** als überlebende Datenquelle anbinden.

## Ausgangslage / Architektur

- Proxy `deepseek-proxy/` läuft lokal je Host (bytegenauer SSE-Forwarder) und
  schreibt Usage in SQLite `~/.local/state/deepseek/usage.db`.
- InfluxDB 2.7.10 permanent unter `http://192.168.0.32:8086` (LAN), Org `Jolly`.
- Bucket `deepseek-usage` (id `ee93ddda64c14b21`) vom User angelegt.

## Umgesetzt (Übersicht)

### Phase A — Proxy-Daten statt Schätzung (v2.3.0)
- Neue App-Version `deepseek_rates_v2.3.0.py`: `fetch_proxy_usage` (HTTP
  `/api/usage`) + `analyze_proxy_usage` (gleiche Struktur wie altes
  `analyze_history`), `_refresh_analytics_state` → nicht-blockierender Thread,
  Fallback-Hinweis statt Schätzung.
- UI-Fixes: `format_money` mit `decimals`-Parameter, Balance-Anzeige 2 Nachkommastellen,
  sprachabhängige Dezimaltrennung; Proxy-Dashboard-Timestamp ohne Offset (`fmtTs`).
- Legende (zentriert) unter dem Chart; Chart-Kopfzeile gegen Überlagerung entschärft.

### Phase B — InfluxDB 2.x (v2.4.0)
- **Schreiben (Proxy):** `proxy/influx_sink.py` (`InfluxSink`, lazy import
  `influxdb-client`); `main.py` Flags + `_build_usage_callback` (SQLite immer,
  Influx additiv best effort); `requirements.txt` + `influxdb-client>=1.39`.
  Neues `host`-Tag (Hostname) + Feld `requests`.
- **Lesen (App):** `fetch_influx_usage` (Flux + annotated-CSV-Parser via stdlib
  `csv`), `fetch_influx_hosts` (schema.tagValues), `fetch_usage`-Dispatcher.
- **GUI-Schalter „Datenquelle"** in den Einstellungen (Lokal ↔ InfluxDB) mit
  Feldern URL/Token/Org/Bucket; persistiert in `~/.config/deepseek/influx_config`
  (0600); Priorität: Datei > Env > lokal.
- **Host-Filter-Dropdown** im Kostenfenster (nur Influx-Modus).
- `changelog.md` Einträge 2.3.0 + 2.4.0; `.env.example`.

### Phase C — Downsampling (vorbereitet, nicht aktiviert)
- `.copilot/influx-downsampling/`: `downsample_daily.flux` + `README.md`
  (täglich aggregieren, Aggregat unendlich, Rohdaten 90 Tage). Einrichtung manuell
  per SSH (Passwort bleibt beim User).

## Entscheidungen (User)
1. Influx 2.x; 2. direktes Lesen in der App (Env-Schalter Influx vs. SQLite/Proxy);
3. neue Dateien/Versionen, Stand bleibt lauffähig; 4. keine Währungsumrechnung;
5. Downsampling täglich/unendlich, als Skript vorbereitet; 6. GUI-Schalter ergänzt.

## Wichtige Bugfixes im Verlauf
- **GUI-Schalter „Datenquelle" übernahm nicht:** Startwert des Dropdowns war
  `"local"`/`"influx"`, aber die Auswahl-Werte hießen `"Lokal (Proxy/SQLite)"`/
  `"InfluxDB (zentral)"` → Vergleich schlug immer fehl. Fix: Startwert auf den
  echten Anzeige-String gesetzt.
- **„Keine Daten" nach Umschalten:** Proxy schrieb nicht in InfluxDB, weil die
  systemd-Unit keine Influx-Konfiguration hatte → Bucket leer. Fix:
  `deepseek-proxy/influx.env` (0600) angelegt, Unit um `EnvironmentFile=` erweitert,
  `daemon-reload` + `restart`. Danach flossen die ersten Events (Log:
  „InfluxDB-Sink aktiviert … host=raspi5").

## Verifizierte Fakten
- Proxy `/api/usage`-Format: `timestamp` (Berlin-ISO+Offset), `model`, `input_tokens`
  (Cache-MISS), `output_tokens`, `cache_read_tokens` (Cache-HIT), `cost_usd`, `tariff`.
- Influx Roundtrip: Schreiben → `fetch_influx_usage` lesen korrekt (host/model/cost/tokens).
- Echter `InfluxSink.write()` → `cost_usd` exakt nach Formel.
- Host-Filter: `schema.tagValues` → `['hostA','hostB']`; Filter alle=2/hostA=1/hostB=1.
- Testpunkte (`host=test-verify`, `hostA`, `hostB`) jeweils wieder gelöscht; Bucket leer.
- **81 Proxy-Tests grün** (74 bestehend + 7 neu), keine Regression.

## Dateien (neu/geändert)
- `deepseek_rates_v2.4.0.py` (neu), `deepseek_rates_v2.3.0.py` (neu), Symlink
  `deepseek_rates_latest.py` → v2.4.0.
- `deepseek-proxy/proxy/influx_sink.py` (neu), `main.py`, `requirements.txt`,
  `deepseek-proxy.service` (EnvironmentFile), `influx.env` (neu, 0600, ignoriert).
- `deepseek-proxy/tests/test_influx_sink.py` (neu).
- `.copilot/INFLUXDB.md` (Dokumentation), `.copilot/influx-downsampling/`.
- `changelog.md`, `.gitignore`, `.env.example`.

## Offene Punkte / Empfehlungen
- **Downsampling noch nicht aktiviert** (Bucket-Retention `deepseek-usage` steht
  noch auf 0/unendlich). Skript + Anleitung liegen bereit; User führt es per SSH aus.
- **Token rotieren** empfohlen (war im Chat-/Terminal-Verlauf sichtbar).
- `.env`-Loader für die App nicht eingebaut (kein python-dotenv); Env-Vars müssen
  real gesetzt sein bzw. GUI-Schalter nutzen.
