# InfluxDB integration — documentation

> Status: 2026-09-25 · applies to `deepseek_rates_v2.4.0.py` and `deepseek-proxy/`

This documentation describes the architecture, configuration and operation of
the InfluxDB 2.x integration for DeepSeek cost monitoring.

---

## 1. Overview

Originally the proxy stored token usage only in **SQLite** (locally, per host).
For a **multi-host/multi-user scenario** (several development machines that do
not run permanently), a **central InfluxDB 2.x** was added:

```
Host A (VS Code + Proxy) ─┐
Host B (VS Code + Proxy) ─┼─► InfluxDB 2.x (permanent) ◄─ "Cost & Usage" detail page
Host C (VS Code + Proxy) ─┘        (write)                 (read)
```

- **Write**: each local proxy writes (in addition to SQLite) to the central
  InfluxDB.
- **Read**: the monitor app reads — depending on configuration — from InfluxDB
  (central, across hosts) or from the local proxy/SQLite.

---

## 2. Two separate switches (important!)

There are **two independent** configuration points:

| Component | Where | What it controls |
|---|---|---|
| **Proxy** | `deepseek-proxy/influx.env` + systemd unit | Whether usage is **additionally written** to InfluxDB |
| **App** (monitor) | GUI settings → "Data source" | Where data is **read** (Influx or local proxy→SQLite) |

The **proxy has no Influx/SQLite switch** — SQLite is always active, Influx is
purely additive.

---

## 3. Proxy: configure writing

### File layout

```
deepseek-proxy/
├── main.py                       # entry point; flags + _build_usage_callback
├── deepseek-proxy.service        # systemd unit (references influx.env)
├── influx.env                    # Influx credentials (0600, NOT committed)
└── proxy/
    └── influx_sink.py            # InfluxSink (lazy import influxdb-client)
```

### `influx.env` (secret, permissions 0600)

```ini
DEEPSEEK_INFLUX_URL=http://<INFLUX_HOST>:8086
DEEPSEEK_INFLUX_TOKEN=<TOKEN>
DEEPSEEK_INFLUX_ORG=<ORG>
DEEPSEEK_INFLUX_BUCKET=<BUCKET>
# optional:
# DEEPSEEK_INFLUX_HOST_NAME=<hostname>
```

### systemd unit (excerpt)

```ini
[Service]
ExecStart=/home/USER/DeepSeek-Monitor/deepseek-proxy/.venv/bin/python /home/USER/DeepSeek-Monitor/deepseek-proxy/main.py
EnvironmentFile=-/home/USER/DeepSeek-Monitor/deepseek-proxy/influx.env
```

> Replace `/home/USER` with your installation path (see `deepseek-proxy.service`).
> The leading `-` in `EnvironmentFile=-...` means: no error if the file is
> missing (→ the proxy then automatically runs SQLite-only).

### Enable/disable Influx

- **On:** `influx.env` present + `EnvironmentFile` set (current state).
- **Off:** empty/remove `influx.env` **or** remove the `EnvironmentFile` line
  from the unit → proxy writes only SQLite.

### Alternative: start arguments (without systemd/env)

```bash
.venv/bin/python main.py \
  --influx-url http://<INFLUX_HOST>:8086 \
  --influx-token <TOKEN> \
  --influx-org <ORG> \
  --influx-bucket <BUCKET> \
  --host-name <hostname>
```

### After a change

```bash
sudo cp deepseek-proxy.service /etc/systemd/system/deepseek-proxy.service
sudo systemctl daemon-reload
sudo systemctl restart deepseek-proxy
journalctl -u deepseek-proxy -f   # log: "InfluxDB sink enabled …"
```

### SQLite

Always active, no configuration required. Path: `~/.local/state/deepseek/usage.db`
(overridable via `--db`).

---

## 4. App (monitor): configure reading

### GUI settings

Open via the ⚙ symbol in the mini tariff window → section **"Data source"**:

- **Selection:** "Local (Proxy/SQLite)" ↔ "InfluxDB (central)".
- **Fields** (for Influx): URL, token (hidden), organization, bucket.
- **"Apply"** saves and reloads immediately.

Recommended values:

| Field | Value |
|---|---|
| URL | `http://<INFLUX_HOST>:8086` |
| Token | Influx API token |
| Organization | `<ORG>` |
| Bucket | `<BUCKET>` |

### Configuration priority

1. **GUI entry** (persisted in `~/.config/deepseek/influx_config`, permissions 0600).
2. Otherwise **environment variables** `DEEPSEEK_INFLUX_URL/TOKEN/ORG/BUCKET`.
3. Otherwise **Local (Proxy/SQLite)** — default.

Selecting "Local" deletes the persisted Influx configuration again.

### Host filter

In InfluxDB mode, a **host dropdown** (top left) appears in the "Cost & Usage"
window. It lists all known hosts; "All hosts" = aggregated across all.

---

## 5. Data model (InfluxDB)

- **Measurement:** `usage`
- **Tags:** `host`, `model`, `fingerprint`, `tariff` (`peak`/`off_peak`)
- **Fields:** `input_tokens`, `output_tokens`, `cache_read_tokens`,
  `cache_creation_tokens`, `total_tokens`, `cost_usd`, `requests`
- **Timestamp:** event time (Berlin ISO is parsed correctly)

---

## 6. Important files (overview)

| File | Purpose |
|---|---|
| `deepseek_rates_v2.4.0.py` | App, env switch + GUI switch + host filter |
| `deepseek_rates_latest.py` | Symlink → `deepseek_rates_v2.4.0.py` |
| `deepseek-proxy/proxy/influx_sink.py` | Write sink (InfluxSink) |
| `deepseek-proxy/main.py` | Flags + combined callback |
| `deepseek-proxy/influx.env` | Influx credentials (secret, 0600, ignored) |
| `deepseek-proxy/deepseek-proxy.service` | systemd unit (EnvironmentFile) |
| `deepseek-proxy/requirements.txt` | + `influxdb-client>=1.39` |
| `deepseek-proxy/tests/test_influx_sink.py` | Tests (parser/mapping/sink) |
| `~/.config/deepseek/influx_config` | App-side persisted Influx config (0600) |
| `.env.example` | Template for app environment variables |

---

## 7. Security

- The Influx **token** lives **only** in `influx.env` (proxy) or `influx_config`
  (app), both with permissions `0600` and excluded via `.gitignore`.
- No credentials in code.
- **Recommendation:** rotate the token on the InfluxDB instance after setup,
  since it was visible in the chat/terminal history.

---

## 8. Operation / verification

1. Proxy running: `systemctl is-active deepseek-proxy` → `active`.
2. Influx sink active: `journalctl -u deepseek-proxy | grep "InfluxDB-Sink"`.
3. Data present:
   ```bash
   curl -s -X POST "http://<INFLUX_HOST>:8086/api/v2/query?org=<ORG>" \
     -H "Authorization: Token <TOKEN>" -H "Accept: application/csv" \
     -H "Content-Type: application/json" \
     -d '{"query":"from(bucket: \"<BUCKET>\") |> range(start: -1h) |> filter(fn: (r) => r._measurement == \"usage\") |> count()"}'
   ```
4. Start the app → "C" (Cost & Usage) → data from InfluxDB or locally.
5. Tests: `cd deepseek-proxy && .venv/bin/python -m pytest -q` → 81 green.
