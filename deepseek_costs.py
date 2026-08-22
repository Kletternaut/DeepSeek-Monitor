import datetime
import os
import tkinter as tk
import zoneinfo

# ==========================================
# KONFIGURATION DER DEEPSEEK V4 PEAK-ZEITEN
# ==========================================
# Peak-Zeiten in UTC (Minuten seit Mitternacht)
PEAK1_UTC_START = 1 * 60 + 0  # 01:00 UTC
PEAK1_UTC_END = 4 * 60 + 0  # 04:00 UTC

PEAK2_UTC_START = 6 * 60 + 0  # 06:00 UTC
PEAK2_UTC_END = 10 * 60 + 0  # 10:00 UTC

# Farben für das filigrane Design
COLOR_BG = "#121212"  # Dunkler Hintergrund
COLOR_CARD = "#1a1a1a"  # Card-Hintergrund
COLOR_CARD_BORDER = "#2d2d2d"  # Rahmen
COLOR_STANDARD = "#2e7d32"  # Grün (Standard 1x)
COLOR_PEAK = "#c62828"  # Rot (Peak 2x)
COLOR_TEXT_MAIN = "#ffffff"  # Weiß
COLOR_TEXT_DIM = "#78909c"  # Filigranes Grau
COLOR_TICK_MINOR = "#404040"  # Korrigierter Hex-Farbwert für kleine Ticks
COLOR_ACCENT = "#64b5f6"  # Hellblau
COLOR_NEEDLE = "#ffffff"  # Zeiger-Farbe


def get_times():
  """Holt aktuelle UTC- und lokale deutsche Zeit (inkl. Sommerzeit)."""
  tz_de = zoneinfo.ZoneInfo("Europe/Berlin")
  now_local = datetime.datetime.now(tz_de)
  now_utc = now_local.astimezone(datetime.timezone.utc)
  return now_utc, now_local


def utc_to_local_string(utc_minutes, is_dst):
  """Konvertiert UTC-Minuten in deutsche Lokalzeit (HH:MM)."""
  offset_hours = 2 if is_dst else 1
  total_local_minutes = (utc_minutes + offset_hours * 60) % (24 * 60)
  hours = total_local_minutes // 60
  minutes = total_local_minutes % 60
  return f"{hours:02d}:{minutes:02d}"


def calculate_status(now_utc):
  """Berechnet Preiskategorie und verbleibende Sekunden bis Wechsel."""
  current_min = now_utc.hour * 60 + now_utc.minute
  seconds_offset = now_utc.second

  if PEAK1_UTC_START <= current_min < PEAK1_UTC_END:
    min_left = PEAK1_UTC_END - current_min
    return (
        "PEAK TARIF",
        min_left * 60 - seconds_offset,
        COLOR_PEAK,
    )

  elif PEAK2_UTC_START <= current_min < PEAK2_UTC_END:
    min_left = PEAK2_UTC_END - current_min
    return (
        "PEAK TARIF",
        min_left * 60 - seconds_offset,
        COLOR_PEAK,
    )

  else:
    if current_min < PEAK1_UTC_START:
      min_left = PEAK1_UTC_START - current_min
    elif current_min < PEAK2_UTC_START:
      min_left = PEAK2_UTC_START - current_min
    else:
      min_left = (24 * 60 - current_min) + PEAK1_UTC_START

    return (
        "STANDARD TARIF",
        min_left * 60 - seconds_offset,
        COLOR_STANDARD,
    )


# Pfad für Positionsspeicherung
POS_FILE = os.path.expanduser("~/.deepseek_tarif_pos")


class DeepSeekFiligreeGUI:

  def __init__(self, root):
    self.root = root
    self.root.title("DeepSeek Tarif DE")
    self.root.resizable(False, False)
    self.root.configure(bg=COLOR_BG)

    # Fensterposition wiederherstellen + Größe in einem Rutsch
    geo = self._read_position()
    self.root.geometry(geo)
    # Beim Schließen Position speichern
    self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # Status-Badge
    self.lbl_badge = tk.Label(
        root,
        text="",
        font=("Segoe UI", 9, "bold"),
        fg="#ffffff",
        bg=COLOR_STANDARD,
        padx=8,
        pady=1,
    )
    self.lbl_badge.pack(pady=(6, 3))

    # Countdown-Timer
    self.lbl_timer = tk.Label(
        root,
        text="00:00:00",
        font=("Segoe UI", 22, "bold"),
        fg=COLOR_TEXT_MAIN,
        bg=COLOR_BG,
    )
    self.lbl_timer.pack()

    # Zeitleiste
    self.canvas = tk.Canvas(
        root, height=28, bg=COLOR_BG, highlightthickness=0
    )
    self.canvas.pack(padx=6, fill=tk.X)

    self.update_loop()

  def _read_position(self):
    """Liest die letzte Fensterposition, gibt geometrie-String zurück."""
    W = 360
    H = 110
    try:
      with open(POS_FILE, "r") as f:
        geo = f.read().strip()
        # Format: "WxH+X+Y" oder altes "X,Y"
        if "+" in geo:
          parts = geo.replace("x", "+").split("+")
          # parts = [W, H, X, Y]; W/H aus Datei ignorieren, X/Y verwenden
          x, y = parts[2], parts[3]
        else:
          x, y = geo.split(",")
        return f"{W}x{H}+{x}+{y}"
    except Exception:
      return f"{W}x{H}"

  def _on_close(self):
    """Speichert die Position und beendet."""
    try:
      geo = self.root.geometry()  # z.B. "360x150+100+200"
      with open(POS_FILE, "w") as f:
        f.write(geo)
    except Exception:
      pass
    self.root.destroy()

  def draw_filigree_timeline(self, now_utc, is_dst):
    """Zeichnet die filigrane Zeitleiste mit feinen Strichen und gedruckten Uhrzeiten."""
    self.canvas.delete("all")
    w = self.canvas.winfo_width()
    if w < 10:
      w = 340

    padding_x = 12
    usable_w = w - (2 * padding_x)

    bar_y_top = 6
    bar_y_bottom = 16

    def utc_min_to_px(utc_min):
      local_offset_min = (2 * 60) if is_dst else (1 * 60)
      local_min = (utc_min + local_offset_min) % (24 * 60)
      return padding_x + (local_min / (24 * 60)) * usable_w

    # --- 1. Grundbalken (Grün / Standard) ---
    self.canvas.create_rectangle(
        padding_x,
        bar_y_top,
        padding_x + usable_w,
        bar_y_bottom,
        fill=COLOR_STANDARD,
        outline="",
    )

    # --- 2. Peak-Zonen (Rot) ---
    p1_s, p1_e = utc_min_to_px(PEAK1_UTC_START), utc_min_to_px(PEAK1_UTC_END)
    p2_s, p2_e = utc_min_to_px(PEAK2_UTC_START), utc_min_to_px(PEAK2_UTC_END)

    for ps, pe in [(p1_s, p1_e), (p2_s, p2_e)]:
      if ps < pe:
        self.canvas.create_rectangle(
            ps, bar_y_top, pe, bar_y_bottom, fill=COLOR_PEAK, outline=""
        )
      else:
        self.canvas.create_rectangle(
            ps,
            bar_y_top,
            padding_x + usable_w,
            bar_y_bottom,
            fill=COLOR_PEAK,
            outline="",
        )
        self.canvas.create_rectangle(
            padding_x, bar_y_top, pe, bar_y_bottom, fill=COLOR_PEAK, outline=""
        )

    # --- 3. Filigrane Stunden-Ticks & Uhrzeiten ---
    for h in range(25):
      x = padding_x + (h / 24.0) * usable_w

      if h % 3 == 0:
        # Großer, filigraner Strich durch den Balken
        self.canvas.create_line(
            x,
            bar_y_top - 3,
            x,
            bar_y_bottom + 3,
            fill="#ffffff",
            width=1,
        )
        # Uhrzeit-Text unter dem Balken
        time_lbl = f"{h:02d}"
        self.canvas.create_text(
            x,
            bar_y_bottom + 8,
            text=time_lbl,
            fill=COLOR_TEXT_DIM,
            font=("Segoe UI", 7),
        )
      else:
        # Kleiner Zwischen-Tick (jede Stunde)
        self.canvas.create_line(
            x,
            bar_y_top + 2,
            x,
            bar_y_bottom - 2,
            fill=COLOR_TICK_MINOR,
            width=1,
        )

    # --- 4. Filigraner Zeiger für die aktuelle Uhrzeit ---
    current_utc_min = now_utc.hour * 60 + now_utc.minute
    cx = utc_min_to_px(current_utc_min)

    self.canvas.create_line(
        cx, 0, cx, bar_y_bottom + 4, fill=COLOR_NEEDLE, width=2
    )

    self.canvas.create_polygon(
        cx - 4,
        0,
        cx + 4,
        0,
        cx,
        6,
        fill=COLOR_NEEDLE,
    )

  def update_loop(self):
    now_utc, now_local = get_times()
    is_dst = bool(now_local.dst())

    status_text, sec_left, bg_color = calculate_status(now_utc)

    hrs = int(sec_left // 3600)
    mins = int((sec_left % 3600) // 60)
    secs = int(sec_left % 60)

    self.lbl_badge.config(text=status_text, bg=bg_color)
    self.lbl_timer.config(text=f"{hrs:02d}:{mins:02d}:{secs:02d} h")

    self.draw_filigree_timeline(now_utc, is_dst)

    self.root.after(1000, self.update_loop)


if __name__ == "__main__":
  root = tk.Tk()
  app = DeepSeekFiligreeGUI(root)
  root.after(100, app.update_loop)
  root.mainloop()