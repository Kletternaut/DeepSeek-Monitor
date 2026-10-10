# DeepSeek-Monitor

> **Note:** This project is an independent open-source project and is in **no
> way affiliated with DeepSeek (深度求索)**. It is not developed, sponsored,
> endorsed, or operated by DeepSeek.

**Cost and usage monitoring for the DeepSeek API** — a small toolkit of two
components: a **proxy** (the data source) and a **monitor GUI** (the
analytics).

---

## Why this tool exists (the proxy's purpose)

The DeepSeek API reports only a **total balance** in a single currency via its
balance endpoint (`/user/balance`) — **no** breakdown by individual request,
model, or time window. If you want to know what a chat, a day, or a specific
model **actually costs**, you only get a rough delta estimate from that.

That is exactly where the **proxy** comes in: it sits as a transparent
intermediary between your client (e.g. VS Code) and the DeepSeek API and
**reads the real `usage` objects from every streamed response** (input/output/
cache tokens). This is what makes precise accounting possible in the first
place:

- **Exact token counts per request** — instead of the imprecise balance delta.
- **Real costs** accounting for the DeepSeek tariff (peak/off-peak, weekend,
  cache hit/miss, per model).
- **Centralize multiple hosts** (via InfluxDB), including a host filter.

The proxy changes **not a single byte** of the request or the response — it
forwards them unchanged and only observes.

---

## What exactly the proxy does

| Aspect | Description |
|---|---|
| **Transport** | Streaming proxy between VS Code (Anthropic Messages API) and `https://api.deepseek.com/anthropic/v1/messages` |
| **Endpoints** | `POST /anthropic/v1/messages` and `POST /v1/messages` |
| **Forwarding** | Byte-exact and unchanged; the response is streamed back unchanged as Server-Sent Events (SSE) |
| **Decoupled capture** | Every streamed chunk is parsed incrementally, the `usage` objects are merged |
| **Persistence** | SQLite (always on, local per host) + optional InfluxDB 2.x (central, multi-host) |
| **Cost calculation** | Price model (`pricing.py` / `cost_calc.py`) with peak/off-peak, weekend and cache hit/miss |
| **Interfaces** | Web dashboard (`http://127.0.0.1:8000/`), Tkinter detail view (`--view`), Windows tray icon (`--tray`) |

The **monitor GUI** ("Cost & Usage") is the pure analytics layer: it reads the
data collected by the proxy from SQLite or InfluxDB and shows tariff, balance,
cost and usage.

---

## Components

| Component | Path | Purpose |
|---|---|---|
| **Proxy** | `deepseek-proxy/` | Captures the real token/cost data (data source) |
| **Monitor** | `deepseek_rates.py` | Tkinter app "Cost & Usage" (analytics) |

## Architecture

```
Host A (VS Code + Proxy) ─┐
Host B (VS Code + Proxy) ─┼─► InfluxDB 2.x (permanent) ◄─ Monitor "Cost & Usage"
Host C (VS Code + Proxy) ─┘        (write)                 (read)
```

- **The proxy always writes** SQLite, plus InfluxDB if `influx.env` is present
  (`DEEPSEEK_INFLUX_URL/TOKEN/ORG/BUCKET`).
- **The monitor reads** either "Local (Proxy/SQLite)" or "InfluxDB (central)"
  depending on the GUI setting.

## Screenshots

The monitor app (Tkinter) — main tariff widget, "Cost & Usage" window, settings
and the full overview:

| Main widget | Cost & Usage | Settings |
|---|---|---|
| ![Main](screenshots/main.png) | ![Cost & Usage](screenshots/costs-usage.png) | ![Settings](screenshots/settings.png) |

![All windows](screenshots/all.png)

The proxy's web dashboard (`http://127.0.0.1:8000/`) — recorded token usage and
the costs calculated from it:

![Proxy dashboard](screenshots/proxy.png)

## Documentation

| File | Content |
|---|---|
| [`docs/INSTALLATION.md`](docs/INSTALLATION.md) | Installation (Proxy, Monitor, systemd, VS Code, Windows) |
| [`docs/WINDOWS_SETUP.md`](docs/WINDOWS_SETUP.md) | Windows 11: build & test the `.exe` files |
| [`docs/INFLUXDB.md`](docs/INFLUXDB.md) | InfluxDB 2.x integration in detail |
| [`docs/STRUCTURE.md`](docs/STRUCTURE.md) | Directory/file reference |
| [`packaging/README.md`](packaging/README.md) | PyInstaller build for Windows |

## Quickstart

```bash
git clone --branch main https://github.com/Kletternaut/DeepSeek-Monitor.git
cd DeepSeek-Monitor/deepseek-proxy
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python main.py            # proxy + web dashboard on http://127.0.0.1:8000/
```

For the full steps (including `influx.env`, VS Code setup, systemd and starting
the monitor): see **[`docs/INSTALLATION.md`](docs/INSTALLATION.md)**.

## License

MIT — see [LICENSE](LICENSE).

## Contributing

See [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md).
