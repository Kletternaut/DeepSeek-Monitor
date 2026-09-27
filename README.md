# DeepSeek-Monitor

Überwachung der DeepSeek-API-Nutzung — bestehend aus zwei Komponenten:

| Komponente | Datei | Aufgabe |
|---|---|---|
| **Proxy** | `deepseek-proxy/` | Streaming-Proxy (VS Code → DeepSeek), zeichnet Token-Usage auf, schreibt SQLite + optional InfluxDB |
| **Monitor** | `deepseek_rates_latest.py` → `deepseek_rates_v2.4.0.py` | Tkinter-App „Kosten & Verbrauch", liest aus lokalem Proxy/SQLite oder zentraler InfluxDB |

## Architektur

```
Host A (VS Code + Proxy) ─┐
Host B (VS Code + Proxy) ─┼─► InfluxDB 2.x (permanent) ◄─ Monitor „Kosten & Verbrauch"
Host C (VS Code + Proxy) ─┘        (write)                 (read)
```

- **Proxy schreibt** immer SQLite, zusätzlich InfluxDB, wenn `influx.env`
  vorhanden ist (`DEEPSEEK_INFLUX_URL/TOKEN/ORG/BUCKET`).
- **Monitor liest** je nach GUI-Einstellung „Lokal (Proxy/SQLite)" oder
  „InfluxDB (zentral)".

## Dokumentation

| Datei | Inhalt |
|---|---|
| [`INSTALLATION.md`](INSTALLATION.md) | Installation (Proxy, Monitor, systemd, VS Code) |
| [`INFLUXDB.md`](INFLUXDB.md) | InfluxDB-2.x-Integration im Detail |
| [`STRUCTURE.md`](STRUCTURE.md) | Verzeichnis-/Datei-Referenz |

## Quickstart

```bash
git clone --branch main https://github.com/Kletternaut/DeepSeek-Monitor.git
cd DeepSeek-Monitor/deepseek-proxy
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
sudo cp deepseek-proxy.service /etc/systemd/system/
sudo systemctl enable --now deepseek-proxy
```

Vollständige Schritte (inkl. `influx.env`, VS-Code-Setup und Monitor-Start):
siehe **[`INSTALLATION.md`](INSTALLATION.md)**.
