# DeepSeek API Proxy — Token Usage Monitor

> **Note:** This is an independent open-source project. It is not developed,
> sponsored, endorsed, or operated by DeepSeek (深度求索).

The proxy is the core of this project: it sits between a client that speaks the
Anthropic Messages API and DeepSeek's Anthropic-compatible API, forwards the
request, streams the response, and records token usage for cost analysis.

By default it exposes `POST /anthropic/v1/messages` and `POST /v1/messages`
and forwards requests to
`https://api.deepseek.com/anthropic/v1/messages`. The request body is passed
to the upstream as raw bytes, and response-body chunks are streamed back
without parsing or re-encoding them. HTTP transport headers are not forwarded
byte-for-byte: hop-by-hop headers are removed, and headers such as `Host` and
`Content-Length` are handled for the upstream/streaming connection.

In parallel, a separate usage path observes the upstream stream, parses
Server-Sent Events incrementally, merges usage reported across events, and
persists one usage record in SQLite. Monitoring errors are logged and should
not alter the response body sent to the client.

## Supported clients

The proxy works with clients that can be configured to send Anthropic Messages
API requests to a custom base URL. The documented setup below covers VS Code's
custom Anthropic-compatible endpoint.

Copilot CLI, Claude Code, and other clients may be usable when their current
configuration supports a compatible Anthropic Messages endpoint and API-key
header. Compatibility is client/version/configuration dependent; this proxy
does not translate OpenAI Chat Completions, the Copilot protocol, or any other
non-Anthropic API into Anthropic Messages requests. Check each client's current
documentation before configuring it.

## Prerequisites

- Python 3.10+
- Linux (e.g. Raspberry Pi OS) or Windows 10/11

## Installation

Linux / macOS shell:

```bash
cd deepseek-proxy
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Windows PowerShell:

```powershell
cd deepseek-proxy
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

On Windows, the requirements include the optional tray dependencies through a
`sys_platform == 'win32'` marker.

## Start

Linux / macOS shell:

```bash
.venv/bin/python main.py
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe main.py
```

Options:

| Option | Default | Description |
| --- | --- | --- |
| `--host` | `127.0.0.1` | Bind address |
| `--port` | `8000` | Bind port |
| `--upstream` | `https://api.deepseek.com/anthropic/v1/messages` | Upstream API URL |
| `--db` | Storage default path | SQLite database path |
| `--no-dashboard` | Off | Do not register the web dashboard |
| `--view` | Off | Start only the Tkinter detail view; no proxy server |
| `--tray` | Off | Show a system-tray icon (Windows only) |
| `--influx-url` | `DEEPSEEK_INFLUX_URL` | InfluxDB URL (write usage) |
| `--influx-token` | `DEEPSEEK_INFLUX_TOKEN` | InfluxDB token |
| `--influx-org` | `DEEPSEEK_INFLUX_ORG` | InfluxDB organization |
| `--influx-bucket` | `DEEPSEEK_INFLUX_BUCKET` | InfluxDB bucket |
| `--host-name` | `DEEPSEEK_INFLUX_HOST_NAME` | Host label for multi-host InfluxDB use |

Stop with `Ctrl+C` (SIGINT) or SIGTERM. On Windows the optional tray icon has
an additional quit action.

## Configure VS Code

Configure a custom endpoint that supports the Anthropic Messages API:

- **Base URL:** `http://127.0.0.1:8000`
- **API route:** `/anthropic/v1/messages` (the proxy also exposes `/v1/messages`)
- **API key:** provide the DeepSeek API key in the client endpoint configuration.

The proxy does not create, issue, or persist a replacement API key. The client's
`x-api-key` header is forwarded upstream (subject to HTTP transport handling).

## Dashboard and stored data

Open the local dashboard at:

```text
http://127.0.0.1:8000/
```

It shows recorded token usage and cost calculated from the configured price
table. The default database path is `~/.local/state/deepseek/usage.db` on Linux
and `%LOCALAPPDATA%\deepseek\usage.db` on Windows. Override it with `--db`.

## Security notes

- By default the server binds to loopback (`127.0.0.1`) and the dashboard has
  no authentication. Do not expose it to an untrusted network without adding
  appropriate access controls.
- Keep your DeepSeek API key in the client configuration; do not place it in
  source control or share logs containing secrets.
- See [the project security notes](../docs/SECURITY.md).

## Tests

From the `deepseek-proxy` directory:

Linux / macOS shell:

```bash
.venv/bin/python -m pytest tests/
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/
```

## systemd

The bundled systemd unit can start the proxy at boot:

```bash
sudo cp deepseek-proxy.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now deepseek-proxy
```

## Project structure

```text
deepseek-proxy/
├── main.py                     # standalone entry point
├── deepseek-proxy.service      # systemd unit
├── requirements.txt
├── proxy/
│   ├── server.py               # streaming proxy (FastAPI + uvicorn + httpx)
│   ├── usage.py                # TokenUsage + usage merge
│   ├── sse.py                  # incremental SSE parser
│   ├── storage.py              # SQLite persistence
│   ├── dashboard.py            # dashboard and API routes
│   ├── pricing.py              # prices, validation and peak/off-peak logic
│   ├── cost_calc.py            # usage cost calculation
│   ├── labels.py               # API-key fingerprints and labels
│   ├── paths.py                # cross-platform config/state paths
│   ├── influx_sink.py          # optional InfluxDB sink
│   ├── tkinter_view.py         # Tkinter detail view (--view)
│   ├── influx_dialog.py        # InfluxDB settings dialog
│   └── tray.py                 # Windows tray icon (--tray)
└── tests/                      # pytest suite
```
