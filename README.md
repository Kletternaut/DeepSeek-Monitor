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

Ausführliche Schritt-für-Schritt-Anleitung: **[`INSTALLATION.md`](INSTALLATION.md)**

Verzeichnis-/Datei-Referenz (was ist wo, was macht was): **[`STRUCTURE.md`](STRUCTURE.md)**

Kurzfassung:

```bash
git clone --branch main https://github.com/Kletternaut/DeepSeek-Monitor.git
cd DeepSeek-Monitor/deepseek-proxy
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# influx.env anlegen (0600, wird NICHT via Git verteilt)
sudo cp deepseek-proxy.service /etc/systemd/system/
sudo systemctl enable --now deepseek-proxy
```

Proxy-Quellcode im Repository:
<https://github.com/Kletternaut/DeepSeek-Monitor/tree/main/deepseek-proxy>

---

## Konfiguration

- **Proxy schreibt** immer SQLite, zusätzlich InfluxDB, wenn `influx.env`
  vorhanden ist (`DEEPSEEK_INFLUX_URL/TOKEN/ORG/BUCKET`).
- **Monitor liest** je nach GUI-Einstellung „Lokal (Proxy/SQLite)" oder
  „InfluxDB (zentral)".
- VS Code: `chatLanguageModels.json` → Modell-URL auf `http://127.0.0.1:8000/anthropic`
  umbiegen (Secret unverändert lassen).

Details zur InfluxDB-Integration: **[`INFLUXDB.md`](INFLUXDB.md)**

---

## VS Code einrichten (Custom Endpoint)

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
