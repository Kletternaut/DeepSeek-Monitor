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

# Weekend off-peak rule (DeepSeek announcement):
# From Sunday 2026-08-23 00:00 Beijing time the off-peak rate applies
# for the entire weekend (Saturday + Sunday, Beijing time).
WEEKEND_RULE_START_UTC = datetime.datetime(
    2026, 8, 22, 16, 0, tzinfo=datetime.timezone.utc
)  # = 2026-08-23 00:00 Asia/Shanghai
BJ_TZ = zoneinfo.ZoneInfo("Asia/Shanghai")

# Pfade für Konfigurationsspeicherung
POS_FILE = os.path.expanduser("~/.deepseek_tarif_pos")
CONFIG_FILE = os.path.expanduser("~/.deepseek_tarif_config")
DEFAULT_TZ = "Europe/Berlin"

# Häufig genutzte Zeitzonen (Dropdown-Liste)
COMMON_TIMEZONES = [
    "UTC",
    "US/Eastern", "US/Central", "US/Mountain", "US/Pacific",
    "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles",
    "America/Sao_Paulo", "America/Argentina/Buenos_Aires",
    "Europe/London", "Europe/Berlin", "Europe/Paris", "Europe/Madrid",
    "Europe/Rome", "Europe/Amsterdam", "Europe/Stockholm", "Europe/Warsaw",
    "Europe/Istanbul", "Europe/Moscow",
    "Asia/Dubai", "Asia/Kolkata", "Asia/Jakarta", "Asia/Shanghai",
    "Asia/Tokyo", "Asia/Seoul", "Asia/Singapore", "Asia/Bangkok",
    "Australia/Sydney", "Australia/Melbourne", "Australia/Perth",
    "Pacific/Auckland", "Pacific/Fiji",
    "Africa/Cairo", "Africa/Johannesburg", "Africa/Lagos",
]


def load_timezone():
    """Liest die gespeicherte Zeitzone aus der Config-Datei."""
    try:
        with open(CONFIG_FILE, "r") as f:
            tz = f.read().strip()
            if tz and tz in zoneinfo.available_timezones():
                return tz
    except Exception:
        pass
    return DEFAULT_TZ


def save_timezone(tz_name):
    """Speichert die gewählte Zeitzone."""
    try:
        with open(CONFIG_FILE, "w") as f:
            f.write(tz_name)
    except Exception:
        pass

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
COLOR_WEEKEND = "#f57c00"  # Orange (Wochenende: ganztägig Off-Peak)


def get_times(tz_name):
  """Holt aktuelle UTC- und lokale Zeit für die angegebene Zeitzone."""
  tz = zoneinfo.ZoneInfo(tz_name)
  now_local = datetime.datetime.now(tz)
  now_utc = now_local.astimezone(datetime.timezone.utc)
  offset = now_local.utcoffset()
  offset_hours = offset.total_seconds() / 3600 if offset else 0
  return now_utc, now_local, offset_hours


def utc_to_local_string(utc_minutes, offset_hours):
  """Konvertiert UTC-Minuten in Lokalzeit (HH:MM)."""
  total_local_minutes = (utc_minutes + int(offset_hours * 60)) % (24 * 60)
  hours = total_local_minutes // 60
  minutes = total_local_minutes % 60
  return f"{hours:02d}:{minutes:02d}"


def status_at(now_utc):
  """Returns the current rate key: 'PEAK', 'STANDARD' or 'WEEKEND'."""
  bj_now = now_utc.astimezone(BJ_TZ)
  if now_utc >= WEEKEND_RULE_START_UTC and bj_now.weekday() >= 5:
    return "WEEKEND"
  current_min = now_utc.hour * 60 + now_utc.minute
  if PEAK1_UTC_START <= current_min < PEAK1_UTC_END:
    return "PEAK"
  if PEAK2_UTC_START <= current_min < PEAK2_UTC_END:
    return "PEAK"
  return "STANDARD"


def day_transitions(day_bj):
  """All rate changes on one Beijing calendar day (aware 00:00 Beijing).

  Returns a list of (utc_datetime, status_after) tuples.
  """
  out = []
  w = day_bj.weekday()
  weekend_rule_day = day_bj.date() >= datetime.date(2026, 8, 23)

  if weekend_rule_day and w >= 5:
    # Weekend: off-peak all day, next change is Monday 00:00 Beijing
    monday_bj = day_bj + datetime.timedelta(days=(7 - w))
    out.append((monday_bj.astimezone(datetime.timezone.utc), "STANDARD"))
    return out

  # Weekday logic (also applies to Saturday 2026-08-22 before the rule start)
  d = day_bj.date()
  for hh, status in [
      (1, "PEAK"), (4, "STANDARD"), (6, "PEAK"), (10, "STANDARD")]:
    out.append((
        datetime.datetime(d.year, d.month, d.day, hh, 0,
                          tzinfo=datetime.timezone.utc),
        status,
    ))

  # Friday: weekend off-peak starts Saturday 00:00 Beijing
  if w == 4 and (day_bj + datetime.timedelta(days=1)).date() >= datetime.date(2026, 8, 23):
    out.append((
        (day_bj + datetime.timedelta(days=1)).astimezone(datetime.timezone.utc),
        "WEEKEND",
    ))

  # Special case: the rule start instant on 2026-08-22 16:00 UTC
  if day_bj.date() == datetime.date(2026, 8, 22):
    out.append((WEEKEND_RULE_START_UTC, "WEEKEND"))

  return out


def calculate_status(now_utc):
  """Berechnet Preiskategorie, Sekunden bis zum nächsten Wechsel, Farbe und Key."""
  key = status_at(now_utc)

  bj_now = now_utc.astimezone(BJ_TZ)
  bj_today = bj_now.replace(hour=0, minute=0, second=0, microsecond=0)
  candidates = []
  for offset in range(9):
    day_bj = bj_today + datetime.timedelta(days=offset)
    candidates.extend(day_transitions(day_bj))
  future = [(t, s) for t, s in candidates if t > now_utc]
  next_t, _ = min(future, key=lambda item: item[0]) if future else (None, None)
  sec_left = (next_t - now_utc).total_seconds() if next_t else 0

  if key == "WEEKEND":
    return "WOCHENENDE OFF-PEAK", sec_left, COLOR_WEEKEND, key
  if key == "PEAK":
    return "PEAK", sec_left, COLOR_PEAK, key
  return "STANDARD", sec_left, COLOR_STANDARD, key


class DeepSeekFiligreeGUI:

  def __init__(self, root):
    self.root = root
    self.root.title("DeepSeek V4 Rates")
    self.root.resizable(False, False)
    self.root.configure(bg=COLOR_BG)

    # Zeitzone laden
    self.tz_name = load_timezone()

    # Fensterposition wiederherstellen + Größe in einem Rutsch
    geo = self._read_position()
    self.root.geometry(geo)
    # Beim Schließen Position speichern
    self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # Header-Zeile: Badge + TZ-Dropdown
    self.header_frame = tk.Frame(root, bg=COLOR_BG)
    self.header_frame.pack(pady=(6, 3), padx=6, fill=tk.X)

    self.lbl_badge = tk.Label(
        self.header_frame,
        text="",
        font=("Segoe UI", 9, "bold"),
        fg="#ffffff",
        bg=COLOR_STANDARD,
        padx=8,
        pady=1,
    )
    self.lbl_badge.pack(side=tk.LEFT)

    tk.Label(self.header_frame, text="", bg=COLOR_BG).pack(
        side=tk.LEFT, fill=tk.X, expand=True)

    tk.Label(
        self.header_frame, text="TZ:", font=("Segoe UI", 8),
        fg=COLOR_TEXT_DIM, bg=COLOR_BG
    ).pack(side=tk.LEFT, padx=(0, 2))

    self.tz_btn = tk.Menubutton(
        self.header_frame,
        text=self.tz_name,
        font=("Segoe UI", 8),
        fg=COLOR_TEXT_MAIN,
        bg=COLOR_CARD,
        activebackground=COLOR_CARD_BORDER,
        activeforeground=COLOR_TEXT_MAIN,
        relief="flat",
        borderwidth=0,
        padx=5,
        pady=1,
    )
    self.tz_btn.pack(side=tk.LEFT)

    self.tz_menu = tk.Menu(
        self.tz_btn,
        tearoff=0,
        bg=COLOR_CARD,
        fg=COLOR_TEXT_MAIN,
        activebackground=COLOR_ACCENT,
        activeforeground="#000000",
        font=("Segoe UI", 8),
    )
    for tz in COMMON_TIMEZONES:
      self.tz_menu.add_command(
          label=tz, command=lambda t=tz: self._on_tz_select(t))
    self.tz_btn.config(menu=self.tz_menu)

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
        root, height=55, bg=COLOR_BG, highlightthickness=0
    )
    self.canvas.pack(padx=6, fill=tk.X)

    # Peak-/Standard-Fenster (zwei Labels für farbliche Unterscheidung)
    self.win_frame = tk.Frame(root, bg=COLOR_BG)
    self.win_frame.pack(pady=(0, 0))

    self.lbl_peak_label = tk.Label(
        self.win_frame,
        text="Peak:",
        font=("Segoe UI", 7, "bold"),
        fg="#ef5350",
        bg=COLOR_BG,
    )
    self.lbl_peak_label.pack(side=tk.LEFT)

    self.lbl_peak_times = tk.Label(
        self.win_frame,
        text="",
        font=("Segoe UI", 7),
        fg="#ffffff",
        bg=COLOR_BG,
    )
    self.lbl_peak_times.pack(side=tk.LEFT)

    self.lbl_std_label = tk.Label(
        self.win_frame,
        text="Std:",
        font=("Segoe UI", 7, "bold"),
        fg="#66bb6a",
        bg=COLOR_BG,
    )
    self.lbl_std_label.pack(side=tk.LEFT, padx=(12, 0))

    self.lbl_std_times = tk.Label(
        self.win_frame,
        text="",
        font=("Segoe UI", 7),
        fg="#ffffff",
        bg=COLOR_BG,
    )
    self.lbl_std_times.pack(side=tk.LEFT)

    # Wochenend-Hinweis (orange)
    self.lbl_weekend_info = tk.Label(
        root,
        text="Wochenende (Sa + So, Peking): ganztägig Off-Peak ab 23.08.2026",
        font=("Segoe UI", 7),
        fg=COLOR_WEEKEND,
        bg=COLOR_BG,
    )
    self.lbl_weekend_info.pack()

    self.update_loop()

  def _read_position(self):
    """Liest die letzte Fensterposition, gibt geometrie-String zurück."""
    try:
      with open(POS_FILE, "r") as f:
        geo = f.read().strip()
        if "x" in geo and "+" in geo:
          return geo  # voller String: "360x180+100+200"
        # Altes Format: "x,y"
        x, y = geo.split(",")
        return f"360x180+{x}+{y}"
    except Exception:
      return "360x180"

  def _on_close(self):
    """Speichert die Position und beendet."""
    try:
      geo = self.root.geometry()  # z.B. "360x150+100+200"
      with open(POS_FILE, "w") as f:
        f.write(geo)
    except Exception:
      pass
    self.root.destroy()

  def draw_filigree_timeline(self, now_utc, offset_hours):
    """Zeichnet die filigrane Zeitleiste mit feinen Strichen und gedruckten Uhrzeiten."""
    self.canvas.delete("all")
    w = self.canvas.winfo_width()
    if w < 10:
      w = 340

    padding_x = 12
    usable_w = w - (2 * padding_x)

    bar_y_top = 12
    bar_y_bottom = 24

    def utc_min_to_px(utc_min):
      local_offset_min = int(offset_hours * 60)
      local_min = (utc_min + local_offset_min) % (24 * 60)
      return padding_x + (local_min / (24 * 60)) * usable_w

    # --- 1. Grundbalken (Grün / Standard, Orange am Wochenende) ---
    status_key = status_at(now_utc)
    base_color = COLOR_WEEKEND if status_key == "WEEKEND" else COLOR_STANDARD
    self.canvas.create_rectangle(
        padding_x,
        bar_y_top,
        padding_x + usable_w,
        bar_y_bottom,
        fill=base_color,
        outline="",
    )

    # --- 2. Peak-Zonen (Rot) – nur an Werktagen ---
    if status_key != "WEEKEND":
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
            bar_y_bottom + 12,
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

  def _on_tz_select(self, tz):
    """Wird aufgerufen, wenn der Benutzer die Zeitzone ändert."""
    if tz in zoneinfo.available_timezones():
      self.tz_name = tz
      self.tz_btn.config(text=tz)
      save_timezone(tz)

  def update_loop(self):
    now_utc, now_local, offset_hours = get_times(self.tz_name)

    status_text, sec_left, bg_color, status_key = calculate_status(now_utc)

    hrs = int(sec_left // 3600)
    mins = int((sec_left % 3600) // 60)
    secs = int(sec_left % 60)

    self.lbl_badge.config(text=status_text, bg=bg_color)
    self.lbl_timer.config(text=f"{hrs:02d}:{mins:02d}:{secs:02d} h")

    self.draw_filigree_timeline(now_utc, offset_hours)

    if status_key == "WEEKEND":
      self.lbl_peak_times.config(text=" –", fg="#ffffff")
      self.lbl_std_times.config(
          text=" ganztägig 00:00–24:00 (Wochenende)",
          fg=COLOR_WEEKEND,
      )
    else:
      p1_s = utc_to_local_string(PEAK1_UTC_START, offset_hours)
      p1_e = utc_to_local_string(PEAK1_UTC_END, offset_hours)
      p2_s = utc_to_local_string(PEAK2_UTC_START, offset_hours)
      p2_e = utc_to_local_string(PEAK2_UTC_END, offset_hours)
      wins = sorted([(p1_s, p1_e), (p2_s, p2_e)])

      self.lbl_peak_times.config(
          text=f" {wins[0][0]}–{wins[0][1]} & {wins[1][0]}–{wins[1][1]}",
          fg="#ffffff",
      )
      self.lbl_std_times.config(
          text=f" {wins[0][1]}–{wins[1][0]} & {wins[1][1]}–{wins[0][0]}",
          fg="#ffffff",
      )

    self.root.after(1000, self.update_loop)


if __name__ == "__main__":
  root = tk.Tk()
  app = DeepSeekFiligreeGUI(root)
  root.after(100, app.update_loop)
  root.mainloop()