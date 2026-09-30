# Chat-Zusammenfassung — HiDPI/DPI-Awareness-Fix für Tkinter-Fenster

**Datum:** 2026-09-30 · **Uhrzeit:** 16:14

## Thema

Behebung unscharfer Fenster (Monitor, Settings, Kosten & Verbrauch, Proxy,
Proxy-Settings) auf einem 4K-Monitor im Multi-Monitor-Setup; auf 100 %-Monitoren
war alles scharf.

## Ursache

Tkinter ist standardmäßig **nicht DPI-aware**. Windows behandelt den Prozess als
DPI-unaware und skaliert das Fenster auf dem hochauflösenden 4K-Monitor per
Bitmap-Stretching hoch → unscharf. Auf 100 %-Monitoren gibt es keine
Hochskalierung → scharf. Kein Code-Bug, sondern fehlende DPI-Awareness.

## Lösung

Neues Modul `deepseek_monitor/dpi.py` (beide Funktionen no-op auf Nicht-Windows):

- `enable_dpi_awareness()` → `SetProcessDpiAwareness(2)` (per-monitor V2, ideal
  für gemischte DPI-Multi-Monitor-Setups), Fallback `SetProcessDPIAware()`.
  Muss **vor** dem ersten `tk.Tk()` aufgerufen werden.
- `apply_tk_scaling(root)` → korrigiert Tk-interne Skalierung auf die echte DPI
  (`tk scaling` = `dpi/72`), sonst wäre Text auf HiDPI trotz Awareness zu klein.
  Aufruf **nach** Erstellung des Root-Fensters.

### Angewendet an allen drei Tkinter-Startstellen

- `deepseek_rates_v2.4.0.py` (Monitor-Hauptfenster, `__main__`)
- `deepseek-proxy/proxy/tkinter_view.py` (`UsageDetailApp.__init__`, `--view`)
- `deepseek-proxy/proxy/influx_dialog.py` (Tray-Konfigurationsdialog)

### Build

- `packaging/build-windows.ps1`: Hidden-Import `deepseek_monitor.dpi` für die
  Proxy-`.exe` ergänzt (lazy Import).
- Beide `.exe` neu gebaut (`DeepSeek-Monitor.exe`, `DeepSeek-Proxy.exe`).

## Verifikation

- Tests: `103 passed, 1 skipped`.
- `ruff check` + `black --check` für `deepseek-proxy` + `deepseek_monitor/dpi.py`
  → sauber.
- Build beider `.exe` erfolgreich.

## Hinweis

Der Effekt ist nur visuell auf dem 4K-Monitor prüfbar (Nutzer testet manuell).
Linux/macOS bleiben unberührt (No-op).
