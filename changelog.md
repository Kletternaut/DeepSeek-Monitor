# Changelog

## Unreleased

- **Open-source maturity review**: MIT license (`LICENSE`), disclaimer ("independent open-source project, no affiliation with DeepSeek"), `CONTRIBUTING.md`, `pyproject.toml`, `.editorconfig`, `.pre-commit-config.yaml`, CI (`.github/workflows/ci.yml`).
- **Split monofile**: the pure, Tkinter-free logic of `deepseek_rates_v2.4.0.py` was extracted into the `deepseek_monitor/` package (`config`, `i18n`, `paths`, `persistence`, `api`, `tariff`, `analytics`). The monofile is now a slim Tkinter frontend.
- **Portable paths**: absolute `/home/admin` paths and internal IP addresses removed from tracked files (relative/`~` paths, placeholders).
- **New app icon**: brand-neutral icon (`assets/deepseek-monitor.svg` + PNG sizes) instead of DeepSeek trademarks.
- **Added tests**: `tests/test_deepseek_monitor.py` for tariff logic, API parsing, currency formatting and color helpers.

## 2.4.0 — 2026-09-25

- Optional **central InfluxDB 2.x** as the data source for the "Cost & Usage" detail page (multi-host).
- **Configuration switch via environment variables** (no hardcodes): `DEEPSEEK_INFLUX_URL`, `DEEPSEEK_INFLUX_TOKEN`, `DEEPSEEK_INFLUX_ORG`, `DEEPSEEK_INFLUX_BUCKET`. When set, the app reads directly from InfluxDB (HTTP query API, Flux); otherwise still via the local proxy `/api/usage` (unchanged behavior).
- **Proxy write path**: new module `deepseek-proxy/proxy/influx_sink.py`. The proxy additionally writes usage to InfluxDB when `--influx-url` (or `DEEPSEEK_INFLUX_URL`) is set. SQLite always stays active; Influx is purely additive and best effort.
- New `host` tag (hostname via `--host-name` or `DEEPSEEK_INFLUX_HOST_NAME`) to distinguish multiple development hosts.
- New field `requests` (value 1) per event for later daily aggregation.
- Added dependency `influxdb-client`.
- Downsampling concept prepared: daily aggregation via a Flux task.
- **Host filter** on the detail page (dropdown "Host"), visible only in InfluxDB mode; lists all hosts and filters the data per host ("All hosts" = aggregated across all).
- **GUI switch "Data source"** in the settings: toggle between "Local (Proxy/SQLite)" and "InfluxDB (central)" including input fields for URL/token/org/bucket. The values are persisted locally in `~/.config/deepseek/influx_config` (file `0600`); without a GUI entry the environment variables `DEEPSEEK_INFLUX_*` continue to apply.

## 2.3.0 — 2026-09-25

- The "Cost & Usage" detail page now uses the **real** token usage data of the proxy (`deepseek-proxy/`, HTTP `GET /api/usage`) instead of the imprecise balance-delta estimate. This keeps the values correct across day changes, regardless of whether the app is running.
- Proxy base URL overridable via the environment variable `DEEPSEEK_PROXY_URL` (default `http://127.0.0.1:8000`).
- The realtime balance header (total/topped up/granted) still comes from the `/user/balance` API.
- The intervals table now shows one entry per request (instead of proportionally estimated intervals).
- When the proxy is unreachable, a hint appears instead of the old estimate.
- The detail page currency switches to USD (the proxy calculates USD per 1M tokens).

## 2.2.9 — 2026-08-30

- Version number is now part of the file name (`deepseek_rates_v2.2.9.py`); `deepseek_rates_latest.py` symlink always points to the newest version, and `~/Desktop/deepseek.desktop` runs the symlink (no update needed on version bumps).
- Old versioned files are kept as version history and fallback from now on.

## 2.2.8 — 2026-08-30

- Replaced the colored 📌 pin emoji on the "always on top" button with the monochrome ⚲ symbol to match the style of the other action buttons.

## 2.2.7 — 2026-08-30

- Costs & usage: Days without consumption now appear as an empty slot with date in the daily breakdown (previously they were skipped entirely).
- Analytics chart: No "0.0000" label is drawn above the axis for days without consumption.
- Version information (`APP_VERSION`) introduced in the source code and shown in the settings header.
- Introduced changelog.md; will be updated with every change from now on.
