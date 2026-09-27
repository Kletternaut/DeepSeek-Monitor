# Repo-Übersicht — DeepSeek-Monitor

> Verzeichnis-/Datei-Referenz: was ist wo und wofür.

Dieses Repository bündelt zwei Komponenten zur Überwachung der DeepSeek-API-Nutzung:

- **Proxy** (`deepseek-proxy/`) — Streaming-Proxy zwischen VS Code und DeepSeek, zeichnet Token-Usage auf.
- **Monitor** (`deepseek_rates_latest.py` → `deepseek_rates_v2.4.0.py`) — Tkinter-App „Kosten & Verbrauch".

---

## Projektstruktur

```
.
├── README.md                    # Einstieg: Überblick, Schnellinstallation, VS-Code-Setup
├── INSTALLATION.md              # Schritt-für-Schritt-Anleitung: Installation auf zusätzlichen Hosts
├── INFLUXDB.md                  # InfluxDB-2.x-Integration: Architektur, Schreib-/Lesepfad, Downsampling
├── STRUCTURE.md                 # Diese Datei: Verzeichnis-/Datei-Referenz
├── changelog.md                 # Änderungshistorie
├── deepseek_rates_v2.4.0.py     # Der Monitor (Tkinter-App, Einzeldatei): Tarif, Balance, "Kosten & Verbrauch" (Zone×Host-Balkendiagramm)
├── deepseek_rates_latest.py     # Symlink → deepseek_rates_v2.4.0.py (stabiler Startpfad)
├── deepseek_logo.png            # App-Icon (Fenster- und Desktop-Icon)
├── .env.example                 # Vorlage für App-Umgebungsvariablen (DEEPSEEK_API_KEY, DEEPSEEK_INFLUX_*, DEEPSEEK_PROXY_URL)
├── .gitignore                   # Ausschlüsse (siehe Datei selbst)
│
└── deepseek-proxy/
    ├── main.py                  # Standalone-Einstiegspunkt (Flags + Usage-Callback, --view = nur Tkinter-Ansicht)
    ├── deepseek-proxy.service   # systemd-Unit (Auto-Start, EnvironmentFile=influx.env)
    ├── requirements.txt         # Abhängigkeiten (fastapi, uvicorn, httpx, influxdb-client, pytest)
    ├── checkpoint.py            # Validierung: Kontrollpunkte + Cache-Test gegen DeepSeek-Ground-Truth
    ├── e2e_test.py              # End-to-End-Test: echter Request durch den lokalen Proxy
    ├── live_test.py             # Einmaliger Live-Test: echte DeepSeek-Antwort (Usage-Format) inspizieren
    ├── validation_test.py       # Vergleicht Proxy-erfasste Token/Kosten mit offiziellen DeepSeek-Quellen
    ├── proxy/
    │   ├── __init__.py          # Paket-Markierung
    │   ├── server.py            # Streaming-Proxy (FastAPI + uvicorn + httpx)
    │   ├── usage.py             # TokenUsage + merge_usage
    │   ├── sse.py               # Inkrementeller SSE-Parser
    │   ├── storage.py           # SQLite-Persistenz (~/.local/state/deepseek/usage.db)
    │   ├── dashboard.py         # Web-Dashboard-Routen (http://127.0.0.1:8000/)
    │   ├── pricing.py           # Preis-Modell + Peak/Off-Peak-Logik
    │   ├── cost_calc.py         # Kostenberechnung aus Token-Nutzung + Preis-Tabelle
    │   ├── labels.py            # API-Key-Fingerprints (SHA-256) + Key-Labels
    │   ├── influx_sink.py       # InfluxDB-Sink (schreibt Usage zusätzlich in die zentrale InfluxDB)
    │   └── tkinter_view.py      # Tkinter-Detailansicht ("Kosten & Verbrauch", read-only, ohne Proxy-Server)
    └── tests/                   # pytest-Suite
        ├── test_server.py       # Streaming-Proxy
        ├── test_usage.py        # Usage-Modell + merge
        ├── test_sse.py          # SSE-Parser
        ├── test_storage.py      # SQLite-Persistenz
        ├── test_dashboard.py    # Web-Dashboard
        ├── test_pricing.py      # Preis-/Peak-Logik
        ├── test_influx_sink.py  # InfluxDB-Sink
        └── test_tkinter_view.py # Tkinter-Detailansicht
```
