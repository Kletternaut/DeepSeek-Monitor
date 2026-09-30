# Repo overview — DeepSeek-Monitor

> Directory/file reference: what is where and what for.

> **Note:** This project is an independent open-source project and is in **no
> way affiliated with DeepSeek (深度求索)**.

This repository bundles two components for monitoring DeepSeek API usage:

- **Proxy** (`deepseek-proxy/`) — streaming proxy between VS Code and DeepSeek, records token usage.
- **Monitor** (`deepseek_rates.py` + `deepseek_monitor/`) — Tkinter app "Cost & Usage"; the pure, Tkinter-free logic lives in the `deepseek_monitor/` package.

---

## Project structure

```
.
├── README.md                    # entry point: overview, quick install, VS Code setup
├── INSTALLATION.md              # step-by-step guide: installation on additional hosts
├── WINDOWS_SETUP.md             # Windows 11: build & test the standalone .exe files
├── INFLUXDB.md                  # InfluxDB 2.x integration: architecture, write/read path, downsampling
├── STRUCTURE.md                 # this file: directory/file reference
├── changelog.md                 # change history
├── deepseek_rates.py            # the monitor (Tkinter frontend); imports the logic from deepseek_monitor/
├── deepseek-monitor.desktop.example  # desktop entry to start the monitor app manually
├── deepseek_monitor/            # pure, Tkinter-free logic of the monitor app (package)
│   ├── __init__.py              # package marker + version
│   ├── _version.py              # single source of truth for the version (dependency-free)
│   ├── config.py                # constants & colors (tariff assumptions, poll intervals, URLs)
│   ├── assets.py                # asset path resolution + window icon helper (PyInstaller-aware)
│   ├── i18n.py                  # languages & currencies (translations, currency symbols)
│   ├── paths.py                 # cross-platform paths (XDG on Linux, %APPDATA%/%LOCALAPPDATA% on Windows) & timezones
│   ├── persistence.py           # persistence (timezone, language, API key, poll, Influx, history)
│   ├── api.py                   # balance API (fetch_balance, parse_balance)
│   ├── tariff.py                # tariff logic (peak/standard/weekend)
│   └── analytics.py             # usage analysis (proxy/Influx data)
├── LICENSE                      # MIT license
├── CONTRIBUTING.md              # contribution guide
├── pyproject.toml               # project metadata + tooling (ruff/black/pytest/mypy)
├── .editorconfig                # editor conventions
├── .pre-commit-config.yaml      # pre-commit hooks (ruff, black)
├── conftest.py                  # pytest: make both packages importable
├── assets/                      # app icon (deepseek-monitor.svg + PNG sizes)
├── screenshots/                 # app screenshots (main, costs-usage, settings, all)
├── packaging/                   # Windows PyInstaller build + start/service scripts
│   ├── build-windows.ps1        # builds DeepSeek-Monitor.exe + DeepSeek-Proxy.exe
│   ├── start-proxy.ps1          # starts the proxy .exe and loads influx.env
│   ├── install-proxy-service.ps1 # registers the proxy as a scheduled task (autostart)
│   └── README.md                # Windows packaging notes
├── tests/                       # pytest suite for deepseek_monitor (tariff, API, formatting)
│   └── test_deepseek_monitor.py
├── .github/workflows/ci.yml     # CI (lint + tests, Python 3.10–3.12)
├── deepseek_logo.png            # legacy app icon (replaced by assets/deepseek-monitor-*.png)
├── .env.example                 # template for app environment variables (DEEPSEEK_API_KEY, DEEPSEEK_INFLUX_*, DEEPSEEK_PROXY_URL)
├── .gitignore                   # exclusions (see the file itself)
│
└── deepseek-proxy/
    ├── main.py                  # standalone entry point (flags + usage callback, --view = Tkinter view only, --tray = tray icon)
    ├── deepseek-proxy.service   # systemd unit (auto-start, EnvironmentFile=influx.env)
    ├── deepseek-proxy.desktop.example  # desktop entry to start the proxy manually (instead of systemd)
    ├── requirements.txt         # dependencies (fastapi, uvicorn, httpx, influxdb-client, pytest)
    ├── checkpoint.py            # validation: checkpoints + cache test against DeepSeek ground truth
    ├── e2e_test.py              # end-to-end test: real request through the local proxy
    ├── live_test.py             # one-off live test: inspect a real DeepSeek response (usage format)
    ├── validation_test.py       # compares proxy-recorded tokens/cost with official DeepSeek sources
    ├── proxy/
    │   ├── __init__.py          # package marker
    │   ├── server.py            # streaming proxy (FastAPI + uvicorn + httpx)
    │   ├── usage.py             # TokenUsage + merge_usage
    │   ├── sse.py               # incremental SSE parser
    │   ├── storage.py           # SQLite persistence (~/.local/state/deepseek/usage.db or %LOCALAPPDATA%)
    │   ├── dashboard.py         # web dashboard routes (http://127.0.0.1:8000/)
    │   ├── pricing.py           # price model + peak/off-peak logic
    │   ├── cost_calc.py         # cost calculation from token usage + price table
    │   ├── labels.py            # API key fingerprints (SHA-256) + key labels
    │   ├── paths.py             # cross-platform config/state paths (shared API-key path)
    │   ├── influx_sink.py       # InfluxDB sink (additionally writes usage to the central InfluxDB)
    │   ├── tkinter_view.py      # Tkinter detail view ("Cost & Usage", read-only, no proxy server)
    │   ├── influx_dialog.py     # Tkinter dialog to configure the InfluxDB data source
    │   └── tray.py              # Windows system-tray icon (--tray): port/version/dashboard/GitHub/quit
    └── tests/                   # pytest suite
        ├── test_server.py       # streaming proxy
        ├── test_usage.py        # usage model + merge
        ├── test_sse.py          # SSE parser
        ├── test_storage.py      # SQLite persistence
        ├── test_dashboard.py    # web dashboard
        ├── test_pricing.py      # price/peak logic
        ├── test_influx_sink.py  # InfluxDB sink
        ├── test_tray.py         # tray integration (lazy import + version)
        └── test_tkinter_view.py # Tkinter detail view
```
