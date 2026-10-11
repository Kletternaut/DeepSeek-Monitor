# DeepSeek-Monitor

**DeepSeek token usage analytics, API cost tracking and real-time pricing monitoring — for BYOK workflows and beyond.**

When developing with DeepSeek through BYOK tools such as VS Code with GitHub Copilot Chat, API costs depend not only on how much you use a model, but also on the pricing period in which that usage takes place. This is easy to miss when your local time differs from the time windows used for DeepSeek billing.

**DeepSeek-Monitor makes those billing windows visible.** Its visual tariff monitor shows which pricing-factor period is active, how long it will continue, and when the next period begins—in your chosen local timezone. That makes it easier to plan longer coding sessions and shift work to lower-cost periods. The project also includes request-level token usage and cost analytics, supported by a proxy that captures usage reported by DeepSeek.

This is an independent open-source project. It is not affiliated with, developed, sponsored, endorsed, or operated by DeepSeek (深度求索).

## Features at a glance

- **Visual billing-window monitor:** At a glance, see the active DeepSeek pricing factor, the time remaining in that period, and the upcoming zone changes in your own timezone—helping you plan coding work around lower-cost windows.
- **Token usage and cost analytics:** Review recorded token consumption, calculated API costs, daily totals and individual request details.
- **API proxy:** Forward compatible client requests to the DeepSeek API and capture usage data reported in API responses.
- **Flexible storage:** SQLite stores usage data locally; InfluxDB 2.x can optionally be used for centralized, multi-host reporting.
- **Account balance monitoring:** A separate desktop window monitors the DeepSeek API account balance.

Cost figures are calculated from the usage data reported by the API and the configured price table. Check the configured prices and note the limitations around historical pricing and the Chinese public-holiday calendar before treating these figures as financial records.

## Visual real-time pricing monitor

The tariff monitor is more than a status display: it is a practical planning tool for developers whose local timezone differs from the time windows used for DeepSeek billing. It translates those billing rules into a clear, at-a-glance visual display in the timezone you work in.

![Compact DeepSeek tariff monitor showing the current pricing zone, countdown and visual timeline](screenshots/main.png)

*The compact, unobtrusive widget keeps the current pricing factor and upcoming zone changes visible while you code.*

The monitor shows **which pricing-factor period is active right now and exactly how long it has left**. A live countdown tells you when the current period ends; the color-coded 24-hour timeline shows the sequence of pricing zones, with a marker for the current time. Together, these make it easy to see when a lower-cost period starts, when a higher-cost period begins or ends, and how much time remains before the next transition—without having to mentally convert DeepSeek's billing schedule from another timezone.

This visibility can materially reduce development costs when you use it to plan longer coding sessions, agent runs, or other token-intensive work around lower-cost periods. Instead of discovering after the fact that a long session ran during a higher-factor window, you can see the upcoming transition and decide when to start, pause, or continue work. The monitor does not reduce costs automatically; it gives you timely information to make that decision.

Features include:

- **Active factor at a glance:** the current tariff zone (Peak, Standard, Weekend or Holiday) and its applicable factor. The current implementation uses a 2× Peak multiplier.
- **Live remaining-time countdown:** see how long the current zone will continue and when the next zone change is due.
- **Local-time timeline:** a color-coded 24-hour view with a current-time marker, so zone boundaries are understandable in your own timezone.
- **Configurable display timezone:** choose your local timezone independently of the tariff rules used for billing-period calculations.
- **Optional audible warning:** the monitor can alert you 60 seconds before an upcoming tariff transition.
- **Compact desktop widget:** keep the current factor and countdown visible while coding; the mini monitor also offers an always-on-top toggle.
- **Account balance monitoring:** monitor your DeepSeek API balance in a separate view.

The tariff calculation and the display timezone are separate concerns. Tariff rules use configured UTC time windows and the date in the **Asia/Shanghai** timezone. Weekends and Chinese public holidays in the calendar currently maintained in the code are treated as off-peak. The calendar currently covers 2026 and must be updated for subsequent years.

> **Important — verify tariff times before relying on the monitor.** Correctly mapping Peak/Off-peak windows, weekend boundaries and Chinese public holidays to a user's local timezone is complex, especially across European standard-time/daylight-saving-time changes. DeepSeek may also change its billing rules, and the holiday calendar is maintained in code. **Before using this monitor to plan paid API work, you must independently verify that the initially displayed zones and their start/end times match DeepSeek's current official pricing schedule.** Repeat the check after application updates, timezone or daylight-saving-time changes, annual holiday-calendar updates, and DeepSeek pricing-policy changes. The monitor is a planning aid, not an authoritative billing source. Read the [Tariff Accuracy, Verification and Liability Disclaimer](docs/DISCLAIMER.md).

## Token usage and API cost analytics

The proxy captures usage fields reported by DeepSeek in API responses, including streamed Server-Sent Events (SSE). It merges these fields into request records and calculates costs using the configured price table.

![DeepSeek-Monitor Cost & Usage analytics](screenshots/costs-usage.png)

*The Cost & Usage view turns API-reported token usage into request-level and period-based cost breakdowns.*

The analytics can show, among other things:

- input and output tokens
- reported cache-hit and cache-miss tokens
- calculated costs per request and over selected periods
- tariff zone and applied pricing category
- request details and daily summaries
- optional centralized, multi-host reporting through InfluxDB 2.x

**Important:** DeepSeek-Monitor does not tokenize prompt or response text itself. Usage tracking relies on token counts actually reported by the API stream. If the API does not report a field, the monitor cannot independently reconstruct that value from the text.

Cost calculations depend on the captured usage fields and the price table configured in the proxy. The price table can be updated, so historical requests are not automatically an audit-grade record of the prices that applied at the time.

### Data storage

- **SQLite:** local storage for proxy usage records; always enabled.
- **InfluxDB 2.x (optional):** additional centralized storage for multi-host reporting.
- **API-key fingerprint:** the usage database stores a fingerprint for grouping requests, not the plaintext of the API key supplied by the client.

The desktop monitor can use local proxy/SQLite data or a configured InfluxDB data source. Account balance monitoring is a separate feature and should not be confused with request-level cost analytics.

## Settings and configuration

Use the settings window to tailor the monitor to your workflow. The display timezone can be configured independently of the billing-time calculations, and the analytics data source can be set to local proxy/SQLite data or a configured InfluxDB instance.

![DeepSeek-Monitor settings](screenshots/settings.png)

*The settings window brings monitor preferences and analytics data-source configuration together.*

## API proxy and client compatibility

The proxy is the technical foundation for detailed usage capture. It forwards compatible Anthropic Messages requests to **DeepSeek** and streams the responses back to the client.

![DeepSeek API proxy dashboard](screenshots/proxy.png)

*The proxy dashboard displays captured token usage and the costs calculated from the reported usage.*

By default, the proxy listens locally at:

- Dashboard: `http://127.0.0.1:8000/`
- Anthropic Messages base URL: `http://127.0.0.1:8000/anthropic`
- Endpoints: `/anthropic/v1/messages` and `/v1/messages` (alias)

A client must support a custom Anthropic Messages-compatible endpoint. Configuration examples and further notes are available in the [installation guide](docs/INSTALLATION.md).

Possible use cases include:

- **VS Code with GitHub Copilot Chat:** a custom model endpoint, provided the VS Code version and configuration support the required model protocol.
- **GitHub Copilot CLI:** an experimental BYOK configuration using the Anthropic provider. GitHub's documented provider list names Anthropic, not DeepSeek; streaming and tool calls should be tested with the CLI version in use.
- **Claude Code:** an experimental configuration using a custom Anthropic Messages endpoint. Anthropic does not officially support Claude Code with non-Claude models; in this configuration, requests go to DeepSeek.

These are compatibility configurations, not a blanket guarantee that every version, model or feature will work. Tool calls, reasoning/thinking features and other protocol-specific capabilities depend on the client and the DeepSeek model being used.

**Scope:** DeepSeek is the upstream provider. This is not a generic multi-provider router and it does not forward requests to Anthropic's hosted Claude models.

The proxy forwards request and response bodies without decoding and re-encoding them. HTTP transport headers may be filtered or regenerated as needed, so the proxy is not byte-for-byte transparent at the level of the complete HTTP message.

## Components

| Component | Path | Purpose |
|---|---|---|
| API proxy | `deepseek-proxy/` | DeepSeek upstream, streaming usage capture, dashboard and storage |
| Desktop monitor | `deepseek_rates.py` | Tkinter UI for tariff monitoring, account balance and cost/usage analytics |
| Monitor logic | `deepseek_monitor/` | Tariff calculation, configuration, persistence and analytics |

## Architecture

```text
Compatible client
(VS Code / Copilot CLI / Claude Code)
          |
          | Anthropic Messages API
          v
DeepSeek API Proxy  ------->  DeepSeek API
      |      |
      |      +--> Usage extraction from the SSE stream
      |
      +--> SQLite (local, always enabled)
      |
      +--> InfluxDB 2.x (optional, multiple hosts)
                    ^
                    |
               Desktop monitor
               / dashboard
```

- SQLite is always enabled for proxy usage records.
- InfluxDB is optional and receives usage data in addition to SQLite.
- The monitor can use local proxy data or a centralized InfluxDB data source.
- The proxy does not store the client-supplied API key in plaintext in the usage database.

## Screenshots

![All monitor windows](screenshots/all.png)

## Quick start

Linux example. For complete installation steps, client configuration, systemd, Windows and InfluxDB, see the [installation guide](docs/INSTALLATION.md).

### 1. Clone the repository and install the proxy

```bash
git clone --branch main https://github.com/Kletternaut/DeepSeek-Monitor.git
cd DeepSeek-Monitor/deepseek-proxy
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py
```

The proxy dashboard is then available at `http://127.0.0.1:8000/` by default.

### 2. Start the desktop monitor

In a second terminal, from the repository root:

```bash
python3 deepseek_rates.py
```

The proxy and desktop monitor are separate components. For the detailed cost/usage view, the proxy must be running and collecting usage data. See [docs/INSTALLATION.md](docs/INSTALLATION.md) for further details.

## Security

The proxy and dashboard have **no built-in authentication or authorization**. By default, they bind to `127.0.0.1`. Do not expose the service to a LAN or the Internet without appropriate access control, TLS and restrictive network access rules.

The proxy forwards the API key supplied by the client to DeepSeek and does not require a separate key to proxy requests. The desktop monitor may independently store an API key for account balance queries; protect its configuration and backups. The proxy is not intended to persist prompt or response content, but usage records contain metadata such as timestamps, model names, token counts, request IDs and API-key fingerprints. Protect the SQLite file and any configured InfluxDB instance.

See [docs/SECURITY.md](docs/SECURITY.md) for further notes.

## Documentation

| File | Contents |
|---|---|
| [docs/INSTALLATION.md](docs/INSTALLATION.md) | Installation, client configuration, systemd and Windows |
| [docs/DISCLAIMER.md](docs/DISCLAIMER.md) | Tariff accuracy, required user verification and liability disclaimer |
| [docs/WINDOWS_SETUP.md](docs/WINDOWS_SETUP.md) | Windows builds and tests for `.exe` files |
| [docs/INFLUXDB.md](docs/INFLUXDB.md) | InfluxDB integration and multi-host reporting |
| [docs/STRUCTURE.md](docs/STRUCTURE.md) | Repository directory and file reference |
| [docs/changelog.md](docs/changelog.md) | Change history |
| [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) | Contribution guide |
| [packaging/README.md](packaging/README.md) | Windows packaging |

## License

MIT — see [LICENSE](LICENSE).
