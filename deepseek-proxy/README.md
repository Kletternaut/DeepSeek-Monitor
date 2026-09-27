# DeepSeek Anthropic Proxy

> **Note:** This project is an independent open-source project and is in **no
> way affiliated with DeepSeek (深度求索)**. It is not developed, sponsored,
> endorsed, or operated by DeepSeek.

A slim **streaming proxy** between VS Code (Anthropic Messages API) and
DeepSeek. The proxy exposes the endpoints `POST /anthropic/v1/messages` and
`POST /v1/messages`, forwards requests **byte-exact and unchanged** to
`https://api.deepseek.com/anthropic/v1/messages`, and streams the response
(Server-Sent Events) back unchanged.

Alongside the transport runs a decoupled **token usage monitor**: a copy of
every upstream chunk is parsed incrementally, the `usage` objects are merged and
persisted in SQLite. A **web dashboard** shows real usage and cost data.

## Prerequisites

- Python 3.10+
- Linux (e.g. Raspberry Pi OS)

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

Options:

| Option           | Default                                          | Description                      |
| ---------------- | ------------------------------------------------ | -------------------------------- |
| `--host`         | `127.0.0.1`                                       | Bind address                     |
| `--port`         | `8000`                                            | Bind port                        |
| `--upstream`     | `https://api.deepseek.com/anthropic/v1/messages`  | Upstream URL of the DeepSeek API |
| `--db`           | storage default path                             | Path to the SQLite database      |
| `--no-dashboard` | off                                              | Do not register the web dashboard |

Stop with `Ctrl+C` (SIGINT) or SIGTERM — the server shuts down cleanly.

## Set up VS Code

Configure as a **custom endpoint** in VS Code:

- `apiType = "messages"`
- **Base URL**: `http://127.0.0.1:8000`

The proxy resolves the path `/anthropic/v1/messages`. The **API key is
configured in the VS Code custom endpoint** and passed through transparently by
the proxy via header (`x-api-key`) — the proxy creates or stores no API key of
its own.

## Dashboard

Open in the browser:

```
http://127.0.0.1:8000/
```

Shows real recorded token usage and the costs calculated from it.

## Data

SQLite database at:

```
~/.local/state/deepseek/usage.db
```

The path can be overridden via `--db`.

## Tests

```bash
.venv/bin/python -m pytest tests/
```

## systemd

The bundled unit `deepseek-proxy.service` starts the proxy at boot:

```bash
sudo cp deepseek-proxy.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now deepseek-proxy
```

## Project structure

```
deepseek-proxy/
├── main.py                     # standalone entry point
├── deepseek-proxy.service      # systemd unit
├── requirements.txt
├── proxy/
│   ├── server.py               # streaming proxy (FastAPI + uvicorn + httpx)
│   ├── usage.py                # TokenUsage + merge_usage
│   ├── sse.py                  # incremental SSE parser
│   ├── storage.py              # SQLite persistence
│   ├── dashboard.py            # web dashboard routes
│   ├── pricing.py              # prices / peak detection
│   ├── cost_calc.py            # cost calculation
│   └── labels.py               # key labels / fingerprints
└── tests/                      # pytest suite
```
