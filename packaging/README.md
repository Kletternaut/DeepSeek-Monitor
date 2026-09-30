# Windows-Packaging (Win11 / 25H2)

Scripts and instructions for building and running the DeepSeek Monitor and
Proxy as standalone Windows executables.

## Files

| File | Purpose |
|---|---|
| `build-windows.ps1` | Builds `DeepSeek-Monitor.exe` and `DeepSeek-Proxy.exe` with PyInstaller |
| `start-proxy.ps1` | Starts the proxy from the built `.exe` and loads `influx.env` |
| `install-proxy-service.ps1` | Registers the proxy as a scheduled task (autostart, optional) |

## Build

See [`../INSTALLATION.md`](../INSTALLATION.md) section "Windows (Win11 / 25H2)".

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build-windows.ps1
```

Output: `dist\DeepSeek-Monitor.exe` (GUI) and `dist\DeepSeek-Proxy.exe`
(server + web dashboard).

## Notes for developers

- **`tzdata`** is bundled (`--collect-data tzdata`) because Windows has no
  IANA timezone database; `zoneinfo` would otherwise raise
  `ZoneInfoNotFoundError`.
- **`uvloop`** is deliberately not used: the proxy pins `uvicorn` (not
  `uvicorn[standard]`) and runs `loop="asyncio"`, so there is no Windows
  incompatibility.
- The monitor is built `--windowed` (no console), the proxy `--console`
  (visible logs). Both are `--onefile`.
- Data locations differ per OS (see `docs/INSTALLATION.md`); the shared API key
  lives in `%APPDATA%\deepseek\api_key` on Windows.
