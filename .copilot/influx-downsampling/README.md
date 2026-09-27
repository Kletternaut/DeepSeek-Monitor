# InfluxDB Downsampling einrichten (tägliche Aggregation)

Dieses Dokument beschreibt, wie du das tägliche Downsampling der
DeepSeek-Usage-Daten auf deiner permanenten InfluxDB 2.x einrichtest.
**Du führst die Befehle selbst aus** (Passwort/Token bleiben bei dir).

## Ziel

- Rohdaten (Measurement `usage`) im Bucket `deepseek-usage` verfallen nach **90 Tagen**.
- Ein täglicher Task aggregiert sie zu `usage_daily` und schreibt sie in den
  Bucket `deepseek-usage-daily` (Retention **unendlich**).
- Ergebnis: „sinnvolle Reduktion" — einzelne Requests verschwinden nach 90 Tagen,
  die Tages-Summen (Tokens + Kosten + Anzahl Requests je Host/Modell/Zone) bleiben.

## Voraussetzungen

- SSH-Zugang: `ssh admin@192.168.0.32`
- Auf dem DB-Host die Influx-CLI (`influx`) mit Token/Org/Host konfiguriert,
  oder du nutzt die HTTP-API-Variante (unten) mit deinem Token.

---

## Variante A — per Influx-CLI (empfohlen, läuft auf dem DB-Host)

```bash
ssh admin@192.168.0.32
```

### 1. Aggregat-Bucket anlegen (Retention unendlich)

```bash
influx bucket create --name deepseek-usage-daily --org Jolly --retention 0
```

### 2. Rohdaten-Bucket auf 90 Tage begrenzen

```bash
influx bucket update --id ee93ddda64c14b21 --retention 7776000
```

> `ee93ddda64c14b21` ist die aktuell ausgelesene ID des Buckets `deepseek-usage`.
> Zur Sicherheit zuerst die ID verifizieren: `influx bucket list --org Jolly`.
> `7776000` Sekunden = 90 Tage.

### 3. Task anlegen (Flux aus `downsample_daily.flux`)

```bash
influx task create --org Jolly --file downsample_daily.flux
```

> Die Datei `downsample_daily.flux` liegt im Workspace unter
> `.copilot/influx-downsampling/downsample_daily.flux`. Auf den DB-Host kopieren
> (z. B. `scp`) oder den Inhalt direkt einfügen.

### 4. Prüfen

```bash
influx task list --org Jolly
```

---

## Variante B — per HTTP-API (von deinem Rechner aus, kein SSH nötig)

Ersetze `<DEIN_TOKEN>` durch deinen Token.

### 1. Aggregat-Bucket anlegen

```bash
curl -X POST http://192.168.0.32:8086/api/v2/buckets \
  -H "Authorization: Token <DEIN_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"org":"Jolly","name":"deepseek-usage-daily","retentionRules":[{"type":"expire","everySeconds":0}]}'
```

### 2. Rohdaten-Bucket auf 90 Tage begrenzen

```bash
curl -X PATCH http://192.168.0.32:8086/api/v2/buckets/ee93ddda64c14b21 \
  -H "Authorization: Token <DEIN_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"retentionRules":[{"type":"expire","everySeconds":7776000}]}'
```

### 3. Task anlegen (Flux-Body)

```bash
curl -X POST "http://192.168.0.32:8086/api/v2/tasks" \
  -H "Authorization: Token <DEIN_TOKEN>" \
  -H "Content-Type: application/json" \
  -d @task.json
```

Wobei `task.json` so aussieht:

```json
{
  "org": "Jolly",
  "flux": "option task = {name: \"downsample_usage_daily\", every: 1d, offset: 5m}\n\nfrom(bucket: \"deepseek-usage\")\n  |> range(start: -task.every)\n  |> filter(fn: (r) => r._measurement == \"usage\")\n  |> aggregateWindow(every: 1d, fn: sum, createEmpty: false)\n  |> set(key: \"_measurement\", value: \"usage_daily\")\n  |> to(bucket: \"deepseek-usage-daily\", org: \"Jolly\")"
}
```

---

## Hinweise / Sicherheit

- Die Rohdaten verfallen **nicht sofort** nach dem Setzen der 90-Tage-Retention;
  InfluxDB räumt alte Punkte periodisch im Hintergrund auf.
- Das Aggregat (`usage_daily`) enthält dieselben Tags (`host`, `model`,
  `fingerprint`, `tariff`) plus die summierten Felder (`input_tokens`,
  `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `total_tokens`,
  `cost_usd`, `requests`).
- **Lesezugriff auf >90 Tage alte Daten** (z. B. für eine spätere Langzeit-Ansicht
  oder Grafana) läuft dann über den Bucket `deepseek-usage-daily` und das
  Measurement `usage_daily` — nicht über `usage`.
- Die Detailseite „Kosten & Verbrauch" liest aktuell die letzten 14 Tage und
  damit weiterhin die Rohdaten (`usage`); das Downsampling betrifft sie also
  nicht, sondern schafft die Grundlage für künftige Langzeit-Auswertungen.
