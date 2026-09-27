# DeepSeek-Monitor — App-Icons

Zwei markenneutrale App-Icons für das Open-Source-Tool „DeepSeek-Monitor“
(API-Kosten- und Verbrauchsüberwachung). Bewusst ohne DeepSeek-Markenzeichen
(kein Wal, kein Logo, kein Schriftzug, kein DeepSeek-Blau).

- **`deepseek-monitor.svg`** — Icon der Monitor-App (Tkinter). Konzept: ein
  Tacho/Gauge mit grünem „Niedrigkosten“-Segment und Zeiger, kombiniert mit
  einer fallenden Verbrauchs-Linie („Kosten sinken“). Farben: dunkler
  Slate-Verlauf + Smaragdgrün (#34D399) / Türkis (#2DD4BF).
- **`deepseek-proxy.svg`** — Icon für den Streaming-Proxy (manueller Start
  per `.desktop`). Konzept: identisch zum Monitor-Icon (Tacho/Gauge mit
  grünem Segment und Zeiger), jedoch mit dem Text „proxy" unter dem
  Tachometer statt der fallenden Verbrauchs-Linie.

Rendering/Export: einzelne SVG-Dateien (viewBox 0 0 512 512), skalierbar von
16×16 bis 512×512. Export in PNG/ICO über z. B. `rsvg-convert` oder Inkscape;
keine externen Referenzen oder Bitmaps. Die `-32/64/128/256/512.png`-Dateien
sind vorgerenderte Größen für Desktop-Einträge und App-Icons.

