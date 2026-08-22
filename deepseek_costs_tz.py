import csv
import datetime
import json
import os
import threading
import time
import tkinter as tk
import urllib.request
import zoneinfo
from tkinter import ttk

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

# Realtime-Guthaben-Überwachung (GET /user/balance)
BALANCE_URL = "https://api.deepseek.com/user/balance"
API_KEY_FILE = os.path.expanduser("~/.deepseek_api_key")
HISTORY_FILE = os.path.expanduser("~/.deepseek_balance_history.json")
DAILY_CSV_FILE = os.path.expanduser("~/.deepseek_daily_usage.csv")
POLL_INTERVAL_S = 300  # alle 5 Minuten abfragen
MAX_HISTORY = 50000

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


def load_api_key():
    """Liest den API-Key aus der Umgebungsvariable oder ~/.deepseek_api_key."""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    try:
        with open(API_KEY_FILE, "r") as f:
            return f.read().strip()
    except Exception:
        return ""


def fetch_balance(api_key):
    """Ruft GET /user/balance ab und liefert das JSON-Objekt."""
    req = urllib.request.Request(
        BALANCE_URL,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_balance(data):
    """Extrahiert die Guthaben-Felder (unterstützt Flat- und balance_infos-Format)."""
    def _f(obj, field):
        try:
            return float(obj.get(field, 0))
        except (TypeError, ValueError):
            return 0.0

    is_available = bool(data.get("is_available", True))
    infos = data.get("balance_infos") or []
    if infos:
        info = infos[0]
        return {
            "total": _f(info, "total_balance"),
            "topped": _f(info, "topped_up_balance"),
            "granted": _f(info, "granted_balance"),
            "available": is_available,
            "currency": info.get("currency", ""),
        }
    return {
        "total": _f(data, "total_balance"),
        "topped": _f(data, "topped_up_balance"),
        "granted": _f(data, "granted_balance"),
        "available": is_available,
        "currency": data.get("currency", ""),
    }


def load_history():
    """Lädt den gespeicherten Guthaben-Verlauf."""
    try:
        with open(HISTORY_FILE, "r") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def save_history(records):
    """Speichert den Guthaben-Verlauf (auf MAX_HISTORY begrenzt)."""
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(records[-MAX_HISTORY:], f)
    except Exception:
        pass


def daily_summary(records, tz_name):
    """Tagesauswertung: Verbrauch = erstes minus letztes Guthaben des Tages."""
    tz = zoneinfo.ZoneInfo(tz_name)
    by_day = {}
    for rec in records:
        try:
            ts = datetime.datetime.fromisoformat(rec["ts"])
        except Exception:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=datetime.timezone.utc)
        day = ts.astimezone(tz).date()
        by_day.setdefault(day, []).append(rec.get("total", 0))
    rows = []
    for day in sorted(by_day):
        vals = by_day[day]
        if len(vals) >= 2:
            rows.append({
                "date": day.isoformat(),
                "start": vals[0],
                "end": vals[-1],
                "usage": round(vals[0] - vals[-1], 4),
            })
    return rows


def write_daily_csv(rows):
    """Schreibt die Tagesauswertung als CSV."""
    try:
        with open(DAILY_CSV_FILE, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["date", "balance_start", "balance_end", "usage"])
            for r in rows:
                writer.writerow([r["date"], r["start"], r["end"], r["usage"]])
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

    # Balance-Status (Thread-sicher)
    self._balance_lock = threading.Lock()
    self._fetch_lock = threading.Lock()
    self._balance_state = {}
    self._daily_rows = []
    self._balance_version = 0
    self._balance_version_seen = -1

    # Fensterposition wiederherstellen + Größe in einem Rutsch
    geo = self._read_position()
    self.root.geometry(geo)
    # Beim Schließen Position speichern
    self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # Tabs: Tarif + Realtime
    self._setup_ttk_style()
    self.notebook = ttk.Notebook(root)
    self.notebook.pack(fill=tk.BOTH, expand=True)
    self.tab_tarif = tk.Frame(self.notebook, bg=COLOR_BG)
    self.tab_balance = tk.Frame(self.notebook, bg=COLOR_BG)
    self.notebook.add(self.tab_tarif, text=" Tarif ")
    self.notebook.add(self.tab_balance, text=" Realtime ")

    # Header-Zeile: Badge + TZ-Dropdown
    self.header_frame = tk.Frame(self.tab_tarif, bg=COLOR_BG)
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
        self.tab_tarif,
        text="00:00:00",
        font=("Segoe UI", 22, "bold"),
        fg=COLOR_TEXT_MAIN,
        bg=COLOR_BG,
    )
    self.lbl_timer.pack()

    # Zeitleiste
    self.canvas = tk.Canvas(
        self.tab_tarif, height=55, bg=COLOR_BG, highlightthickness=0
    )
    self.canvas.pack(padx=6, fill=tk.X)

    # Peak-/Standard-Fenster (zwei Labels für farbliche Unterscheidung)
    self.win_frame = tk.Frame(self.tab_tarif, bg=COLOR_BG)
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
        self.tab_tarif,
        text="Wochenende (Sa + So, Peking): ganztägig Off-Peak ab 23.08.2026",
        font=("Segoe UI", 7),
        fg=COLOR_WEEKEND,
        bg=COLOR_BG,
    )
    self.lbl_weekend_info.pack()

    # Realtime-Tab bauen
    self._build_balance_tab()

    # Tagesauswertung aus vorhandenem Verlauf vorladen
    self._daily_rows = daily_summary(load_history(), self.tz_name)
    self._balance_version = 1

    # Abfrage-Thread starten (sofort + alle 5 Minuten)
    threading.Thread(target=self._poll_worker, daemon=True).start()

    self.update_loop()

  def _read_position(self):
    """Liest die letzte Fensterposition, erzwingt Mindestgröße für die Tabs."""
    W, H = 400, 340
    try:
      with open(POS_FILE, "r") as f:
        geo = f.read().strip()
        if "x" in geo and "+" in geo:
          # "WxH+X+Y" – Größe auf Minimum anheben, Position beibehalten
          size, _, pos = geo.partition("+")
          w, _, h = size.partition("x")
          x, _, y = pos.partition("+")
          try:
            w = max(int(w), W)
            h = max(int(h), H)
            return f"{w}x{h}+{x}+{y}"
          except Exception:
            return f"{W}x{H}"
        # Altes Format: "x,y"
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
      # Tagesauswertung in der neuen Zeitzone neu gruppieren
      self._daily_rows = daily_summary(load_history(), self.tz_name)
      self._balance_version += 1

  # ------------------------------------------------------------
  # Realtime-Guthaben (GET /user/balance) + Tagesauswertung
  # ------------------------------------------------------------

  def _setup_ttk_style(self):
    """Dunkles Styling für das Notebook."""
    style = ttk.Style(self.root)
    try:
      style.theme_use("clam")
    except Exception:
      pass
    style.configure("TNotebook", background=COLOR_BG, borderwidth=0)
    style.configure(
        "TNotebook.Tab",
        background=COLOR_CARD,
        foreground=COLOR_TEXT_DIM,
        padding=(14, 4),
        borderwidth=0,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", COLOR_CARD_BORDER)],
        foreground=[("selected", COLOR_TEXT_MAIN)],
    )

  def _build_balance_tab(self):
    """Baut den Realtime-Tab (Guthaben + Tagesauswertung)."""
    tab = self.tab_balance

    self.lbl_balance_status = tk.Label(
        tab,
        text="Verbinde mit DeepSeek API…",
        font=("Segoe UI", 9, "bold"),
        fg=COLOR_TEXT_DIM,
        bg=COLOR_BG,
    )
    self.lbl_balance_status.pack(pady=(8, 2))

    self.lbl_balance_big = tk.Label(
        tab,
        text="–",
        font=("Segoe UI", 22, "bold"),
        fg=COLOR_TEXT_MAIN,
        bg=COLOR_BG,
    )
    self.lbl_balance_big.pack()

    self.lbl_balance_sub = tk.Label(
        tab,
        text="",
        font=("Segoe UI", 8),
        fg=COLOR_TEXT_DIM,
        bg=COLOR_BG,
    )
    self.lbl_balance_sub.pack(pady=(2, 4))

    btn_row = tk.Frame(tab, bg=COLOR_BG)
    btn_row.pack()
    self.btn_refresh = tk.Button(
        btn_row,
        text="Jetzt aktualisieren",
        font=("Segoe UI", 8),
        fg=COLOR_TEXT_MAIN,
        bg=COLOR_CARD,
        activebackground=COLOR_CARD_BORDER,
        activeforeground=COLOR_TEXT_MAIN,
        relief="flat",
        borderwidth=0,
        padx=8,
        pady=2,
        command=self._on_refresh,
    )
    self.btn_refresh.pack(side=tk.LEFT)

    self.lbl_balance_update = tk.Label(
        btn_row,
        text="",
        font=("Segoe UI", 7),
        fg=COLOR_TEXT_DIM,
        bg=COLOR_BG,
    )
    self.lbl_balance_update.pack(side=tk.LEFT, padx=(8, 0))

    tk.Label(
        tab,
        text="Tagesauswertung (letzte 14 Tage)",
        font=("Segoe UI", 8, "bold"),
        fg=COLOR_ACCENT,
        bg=COLOR_BG,
    ).pack(pady=(8, 2), anchor="w", padx=10)

    self.txt_daily = tk.Text(
        tab,
        height=10,
        bg=COLOR_CARD,
        fg=COLOR_TEXT_MAIN,
        relief="flat",
        borderwidth=0,
        font=("Consolas", 8),
        padx=6,
        pady=4,
        state="disabled",
    )
    self.txt_daily.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 4))

    self.lbl_csv_path = tk.Label(
        tab,
        text="",
        font=("Segoe UI", 7),
        fg=COLOR_TEXT_DIM,
        bg=COLOR_BG,
    )
    self.lbl_csv_path.pack(pady=(0, 6))

  def _on_refresh(self):
    """Manuelles Abrufen des Guthabens (eigener kurzer Thread)."""
    threading.Thread(target=self._fetch_and_store, daemon=True).start()

  def _poll_worker(self):
    """Abfrage-Schleife: sofort und danach alle POLL_INTERVAL_S Sekunden."""
    while True:
      self._fetch_and_store()
      time.sleep(POLL_INTERVAL_S)

  def _fetch_and_store(self):
    """Holt das Guthaben, speichert es im Verlauf, baut die Tagesauswertung."""
    with self._fetch_lock:
      api_key = load_api_key()
      if not api_key:
        with self._balance_lock:
          self._balance_state = {
              "error": "Kein API-Key: DEEPSEEK_API_KEY setzen oder "
                       "~/.deepseek_api_key anlegen"
          }
          self._balance_version += 1
        return
      try:
        data = fetch_balance(api_key)
        info = parse_balance(data)
        now = datetime.datetime.now(datetime.timezone.utc)
        record = {
            "ts": now.isoformat(),
            "total": info["total"],
            "topped": info["topped"],
            "granted": info["granted"],
            "available": info["available"],
            "currency": info["currency"],
        }
        history = load_history()
        history.append(record)
        save_history(history)
        rows = daily_summary(history, self.tz_name)
        write_daily_csv(rows)
        info["last_update"] = now
        info["error"] = None
        with self._balance_lock:
          self._balance_state = info
          self._daily_rows = rows
          self._balance_version += 1
      except Exception as exc:
        with self._balance_lock:
          if self._balance_state.get("total") is None:
            self._balance_state = {"error": f"API-Fehler: {exc}"}
          else:
            self._balance_state["error"] = f"API-Fehler: {exc}"
          self._balance_version += 1

  def _update_balance_ui(self):
    """Aktualisiert den Realtime-Tab, wenn neue Daten vorliegen."""
    with self._balance_lock:
      state = dict(self._balance_state or {})
      version = self._balance_version
      rows = list(self._daily_rows)
    if version == self._balance_version_seen:
      return
    self._balance_version_seen = version

    total = state.get("total")
    err = state.get("error")

    if total is None:
      self.lbl_balance_status.config(
          text="Fehler" if err else "Verbinde mit DeepSeek API…",
          fg=COLOR_PEAK if err else COLOR_TEXT_DIM,
      )
      self.lbl_balance_big.config(text="–")
      self.lbl_balance_sub.config(text="")
      if err:
        self.lbl_balance_update.config(text=err, fg=COLOR_PEAK)
      else:
        self.lbl_balance_update.config(text="")
    else:
      cur = state.get("currency", "")
      avail_txt = "verfügbar" if state.get("available", True) else "nicht ausreichend"
      self.lbl_balance_status.config(text="Guthaben (Realtime)", fg=COLOR_TEXT_DIM)
      self.lbl_balance_big.config(text=f"{total:.2f} {cur}".strip())
      self.lbl_balance_sub.config(
          text=(f"Aufgeladen: {state.get('topped', 0):.2f} {cur}  ·  "
                f"Geschenkt: {state.get('granted', 0):.2f} {cur}  ·  {avail_txt}")
      )
      lu = state.get("last_update")
      txt = ""
      if lu:
        lu_local = lu.astimezone(zoneinfo.ZoneInfo(self.tz_name))
        txt = f"Stand: {lu_local.strftime('%d.%m.%Y %H:%M:%S')}"
      if err:
        txt = (txt + "  ·  " if txt else "") + err
      self.lbl_balance_update.config(
          text=txt, fg=COLOR_PEAK if err else COLOR_TEXT_DIM
      )

    self.txt_daily.config(state="normal")
    self.txt_daily.delete("1.0", tk.END)
    if rows:
      lines = [f"{'Datum':<12}{'Verbrauch':>14}{'Endstand':>14}"]
      for r in rows[-14:]:
        lines.append(f"{r['date']:<12}{r['usage']:>14.4f}{r['end']:>14.4f}")
      self.txt_daily.insert(tk.END, "\n".join(lines))
    else:
      self.txt_daily.insert(
          tk.END,
          "Noch keine Daten – die Auswertung baut sich mit jeder Abfrage auf.",
      )
    self.txt_daily.config(state="disabled")
    self.lbl_csv_path.config(text=f"CSV: {DAILY_CSV_FILE}")

  def update_loop(self):
    self._update_balance_ui()
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