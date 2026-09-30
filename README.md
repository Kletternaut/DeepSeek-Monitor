# DeepSeek-Monitor

> **Note:** This project is an independent open-source project and is in **no
> way affiliated with DeepSeek (深度求索)**. It is not developed, sponsored,
> endorsed, or operated by DeepSeek.

**Kosten- und Verbrauchsüberwachung für die DeepSeek-API** — ein kleines
Toolkit aus zwei Komponenten: einem **Proxy** (die Datenquelle) und einer
**Monitor-GUI** (die Auswertung).

---

## Warum es dieses Tool gibt (Daseinsberechtigung des Proxys)

Die DeepSeek-API meldet über ihren Balance-Endpunkt (`/user/balance`) nur ein
**Gesamtguthaben** in einer Währung — **keine** Aufschlüsselung nach einzelnen
Requests, Modellen oder Zeitfenstern. Wer wissen will, was ein Chat, ein Tag
oder ein bestimmtes Modell **wirklich kostet**, bekommt daraus nur eine grobe
Delta-Schätzung.

Genau hier setzt der **Proxy** an: Er sitzt als transparenter Mittelsmann
zwischen deinem Client (z. B. VS Code) und der DeepSeek-API und **liest aus
jedem gestreamten Response die echten `usage`-Objekte mit** (Input-/Output-/
Cache-Tokens). Damit wird präzise Abrechnung überhaupt erst möglich:

- **Exakte Token-Zahlen pro Request** — statt der ungenauen Guthaben-Differenz.
- **Echte Kosten** unter Berücksichtigung des DeepSeek-Tarifs (Peak/Off-Peak,
  Wochenende, Cache-Hit/-Miss, je Modell).
- **Mehrere Hosts zentral** zusammenführen (via InfluxDB), inkl. Host-Filter.

Der Proxy verändert dabei **kein Byte** des Requests oder der Antwort — er
leitet sie unverändert weiter und beobachtet nur.

---

## Was genau der Proxy macht

| Aspekt | Beschreibung |
|---|---|
| **Transport** | Streaming-Proxy zwischen VS Code (Anthropic Messages API) und `https://api.deepseek.com/anthropic/v1/messages` |
| **Endpunkte** | `POST /anthropic/v1/messages` und `POST /v1/messages` |
| **Weiterleitung** | Byte-exakt und unverändert; die Antwort wird als Server-Sent Events (SSE) unverändert zurückgestreamt |
| **Nebenläufige Erfassung** | Jeder gestreamte Chunk wird inkrementell geparst, die `usage`-Objekte werden gemergt |
| **Persistenz** | SQLite (immer aktiv, lokal pro Host) + optional InfluxDB 2.x (zentral, Multi-Host) |
| **Kostenberechnung** | Preis-Modell (`pricing.py` / `cost_calc.py`) mit Peak/Off-Peak, Wochenende und Cache-Hit/-Miss |
| **Oberflächen** | Web-Dashboard (`http://127.0.0.1:8000/`), Tkinter-Detailansicht (`--view`), Windows-Tray-Icon (`--tray`) |

Die **Monitor-GUI** („Cost & Usage") ist die reine Auswertung: Sie liest die
vom Proxy gesammelten Daten aus SQLite oder InfluxDB und zeigt Tarif, Balance,
Kosten und Verbrauch an.

---

## Komponenten

| Komponente | Pfad | Zweck |
|---|---|---|
| **Proxy** | `deepseek-proxy/` | Erfasst die realen Token-/Kostendaten (Datenquelle) |
| **Monitor** | `deepseek_rates_latest.py` → `deepseek_rates_v2.4.0.py` | Tkinter-App „Cost & Usage" (Auswertung) |

## Architecture

```
Host A (VS Code + Proxy) ─┐
Host B (VS Code + Proxy) ─┼─► InfluxDB 2.x (permanent) ◄─ Monitor "Cost & Usage"
Host C (VS Code + Proxy) ─┘        (write)                 (read)
```

- **The proxy always writes** SQLite, plus InfluxDB if `influx.env` is present
  (`DEEPSEEK_INFLUX_URL/TOKEN/ORG/BUCKET`).
- **The monitor reads** either "Local (Proxy/SQLite)" or "InfluxDB (central)"
  depending on the GUI setting.

## Screenshots

The monitor app (Tkinter) — main tariff widget, "Cost & Usage" window, settings
and the full overview:

| Main widget | Cost & Usage | Settings |
|---|---|---|
| ![Main](screenshots/main.png) | ![Cost & Usage](screenshots/costs-usage.png) | ![Settings](screenshots/settings.png) |

![All windows](screenshots/all.png)

## Documentation

| File | Content |
|---|---|
| [`INSTALLATION.md`](INSTALLATION.md) | Installation (Proxy, Monitor, systemd, VS Code, Windows) |
| [`WINDOWS_SETUP.md`](WINDOWS_SETUP.md) | Windows 11: Build & Test der `.exe`-Dateien |
| [`INFLUXDB.md`](INFLUXDB.md) | InfluxDB 2.x integration in detail |
| [`STRUCTURE.md`](STRUCTURE.md) | Directory/file reference |
| [`packaging/README.md`](packaging/README.md) | PyInstaller-Build für Windows |

## Quickstart

```bash
git clone --branch main https://github.com/Kletternaut/DeepSeek-Monitor.git
cd DeepSeek-Monitor/deepseek-proxy
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python main.py            # Proxy + Web-Dashboard auf http://127.0.0.1:8000/
```

For the full steps (including `influx.env`, VS Code setup, systemd and starting
the monitor): see **[`INSTALLATION.md`](INSTALLATION.md)**.

## License

MIT — see [LICENSE](LICENSE).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).
