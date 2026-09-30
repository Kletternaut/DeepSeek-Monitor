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
3. (Optional) If the desktop environment does not trust the file yet, mark it
   executable:
   ```bash
   chmod +x ~/.local/share/applications/deepseek-monitor.desktop
   ```

The icon files referenced by `Icon=` (`assets/deepseek-monitor-256.png` and
`assets/deepseek-proxy-256.png`) are already part of the repository, so no
separate download is needed.

## 6. Set up VS Code (custom endpoint)

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

## Windows (Win11 / 25H2)

The monitor and proxy are cross-platform. On Windows the application data is
stored in the native locations instead of the XDG directories:

| Purpose | Linux | Windows |
|---|---|---|
| Config / API key | `~/.config/deepseek/` | `%APPDATA%\deepseek\` |
| State / SQLite DB | `~/.local/state/deepseek/` | `%LOCALAPPDATA%\deepseek\` |

The API key is read from `%APPDATA%\deepseek\api_key` on Windows — by the
**monitor** (which stores and reads it) and by the proxy's validation script
(`checkpoint.py`). The running proxy server itself does **not** read the key:
it only forwards the client's `x-api-key` header unchanged.

### A. Build standalone .exe files (PyInstaller)

Prerequisites:

- Python 3.11 or 3.12 from <https://www.python.org/downloads/> — tick
  **"Add python.exe to PATH"**; the **tcl/tk** option must be installed
  (enabled by default), because the monitor is a Tkinter app.
- The repository checked out (see section 1).

Build (PowerShell):

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build-windows.ps1
```

This produces `dist\DeepSeek-Monitor.exe` (GUI) and `dist\DeepSeek-Proxy.exe`
(server + web dashboard). `tzdata` is bundled so `zoneinfo` works without a
system timezone database.

### B. Start the proxy

```powershell
# simple (server + dashboard on port 8000):
.\dist\DeepSeek-Proxy.exe

# or via the wrapper that also loads influx.env:
powershell -ExecutionPolicy Bypass -File packaging\start-proxy.ps1 --port 8000
```

The web dashboard is available at <http://127.0.0.1:8000/>.

### C. Run the proxy as a scheduled task (autostart, optional)

```powershell
# start at LOGON of the current user (API key readable from %APPDATA%):
powershell -ExecutionPolicy Bypass -File packaging\install-proxy-service.ps1

# or start at BOOT as SYSTEM (then set DEEPSEEK_API_KEY manually):
powershell -ExecutionPolicy Bypass -File packaging\install-proxy-service.ps1 -AtStartup

# remove again:
Unregister-ScheduledTask -TaskName "DeepSeek-Proxy" -Confirm:$false
```

### D. Start the monitor

Double-click `dist\DeepSeek-Monitor.exe` (or create a shortcut to it). The
monitor has no autostart by default; start it manually.

### E. Configure VS Code on Windows

The `chatLanguageModels.json` entry is identical to section 6 above — the proxy
still listens on `http://127.0.0.1:8000/anthropic`.

---

Weitere Details: [`README.md`](README.md) · [`INFLUXDB.md`](INFLUXDB.md) · [`STRUCTURE.md`](STRUCTURE.md)
