# DeepSeek Kosten-Monitoring (Proxy + Monitor)

Zwei Komponenten zur Überwachung der DeepSeek-API-Nutzung:

| Komponente | Datei | Aufgabe |
|---|---|---|
| **Proxy** | `deepseek-proxy/` | Streaming-Proxy (VS Code → DeepSeek), zeichnet Token-Usage auf, schreibt SQLite + optional InfluxDB |
| **Monitor** | `deepseek_rates_latest.py` → `deepseek_rates_v2.4.0.py` | Tkinter-App „Kosten & Verbrauch", liest aus lokalem Proxy/SQLite oder zentraler InfluxDB |

Architektur:

```
Host A (VS Code + Proxy) ─┐
Host B (VS Code + Proxy) ─┼─► InfluxDB 2.x (permanent) ◄─ Monitor „Kosten & Verbrauch"
Host C (VS Code + Proxy) ─┘        (write)                 (read)
```

---

## Installation auf einem Host

Ausführliche Schritt-für-Schritt-Anleitung: **[`.copilot/INSTALLATION.md`](.copilot/INSTALLATION.md)**

Kurzfassung:

```bash
git clone --branch realtime-cost-monitor https://github.com/Kletternaut/DeepSeek.git deepseek
cd ~/deepseek/deepseek-proxy
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# influx.env anlegen (0600, wird NICHT via Git verteilt)
sudo cp deepseek-proxy.service /etc/systemd/system/
sudo systemctl enable --now deepseek-proxy
```

---

## WICHTIG: `DISPLAY` beim Start des Monitors (xrdp)

Der Monitor ist eine **Tkinter-App** und benötigt ein gültiges `$DISPLAY`. Auf
den Raspberry-Pi-Hosts läuft der Desktop jedoch über **xrdp auf Display `:10`**
(`Xorg :10`) — nicht auf dem Standard-Display `:0`. Im Desktop-Startkontext ist
`$DISPLAY` deshalb **leer**, der Monitor bricht ab und das Icon scheint „nichts
zu tun".

**Fix:** Im Desktop-Entry mit `env DISPLAY=:10.0` starten:

```ini
[Desktop Entry]
Type=Application
Name=DeepSeek Monitor
Icon=/home/admin/deepseek/deepseek_logo.png
Exec=env DISPLAY=:10.0 python3 /home/admin/deepseek/deepseek_rates_latest.py
Terminal=false
```

Aktives Display ermitteln:

```bash
ps aux | grep -E "Xorg|Xvnc|Xvfb" | grep -v grep
ls -la /tmp/.X11-unix/     # z. B. X0, X10
```

Schnelltest:

```bash
DISPLAY=:10.0 timeout 5 python3 ~/deepseek/deepseek_rates_latest.py
```

> Zusätzlich kann ein **altes Desktop-Icon** (`~/Desktop/deepseek.desktop`) auf
> inzwischen entfernte Dateien (`deepseek_rates.py`, `DeepSeek.svg`) zeigen —
> auch dann tut sich nichts. Details siehe `INSTALLATION.md`, Abschnitt 2.7.

---

## Konfiguration

- **Proxy schreibt** immer SQLite, zusätzlich InfluxDB, wenn `influx.env`
  vorhanden ist (`DEEPSEEK_INFLUX_URL/TOKEN/ORG/BUCKET`).
- **Monitor liest** je nach GUI-Einstellung „Lokal (Proxy/SQLite)" oder
  „InfluxDB (zentral)".
- VS Code: `chatLanguageModels.json` → Modell-URL auf `http://127.0.0.1:8000/anthropic`
  umbiegen (Secret unverändert lassen).

Details zur InfluxDB-Integration: **[`.copilot/INFLUXDB.md`](.copilot/INFLUXDB.md)**
