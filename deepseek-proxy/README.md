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
- Linux (e.g. Raspberry Pi OS) or Windows 10/11

## Installation

```bash
cd deepseek-proxy
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

On Windows the package also needs the optional extras for the tray icon
(`pystray`/`Pillow`) — they are declared in `requirements.txt` with a
`sys_platform == 'win32'` marker and installed automatically there.

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
| `--view`         | off                                              | Start only the Tkinter detail view (no proxy server) |
| `--tray`         | off                                              | Show a system-tray icon (Windows only) |
| `--influx-url`   | env `DEEPSEEK_INFLUX_URL`                         | InfluxDB URL (write usage)       |
| `--influx-token` | env `DEEPSEEK_INFLUX_TOKEN`                       | InfluxDB token                   |
| `--influx-org`   | env `DEEPSEEK_INFLUX_ORG`                         | InfluxDB organization            |
| `--influx-bucket`| env `DEEPSEEK_INFLUX_BUCKET`                      | InfluxDB bucket                  |
| `--host-name`    | env `DEEPSEEK_INFLUX_HOST_NAME`                   | Host label (multi-host)          |

Stop with `Ctrl+C` (SIGINT) or SIGTERM — the server shuts down cleanly. On
Windows the tray icon (via `--tray`) offers an additional "Beenden" action.

## Windows

For building and running standalone `.exe` files, see the Windows packaging
documentation: [`../packaging/README.md`](../packaging/README.md) and
[`../docs/WINDOWS_SETUP.md`](../docs/WINDOWS_SETUP.md).

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

SQLite database at (Linux) `~/.local/state/deepseek/usage.db` or (Windows)
`%LOCALAPPDATA%\deepseek\usage.db`. The path can be overridden via `--db`.

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
│   ├── labels.py               # key labels / fingerprints
│   ├── paths.py                # cross-platform config/state paths
│   ├── influx_sink.py          # optional InfluxDB sink
│   ├── tkinter_view.py         # Tkinter detail view (--view)
│   ├── influx_dialog.py        # InfluxDB config dialog (Tkinter)
│   └── tray.py                 # Windows tray icon (--tray)
└── tests/                      # pytest suite
```
