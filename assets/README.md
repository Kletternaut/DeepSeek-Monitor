# DeepSeek-Monitor — App Icons

Two brand-neutral app icons for the open-source tool "DeepSeek-Monitor" (API
cost and usage monitoring). Deliberately without DeepSeek trademarks (no whale,
no logo, no wordmark, no DeepSeek blue).

- **`deepseek-monitor.svg`** — icon of the monitor app (Tkinter). Concept: a
  gauge with a green "low cost" segment and a needle, combined with a falling
  usage line ("costs dropping"). Colors: dark slate gradient + emerald green
  (#34D399) / teal (#2DD4BF).
- **`deepseek-proxy.svg`** — icon for the streaming proxy (manual start via
  `.desktop`). Concept: identical to the monitor icon (gauge with a green
  segment and a needle), but with the text "proxy" under the gauge instead of
  the falling usage line.

Rendering/export: single SVG files (viewBox 0 0 512 512), scalable from 16×16
to 512×512. Export to PNG/ICO via e.g. `rsvg-convert` or Inkscape; no external
references or bitmaps. The `-32/64/128/256/512.png` files are pre-rendered
sizes for desktop entries and app icons.

