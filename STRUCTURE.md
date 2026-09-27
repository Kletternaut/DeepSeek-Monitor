# Repo-Übersicht — DeepSeek-Monitor

> Verzeichnis-/Datei-Referenz: was ist wo und wofür.

Dieses Repository bündelt zwei Komponenten zur Überwachung der DeepSeek-API-Nutzung:

- **Proxy** (`deepseek-proxy/`) — Streaming-Proxy zwischen VS Code und DeepSeek, zeichnet Token-Usage auf.
- **Monitor** (`deepseek_rates_latest.py` → `deepseek_rates_v2.4.0.py`) — Tkinter-App „Kosten & Verbrauch".

---

## Root-Dateien

| Datei | Zweck |
|---|---|
| `README.md` | Einstieg: Überblick, Schnellinstallation, Display-Fix (xrdp), VS-Code-Setup |
| `INSTALLATION.md` | Schritt-für-Schritt-Anleitung für die Installation auf zusätzlichen Hosts (Proxy + Monitor + Config) |
| `INFLUXDB.md` | InfluxDB-2.x-Integration: Architektur, Schreib-/Lesepfad, Konfiguration, Downsampling |
| `changelog.md` | Änderungshistorie |
| `deepseek_rates_v2.4.0.py` | **Der Monitor** (Tkinter-App, Einzeldatei, ~5000 Zeilen). Tarif-Anzeige, Balance-Abfrage, „Kosten & Verbrauch"-Detailseite mit Zone×Host-Balkendiagramm |
| `deepseek_rates_latest.py` | Symlink → `deepseek_rates_v2.4.0.py` (stabiler Startpfad) |
| `deepseek_logo.png` | App-Icon (Fenster- und Desktop-Icon) |
| `.env.example` | Vorlage für App-Umgebungsvariablen (`DEEPSEEK_API_KEY`, `DEEPSEEK_INFLUX_*`, `DEEPSEEK_PROXY_URL`) |
| `.gitignore` | Ausschlüsse: `.copilot/`, `BACKUP/`, `DEVELOPMENT/`, `deepseek-cost-monitor/`, `.venv/`, `influx.env`, etc. |

---

## `deepseek-proxy/` — der Proxy

| Datei | Zweck |
|---|---|
| `main.py` | Einstiegspunkt. Parset Flags, baut den Usage-Callback (inkl. optionalem Influx-Sink) und startet den Server. `--view` startet nur die Tkinter-Detailansicht |
| `deepseek-proxy.service` | systemd-Unit (Auto-Start, `EnvironmentFile=influx.env`) |
| `requirements.txt` | Python-Abhängigkeiten (fastapi, uvicorn, httpx, influxdb-client, pytest) |
| `checkpoint.py` | Validierungs-Skript: Kontrollpunkte + Cache-Test gegen DeepSeek-Ground-Truth |
| `e2e_test.py` | End-to-End-Test: echter Request durch den lokalen Proxy |
| `live_test.py` | Einmaliger Live-Test: echte DeepSeek-Antwort inspizieren (Usage-Format) |
| `validation_test.py` | Vergleicht Proxy-erfasste Token/Kosten mit offiziellen DeepSeek-Quellen |

### `deepseek-proxy/proxy/` — Kernmodul

| Datei | Zweck |
|---|---|
| `server.py` | Streaming-Proxy (FastAPI + uvicorn + httpx). Reicht Requests bytegenau an DeepSeek weiter, streamt SSE-Antwort zurück. Entkoppelte Token-Überwachung |
| `usage.py` | `TokenUsage`-Datenmodell + `merge_usage` (Usage-Objekte zusammenführen) |
| `sse.py` | Inkrementeller SSE-Parser |
| `storage.py` | SQLite-Persistenz (`~/.local/state/deepseek/usage.db`) |
| `dashboard.py` | Web-Dashboard-Routen (`http://127.0.0.1:8000/`) |
| `pricing.py` | Preis-Modell + Peak/Off-Peak-Logik (USD pro 1M Token, anpassbar) |
| `cost_calc.py` | Kostenberechnung aus Token-Nutzung + Preis-Tabelle |
| `labels.py` | API-Key-Fingerprints (SHA-256, nie Klartext) + manuelle Key-Labels |
| `influx_sink.py` | InfluxDB-Sink (schreibt Usage zusätzlich in die zentrale InfluxDB) |
| `tkinter_view.py` | Tkinter-Detailansicht („Kosten & Verbrauch", read-only, ohne Proxy-Server) |

### `deepseek-proxy/tests/` — pytest-Suite

| Datei | Testet |
|---|---|
| `test_server.py` | Streaming-Proxy |
| `test_usage.py` | Usage-Modell + merge |
| `test_sse.py` | SSE-Parser |
| `test_storage.py` | SQLite-Persistenz |
| `test_dashboard.py` | Web-Dashboard |
| `test_pricing.py` | Preis-/Peak-Logik |
| `test_influx_sink.py` | InfluxDB-Sink |
| `test_tkinter_view.py` | Tkinter-Detailansicht |

---

## NICHT im Repo (nur lokal)

| Pfad | Inhalt |
|---|---|
| `BACKUP/` | Altlasten (alte `deepseek_costs*`, `gpt5/`, `deepseek_v4_tokenizer/`, SVGs) |
| `DEVELOPMENT/` | Screenshots / Entwicklungshilfen |
| `.copilot/` | Chat-Zusammenfassungen, Downsampling-Skripte (nur lokal) |
| `deepseek-cost-monitor/` | Fremdes Repo (Windows-Widget von `guanxingxing`), nicht Teil dieses Projekts |
| `.venv/`, `__pycache__/`, `.history/` | Build-/Cache-Artefakte |
