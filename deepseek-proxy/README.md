# DeepSeek Anthropic Proxy

Ein schlanker **Streaming-Proxy** zwischen VS Code (Anthropic Messages API) und
DeepSeek. Der Proxy stellt die Endpunkte `POST /anthropic/v1/messages` und
`POST /v1/messages` bereit, reicht Requests **bytegenau und unverändert** an
`https://api.deepseek.com/anthropic/v1/messages` weiter und streamt die Antwort
(Server-Sent-Events) unverändert zurück.

Neben dem Transport läuft eine entkoppelte **Token-Usage-Überwachung**: eine
Kopie jedes Upstream-Chunks wird inkrementell geparst, die `usage`-Objekte
zusammengeführt und in SQLite persistiert. Ein **Web-Dashboard** zeigt echte
Usage- und Kostendaten.

## Voraussetzungen

- Python 3.10+
- Linux (z. B. Raspberry Pi OS)

## Installation

```bash
cd ~/deepseek/deepseek-proxy
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Start

```bash
.venv/bin/python main.py
```

Optionen:

| Option           | Default                                          | Beschreibung                     |
| ---------------- | ------------------------------------------------ | -------------------------------- |
| `--host`         | `127.0.0.1`                                       | Bind-Adresse                     |
| `--port`         | `8000`                                            | Bind-Port                        |
| `--upstream`     | `https://api.deepseek.com/anthropic/v1/messages`  | Upstream-URL der DeepSeek-API    |
| `--db`           | Storage-Standardpfad                              | Pfad zur SQLite-Datenbank        |
| `--no-dashboard` | aus                                               | Web-Dashboard nicht registrieren |

Beenden mit `Strg+C` (SIGINT) oder SIGTERM — der Server fährt sauber herunter.

## VS Code einrichten

Als **Custom Endpoint** in VS Code konfigurieren:

- `apiType = "messages"`
- **Base-URL**: `http://127.0.0.1:8000`

Der Proxy löst den Pfad `/anthropic/v1/messages` auf. Der **API-Key wird im
VS-Code-Custom-Endpoint konfiguriert** und vom Proxy transparent per Header
(`x-api-key`) durchgereicht — der Proxy erzeugt oder speichert keinen eigenen
API-Key.

## Dashboard

Im Browser öffnen:

```
http://127.0.0.1:8000/
```

Zeigt real aufgezeichnete Token-Nutzung und daraus berechnete Kosten.

## Daten

SQLite-Datenbank unter:

```
~/.local/state/deepseek/usage.db
```

Der Pfad kann über `--db` überschrieben werden.

## Tests

```bash
.venv/bin/python -m pytest tests/
```

## systemd

Die mitgelieferte Unit `deepseek-proxy.service` startet den Proxy beim Booten:

```bash
sudo cp deepseek-proxy.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now deepseek-proxy
```

## Projektstruktur

```
deepseek-proxy/
├── main.py                     # Standalone-Einstiegspunkt
├── deepseek-proxy.service      # systemd-Unit
├── requirements.txt
├── proxy/
│   ├── server.py               # Streaming-Proxy (FastAPI + uvicorn + httpx)
│   ├── usage.py                # TokenUsage + merge_usage
│   ├── sse.py                  # Inkrementeller SSE-Parser
│   ├── storage.py              # SQLite-Persistenz
│   ├── dashboard.py            # Web-Dashboard-Routen
│   ├── pricing.py              # Preise / Peak-Erkennung
│   ├── cost_calc.py            # Kostenberechnung
│   └── labels.py               # Key-Labels / Fingerprints
└── tests/                      # pytest-Suite
```
