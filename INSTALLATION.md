# Installation

Voraussetzungen:

- Python 3.10+ (mit `venv` und `tkinter`)
- `git`

## 1. Repository klonen

```bash
git clone --branch main https://github.com/Kletternaut/DeepSeek-Monitor.git
cd DeepSeek-Monitor
```

## 2. Proxy installieren

```bash
cd deepseek-proxy
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### InfluxDB (optional)

Damit der Proxy die Usage zusätzlich in eine zentrale InfluxDB 2.x schreibt,
eine Datei `influx.env` anlegen (Rechte `0600`, wird **nicht** committet):

```ini
DEEPSEEK_INFLUX_URL=http://<INFLUX_HOST>:8086
DEEPSEEK_INFLUX_TOKEN=<TOKEN>
DEEPSEEK_INFLUX_ORG=<ORG>
DEEPSEEK_INFLUX_BUCKET=<BUCKET>
# optional:
# DEEPSEEK_INFLUX_HOST_NAME=<hostname>
```

Ohne diese Datei schreibt der Proxy nur lokal in SQLite.

### systemd-Service aktivieren

```bash
sudo cp deepseek-proxy.service /etc/systemd/system/deepseek-proxy.service
sudo systemctl daemon-reload
sudo systemctl enable --now deepseek-proxy
```

Prüfen:

```bash
systemctl is-active deepseek-proxy          # → active
curl -s -o /dev/null -w "HTTP %{http_code}\n" http://127.0.0.1:8000/   # → HTTP 200
```

## 3. Proxy starten (ohne systemd, alternativ)

```bash
cd deepseek-proxy
.venv/bin/python main.py
```

Optionen: `--host`, `--port`, `--upstream`, `--db`, `--no-dashboard`, `--view`
sowie die InfluxDB-Flags `--influx-url/--influx-token/--influx-org/--influx-bucket/--host-name`.
Siehe `deepseek-proxy/README.md`.

## 4. Monitor starten

```bash
python3 deepseek_rates_latest.py
```

Die Datenquelle (lokaler Proxy/SQLite vs. zentrale InfluxDB) wird in den
GUI-Einstellungen unter „Datenquelle" gewählt.

## 5. VS Code einrichten (Custom Endpoint)

Damit VS Code über den lokalen Proxy läuft, die Datei
`~/.config/Code/User/chatLanguageModels.json` wie folgt konfigurieren. Der
`apiKey` bleibt eine Referenz auf den im VS-Code-Secret-Store hinterlegten
Schlüssel — **kein echter Key eintragen**.

```json
[
  {
    "name": "DeepSeek Official",
    "vendor": "customendpoint",
    "apiKey": "${input:chat.lm.secret.<IHRE_SECRET_ID>}",
    "apiType": "messages",
    "models": [
      {
        "id": "deepseek-flash",
        "name": "DeepSeek V4.1 Flash",
        "url": "http://127.0.0.1:8000/anthropic",
        "toolCalling": true,
        "vision": true,
        "thinking": true,
        "supportsReasoningEffort": ["low", "high", "max"],
        "reasoningEffortFormat": "messages",
        "contextWindow": 1000000,
        "maxOutputTokens": 32768,
        "includeReasoning": true
      },
      {
        "id": "deepseek-v4-pro",
        "name": "DeepSeek V4 Pro",
        "url": "http://127.0.0.1:8000/anthropic",
        "toolCalling": true,
        "vision": false,
        "thinking": true,
        "supportsReasoningEffort": ["low", "high", "max"],
        "reasoningEffortFormat": "messages",
        "contextWindow": 1000000,
        "maxOutputTokens": 32768,
        "includeReasoning": true
      }
    ]
  }
]
```

Nach der Änderung VS Code neu laden (Reload Window).

---

Weitere Details: [`README.md`](README.md) · [`INFLUXDB.md`](INFLUXDB.md) · [`STRUCTURE.md`](STRUCTURE.md)
