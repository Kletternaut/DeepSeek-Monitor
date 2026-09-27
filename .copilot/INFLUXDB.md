# InfluxDB-Integration — Dokumentation

> Stand: 2026-09-25 · betrifft `deepseek_rates_v2.4.0.py` und `deepseek-proxy/`

Diese Dokumentation beschreibt die Architektur, Konfiguration und Bedienung der
InfluxDB-2.x-Integration für die DeepSeek-Kostenüberwachung.

---

## 1. Überblick

Ursprünglich speicherte der Proxy die Token-Usage nur in **SQLite** (lokal, je
Host). Für ein **Multi-Host-/Multi-User-Szenario** (mehrere Entwicklungs-Rechner,
die nicht dauerhaft laufen) wurde eine **zentrale InfluxDB 2.x** angebunden:

```
Host A (VS Code + Proxy) ─┐
Host B (VS Code + Proxy) ─┼─► InfluxDB 2.x (permanent) ◄─ Detailseite „Kosten & Verbrauch"
Host C (VS Code + Proxy) ─┘        (write)                 (read)
```

- **Schreiben**: Jeder lokale Proxy schreibt (zusätzlich zu SQLite) in die
  zentrale InfluxDB.
- **Lesen**: Die Monitor-App liest — je nach Konfiguration — aus InfluxDB
  (zentral, host-übergreifend) oder aus dem lokalen Proxy/SQLite.

---

## 2. Zwei getrennte Schalter (wichtig!)

Es gibt **zwei unabhängige** Konfigurationspunkte:

| Komponente | Wo | Was sie steuert |
|---|---|---|
| **Proxy** | `deepseek-proxy/influx.env` + systemd-Unit | Ob **zusätzlich** in InfluxDB **geschrieben** wird |
| **App** (Monitor) | GUI-Einstellungen → „Datenquelle" | Wo **gelesen** wird (Influx oder lokaler Proxy→SQLite) |

Der **Proxy kennt keinen Influx/SQLite-Umschalter** — SQLite ist immer aktiv,
Influx ist rein additiv.

---

## 3. Proxy: Schreiben konfigurieren

### Datei-Layout

```
deepseek-proxy/
├── main.py                       # Einstieg; Flags + _build_usage_callback
├── deepseek-proxy.service        # systemd-Unit (referenziert influx.env)
├── influx.env                    # Influx-Zugangsdaten (0600, NICHT committen)
└── proxy/
    └── influx_sink.py            # InfluxSink (lazy import influxdb-client)
```

### `influx.env` (Secret, Rechte 0600)

```ini
DEEPSEEK_INFLUX_URL=http://192.168.0.32:8086
DEEPSEEK_INFLUX_TOKEN=<TOKEN>
DEEPSEEK_INFLUX_ORG=Jolly
DEEPSEEK_INFLUX_BUCKET=deepseek-usage
# optional:
# DEEPSEEK_INFLUX_HOST_NAME=raspi5
```

### systemd-Unit (Auszug)

```ini
[Service]
ExecStart=/home/admin/deepseek/deepseek-proxy/.venv/bin/python /home/admin/deepseek/deepseek-proxy/main.py
EnvironmentFile=-/home/admin/deepseek/deepseek-proxy/influx.env
```

> Das führende `-` bei `EnvironmentFile=-...` bedeutet: kein Fehler, wenn die
> Datei fehlt (→ Proxy läuft dann automatisch SQLite-only).

### Influx ein-/ausschalten

- **An:** `influx.env` vorhanden + `EnvironmentFile` gesetzt (aktueller Zustand).
- **Aus:** `influx.env` leeren/entfernen **oder** `EnvironmentFile`-Zeile aus der
  Unit entfernen → Proxy schreibt nur noch SQLite.

### Alternative: Start-Argumente (ohne systemd/env)

```bash
.venv/bin/python main.py \
  --influx-url http://192.168.0.32:8086 \
  --influx-token <TOKEN> \
  --influx-org Jolly \
  --influx-bucket deepseek-usage \
  --host-name raspi5
```

### Nach Änderung

```bash
sudo cp deepseek-proxy.service /etc/systemd/system/deepseek-proxy.service
sudo systemctl daemon-reload
sudo systemctl restart deepseek-proxy
journalctl -u deepseek-proxy -f   # Log: „InfluxDB-Sink aktiviert …"
```

### SQLite

Immer aktiv, keine Konfiguration nötig. Pfad: `~/.local/state/deepseek/usage.db`
(überschreibbar per `--db`).

---

## 4. App (Monitor): Lesen konfigurieren

### GUI-Einstellungen

Öffnen über das ⚙-Symbol im Mini-Tarif-Fenster → Sektion **„Datenquelle"**:

- **Auswahl:** „Lokal (Proxy/SQLite)" ↔ „InfluxDB (zentral)".
- **Felder** (bei Influx): URL, Token (verdeckt), Organisation, Bucket.
- **„Übernehmen"** speichert und lädt sofort neu.

Empfohlene Werte:

| Feld | Wert |
|---|---|
| URL | `http://192.168.0.32:8086` |
| Token | Influx-API-Token |
| Organisation | `Jolly` |
| Bucket | `deepseek-usage` |

### Priorität der Konfiguration

1. **GUI-Eintrag** (persistiert in `~/.config/deepseek/influx_config`, Rechte 0600).
2. Sonst **Env-Variablen** `DEEPSEEK_INFLUX_URL/TOKEN/ORG/BUCKET`.
3. Sonst **Lokal (Proxy/SQLite)** — Default.

„Lokal" wählen löscht die persistierte Influx-Konfiguration wieder.

### Host-Filter

Im InfluxDB-Modus erscheint im Fenster „Kosten & Verbrauch" ein **Host-Dropdown**
(oben links). Es listet alle bekannten Hosts; „Alle Hosts" = aggregiert über alle.

---

## 5. Datenmodell (InfluxDB)

- **Measurement:** `usage`
- **Tags:** `host`, `model`, `fingerprint`, `tariff` (`peak`/`off_peak`)
- **Fields:** `input_tokens`, `output_tokens`, `cache_read_tokens`,
  `cache_creation_tokens`, `total_tokens`, `cost_usd`, `requests`
- **Timestamp:** Event-Zeit (Berlin-ISO wird korrekt geparst)

---

## 6. Downsampling (tägliche Aggregation, optional)

Siehe `.copilot/influx-downsampling/README.md` und `downsample_daily.flux`.

Kurzfassung:
- Rohdaten-Bucket `deepseek-usage`: Retention **90 Tage**.
- Aggregat-Bucket `deepseek-usage-daily` (Measurement `usage_daily`): Retention **unendlich**.
- Ein Task aggregiert täglich `usage` → `usage_daily` (Summen je Host/Modell/Zone/Tag).

Die Einrichtung erfolgt **manuell per SSH** (Passwort bleibt beim User); das
Flux-Skript und die Anleitung liegen bereit.

---

## 7. Wichtige Dateien (Übersicht)

| Datei | Zweck |
|---|---|
| `deepseek_rates_v2.4.0.py` | App, Env-Schalter + GUI-Schalter + Host-Filter |
| `deepseek_rates_latest.py` | Symlink → `deepseek_rates_v2.4.0.py` |
| `deepseek-proxy/proxy/influx_sink.py` | Schreib-Sink (InfluxSink) |
| `deepseek-proxy/main.py` | Flags + kombinierter Callback |
| `deepseek-proxy/influx.env` | Influx-Zugangsdaten (Secret, 0600, ignoriert) |
| `deepseek-proxy/deepseek-proxy.service` | systemd-Unit (EnvironmentFile) |
| `deepseek-proxy/requirements.txt` | + `influxdb-client>=1.39` |
| `deepseek-proxy/tests/test_influx_sink.py` | Tests (Parser/Mapping/Sink) |
| `~/.config/deepseek/influx_config` | App-seitige persistierte Influx-Config (0600) |
| `.env.example` | Vorlage für App-Env-Variablen |

---

## 8. Sicherheit

- Das Influx-**Token** liegt **nur** in `influx.env` (Proxy) bzw. `influx_config`
  (App), beide mit Rechten `0600` und per `.gitignore` ausgeschlossen.
- Keine Zugangsdaten im Code.
- **Empfehlung:** Token nach der Einrichtung auf der InfluxDB-Instanz rotieren,
  da es im Chat-/Terminal-Verlauf sichtbar war.

---

## 9. Betrieb / Verifikation

1. Proxy läuft: `systemctl is-active deepseek-proxy` → `active`.
2. Influx-Sink aktiv: `journalctl -u deepseek-proxy | grep "InfluxDB-Sink"`.
3. Daten vorhanden:
   ```bash
   curl -s -X POST "http://192.168.0.32:8086/api/v2/query?org=Jolly" \
     -H "Authorization: Token <TOKEN>" -H "Accept: application/csv" \
     -H "Content-Type: application/json" \
     -d '{"query":"from(bucket: \"deepseek-usage\") |> range(start: -1h) |> filter(fn: (r) => r._measurement == \"usage\") |> count()"}'
   ```
4. App starten → „C" (Kosten & Verbrauch) → Daten aus InfluxDB bzw. lokal.
5. Tests: `cd deepseek-proxy && .venv/bin/python -m pytest -q` → 81 grün.
