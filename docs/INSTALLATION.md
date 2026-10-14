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

> **Note:** the proxy decompresses `gzip`/`deflate`/`zstd` responses from the
> upstream API so token usage is captured even when DeepSeek answers with a
> compressed body (VS Code advertises `Accept-Encoding: zstd`). This requires
> the `zstandard` package, which is part of `requirements.txt`. If you already
> had the proxy installed, run `.venv/bin/pip install zstandard` and restart
> the service.

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

The bundled systemd unit loads `influx.env` automatically. A direct invocation
of `main.py` does **not** auto-load this file. For a manual shell start, replace
all placeholders with real values first, then load the file explicitly:

```bash
set -a
. ./influx.env
set +a
.venv/bin/python main.py
```

Without InfluxDB configuration the proxy writes only to local SQLite.

### Activate the systemd service

Before copying the unit, edit `deepseek-proxy.service` and replace `/home/USER`
with your installation path (and `User`/`Group` with your username):

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
python3 deepseek_rates.py
```

The data source (local proxy/SQLite vs. central InfluxDB) is selected in the
GUI settings under "Data source".

## 5. Desktop entries (optional)

The repository ships two example desktop entries so you can start the proxy and
the monitor from your desktop menu instead of the terminal:

| File | Starts |
|---|---|
| `deepseek-monitor.desktop.example` | The monitor app (Tkinter GUI) |
| `deepseek-proxy/deepseek-proxy.desktop.example` | The proxy (in a terminal, without systemd) |

To use one:

1. Copy the example to your applications directory:
   ```bash
   cp deepseek-monitor.desktop.example ~/.local/share/applications/deepseek-monitor.desktop
   cp deepseek-proxy/deepseek-proxy.desktop.example ~/.local/share/applications/deepseek-proxy.desktop
   ```
2. Edit the copied file and replace `/home/USER` in the `Icon=` and `Exec=` lines
   with your absolute installation path (the directory created by `git clone`,
   e.g. `/home/alice/DeepSeek-Monitor`). Desktop entries do **not** expand shell
   variables like `$HOME`, so an absolute path is required.

The icon files referenced by `Icon=` (`assets/deepseek-monitor-256.png` and
`assets/deepseek-proxy-256.png`) are already part of the repository, so no
separate download is needed.

## 6. Set up VS Code (custom endpoint)

The proxy's upstream is DeepSeek. It can serve clients that support a custom
Anthropic Messages-compatible base URL, including suitable VS Code / GitHub
Copilot Chat configurations and Claude Code configured to use DeepSeek as its
model backend. It does not forward requests to Anthropic's hosted Claude models.

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

## GitHub Copilot CLI (BYOK, Anthropic protocol)

The proxy exposes `/v1/messages` as an alias, so a client that sends the
Anthropic Messages protocol can use the proxy root URL. For a recent Copilot
CLI version whose BYOK provider configuration supports Anthropic Messages:

```bash
export COPILOT_PROVIDER_TYPE=anthropic
export COPILOT_PROVIDER_BASE_URL=http://127.0.0.1:8000
export COPILOT_PROVIDER_API_KEY='YOUR_DEEPSEEK_API_KEY'
export COPILOT_MODEL=deepseek-flash
copilot
```

GitHub's documented provider list names Anthropic (Claude models), not DeepSeek.
This is therefore a protocol-compatibility setup, not an officially documented
DeepSeek/Copilot pairing. Verify streaming and tool calling with your installed
CLI version. See the [Copilot CLI BYOK documentation](https://docs.github.com/en/enterprise-cloud@latest/copilot/how-tos/copilot-cli/customize-copilot/use-byok-models).

## Claude Code

Claude Code can be configured with an Anthropic Messages-compatible custom base
URL, but Anthropic explicitly does not support routing Claude Code to non-Claude
models. The following is an experimental compatibility configuration: Claude
Code requests would be sent to **DeepSeek**, not to Anthropic-hosted Claude.

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:8000/anthropic
export ANTHROPIC_API_KEY='YOUR_DEEPSEEK_API_KEY'
claude --model deepseek-flash
```

Check that your Claude Code version accepts the configured model and that its
requested features are supported by DeepSeek's Anthropic-compatible API. See
[Claude Code's gateway documentation](https://code.claude.com/docs/en/llm-gateway).

---

## Windows (Win11 / 25H2)

The monitor and proxy are cross-platform. For building, running and testing on
Windows (standalone `.exe` files, autostart task, tray icon), see
[`WINDOWS_SETUP.md`](WINDOWS_SETUP.md).

---

Weitere Details: [`README.md`](../README.md) · [`INFLUXDB.md`](INFLUXDB.md) · [`STRUCTURE.md`](STRUCTURE.md)
