// Downsampling-Task: Rohdaten (usage) → Tagesaggregat (usage_daily).
//
// Läuft täglich und aggregiert die letzten 24h der Roh-Events pro
// host/model/fingerprint/tariff zu Tagessummen in den Bucket
// ``deepseek-usage-daily`` (Retention unendlich).
//
// Damit bleiben die Rohdaten im Bucket ``deepseek-usage`` nach 90 Tagen
// verfallen (Retention 90d), während die Tagesaggregate dauerhaft erhalten
// bleiben.
//
// WICHTIG: ``aggregateWindow`` erzeugt Zeitstempel an den UTC-Tagesgrenzen.
// Die Detailseite gruppiert selbst anhand der lokalen Zeitzone; für eine
// konsistente lokale Tageszuordnung ist die UTC-Basis ausreichend, weil die
// Summen über volle 24h gebildet werden.

option task = {
    name: "downsample_usage_daily",
    every: 1d,
    offset: 5m,
}

from(bucket: "deepseek-usage")
    |> range(start: -task.every)
    |> filter(fn: (r) => r._measurement == "usage")
    |> aggregateWindow(every: 1d, fn: sum, createEmpty: false)
    |> set(key: "_measurement", value: "usage_daily")
    |> to(bucket: "deepseek-usage-daily", org: "Jolly")
