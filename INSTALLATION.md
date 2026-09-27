# Installation

Prerequisites:

- Python 3.10+ (with `venv` and `tkinter`)
- `git`

## 1. Clone the repository

```bash
git clone --branch main https://github.com/Kletternaut/DeepSeek-Monitor.git
cd DeepSeek-Monitor
```

## 2. Install the proxy

```bash
cd deepseek-proxy
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### InfluxDB (optional)

To make the proxy additionally write usage to a central InfluxDB 2.x, create an
`influx.env` file (permissions `0600`, is **not** committed):

```ini
DEEPSEEK_INFLUX_URL=http://<INFLUX_HOST>:8086
DEEPSEEK_INFLUX_TOKEN=<TOKEN>
DEEPSEEK_INFLUX_ORG=<ORG>
DEEPSEEK_INFLUX_BUCKET=<BUCKET>
# optional:
# DEEPSEEK_INFLUX_HOST_NAME=<hostname>
```

Without this file the proxy writes only to SQLite locally.

### Activate the systemd service

```bash
sudo cp deepseek-proxy.service /etc/systemd/system/deepseek-proxy.service
sudo systemctl daemon-reload
sudo systemctl enable --now deepseek-proxy
```

Verify:

```bash
systemctl is-active deepseek-proxy          # → active
curl -s -o /dev/null -w "HTTP %{http_code}\n" http://127.0.0.1:8000/   # → HTTP 200
```

## 3. Start the proxy (without systemd, alternative)

```bash
cd deepseek-proxy
.venv/bin/python main.py
```

Options: `--host`, `--port`, `--upstream`, `--db`, `--no-dashboard`, `--view`
as well as the InfluxDB flags `--influx-url/--influx-token/--influx-org/--influx-bucket/--host-name`.
See `deepseek-proxy/README.md`.

## 4. Start the monitor

```bash
python3 deepseek_rates_latest.py
```

The data source (local proxy/SQLite vs. central InfluxDB) is selected in the
GUI settings under "Data source".

## 5. Set up VS Code (custom endpoint)

To make VS Code go through the local proxy, configure the file
`~/.config/Code/User/chatLanguageModels.json` as follows. The `apiKey` remains a
reference to the key stored in the VS Code secret store — do **not** enter a
real key.

```json
[
  {
    "name": "DeepSeek Official",
    "vendor": "customendpoint",
    "apiKey": "${input:chat.lm.secret.<YOUR_SECRET_ID>}",
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

After the change, reload VS Code (Reload Window).

---

Weitere Details: [`README.md`](README.md) · [`INFLUXDB.md`](INFLUXDB.md) · [`STRUCTURE.md`](STRUCTURE.md)
