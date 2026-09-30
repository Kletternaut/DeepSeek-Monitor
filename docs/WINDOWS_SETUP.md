# Windows Setup & Test Guide (Win11 / 25H2)

This guide describes how to build and test the Windows port.

---

## 0. Prerequisites

- Windows 11 (25H2)
- Python **3.11 or 3.12** from <https://www.python.org/downloads/>
  - Tick **"Add python.exe to PATH"**
  - **tcl/tk** must be installed (enabled by default) — the monitor is a Tkinter app
- PowerShell (pre-installed)
- Git (to clone the repository)

---

## 1. Clone the repository

```powershell
git clone https://github.com/Kletternaut/DeepSeek-Monitor.git
cd DeepSeek-Monitor
```

The Windows port is already integrated into `main` — no separate branch needed.

---

## 2. Build the standalone .exe files (PyInstaller)

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build-windows.ps1
```

The script:
1. installs `pyinstaller`, the proxy dependencies from `requirements.txt` and `tzdata`
2. builds `dist\DeepSeek-Monitor.exe` (GUI, `--windowed`)
3. builds `dist\DeepSeek-Proxy.exe` (server + web dashboard, `--console`)

**Expected result:** both `.exe` files are created without errors.

---

## 3. Test the proxy

```powershell
# start server + dashboard on port 8000:
.\dist\DeepSeek-Proxy.exe --port 8000
```

**Checks:**
- [ ] Console shows `Proxy ready at http://127.0.0.1:8000`
- [ ] Dashboard in the browser: <http://127.0.0.1:8000/> loads (HTML/JS page)
- [ ] API endpoint responds: `http://127.0.0.1:8000/api/summary` (JSON)
- [ ] No `ZoneInfoNotFoundError` in the console
- [ ] Stopping with **Ctrl+C** shuts down cleanly (`Proxy stopped`)

**Data locations:**

| Purpose | Linux | Windows |
|---|---|---|
| Config / API key | `~/.config/deepseek/` | `%APPDATA%\deepseek\` |
| State / SQLite DB | `~/.local/state/deepseek/` | `%LOCALAPPDATA%\deepseek\` |

The SQLite DB is therefore at `%LOCALAPPDATA%\deepseek\usage.db`.

---

## 4. Test the monitor

```powershell
.\dist\DeepSeek-Monitor.exe
```

**Checks:**
- [ ] GUI window opens without crashing
- [ ] No `ZoneInfoNotFoundError` (timezone `Europe/Berlin` / `Asia/Shanghai` works)
- [ ] Language: GUI starts in German (on German Windows) — otherwise switchable via menu
- [ ] Enter an API key: balance is fetched (API `https://api.deepseek.com/user/balance`)
- [ ] Data source switchable (Local/SQLite vs. InfluxDB)

**Important — shared API key:**
The API key is read from `%APPDATA%\deepseek\api_key` — by the **monitor**
(which stores and reads it) and by the proxy's validation script
(`checkpoint.py`). The running proxy server itself does **not** read the key:
it only forwards the client's `x-api-key` header unchanged.

---

## 5. Optional: proxy as an autostart task

```powershell
# start at user logon (recommended):
powershell -ExecutionPolicy Bypass -File packaging\install-proxy-service.ps1

# start at boot as SYSTEM (only if necessary):
powershell -ExecutionPolicy Bypass -File packaging\install-proxy-service.ps1 -AtStartup

# remove:
Unregister-ScheduledTask -TaskName "DeepSeek-Proxy" -Confirm:$false
```

> Note: with `-AtStartup` the proxy runs as SYSTEM and writes the DB to the
> SYSTEM profile, not the user's `%LOCALAPPDATA%`. For normal use, keep the
> default (logon).

---

## 6. VS Code as a client through the proxy (optional)

`~/.config/Code/User/chatLanguageModels.json` (Windows:
`%APPDATA%\Code\User\chatLanguageModels.json`) — see
[section 6 of `INSTALLATION.md`](INSTALLATION.md#6-set-up-vs-code-custom-endpoint);
the proxy listens on `http://127.0.0.1:8000/anthropic`.
