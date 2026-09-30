#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DeepSeek Monitor + Realtime Balance + Analytics

Improvements over the original version:
- tariff calculation without an unnecessary 9-day recomputation every second
- timeline is redrawn only on minute/timezone changes
- history stays in memory and is not re-read on every poll
- atomic writes of JSON/CSV
- cleanly terminable background thread
- no Tkinter access from the worker thread
- new analytics view:
    * peak/standard/weekend usage
    * daily breakdown
    * estimated avoidable peak cost
    * latest usage intervals
    * transparency about the underlying assumption
- acoustic warning 60 seconds before a real tariff change
- API/network errors keep the last good balance state
- time intervals are distributed proportionally over tariff zones
- weekend logic and DST are respected in the tariff calculation

IMPORTANT ABOUT THE ANALYTICS:
The DeepSeek /user/balance API returns balance values. Usage is derived from the
balance loss between two measurement points. The period between two measurement
points is distributed proportionally over the respective tariff zones.

This means:
- The cost analysis is an ESTIMATE as long as only periodic balance values exist.
- A usage interval can span multiple zones across a tariff change.
- "Potential saving" is the avoidable peak premium under the assumption that the
  same usage could have taken place in the standard window.
- PEAK_MULTIPLIER = 2.0 matches the 2x assumption used in the original tool.
  If DeepSeek uses different factors in the future, adjust here.

Python 3.10+ recommended.
"""

from __future__ import annotations

import datetime as dt
import os
import threading
import time
import tkinter as tk
import urllib.error
import zoneinfo
from tkinter import messagebox, ttk
from typing import Any


# ============================================================
# IMPORTE AUS deepseek_monitor (extraktierte Logik-Module)
# ============================================================

import deepseek_monitor.config as config
from deepseek_monitor.config import (
    ANALYTICS_DAYS,
    ANALYTICS_INTERVAL_LIMIT,
    APP_NAME,
    APP_VERSION,
    BALANCE_URL,
    BJ_TZ,
    COLOR_ACCENT,
    COLOR_BG,
    COLOR_CARD,
    COLOR_CARD_BORDER,
    COLOR_NEEDLE,
    COLOR_PEAK,
    COLOR_STANDARD,
    COLOR_TEXT_DIM,
    COLOR_TEXT_MAIN,
    COLOR_TICK_MINOR,
    COLOR_WARNING,
    COLOR_WEEKEND,
    DEFAULT_POLL_INTERVAL_S,
    DEFAULT_TZ,
    MAX_HISTORY,
    MAX_POLL_INTERVAL_S,
    MIN_POLL_INTERVAL_S,
    PEAK_MULTIPLIER,
    PEAK_WINDOWS_UTC,
    POLL_INTERVAL_STEP_S,
    POLL_INTERVAL_S,
    PROXY_BASE_URL,
    PROXY_USAGE_URL,
    STATUS_COLORS,
    UTC,
    WARNING_BEFORE_S,
    WEEKEND_RULE_START_UTC,
    _apply_influx_config,
)
from deepseek_monitor.i18n import (
    BASE_TRANSLATIONS,
    CURRENCY_SYMBOLS,
    LANGUAGES,
    LANGUAGE_ORDER,
    TRANSLATIONS,
    detect_language,
)
from deepseek_monitor.paths import (
    API_KEY_FILE,
    AVAILABLE_TIMEZONES,
    COMMON_TIMEZONES,
    CONFIG_DIR,
    CONFIG_FILE,
    DAILY_CSV_FILE,
    DATA_DIR,
    HISTORY_FILE,
    INFLUX_CONFIG_FILE,
    LANGUAGE_FILE,
    LEGACY_FILES,
    POLL_INTERVAL_FILE,
    POS_FILE,
    STATE_DIR,
    _ensure_dir,
    _migrate_legacy_files,
    _xdg_base,
)
from deepseek_monitor.persistence import (
    _init_influx_config,
    load_api_key,
    load_history,
    load_influx_config,
    load_language,
    load_poll_interval,
    load_timezone,
    remove_influx_config,
    remove_language,
    remove_saved_api_key,
    save_api_key,
    save_history,
    save_influx_config,
    save_language,
    save_poll_interval,
    save_timezone,
    write_daily_csv,
)
from deepseek_monitor.api import (
    currency_symbol,
    fetch_balance,
    parse_balance,
)
from deepseek_monitor.tariff import (
    _all_tariff_boundaries_around,
    calculate_status,
    get_rate_periods,
    next_status_change,
    status_at,
    weekend_segments_utc,
)
from deepseek_monitor.analytics import (
    _flux_row_to_event,
    _influx_query,
    _parse_flux_annotated_csv,
    _parse_history,
    _shade_hex,
    _split_at_local_midnights,
    analyze_history,
    analyze_proxy_usage,
    fetch_influx_hosts,
    fetch_influx_usage,
    fetch_proxy_usage,
    fetch_usage,
    format_money,
)
# ============================================================
# GUI
# ============================================================

class _LegacyDeepSeekFiligreeGUI:

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_NAME)
        self.root.resizable(False, False)
        self.root.configure(bg=COLOR_BG)

        self.tz_name = load_timezone()
        self.tz = zoneinfo.ZoneInfo(self.tz_name)
        self._language_auto = load_language() is None
        self.language = load_language() or detect_language()
        self._api_key_dialog = None

        self._state_lock = threading.Lock()
        self._fetch_lock = threading.Lock()

        self._balance_state: dict[str, Any] = {}
        self._balance_version = 0
        self._balance_version_seen = -1

        self._history = load_history()
        self._daily_rows: list[dict[str, Any]] = []
        self._analytics: dict[str, Any] = {}
        self._analytics_error: str | None = None

        self._stop_event = threading.Event()

        self._timeline_minute_key: tuple[Any, ...] | None = None
        self._warned_boundary: dt.datetime | None = None
        self._warning_enabled = True

        self._read_position_into_window()

        self.root.protocol(
            "WM_DELETE_WINDOW",
            self._on_close,
        )

        self._setup_ttk_style()

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(
            fill=tk.BOTH,
            expand=True,
        )

        self.tab_tarif = tk.Frame(
            self.notebook,
            bg=COLOR_BG,
        )

        self.tab_balance = tk.Frame(
            self.notebook,
            bg=COLOR_BG,
        )

        self.tab_analytics = tk.Frame(
            self.notebook,
            bg=COLOR_BG,
        )

        self.notebook.add(
            self.tab_tarif,
            text=self.t("tariff"),
        )

        self.notebook.add(
            self.tab_balance,
            text=self.t("realtime"),
        )

        self.notebook.add(
            self.tab_analytics,
            text=self.t("analytics"),
        )

        self._build_tariff_tab()
        self._build_balance_tab()
        self._build_analytics_tab()

        self._refresh_analytics_state()

        threading.Thread(
            target=self._poll_worker,
            name="deepseek-balance-poll",
            daemon=True,
        ).start()

    # --------------------------------------------------------
    # Base / window
    # --------------------------------------------------------

    def _read_position_into_window(self) -> None:
        min_width = 520
        min_height = 455

        try:
            with open(POS_FILE, "r", encoding="utf-8") as f:
                geometry = f.read().strip()

            if "x" in geometry and "+" in geometry:
                size, _, position = geometry.partition("+")
                width, _, height = size.partition("x")
                x, _, y = position.partition("+")

                width_i = max(int(width), min_width)
                height_i = max(int(height), min_height)
                height_i = min(height_i, 500)

                self.root.geometry(
                    f"{width_i}x{height_i}+{x}+{y}"
                )
                return

            x, y = geometry.split(",")
            self.root.geometry(
                f"{min_width}x{min_height}+{x}+{y}"
            )
            return

        except (OSError, ValueError):
            pass

        self.root.geometry(
            f"{min_width}x{min_height}"
        )

    def _on_close(self) -> None:
        self._stop_event.set()

        try:
            geometry = self.root.geometry()
            with open(POS_FILE, "w", encoding="utf-8") as f:
                f.write(geometry)
        except OSError:
            pass

        self.root.destroy()

    # --------------------------------------------------------
    # Tariff tab
    # --------------------------------------------------------

    def _build_tariff_tab(self) -> None:
        header = tk.Frame(
            self.tab_tarif,
            bg=COLOR_BG,
        )
        header.pack(
            pady=(6, 3),
            padx=6,
            fill=tk.X,
        )

        self.lbl_badge = tk.Label(
            header,
            text="",
            font=("Segoe UI", 9, "bold"),
            fg=COLOR_TEXT_MAIN,
            bg=COLOR_STANDARD,
            padx=8,
            pady=1,
        )
        self.lbl_badge.pack(side=tk.LEFT)

        tk.Label(
            header,
            text="",
            bg=COLOR_BG,
        ).pack(
            side=tk.LEFT,
            fill=tk.X,
            expand=True,
        )

        tk.Label(
            header,
            text="TZ:",
            font=("Segoe UI", 8),
            fg=COLOR_TEXT_DIM,
            bg=COLOR_BG,
        ).pack(
            side=tk.LEFT,
            padx=(0, 2),
        )

        self.tz_btn = tk.Menubutton(
            header,
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

        for timezone_name in COMMON_TIMEZONES:
            self.tz_menu.add_command(
                label=timezone_name,
                command=lambda t=timezone_name: self._on_tz_select(t),
            )

        self.tz_btn.config(menu=self.tz_menu)

        self.lang_btn = tk.Menubutton(
            header,
            text=self.language.upper(),
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
        self.lang_btn.pack(side=tk.LEFT, padx=(4, 0))

        self.lang_menu = tk.Menu(
            self.lang_btn,
            tearoff=0,
            bg=COLOR_CARD,
            fg=COLOR_TEXT_MAIN,
            activebackground=COLOR_ACCENT,
            activeforeground="#000000",
            font=("Segoe UI", 8),
        )
        self.lang_menu.add_command(
            label=self.t("auto"),
            command=self._reset_language_auto,
        )
        self.lang_menu.add_separator()

        for code in LANGUAGE_ORDER:
            self.lang_menu.add_command(
                label=f"{code.upper()} – {LANGUAGES[code]}",
                command=lambda c=code: self._on_language_select(c),
            )

        self.lang_btn.config(menu=self.lang_menu)

        self.api_header_btn = tk.Button(
            header,
            text=self.t("api"),
            font=("Segoe UI", 8, "bold"),
            fg=COLOR_TEXT_MAIN,
            bg=COLOR_CARD,
            activebackground=COLOR_CARD_BORDER,
            activeforeground=COLOR_TEXT_MAIN,
            relief="flat",
            borderwidth=0,
            padx=6,
            pady=1,
            command=self._open_api_key_dialog,
        )
        self.api_header_btn.pack(side=tk.LEFT, padx=(4, 0))

        self.lbl_timer = tk.Label(
            self.tab_tarif,
            text="00:00:00 h",
            font=("Segoe UI", 22, "bold"),
            fg=COLOR_TEXT_MAIN,
            bg=COLOR_BG,
        )
        self.lbl_timer.pack()

        self.canvas = tk.Canvas(
            self.tab_tarif,
            height=55,
            bg=COLOR_BG,
            highlightthickness=0,
        )
        self.canvas.pack(
            padx=6,
            fill=tk.X,
        )

        self.win_frame = tk.Frame(
            self.tab_tarif,
            bg=COLOR_BG,
        )
        self.win_frame.pack()

        self.lbl_peak_label = tk.Label(
            self.win_frame,
            text=self.t("peak"),
            font=("Segoe UI", 7, "bold"),
            fg="#ef5350",
            bg=COLOR_BG,
        )
        self.lbl_peak_label.pack(side=tk.LEFT)

        self.lbl_peak_times = tk.Label(
            self.win_frame,
            text="",
            font=("Segoe UI", 7),
            fg=COLOR_TEXT_MAIN,
            bg=COLOR_BG,
        )
        self.lbl_peak_times.pack(side=tk.LEFT)

        self.lbl_std_label = tk.Label(
            self.win_frame,
            text=self.t("standard"),
            font=("Segoe UI", 7, "bold"),
            fg="#66bb6a",
            bg=COLOR_BG,
        )
        self.lbl_std_label.pack(
            side=tk.LEFT,
            padx=(12, 0),
        )

        self.lbl_std_times = tk.Label(
            self.win_frame,
            text="",
            font=("Segoe UI", 7),
            fg=COLOR_TEXT_MAIN,
            bg=COLOR_BG,
        )
        self.lbl_std_times.pack(side=tk.LEFT)

        weekend_frame = tk.Frame(
            self.tab_tarif,
            bg=COLOR_BG,
        )
        weekend_frame.pack()

        tk.Label(
            weekend_frame,
            text=self.t("weekend"),
            font=("Segoe UI", 7, "bold"),
            fg=COLOR_WEEKEND,
            bg=COLOR_BG,
        ).pack(side=tk.LEFT)

        tk.Label(
            weekend_frame,
            text=self.t("weekend_text"),
            font=("Segoe UI", 7),
            fg=COLOR_TEXT_MAIN,
            bg=COLOR_BG,
        ).pack(side=tk.LEFT)

        self.lbl_warning = tk.Label(
            self.tab_tarif,
            text="",
            font=("Segoe UI", 7, "bold"),
            fg=COLOR_WARNING,
            bg=COLOR_BG,
        )
        self.lbl_warning.pack(
            pady=(5, 0),
        )

    # --------------------------------------------------------
    # Realtime tab
    # --------------------------------------------------------

    def _build_balance_tab(self) -> None:
        tab = self.tab_balance

        self.lbl_balance_status = tk.Label(
            tab,
            text=self.t("connecting"),
            font=("Segoe UI", 9, "bold"),
            fg=COLOR_TEXT_DIM,
            bg=COLOR_BG,
        )
        self.lbl_balance_status.pack(
            pady=(8, 2),
        )

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
        self.lbl_balance_sub.pack(
            pady=(2, 4),
        )

        button_row = tk.Frame(
            tab,
            bg=COLOR_BG,
        )
        button_row.pack()

        self.btn_refresh = tk.Button(
            button_row,
            text=self.t("refresh"),
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

        self.api_manage_btn = tk.Button(
            button_row,
            text=self.t("api_key"),
            font=("Segoe UI", 8),
            fg=COLOR_TEXT_MAIN,
            bg=COLOR_CARD,
            activebackground=COLOR_CARD_BORDER,
            activeforeground=COLOR_TEXT_MAIN,
            relief="flat",
            borderwidth=0,
            padx=8,
            pady=2,
            command=self._open_api_key_dialog,
        )
        self.api_manage_btn.pack(side=tk.LEFT, padx=(4, 0))

        self.lbl_balance_update = tk.Label(
            button_row,
            text="",
            font=("Segoe UI", 7),
            fg=COLOR_TEXT_DIM,
            bg=COLOR_BG,
        )
        self.lbl_balance_update.pack(
            side=tk.LEFT,
            padx=(8, 0),
        )

        tk.Label(
            tab,
            text=self.t("daily_title", days=ANALYTICS_DAYS),
            font=("Segoe UI", 8, "bold"),
            fg=COLOR_ACCENT,
            bg=COLOR_BG,
        ).pack(
            pady=(8, 2),
            anchor="w",
            padx=10,
        )

        self.canvas_daily = tk.Canvas(
            tab,
            height=140,
            bg=COLOR_CARD,
            highlightthickness=0,
        )
        self.canvas_daily.pack(
            fill=tk.BOTH,
            expand=True,
            padx=10,
            pady=(0, 4),
        )

        self.canvas_daily.bind(
            "<Configure>",
            lambda _event: self._draw_daily_chart(
                self._daily_rows
            ),
        )

        legend = tk.Frame(
            tab,
            bg=COLOR_BG,
        )
        legend.pack(
            anchor="w",
            padx=10,
            pady=(0, 2),
        )

        for legend_color, legend_label in (
            (COLOR_PEAK, "Peak"),
            (COLOR_STANDARD, self.t("normal")),
            (COLOR_WEEKEND, self.t("weekend_label")),
        ):
            swatch = tk.Canvas(
                legend,
                width=10,
                height=10,
                bg=COLOR_BG,
                highlightthickness=0,
            )
            swatch.create_rectangle(
                0,
                0,
                10,
                10,
                fill=legend_color,
                outline="",
            )
            swatch.pack(
                side=tk.LEFT,
                padx=(0, 3),
            )

            tk.Label(
                legend,
                text=legend_label,
                font=("Segoe UI", 7),
                fg=COLOR_TEXT_DIM,
                bg=COLOR_BG,
            ).pack(
                side=tk.LEFT,
                padx=(0, 10),
            )

        self.lbl_csv_path = tk.Label(
            tab,
            text="",
            font=("Segoe UI", 7),
            fg=COLOR_TEXT_DIM,
            bg=COLOR_BG,
        )
        self.lbl_csv_path.pack(
            pady=(0, 6),
        )

    # --------------------------------------------------------
    # Analytics tab
    # --------------------------------------------------------

    def _build_analytics_tab(self) -> None:
        tab = self.tab_analytics

        self.lbl_analytics_title = tk.Label(
            tab,
            text=self.t("analytics_title"),
            font=("Segoe UI", 11, "bold"),
            fg=COLOR_TEXT_MAIN,
            bg=COLOR_BG,
        )
        self.lbl_analytics_title.pack(
            anchor="w",
            padx=10,
            pady=(8, 0),
        )

        self.lbl_analytics_hint = tk.Label(
            tab,
            text=self.t("analytics_hint"),
            font=("Segoe UI", 7),
            fg=COLOR_TEXT_DIM,
            bg=COLOR_BG,
            justify=tk.LEFT,
            wraplength=520,
        )
        self.lbl_analytics_hint.pack(
            anchor="w",
            padx=10,
            pady=(2, 6),
        )

        summary = tk.Frame(
            tab,
            bg=COLOR_BG,
        )
        summary.pack(
            fill=tk.X,
            padx=10,
        )

        self.analytics_vars: dict[str, tk.StringVar] = {}

        cards = [
            (self.t("total"), "total"),
            ("Peak", "peak"),
            (self.t("normal"), "standard"),
            (self.t("weekend_label"), "weekend"),
            (self.t("savings"), "savings"),
        ]

        for title_text, key in cards:
            card = tk.Frame(
                summary,
                bg=COLOR_CARD,
                highlightbackground=COLOR_CARD_BORDER,
                highlightthickness=1,
                padx=7,
                pady=5,
            )
            card.pack(
                side=tk.LEFT,
                fill=tk.BOTH,
                expand=True,
                padx=(0, 4),
            )

            tk.Label(
                card,
                text=title_text,
                font=("Segoe UI", 7),
                fg=COLOR_TEXT_DIM,
                bg=COLOR_CARD,
            ).pack()

            variable = tk.StringVar(value="–")
            self.analytics_vars[key] = variable

            tk.Label(
                card,
                textvariable=variable,
                font=("Segoe UI", 9, "bold"),
                fg=COLOR_TEXT_MAIN,
                bg=COLOR_CARD,
            ).pack()

        self.lbl_peak_share = tk.Label(
            tab,
            text="",
            font=("Segoe UI", 8, "bold"),
            fg=COLOR_ACCENT,
            bg=COLOR_BG,
        )
        self.lbl_peak_share.pack(
            anchor="w",
            padx=10,
            pady=(7, 2),
        )

        table_frame = tk.Frame(
            tab,
            bg=COLOR_BG,
        )
        table_frame.pack(
            fill=tk.BOTH,
            expand=True,
            padx=10,
        )

        columns = (
            "date",
            "usage",
            "peak",
            "standard",
            "weekend",
            "saving",
        )

        self.tree_daily = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            height=8,
        )

        headings = {
            "date": self.t("date"),
            "usage": self.t("usage"),
            "peak": "Peak",
            "standard": self.t("normal"),
            "weekend": self.t("weekend_short"),
            "saving": self.t("saving"),
        }

        widths = {
            "date": 82,
            "usage": 88,
            "peak": 88,
            "standard": 88,
            "weekend": 80,
            "saving": 90,
        }

        for column in columns:
            self.tree_daily.heading(
                column,
                text=headings[column],
            )
            self.tree_daily.column(
                column,
                width=widths[column],
                anchor=tk.E if column != "date" else tk.W,
            )

        scroll = ttk.Scrollbar(
            table_frame,
            orient=tk.VERTICAL,
            command=self.tree_daily.yview,
        )

        self.tree_daily.configure(
            yscrollcommand=scroll.set
        )

        self.tree_daily.pack(
            side=tk.LEFT,
            fill=tk.BOTH,
            expand=True,
        )

        scroll.pack(
            side=tk.RIGHT,
            fill=tk.Y,
        )

        self.intervals_visible = False
        self.intervals_toggle_btn = tk.Button(
            tab,
            text=self.t("intervals"),
            font=("Segoe UI", 8, "bold"),
            fg=COLOR_ACCENT,
            bg=COLOR_BG,
            activebackground=COLOR_BG,
            activeforeground=COLOR_TEXT_MAIN,
            relief="flat",
            borderwidth=0,
            command=self._toggle_intervals,
        )
        self.intervals_toggle_btn.pack(
            anchor="w",
            padx=10,
            pady=(4, 1),
        )

        columns2 = (
            "from",
            "to",
            "status",
            "amount",
            "note",
        )

        interval_frame = tk.Frame(
            tab,
            bg=COLOR_BG,
        )
        self.interval_frame = interval_frame
        self.interval_frame.pack(
            fill=tk.BOTH,
            expand=True,
            padx=10,
            pady=(0, 5),
        )
        self.interval_frame.pack_forget()

        self.tree_intervals = ttk.Treeview(
            interval_frame,
            columns=columns2,
            show="headings",
            height=8,
        )

        interval_headings = {
            "from": self.t("from"),
            "to": self.t("to"),
            "status": self.t("zone"),
            "amount": self.t("amount"),
            "note": self.t("note"),
        }

        interval_widths = {
            "from": 118,
            "to": 118,
            "status": 90,
            "amount": 92,
            "note": 130,
        }

        for column in columns2:
            self.tree_intervals.heading(
                column,
                text=interval_headings[column],
            )
            self.tree_intervals.column(
                column,
                width=interval_widths[column],
                anchor=tk.E
                if column in {"amount"}
                else tk.W,
            )

        interval_scroll = ttk.Scrollbar(
            interval_frame,
            orient=tk.VERTICAL,
            command=self.tree_intervals.yview,
        )

        self.tree_intervals.configure(
            yscrollcommand=interval_scroll.set
        )

        self.tree_intervals.pack(
            side=tk.LEFT,
            fill=tk.BOTH,
            expand=True,
        )

        interval_scroll.pack(
            side=tk.RIGHT,
            fill=tk.Y,
        )

        self.lbl_analytics_note = tk.Label(
            tab,
            text="",
            font=("Segoe UI", 7),
            fg=COLOR_TEXT_DIM,
            bg=COLOR_BG,
            justify=tk.LEFT,
            wraplength=540,
        )
        self.lbl_analytics_note.pack(
            anchor="w",
            padx=10,
            pady=(0, 6),
        )

    # --------------------------------------------------------
    # Styles
    # --------------------------------------------------------

    def _setup_ttk_style(self) -> None:
        style = ttk.Style(self.root)

        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            "TNotebook",
            background=COLOR_BG,
            borderwidth=0,
        )

        style.configure(
            "TNotebook.Tab",
            background=COLOR_CARD,
            foreground=COLOR_TEXT_DIM,
            padding=(14, 4),
            borderwidth=0,
        )

        style.map(
            "TNotebook.Tab",
            background=[
                ("selected", COLOR_CARD_BORDER)
            ],
            foreground=[
                ("selected", COLOR_TEXT_MAIN)
            ],
        )

        style.configure(
            "Treeview",
            background=COLOR_CARD,
            foreground=COLOR_TEXT_MAIN,
            fieldbackground=COLOR_CARD,
            borderwidth=0,
            font=("Segoe UI", 8),
            rowheight=22,
        )

        style.configure(
            "Treeview.Heading",
            background=COLOR_CARD_BORDER,
            foreground=COLOR_TEXT_MAIN,
            font=("Segoe UI", 8, "bold"),
        )

    # --------------------------------------------------------
    # Language / API key
    # --------------------------------------------------------

    def t(self, key: str, **kwargs: Any) -> str:
        text = TRANSLATIONS.get(
            self.language,
            TRANSLATIONS["en"],
        ).get(
            key,
            TRANSLATIONS["en"].get(key, key),
        )
        return text.format(**kwargs) if kwargs else text

    def _on_language_select(self, language: str) -> None:
        self._language_auto = False
        if language not in LANGUAGES:
            return
        self.language = language
        save_language(language)
        if hasattr(self, "lang_btn"):
            self.lang_btn.config(text=language.upper())
        self._retranslate_ui()
        self._timeline_minute_key = None

    def _reset_language_auto(self) -> None:
        self._language_auto = True
        remove_language()
        self.language = detect_language()
        self.lang_btn.config(text=self.language.upper())
        self._retranslate_ui()
        self._timeline_minute_key = None

    def _toggle_intervals(self) -> None:
        self.intervals_visible = not self.intervals_visible
        if self.intervals_visible:
            self.interval_frame.pack(
                fill=tk.BOTH,
                expand=True,
                padx=10,
                pady=(0, 4),
                before=self.lbl_analytics_note,
            )
        else:
            self.interval_frame.pack_forget()

    def _open_api_key_dialog(self) -> None:
        if self._api_key_dialog is not None:
            try:
                self._api_key_dialog.lift()
                self._api_key_dialog.focus_force()
                return
            except tk.TclError:
                self._api_key_dialog = None

        dialog = tk.Toplevel(self.root)
        self._api_key_dialog = dialog
        dialog.title(self.t("api_key_title"))
        dialog.configure(bg=COLOR_BG)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        tk.Label(
            dialog,
            text=self.t("api_key_hint"),
            font=("Segoe UI", 9, "bold"),
            fg=COLOR_TEXT_MAIN,
            bg=COLOR_BG,
        ).pack(anchor="w", padx=14, pady=(12, 5))

        key_var = tk.StringVar()
        entry = tk.Entry(
            dialog,
            textvariable=key_var,
            show="•",
            width=48,
            font=("Segoe UI", 9),
            bg=COLOR_CARD,
            fg=COLOR_TEXT_MAIN,
            insertbackground=COLOR_TEXT_MAIN,
            relief="flat",
        )
        entry.pack(fill=tk.X, padx=14)

        tk.Label(
            dialog,
            text=self.t("env_hint"),
            font=("Segoe UI", 7),
            fg=COLOR_TEXT_DIM,
            bg=COLOR_BG,
        ).pack(anchor="w", padx=14, pady=(4, 8))

        buttons = tk.Frame(dialog, bg=COLOR_BG)
        buttons.pack(fill=tk.X, padx=14, pady=(0, 12))

        def close_dialog():
            try:
                dialog.grab_release()
            except tk.TclError:
                pass
            dialog.destroy()
            self._api_key_dialog = None

        def save():
            value = key_var.get().strip()
            if not value:
                messagebox.showwarning(
                    self.t("api_key_title"),
                    self.t("no_key"),
                    parent=dialog,
                )
                return
            try:
                save_api_key(value)
            except (OSError, ValueError) as exc:
                messagebox.showerror(
                    self.t("api_key_title"),
                    str(exc),
                    parent=dialog,
                )
                return
            close_dialog()
            with self._state_lock:
                self._balance_state.pop("error", None)
                self._balance_version += 1
            self._on_refresh()

        def remove():
            if not messagebox.askyesno(
                self.t("api_key_title"),
                self.t("confirm_remove"),
                parent=dialog,
            ):
                return
            remove_saved_api_key()
            close_dialog()
            self._set_balance_error(self.t("no_key_error"))

        for label, command in (
            (self.t("cancel"), close_dialog),
            (self.t("remove"), remove),
            (self.t("save"), save),
        ):
            tk.Button(
                buttons,
                text=label,
                command=command,
                font=("Segoe UI", 8),
                fg=COLOR_TEXT_MAIN,
                bg=COLOR_CARD,
                activebackground=COLOR_CARD_BORDER,
                relief="flat",
                borderwidth=0,
                padx=8,
                pady=3,
            ).pack(side=tk.RIGHT, padx=(4, 0))

        dialog.protocol("WM_DELETE_WINDOW", close_dialog)
        entry.focus_force()

    def _retranslate_ui(self) -> None:
        self.notebook.tab(self.tab_tarif, text=self.t("tariff"))
        self.notebook.tab(self.tab_balance, text=self.t("realtime"))
        self.notebook.tab(self.tab_analytics, text=self.t("analytics"))

        self.lbl_weekend_label.config(text=self.t("weekend"))
        self.lbl_weekend_text.config(text=self.t("weekend_text"))
        self.api_header_btn.config(text=self.t("api"))
        self.api_manage_btn.config(text=self.t("api_key"))
        self.btn_refresh.config(text=self.t("refresh"))
        self.lbl_analytics_title.config(text=self.t("analytics_title"))
        self.lbl_analytics_hint.config(text=self.t("analytics_hint"))
        self.intervals_toggle_btn.config(text=self.t("intervals"))
        self.lbl_analytics_note.config(
            text=self.t("analytics_note", mult=PEAK_MULTIPLIER)
        )

        for column, key in {
            "date":"date", "usage":"usage", "peak":"peak",
            "standard":"normal", "weekend":"weekend_short",
            "saving":"savings",
        }.items():
            self.tree_daily.heading(column, text=self.t(key))

        for column, key in {
            "from":"from", "to":"to", "status":"zone",
            "amount":"amount", "note":"note",
        }.items():
            self.tree_intervals.heading(column, text=self.t(key))

        self._update_balance_ui()

    # --------------------------------------------------------
    # Timezone
    # --------------------------------------------------------

    def _on_tz_select(self, tz_name: str) -> None:
        if tz_name not in AVAILABLE_TIMEZONES:
            return

        self.tz_name = tz_name
        self.tz = zoneinfo.ZoneInfo(tz_name)

        self.tz_btn.config(text=tz_name)
        save_timezone(tz_name)

        self._timeline_minute_key = None
        self._refresh_analytics_state()

    # --------------------------------------------------------
    # Timeline
    # --------------------------------------------------------

    def draw_filigree_timeline(
        self,
        now_utc: dt.datetime,
    ) -> None:
        self.canvas.delete("all")

        width = self.canvas.winfo_width()
        if width < 10:
            width = 520

        padding_x = 12
        usable_w = width - 2 * padding_x

        bar_y_top = 12
        bar_y_bottom = 24

        local_now = now_utc.astimezone(self.tz)
        day = local_now.date()

        for hour in range(24):
            local_time = dt.datetime(
                day.year,
                day.month,
                day.day,
                hour,
                tzinfo=self.tz,
            )

            key = status_at(
                local_time.astimezone(UTC)
            )

            color = STATUS_COLORS[key]

            x0 = (
                padding_x
                + (hour / 24.0) * usable_w
            )
            x1 = (
                padding_x
                + ((hour + 1) / 24.0) * usable_w
            )

            self.canvas.create_rectangle(
                x0,
                bar_y_top,
                x1,
                bar_y_bottom,
                fill=color,
                outline="",
            )

        for hour in range(25):
            x = (
                padding_x
                + (hour / 24.0) * usable_w
            )

            if hour % 3 == 0:
                self.canvas.create_line(
                    x,
                    bar_y_top - 3,
                    x,
                    bar_y_bottom + 3,
                    fill=COLOR_TEXT_MAIN,
                    width=1,
                )

                self.canvas.create_text(
                    x,
                    bar_y_bottom + 12,
                    text=f"{hour:02d}",
                    fill=COLOR_TEXT_DIM,
                    font=("Segoe UI", 7),
                )
            else:
                self.canvas.create_line(
                    x,
                    bar_y_top + 2,
                    x,
                    bar_y_bottom - 2,
                    fill=COLOR_TICK_MINOR,
                    width=1,
                )

        local_minutes = (
            local_now.hour * 60
            + local_now.minute
        )

        needle_x = (
            padding_x
            + (
                local_minutes
                / (24 * 60)
            ) * usable_w
        )

        self.canvas.create_line(
            needle_x,
            0,
            needle_x,
            bar_y_bottom + 4,
            fill=COLOR_NEEDLE,
            width=2,
        )

        self.canvas.create_polygon(
            needle_x - 4,
            0,
            needle_x + 4,
            0,
            needle_x,
            6,
            fill=COLOR_NEEDLE,
        )

    # --------------------------------------------------------
    # Realtime API / Worker
    # --------------------------------------------------------

    def _on_refresh(self) -> None:
        threading.Thread(
            target=self._fetch_and_store,
            name="deepseek-manual-refresh",
            daemon=True,
        ).start()

    def _poll_worker(self) -> None:
        while not self._stop_event.is_set():
            self._fetch_and_store()

            interval = max(
                MIN_POLL_INTERVAL_S,
                int(getattr(self, "_poll_interval_s", POLL_INTERVAL_S)),
            )

            wake_event = getattr(self, "_poll_wakeup_event", None)
            if wake_event is None:
                self._stop_event.wait(interval)
                continue

            # A change of interval wakes this wait immediately.
            wake_event.wait(interval)
            wake_event.clear()

    def _fetch_and_store(self) -> None:
        if not self._fetch_lock.acquire(blocking=False):
            return

        try:
            api_key = load_api_key()

            if not api_key:
                self._set_balance_error(
                    "No API key: set DEEPSEEK_API_KEY "
                    f"or create {API_KEY_FILE}"
                )
                return

            try:
                data = fetch_balance(api_key)
                info = parse_balance(data)
                now = dt.datetime.now(UTC)

                record = {
                    "ts": now.isoformat(),
                    "total": info["total"],
                    "topped": info["topped"],
                    "granted": info["granted"],
                    "available": info["available"],
                    "currency": info["currency"],
                }

                with self._state_lock:
                    self._history.append(record)

                    if len(self._history) > MAX_HISTORY:
                        del self._history[
                            :-MAX_HISTORY
                        ]

                    self._balance_state = {
                        **info,
                        "last_update": now,
                        "error": None,
                    }

                # Persistence deliberately outside the lock.
                save_history(self._history)

                self._refresh_analytics_state()

                try:
                    write_daily_csv(
                        self._daily_rows
                    )
                except Exception:
                    pass

                with self._state_lock:
                    self._balance_version += 1

            except urllib.error.HTTPError as exc:
                if exc.code == 401:
                    message = (
                        "API key invalid or expired."
                    )
                elif exc.code == 403:
                    message = (
                        "Access to the DeepSeek API denied."
                    )
                elif exc.code == 429:
                    message = (
                        "Too many API requests – please try again later."
                    )
                else:
                    message = (
                        f"DeepSeek API HTTP error {exc.code}"
                    )

                self._set_balance_error(message)

            except urllib.error.URLError as exc:
                self._set_balance_error(
                    f"Network error: {exc.reason}"
                )

            except Exception as exc:
                self._set_balance_error(
                    f"API error: {exc}"
                )

        finally:
            self._fetch_lock.release()

    def _set_balance_error(self, message: str) -> None:
        with self._state_lock:
            if self._balance_state.get("total") is None:
                self._balance_state = {
                    "error": message,
                }
            else:
                self._balance_state["error"] = message

            self._balance_version += 1

    # --------------------------------------------------------
    # Analytics state
    # --------------------------------------------------------

    def _refresh_analytics_state(self) -> None:
        """Load the real proxy usage data (non-blocking) and update the
        analytics structure from it.

        On error (proxy unreachable, no data) the analytics structure is
        cleared and a hint is set -- it deliberately does NOT fall back to the
        old balance-delta estimate.
        """
        threading.Thread(
            target=self._load_proxy_analytics,
            name="deepseek-proxy-analytics",
            daemon=True,
        ).start()

    def _load_proxy_analytics(self) -> None:
        error_message: str | None = None
        analytics: dict[str, Any] = {}

        host_filter = self._host_filter or None

        try:
            events = fetch_usage(ANALYTICS_DAYS, host=host_filter)
            if events:
                analytics = analyze_proxy_usage(
                    events,
                    self.tz_name,
                    ANALYTICS_DAYS,
                )
            else:
                source = "InfluxDB" if config.INFLUX_ENABLED else "Proxy"
                error_message = f"No {source} data"
        except urllib.error.HTTPError as exc:
            error_message = f"Data source HTTP error {exc.code}"
        except urllib.error.URLError as exc:
            error_message = f"Data source unreachable ({exc.reason})"
        except Exception as exc:
            error_message = f"Data source error: {exc}"

        daily_rows = analytics.get("daily", []) if analytics else []

        with self._state_lock:
            self._analytics = analytics
            self._daily_rows = daily_rows
            self._analytics_error = error_message
            self._balance_version += 1

    # --------------------------------------------------------
    # Realtime UI
    # --------------------------------------------------------

    def _update_balance_ui(self) -> None:
        with self._state_lock:
            state = dict(self._balance_state)
            version = self._balance_version
            rows = list(self._daily_rows)
            analytics = dict(self._analytics)

        if version == self._balance_version_seen:
            return

        self._balance_version_seen = version

        total = state.get("total")
        error = state.get("error")

        if total is None:
            self.lbl_balance_status.config(
                text=(
                    "Error"
                    if error
                    else "Connecting to DeepSeek API…"
                ),
                fg=COLOR_PEAK
                if error
                else COLOR_TEXT_DIM,
            )

            self.lbl_balance_big.config(
                text="–"
            )

            self.lbl_balance_sub.config(
                text=""
            )

            self.lbl_balance_update.config(
                text=error or "",
                fg=COLOR_PEAK
                if error
                else COLOR_TEXT_DIM,
            )
        else:
            currency = state.get(
                "currency",
                "",
            )
            symbol = currency_symbol(currency)

            available_text = (
                self.t("available")
                if state.get("available", True)
                else self.t("unavailable")
            )

            self.lbl_balance_status.config(
                text=self.t("realtime"),
                fg=COLOR_TEXT_DIM,
            )

            self.lbl_balance_big.config(
                text=format_money(
                    total,
                    currency,
                    self.language,
                    decimals=2,
                )
            )

            self.lbl_balance_sub.config(
                text=(
                    f"{self.t('loaded')} {format_money(state.get('topped', 0), currency, self.language, decimals=2)}"
                    f"  ·  {self.t('granted')} {format_money(state.get('granted', 0), currency, self.language, decimals=2)}"
                    f"  ·  {available_text}"
                )
            )

            last_update = state.get(
                "last_update"
            )

            text = ""

            if last_update:
                local_update = (
                    last_update.astimezone(
                        self.tz
                    )
                )
                text = (
                    self.t("as_of") + " "
                    f"{local_update.strftime('%d.%m.%Y %H:%M:%S')}"
                )

            if error:
                text = (
                    f"{text}  ·  {error}"
                    if text
                    else error
                )

            self.lbl_balance_update.config(
                text=text,
                fg=COLOR_PEAK
                if error
                else COLOR_TEXT_DIM,
            )

        self._draw_daily_chart(rows)
        self._update_analytics_ui(analytics)

        self.lbl_csv_path.config(
            text=f"CSV: {DAILY_CSV_FILE}"
        )

    def _draw_daily_chart(
        self,
        rows: list[dict[str, Any]],
    ) -> None:
        canvas = self.canvas_daily
        canvas.delete("all")

        width = canvas.winfo_width()
        height = canvas.winfo_height()

        if width < 20:
            width = 520

        if height < 20:
            height = 140

        if not rows:
            canvas.create_text(
                width / 2,
                height / 2,
                text=(
                    self.t("no_data")
                ),
                fill=COLOR_TEXT_DIM,
                font=("Segoe UI", 8),
            )
            return

        data = rows[-ANALYTICS_DAYS:]

        pad_l = 6
        pad_r = 6
        pad_t = 20
        pad_b = 20

        plot_w = width - pad_l - pad_r
        plot_h = height - pad_t - pad_b

        max_value = (
            max(
                (
                    float(row["usage"])
                    for row in data
                ),
                default=0.0,
            )
            or 1.0
        )

        slot = plot_w / len(data)
        bar_w = max(
            3,
            slot * 0.55,
        )
        base_y = pad_t + plot_h

        canvas.create_line(
            pad_l,
            base_y,
            width - pad_r,
            base_y,
            fill=COLOR_CARD_BORDER,
        )

        for index, row in enumerate(data):
            x_center = (
                pad_l
                + slot * index
                + slot / 2
            )

            bar_height = (
                float(row["usage"])
                / max_value
                * plot_h
            )

            standard_height = (
                float(row["standard_usage"])
                / max_value
                * plot_h
            )

            weekend_height = (
                float(row["weekend_usage"])
                / max_value
                * plot_h
            )

            peak_height = (
                float(row["peak_usage"])
                / max_value
                * plot_h
            )

            x_left = x_center - bar_w / 2
            x_right = x_center + bar_w / 2

            # Stacked segments:
            # bottom normal (green), middle weekend (orange),
            # oben Peak (rot).
            segment_top = base_y

            for segment_height, segment_color in (
                (standard_height, COLOR_STANDARD),
                (weekend_height, COLOR_WEEKEND),
                (peak_height, COLOR_PEAK),
            ):
                if segment_height <= 0:
                    continue

                canvas.create_rectangle(
                    x_left,
                    segment_top - segment_height,
                    x_right,
                    segment_top,
                    fill=segment_color,
                    outline="",
                )

                segment_top -= segment_height

            if bar_height > 0:
                canvas.create_text(
                    x_center,
                    base_y - bar_height - 8,
                    text=f"{row['usage']:.4f}",
                    fill=COLOR_TEXT_MAIN,
                    font=("Segoe UI", 7),
                )

            canvas.create_text(
                x_center,
                base_y + 10,
                text=row["date"][5:].replace(
                    "-",
                    ".",
                ),
                fill=COLOR_TEXT_DIM,
                font=("Segoe UI", 7),
            )

    # --------------------------------------------------------
    # Analytics UI
    # --------------------------------------------------------

    def _update_analytics_ui(
        self,
        analytics: dict[str, Any],
    ) -> None:
        currency = analytics.get(
            "currency",
            "",
        )

        self.analytics_vars["total"].set(
            format_money(
                analytics.get("usage", 0.0),
                currency,
                self.language,
            )
        )

        self.analytics_vars["peak"].set(
            format_money(
                analytics.get("peak_usage", 0.0),
                currency,
                self.language,
            )
        )

        self.analytics_vars["standard"].set(
            format_money(
                analytics.get("standard_usage", 0.0),
                currency,
                self.language,
            )
        )

        self.analytics_vars["weekend"].set(
            format_money(
                analytics.get("weekend_usage", 0.0),
                currency,
                self.language,
            )
        )

        self.analytics_vars["savings"].set(
            format_money(
                analytics.get(
                    "avoidable_savings",
                    0.0,
                ),
                currency,
                self.language,
            )
        )

        peak_share = (
            analytics.get("peak_share", 0.0)
            * 100.0
        )

        self.lbl_peak_share.config(
            text=(
                self.t("peak_share") + " "
                f"{peak_share:.1f} %"
            )
        )

        for item in self.tree_daily.get_children():
            self.tree_daily.delete(item)

        daily_rows = analytics.get(
            "daily",
            [],
        )

        for row in reversed(daily_rows):
            self.tree_daily.insert(
                "",
                tk.END,
                values=(
                    row["date"],
                    format_money(
                        row["usage"],
                        currency,
                        self.language,
                    ),
                    format_money(
                        row["peak_usage"],
                        currency,
                        self.language,
                    ),
                    format_money(
                        row["standard_usage"],
                        currency,
                        self.language,
                    ),
                    format_money(
                        row["weekend_usage"],
                        currency,
                        self.language,
                    ),
                    format_money(
                        row["avoidable_savings"],
                        currency,
                        self.language,
                    ),
                ),
            )

        for item in self.tree_intervals.get_children():
            self.tree_intervals.delete(item)

        interval_count = 0

        for interval in analytics.get(
            "intervals",
            [],
        ):
            for part in interval.get(
                "parts",
                [],
            ):
                start = part["start"]
                end = part["end"]

                note = (
                    "Estimate due to larger measurement gap"
                    if interval.get("gap")
                    else ""
                )

                self.tree_intervals.insert(
                    "",
                    tk.END,
                    values=(
                        start.strftime(
                            "%d.%m %H:%M:%S"
                        ),
                        end.strftime(
                            "%d.%m %H:%M:%S"
                        ),
                        part["status"],
                        format_money(
                            part["amount"],
                            currency,
                            self.language,
                        ),
                        note,
                    ),
                )

                interval_count += 1

                if interval_count >= ANALYTICS_INTERVAL_LIMIT:
                    break

            if interval_count >= ANALYTICS_INTERVAL_LIMIT:
                break

        self.lbl_analytics_note.config(
            text=self.t(
                "analytics_note",
                mult=PEAK_MULTIPLIER,
            )
        )

    # --------------------------------------------------------
    # Acoustic warning
    # --------------------------------------------------------

    def _check_zone_warning(
        self,
        now_utc: dt.datetime,
    ) -> None:
        if not self._warning_enabled:
            return

        boundary = next_status_change(now_utc)
        seconds_left = (
            boundary - now_utc
        ).total_seconds()

        if (
            0 < seconds_left <= WARNING_BEFORE_S
            and boundary != self._warned_boundary
        ):
            self._warned_boundary = boundary

            # Tkinter bell() is the simplest cross-platform
            # solution and uses the system/window bell signal.
            try:
                self.root.bell()
            except tk.TclError:
                pass

            boundary_local = boundary.astimezone(
                self.tz
            )

            new_status = status_at(
                boundary + dt.timedelta(seconds=1)
            )

            localized_status = {
                "PEAK": self.t("peak_status"),
                "STANDARD": self.t("standard_status"),
                "WEEKEND": self.t("weekend_status"),
            }.get(new_status, new_status)
            text = self.t(
                "zone_warning",
                seconds=int(seconds_left),
                status=localized_status,
            )

            self.lbl_warning.config(
                text=text
            )

        elif seconds_left > WARNING_BEFORE_S + 2:
            self.lbl_warning.config(
                text=""
            )

    # --------------------------------------------------------
    # Legend
    # --------------------------------------------------------

    def _update_peak_legend(
        self,
        now_utc: dt.datetime,
    ) -> None:
        local_now = now_utc.astimezone(
            self.tz
        )

        offset = local_now.utcoffset()
        offset_minutes = int(
            offset.total_seconds() / 60
        ) if offset else 0

        def local_time_string(
            utc_minutes: int,
        ) -> str:
            total = (
                utc_minutes
                + offset_minutes
            ) % (24 * 60)

            hour = total // 60
            minute = total % 60

            return (
                f"{hour:02d}:{minute:02d}"
            )

        p1_start, p1_end = PEAK_WINDOWS_UTC[0]
        p2_start, p2_end = PEAK_WINDOWS_UTC[1]

        peak_text = (
            f" {local_time_string(p1_start)}–"
            f"{local_time_string(p1_end)} & "
            f"{local_time_string(p2_start)}–"
            f"{local_time_string(p2_end)}"
        )

        self.lbl_peak_times.config(
            text=peak_text
        )

        std_text = (
            f" {local_time_string(p1_end)}–"
            f"{local_time_string(p2_start)} & "
            f"{local_time_string(p2_end)}–"
            f"{local_time_string(p1_start)}"
        )

        self.lbl_std_times.config(
            text=std_text
        )

    # --------------------------------------------------------
    # Main loop
    # --------------------------------------------------------

    def update_loop(self) -> None:
        self._update_balance_ui()

        now_utc = dt.datetime.now(UTC)

        status_text, seconds_left, color, _status_key = (
            calculate_status(now_utc)
        )

        hours, remainder = divmod(
            int(seconds_left),
            3600,
        )

        minutes, seconds = divmod(
            remainder,
            60,
        )

        localized_status = {
            "PEAK": self.t("peak_status"),
            "STANDARD": self.t("standard_status"),
            "WEEKEND": self.t("weekend_status"),
        }.get(_status_key, status_text)

        self.lbl_badge.config(
            text=localized_status,
            bg=color,
        )

        self.lbl_timer.config(
            text=(
                f"{hours:02d}:"
                f"{minutes:02d}:"
                f"{seconds:02d} h"
            )
        )

        local_now = now_utc.astimezone(
            self.tz
        )

        timeline_key = (
            local_now.date(),
            local_now.hour,
            local_now.minute,
        )

        # Expensive canvas redraw only on minute/timezone/day change.
        if timeline_key != self._timeline_minute_key:
            self._timeline_minute_key = timeline_key
            self.draw_filigree_timeline(
                now_utc
            )

        self._update_peak_legend(
            now_utc
        )

        self._check_zone_warning(
            now_utc
        )

        self.root.after(
            1000,
            self.update_loop,
        )



# ============================================================
# V2 GUI – minimalistisches Tarif-Widget + separate Fenster
# ============================================================

TARIFF_POS_FILE = os.path.join(CONFIG_DIR, "tarif_pos")
COST_POS_FILE = os.path.join(CONFIG_DIR, "cost_pos")
SETTINGS_POS_FILE = os.path.join(CONFIG_DIR, "settings_pos")


class DeepSeekFiligreeGUI(_LegacyDeepSeekFiligreeGUI):
    """
    New UI:
      - Root = minimalist tariff widget
      - Cost & Usage = a shared, freely resizable toplevel window
      - Tariff stays a minimalist main window
      - window positions/sizes stored separately
      - the tariff window can optionally be always-on-top

    The existing tariff, balance and analytics logic from the legacy class is
    reused; only the GUI layer is reorganized.
    """

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_NAME)
        self.root.configure(bg=COLOR_BG)
        self.root.resizable(False, False)
        self._set_app_icon()

        self.tz_name = load_timezone()
        self.tz = zoneinfo.ZoneInfo(self.tz_name)
        self._language_auto = load_language() is None
        self.language = load_language() or detect_language()

        self._api_key_dialog = None
        self._state_lock = threading.Lock()
        self._fetch_lock = threading.Lock()
        self._balance_state: dict[str, Any] = {}
        self._balance_version = 0
        self._balance_version_seen = -1
        self._history = load_history()
        self._daily_rows: list[dict[str, Any]] = []
        self._analytics: dict[str, Any] = {}
        self._analytics_error: str | None = None
        self._host_filter: str = ""
        self._stop_event = threading.Event()
        self._poll_wakeup_event = threading.Event()
        self._poll_interval_s = load_poll_interval()
        self._timeline_minute_key: tuple[Any, ...] | None = None
        self._warned_boundary: dt.datetime | None = None
        self._warning_enabled = True
        self._topmost = False
        self._realtime_ui: dict[str, Any] = {}
        self._analytics_ui: dict[str, Any] = {}
        self._cost_window: tk.Toplevel | None = None
        self._cost_ui: dict[str, Any] = {}
        self._cost_rendered_version = -1
        self._settings_window: tk.Toplevel | None = None
        self._settings_last_geometry = ""

        self._setup_ttk_style()
        self._build_mini_tariff()
        self._refresh_analytics_state()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._load_geometry(self.root, TARIFF_POS_FILE, "300x135", 300, 135, 300, 135)

        threading.Thread(
            target=self._poll_worker,
            name="deepseek-balance-poll",
            daemon=True,
        ).start()

    # --------------------------------------------------------
    # Window helpers
    # --------------------------------------------------------

    def _set_app_icon(self) -> None:
        """Set the app icon for the window/taskbar."""
        from deepseek_monitor.assets import set_window_icon

        set_window_icon(self.root, "deepseek-monitor-256.png")

    def _load_geometry(
        self,
        window: tk.Toplevel | tk.Tk,
        path: str,
        default: str,
        min_width: int,
        min_height: int,
        max_width: int | None = None,
        max_height: int | None = None,
    ) -> None:
        geometry = default
        try:
            with open(path, "r", encoding="utf-8") as f:
                geometry = f.read().strip() or default
        except OSError:
            pass

        try:
            if "x" in geometry and "+" in geometry:
                size, _, pos = geometry.partition("+")
                width, _, height = size.partition("x")
                x, _, y = pos.partition("+")
                w = max(int(width), min_width)
                h = max(int(height), min_height)
                if max_width is not None:
                    w = min(w, max_width)
                if max_height is not None:
                    h = min(h, max_height)
                geometry = f"{w}x{h}+{x}+{y}"
            window.geometry(geometry)
        except (ValueError, tk.TclError):
            window.geometry(default)

    def _save_geometry(self, window: tk.Toplevel | tk.Tk, path: str) -> None:
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(window.geometry())
        except OSError:
            pass

    def _load_window_position(self, path: str) -> tuple[int, int] | None:
        """Load only X/Y from settings_pos; width/height are ignored."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                value = f.read().strip()
            if "+" in value:
                # Old form: 320x265+X+Y -> only take X/Y.
                _, x, y = value.rsplit("+", 2)
                return int(x), int(y)
            if "," in value:
                # New form: X,Y
                x, y = value.split(",", 1)
                return int(x), int(y)
        except (OSError, ValueError):
            pass
        return None

    def _save_window_position(self, window: tk.Toplevel | tk.Tk, path: str) -> None:
        """Save only X/Y for position-bound windows."""
        try:
            geometry = window.geometry()
            if "+" not in geometry:
                return
            _, x, y = geometry.rsplit("+", 2)
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"{int(x)},{int(y)}\n")
        except (OSError, ValueError, tk.TclError):
            pass

    def _show_or_focus(self, window: tk.Toplevel | None) -> None:
        if window is None:
            return
        try:
            window.deiconify()
            window.lift()
            window.focus_force()
        except tk.TclError:
            pass

    # --------------------------------------------------------
    # Mini tariff window
    # --------------------------------------------------------

    def _build_mini_tariff(self) -> None:
        top = tk.Frame(self.root, bg=COLOR_BG)
        top.pack(fill=tk.X, padx=5, pady=(3, 1))

        self.lbl_badge = tk.Label(
            top,
            text="",
            font=("Segoe UI", 8, "bold"),
            fg=COLOR_TEXT_MAIN,
            bg=COLOR_STANDARD,
            padx=6,
            pady=1,
        )
        self.lbl_badge.pack(side=tk.LEFT)

        self.lbl_tz_small = tk.Label(
            top,
            text=self.tz_name,
            font=("Segoe UI", 7),
            fg=COLOR_TEXT_DIM,
            bg=COLOR_BG,
        )
        self.lbl_tz_small.pack(side=tk.RIGHT, padx=(4, 0))

        self.lbl_timer = tk.Label(
            self.root,
            text="00:00:00",
            font=("Segoe UI", 19, "bold"),
            fg=COLOR_TEXT_MAIN,
            bg=COLOR_BG,
        )
        self.lbl_timer.pack(pady=(0, 1))

        self.canvas = tk.Canvas(
            self.root,
            height=38,
            bg=COLOR_BG,
            highlightthickness=0,
        )
        self.canvas.pack(fill=tk.X, padx=7, pady=(0, 0))

        self.lbl_warning = tk.Label(
            self.root,
            text="",
            font=("Segoe UI", 7, "bold"),
            fg=COLOR_WARNING,
            bg=COLOR_BG,
        )
        self._warning_visible = False

        actions = tk.Frame(self.root, bg=COLOR_BG)
        actions.pack(pady=(10, 0))

        self.btn_cost = tk.Button(
            actions, text="C", width=2, command=self.open_cost_window,
            **self._mini_button_style(),
        )
        self.btn_cost.pack(side=tk.LEFT, padx=2)

        self.btn_settings = tk.Button(
            actions, text="⚙", width=2, command=self.open_settings,
            **self._mini_button_style(),
        )
        self.btn_settings.pack(side=tk.LEFT, padx=2)

        self.btn_topmost = tk.Button(
            actions, text="⚲", width=2, command=self.toggle_topmost,
            **self._mini_button_style(),
        )
        self.btn_topmost.pack(side=tk.LEFT, padx=2)

        # Ensure the mini-widget does not retain the old 455px-class height.
        self.root.update_idletasks()
        try:
            current = self.root.geometry()
            if "x" in current:
                size = current.split("+", 1)[0]
                width, height = map(int, size.split("x"))
                if height > 135 or width > 300:
                    self.root.geometry("300x135" + ("+" + current.split("+", 1)[1] if "+" in current else ""))
        except (ValueError, tk.TclError):
            pass

    @staticmethod
    def _mini_button_style() -> dict[str, Any]:
        return {
            "font": ("Segoe UI", 8, "bold"),
            "fg": COLOR_TEXT_MAIN,
            "bg": COLOR_CARD,
            "activebackground": COLOR_CARD_BORDER,
            "activeforeground": COLOR_TEXT_MAIN,
            "relief": "flat",
            "borderwidth": 0,
            "padx": 4,
            "pady": 0,
        }

    def toggle_topmost(self) -> None:
        self._topmost = not self._topmost
        self.root.attributes("-topmost", self._topmost)
        self.btn_topmost.config(
            bg=COLOR_ACCENT if self._topmost else COLOR_CARD,
            fg="#000000" if self._topmost else COLOR_TEXT_MAIN,
        )

    def draw_filigree_timeline(self, now_utc: dt.datetime) -> None:
        c = self.canvas
        c.delete("all")
        w = max(self.canvas.winfo_width(), 300)
        left = 8
        usable = max(1, w - 2 * left)

        local_now = now_utc.astimezone(self.tz)
        day = local_now.date()
        bar_y0 = 8
        bar_y1 = 16

        # Color band for each local hour.
        for hour in range(24):
            local_time = dt.datetime(
                day.year, day.month, day.day, hour,
                tzinfo=self.tz,
            )
            status = status_at(local_time.astimezone(UTC))
            x0 = left + hour / 24 * usable
            x1 = left + (hour + 1) / 24 * usable
            c.create_rectangle(
                x0, bar_y0, x1, bar_y1,
                fill=STATUS_COLORS[status], outline="",
            )

        # Time rays: one tick per hour; longer every 6 hours.
        # The visible labels stay compact and sit directly below.
        for hour in range(25):
            x = left + hour / 24 * usable

            if hour % 6 == 0:
                tick_top = 4
                tick_bottom = 23
                tick_color = COLOR_TEXT_MAIN
            elif hour % 3 == 0:
                tick_top = 6
                tick_bottom = 21
                tick_color = COLOR_TEXT_DIM
            else:
                tick_top = 7
                tick_bottom = 19
                tick_color = COLOR_TICK_MINOR

            c.create_line(
                x, tick_top, x, tick_bottom,
                fill=tick_color, width=1,
            )

            if hour % 6 == 0:
                c.create_text(
                    x,
                    31,
                    text=f"{hour:02d}",
                    fill=COLOR_TEXT_DIM,
                    font=("Segoe UI", 6),
                )

        # Current point in time: subtle small yellow triangle.
        local_minutes = local_now.hour * 60 + local_now.minute
        needle_x = left + local_minutes / (24 * 60) * usable
        marker_y = 3
        marker_half = 3
        c.create_polygon(
            needle_x, marker_y,
            needle_x - marker_half, marker_y - 5,
            needle_x + marker_half, marker_y - 5,
            fill="#f4c542",
            outline="",
        )

    # --------------------------------------------------------
    # Window toggle
    # --------------------------------------------------------

    def _sync_window_buttons(self) -> None:
        cost_open = self._cost_window is not None
        self.btn_cost.config(
            text="×" if cost_open else "C",
            bg=COLOR_ACCENT if cost_open else COLOR_CARD,
            fg="#000000" if cost_open else COLOR_TEXT_MAIN,
        )

    # --------------------------------------------------------
    # Cost & Usage
    # --------------------------------------------------------

    def open_cost_window(self) -> None:
        if self._cost_window is not None:
            self._close_cost_window()
            return

        win = tk.Toplevel(self.root)
        self._cost_window = win
        self._sync_window_buttons()
        win.title(self.t("costs"))
        win.configure(bg=COLOR_BG)
        win.resizable(True, True)
        self._load_geometry(win, COST_POS_FILE, "760x620", 600, 520, 1400, 1000)

        header = tk.Frame(win, bg=COLOR_BG)
        header.pack(fill=tk.X, padx=15, pady=(12, 0))

        # Host filter (only useful in active InfluxDB mode).
        if config.INFLUX_ENABLED:
            host_lbl = tk.Label(
                header, text=self.t("host") + ":",
                font=("Segoe UI", 8),
                fg=COLOR_TEXT_DIM, bg=COLOR_BG,
            )
            host_lbl.pack(side=tk.LEFT, padx=(0, 4))

            self._host_combo = ttk.Combobox(
                header,
                state="readonly",
                width=18,
                font=("Segoe UI", 8),
            )
            self._host_combo.pack(side=tk.LEFT, padx=(0, 10))
            self._host_combo.bind(
                "<<ComboboxSelected>>",
                self._on_host_filter_selected,
            )
            self._populate_host_filter()
        else:
            self._host_combo = None

        status = tk.Label(
            header, text=self.t("connecting"),
            font=("Segoe UI", 9, "bold"),
            fg=COLOR_TEXT_DIM, bg=COLOR_BG,
        )
        status.pack(side=tk.LEFT)

        refresh = tk.Button(
            header, text=self.t("refresh"),
            command=self._on_refresh,
            **self._mini_button_style(),
        )
        refresh.pack(side=tk.RIGHT, padx=2)

        api = tk.Button(
            header, text=self.t("api_key"),
            command=self._open_api_key_dialog,
            **self._mini_button_style(),
        )
        api.pack(side=tk.RIGHT, padx=2)

        balance = tk.Label(
            win, text="–",
            font=("Segoe UI", 28, "bold"),
            fg=COLOR_TEXT_MAIN, bg=COLOR_BG,
        )
        balance.pack(pady=(3, 0))

        sub = tk.Label(
            win, text="", justify=tk.CENTER,
            font=("Segoe UI", 8),
            fg=COLOR_TEXT_DIM, bg=COLOR_BG,
        )
        sub.pack(pady=(0, 8))

        summary = tk.Frame(win, bg=COLOR_BG)
        summary.pack(fill=tk.X, padx=15, pady=(2, 6))

        vars_: dict[str, tk.StringVar] = {}
        for title_text, key in (
            (self.t("total"), "total"),
            ("Peak", "peak"),
            (self.t("normal"), "standard"),
            (self.t("weekend_label"), "weekend"),
            (self.t("savings"), "savings"),
        ):
            card = tk.Frame(
                summary, bg=COLOR_CARD,
                highlightbackground=COLOR_CARD_BORDER,
                highlightthickness=1,
                padx=8, pady=5,
            )
            card.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
            tk.Label(
                card, text=title_text,
                font=("Segoe UI", 7),
                fg=COLOR_TEXT_DIM, bg=COLOR_CARD,
            ).pack()
            var = tk.StringVar(value="–")
            vars_[key] = var
            tk.Label(
                card, textvariable=var,
                font=("Segoe UI", 10, "bold"),
                fg=COLOR_TEXT_MAIN, bg=COLOR_CARD,
            ).pack()

        peak_share = tk.StringVar(value="")
        tk.Label(
            win, textvariable=peak_share,
            font=("Segoe UI", 8, "bold"),
            fg=COLOR_ACCENT, bg=COLOR_BG,
        ).pack(anchor="w", padx=15, pady=(1, 4))

        tk.Label(
            win, text=self.t("daily"),
            font=("Segoe UI", 9, "bold"),
            fg=COLOR_ACCENT, bg=COLOR_BG,
        ).pack(anchor="w", padx=15, pady=(0, 2))

        chart = tk.Canvas(win, height=260, bg=COLOR_CARD, highlightthickness=0)
        chart.pack(fill=tk.BOTH, expand=True, padx=15, pady=(0, 5))
        chart.bind("<Configure>", lambda _e: self._draw_stacked_daily_chart(chart, analytics=True))

        # Compact legend below the graph (minimal height, centered).
        legend = tk.Frame(win, bg=COLOR_BG)
        legend.pack(fill=tk.X, padx=15, pady=(0, 1))
        legend_inner = tk.Frame(legend, bg=COLOR_BG)
        legend_inner.pack(anchor=tk.CENTER)
        for legend_color, legend_label in (
            (COLOR_PEAK, "Peak"),
            (COLOR_STANDARD, self.t("normal")),
            (COLOR_WEEKEND, self.t("weekend_label")),
        ):
            swatch = tk.Canvas(
                legend_inner, width=10, height=10,
                bg=COLOR_BG, highlightthickness=0,
            )
            swatch.create_rectangle(0, 0, 10, 10, fill=legend_color, outline="")
            swatch.pack(side=tk.LEFT, padx=(0, 3))
            tk.Label(
                legend_inner, text=legend_label,
                font=("Segoe UI", 7),
                fg=COLOR_TEXT_DIM, bg=COLOR_BG,
            ).pack(side=tk.LEFT, padx=(0, 12))

        controls = tk.Frame(win, bg=COLOR_BG)
        controls.pack(fill=tk.X, padx=15, pady=(0, 2))
        details_visible = tk.BooleanVar(value=False)
        details_frame = tk.Frame(win, bg=COLOR_BG)

        details_button = tk.Button(
            controls,
            text=self.t("intervals"),
            command=lambda: self._toggle_detail_frame(details_frame, details_visible),
            **self._mini_button_style(),
        )
        details_button.pack(side=tk.LEFT)

        note = tk.Label(
            win, text="", font=("Segoe UI", 7),
            fg=COLOR_TEXT_DIM, bg=COLOR_BG,
            justify=tk.LEFT, wraplength=900,
        )
        note.pack(anchor="w", padx=15, pady=(4, 6))

        tree = ttk.Treeview(
            details_frame,
            columns=("from", "to", "status", "amount", "note"),
            show="headings", height=8,
        )
        for column, label, width in (
            ("from", self.t("from"), 145), ("to", self.t("to"), 145),
            ("status", self.t("zone"), 110), ("amount", self.t("amount"), 110),
            ("note", self.t("note"), 240),
        ):
            tree.heading(column, text=label)
            tree.column(column, width=width, anchor=tk.E if column == "amount" else tk.W)
        tree.pack(fill=tk.BOTH, expand=True)

        self._cost_ui = {
            "win": win,
            "status": status,
            "balance": balance,
            "sub": sub,
            "refresh": refresh,
            "api": api,
            "vars": vars_,
            "peak_share": peak_share,
            "canvas": chart,
            "note": note,
            "tree": tree,
            "details_frame": details_frame,
            "details_visible": details_visible,
            "details_button": details_button,
        }

        self._analytics_ui = self._cost_ui
        win.protocol("WM_DELETE_WINDOW", self._close_cost_window)
        self._cost_rendered_version = -1
        self._update_cost_window(force=True)

    def _toggle_detail_frame(self, frame: tk.Frame, variable: tk.BooleanVar) -> None:
        if variable.get():
            variable.set(False)
            frame.pack_forget()
            return
        variable.set(True)
        frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=(0, 8))

    def _populate_host_filter(self) -> None:
        """Populate the host filter dropdown (only in active InfluxDB mode)."""
        combo = getattr(self, "_host_combo", None)
        if combo is None or not config.INFLUX_ENABLED:
            return

        try:
            hosts = fetch_influx_hosts()
        except Exception:
            hosts = []

        values = [self.t("all_hosts")] + hosts
        current = self._host_filter if self._host_filter in hosts else self.t("all_hosts")
        combo["values"] = values
        combo.set(current)

    def _on_host_filter_selected(self, _event: object | None = None) -> None:
        """React to the host selection and reload the data."""
        combo = getattr(self, "_host_combo", None)
        if combo is None:
            return

        value = combo.get().strip()
        if value in ("", self.t("all_hosts")):
            self._host_filter = ""
        else:
            self._host_filter = value

        # Reload data with new filter.
        self._refresh_analytics_state()

    def _close_cost_window(self) -> None:
        win = self._cost_window
        if win is None:
            return
        try:
            self._save_geometry(win, COST_POS_FILE)
            win.destroy()
        except tk.TclError:
            pass
        finally:
            self._cost_window = None
            self._cost_ui = {}
            self._analytics_ui = {}
            self._cost_rendered_version = -1
            self._sync_window_buttons()

    # --------------------------------------------------------
    # Settings
    # --------------------------------------------------------

    def _set_poll_interval_from_gui(
        self,
        value: str,
        settings_window: tk.Toplevel | None = None,
    ) -> None:
        try:
            seconds = int(str(value).strip())
        except (TypeError, ValueError):
            seconds = self._poll_interval_s

        seconds = min(MAX_POLL_INTERVAL_S, max(MIN_POLL_INTERVAL_S, seconds))
        self._poll_interval_s = seconds
        save_poll_interval(seconds)

        # Also adjust analytics gap detection immediately.
        config.MAX_ANALYTICS_GAP_S = seconds * 2.5
        self._refresh_analytics_state()

        # Immediately switch an ongoing wait to the new interval.
        self._poll_wakeup_event.set()

        if settings_window is not None:
            settings_window.focus_set()

        self._show_saved_dialog(settings_window)

    def _close_settings_window(self) -> None:
        win = getattr(self, "_settings_window", None)
        if win is None:
            return
        try:
            self._save_window_position(win, SETTINGS_POS_FILE)
            win.destroy()
        except tk.TclError:
            pass
        finally:
            self._settings_window = None

    def open_settings(self) -> None:
        """Modernes, kompaktes Einstellungsfenster."""
        existing = getattr(self, "_settings_window", None)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    self._close_settings_window()
                    return
            except tk.TclError:
                pass
            self._settings_window = None

        win = tk.Toplevel(self.root)
        self._settings_window = win
        win.title(self.t("settings_title"))
        win.configure(bg=COLOR_BG)
        win.resizable(False, False)
        win.transient(self.root)

        # Settings window width always exactly like the main window.
        # settings_pos stores only the position, never the size.
        saved_pos = self._load_window_position(SETTINGS_POS_FILE)
        if saved_pos is not None:
            win.geometry(f"300x230+{saved_pos[0]}+{saved_pos[1]}")
        else:
            win.geometry("300x230")

        def close_settings() -> None:
            try:
                win.update_idletasks()
                self._save_window_position(win, SETTINGS_POS_FILE)
            except tk.TclError:
                pass
            try:
                win.grab_release()
            except tk.TclError:
                pass
            try:
                win.destroy()
            finally:
                self._settings_window = None

        win.protocol("WM_DELETE_WINDOW", close_settings)
        # Position is saved on actual close; no Configure write needed.

        # Header area
        header = tk.Frame(
            win,
            bg=COLOR_CARD,
            highlightbackground=COLOR_CARD_BORDER,
            highlightthickness=1,
        )
        header.pack(fill=tk.X, padx=10, pady=(10, 8))

        tk.Label(
            header,
            text="⚙",
            font=("Segoe UI", 14),
            fg=COLOR_ACCENT,
            bg=COLOR_CARD,
        ).pack(side=tk.LEFT, padx=(10, 6), pady=8)

        title_box = tk.Frame(header, bg=COLOR_CARD)
        title_box.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=6)

        tk.Label(
            title_box,
            text=self.t("settings_title"),
            font=("Segoe UI", 10, "bold"),
            fg=COLOR_TEXT_MAIN,
            bg=COLOR_CARD,
        ).pack(anchor="w")

        tk.Label(
            title_box,
            text=f"DeepSeek Monitor · v{APP_VERSION}",
            font=("Segoe UI", 7),
            fg=COLOR_TEXT_DIM,
            bg=COLOR_CARD,
        ).pack(anchor="w")

        # Shared style for input fields/buttons
        def section(parent: tk.Misc, caption: str) -> tk.Frame:
            outer = tk.Frame(
                parent,
                bg=COLOR_BG,
                highlightbackground=COLOR_CARD_BORDER,
                highlightthickness=1,
            )
            outer.pack(fill=tk.X, padx=10, pady=(0, 7))
            tk.Label(
                outer,
                text=caption,
                font=("Segoe UI", 7, "bold"),
                fg=COLOR_ACCENT,
                bg=COLOR_BG,
            ).pack(anchor="w", padx=10, pady=(6, 3))
            return outer

        appearance = section(win, self.t("appearance"))

        # Uniform 2-column layout: label left, selection field right.
        # Fixed widths prevent the timezone button from shifting the language row.
        appearance_grid = tk.Frame(appearance, bg=COLOR_BG)
        appearance_grid.pack(fill=tk.X, padx=10, pady=(0, 8))
        appearance_grid.grid_columnconfigure(1, weight=1)

        def grid_label(row: int, text: str) -> None:
            tk.Label(
                appearance_grid,
                text=text,
                font=("Segoe UI", 8),
                fg=COLOR_TEXT_DIM,
                bg=COLOR_BG,
            ).grid(
                row=row,
                column=0,
                sticky="w",
                padx=(0, 12),
                pady=3,
            )

        def option_combo(
            row: int,
            values: list[str],
            current: str,
            on_select,
        ) -> ttk.Combobox:
            combo = ttk.Combobox(
                appearance_grid,
                values=values,
                state="readonly",
                width=22,
                font=("Segoe UI", 8),
            )
            combo.set(current)
            combo.bind(
                "<<ComboboxSelected>>",
                lambda _event: on_select(combo.get(), combo),
            )
            combo.grid(
                row=row,
                column=1,
                sticky="e",
                pady=3,
            )
            return combo

        grid_label(0, self.t("language"))

        language_values = [self.t("auto")] + [
            f"{LANGUAGES[code]} ({code.upper()})"
            for code in LANGUAGE_ORDER
        ]

        language_display = (
            self.t("auto")
            if getattr(self, "_language_auto", False)
            else f"{LANGUAGES.get(self.language, self.language)} ({self.language.upper()})"
        )

        def on_language_combo(value: str, combo: ttk.Combobox) -> None:
            if value == self.t("auto"):
                self._language_auto = True
                self._reset_language_from_settings(combo)
                return

            selected_code = self.language
            for code in LANGUAGE_ORDER:
                if value == f"{LANGUAGES[code]} ({code.upper()})":
                    selected_code = code
                    break

            self._language_auto = False
            self._select_language_from_settings(
                selected_code,
                combo,
            )

        lang_combo = option_combo(
            0,
            language_values,
            language_display,
            on_language_combo,
        )

        grid_label(1, self.t("timezone"))

        def on_timezone_combo(value: str, combo: ttk.Combobox) -> None:
            self._select_timezone_from_settings(
                value,
                combo,
            )

        tz_combo = option_combo(
            1,
            COMMON_TIMEZONES,
            self.tz_name,
            on_timezone_combo,
        )

        data = section(win, self.t("data"))
        r = tk.Frame(data, bg=COLOR_BG)
        r.pack(fill=tk.X, padx=10, pady=(0, 7))
        tk.Label(
            r,
            text=self.t("poll_interval"),
            font=("Segoe UI", 8),
            fg=COLOR_TEXT_DIM,
            bg=COLOR_BG,
        ).pack(side=tk.LEFT)

        poll_var = tk.StringVar(value=str(self._poll_interval_s))
        spin = tk.Spinbox(
            r,
            from_=MIN_POLL_INTERVAL_S,
            to=MAX_POLL_INTERVAL_S,
            increment=POLL_INTERVAL_STEP_S,
            textvariable=poll_var,
            width=7,
            justify=tk.RIGHT,
            font=("Segoe UI", 8),
            bg=COLOR_CARD,
            fg=COLOR_TEXT_MAIN,
            insertbackground=COLOR_TEXT_MAIN,
            buttonbackground=COLOR_CARD_BORDER,
            relief="flat",
        )
        spin.pack(side=tk.RIGHT)

        tk.Label(
            r,
            text=f"{self.t('poll_interval_hint')}",
            font=("Segoe UI", 7),
            fg=COLOR_TEXT_DIM,
            bg=COLOR_BG,
        ).pack(side=tk.RIGHT, padx=(0, 6))

        # ---- Data source (local/proxy vs. InfluxDB) ----
        source = section(win, self.t("data_source"))

        source_row = tk.Frame(source, bg=COLOR_BG)
        source_row.pack(fill=tk.X, padx=10, pady=(0, 4))

        tk.Label(
            source_row,
            text=self.t("data_source") + ":",
            font=("Segoe UI", 8),
            fg=COLOR_TEXT_DIM,
            bg=COLOR_BG,
        ).pack(side=tk.LEFT)

        source_var = tk.StringVar(
            value=self.t("influxdb") if config.INFLUX_ENABLED else self.t("local_proxy")
        )
        source_combo = ttk.Combobox(
            source_row,
            textvariable=source_var,
            values=[self.t("local_proxy"), self.t("influxdb")],
            state="readonly",
            width=26,
            font=("Segoe UI", 8),
        )
        source_combo.pack(side=tk.RIGHT)

        fields = tk.Frame(source, bg=COLOR_BG)
        fields.pack(fill=tk.X, padx=10, pady=(0, 8))

        def field_row(row: int, label: str) -> tk.Entry:
            tk.Label(
                fields,
                text=label,
                font=("Segoe UI", 8),
                fg=COLOR_TEXT_DIM,
                bg=COLOR_BG,
            ).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=2)
            entry = tk.Entry(
                fields,
                font=("Segoe UI", 8),
                bg=COLOR_CARD,
                fg=COLOR_TEXT_MAIN,
                insertbackground=COLOR_TEXT_MAIN,
                relief="flat",
                width=30,
            )
            entry.grid(row=row, column=1, sticky="ew", pady=2)
            return entry

        fields.grid_columnconfigure(1, weight=1)

        url_entry = field_row(0, self.t("influx_url"))
        token_entry = field_row(1, self.t("influx_token"))
        org_entry = field_row(2, self.t("influx_org"))
        bucket_entry = field_row(3, self.t("influx_bucket"))

        # Token with hidden display.
        token_entry.config(show="•")

        url_entry.insert(0, config.INFLUX_URL)
        token_entry.insert(0, config.INFLUX_TOKEN)
        org_entry.insert(0, config.INFLUX_ORG)
        bucket_entry.insert(0, config.INFLUX_BUCKET)

        def _save_influx_settings() -> None:
            chosen = source_var.get()
            if chosen == self.t("influxdb"):
                cfg = {
                    "url": url_entry.get().strip(),
                    "token": token_entry.get().strip(),
                    "org": org_entry.get().strip(),
                    "bucket": bucket_entry.get().strip(),
                }
                save_influx_config(cfg)
                _apply_influx_config(
                    cfg["url"],
                    cfg["token"],
                    cfg["org"],
                    cfg["bucket"],
                )
            else:
                remove_influx_config()
                _apply_influx_config("", "", "", "")

            # Reload data with new source.
            self._refresh_analytics_state()
            if self._cost_window is not None:
                self._cost_rendered_version = -1
                self._update_cost_window(force=True)

            self._show_saved_dialog(win)

        apply_influx_btn = tk.Button(
            source,
            text=self.t("apply"),
            command=_save_influx_settings,
            **self._mini_button_style(),
        )
        apply_influx_btn.pack(anchor="e", padx=10, pady=(0, 8))

        actions = section(win, self.t("actions"))
        button_row = tk.Frame(actions, bg=COLOR_BG)
        button_row.pack(fill=tk.X, padx=10, pady=(0, 8))

        tk.Button(
            button_row,
            text=self.t("apply"),
            command=lambda: self._set_poll_interval_from_gui(poll_var.get(), win),
            **self._mini_button_style(),
        ).pack(side=tk.LEFT, padx=(0, 5))

        tk.Button(
            button_row,
            text=self.t("api_key"),
            command=lambda: (close_settings(), self._open_api_key_dialog()),
            **self._mini_button_style(),
        ).pack(side=tk.LEFT, padx=5)

        tk.Button(
            button_row,
            text=self.t("cancel"),
            command=close_settings,
            **self._mini_button_style(),
        ).pack(side=tk.RIGHT)

        # "Immer im Vordergrund" bewusst in einer eigenen Zeile, damit
        # die Aktionsbuttons in ihrer eigenen Zeile sauber ausgerichtet bleiben.
        topmost_row = tk.Frame(win, bg=COLOR_BG)
        topmost_row.pack(fill=tk.X, padx=10, pady=(2, 6))

        self._settings_topmost_var = tk.BooleanVar(value=self._topmost)
        tk.Checkbutton(
            topmost_row,
            text=self.t("on_top"),
            variable=self._settings_topmost_var,
            command=lambda: self.toggle_topmost(),
            font=("Segoe UI", 8),
            fg=COLOR_TEXT_MAIN,
            bg=COLOR_BG,
            activebackground=COLOR_BG,
            activeforeground=COLOR_TEXT_MAIN,
            selectcolor=COLOR_CARD,
            relief="flat",
            highlightthickness=0,
        ).pack(side=tk.LEFT)

        win.update_idletasks()
        requested_h = min(max(win.winfo_reqheight(), 230), 560)
        current = win.geometry()
        try:
            _, x, y = current.rsplit("+", 2)
            win.geometry(f"300x{requested_h}+{int(x)}+{int(y)}")
        except (ValueError, tk.TclError):
            win.geometry(f"300x{requested_h}")
        win.minsize(300, 230)
        win.maxsize(300, 560)

    def _remember_settings_geometry(self, win: tk.Toplevel) -> None:
        try:
            self._settings_last_geometry = win.geometry()
        except tk.TclError:
            pass

    def _on_settings_close(self) -> None:
        win = getattr(self, "_settings_window", None)
        if win is not None:
            try:
                win.update_idletasks()
                self._save_window_position(win, SETTINGS_POS_FILE)
                win.destroy()
            except tk.TclError:
                pass
        self._settings_window = None

    def _select_language_from_settings(self, language: str, button: ttk.Combobox) -> None:
        if language not in LANGUAGES:
            return
        self._language_auto = False
        self.language = language
        save_language(language)
        self._retranslate_ui()
        button.set(f"{LANGUAGES[language]} ({language.upper()})")

    def _reset_language_from_settings(self, button: ttk.Combobox) -> None:
        self._language_auto = True
        remove_language()
        self.language = detect_language()
        self._retranslate_ui()
        button.set(self.t("auto"))

    def _select_timezone_from_settings(self, timezone_name: str, button: tk.Misc) -> None:
        self._on_tz_select(timezone_name)
        button.set(timezone_name)

    def _set_language_and_close(self, language: str, window: tk.Toplevel) -> None:
        self._select_language_from_settings(language, self.lang_btn)
        window.destroy()

    def _set_timezone_and_close(self, timezone_name: str, window: tk.Toplevel) -> None:
        self._select_timezone_from_settings(timezone_name, self.tz_btn)
        window.destroy()

    def _open_api_then_close(self, window: tk.Toplevel) -> None:
        # Backward-compatible helper: save geometry and clean up reference.
        if window is getattr(self, "_settings_window", None):
            self._on_settings_close()
        else:
            try:
                self._save_window_position(window, SETTINGS_POS_FILE)
                window.destroy()
            except tk.TclError:
                pass
        self._open_api_key_dialog()

    # --------------------------------------------------------
    # Translation / timezone
    # --------------------------------------------------------

    def _retranslate_ui(self) -> None:
        self._sync_window_buttons()
        self.btn_settings.config(text="⚙")
        self.btn_topmost.config(text="⚲")
        self.lbl_tz_small.config(text=self.tz_name)
        if self._cost_window is not None:
            self._cost_window.title(self.t("costs"))
            if self._cost_ui.get("api"):
                self._cost_ui["api"].config(text=self.t("api_key"))
            if self._cost_ui.get("refresh"):
                self._cost_ui["refresh"].config(text=self.t("refresh"))
            if self._cost_ui.get("details_button"):
                self._cost_ui["details_button"].config(text=self.t("intervals"))
        self._timeline_minute_key = None
        if self._cost_window is not None:
            self._cost_rendered_version = -1
            self._update_cost_window(force=True)

    def _on_tz_select(self, tz_name: str) -> None:
        if tz_name not in AVAILABLE_TIMEZONES:
            return
        self.tz_name = tz_name
        self.tz = zoneinfo.ZoneInfo(tz_name)
        self.lbl_tz_small.config(text=tz_name)
        save_timezone(tz_name)
        self._timeline_minute_key = None
        self._refresh_analytics_state()
        if self._cost_window is not None:
            self._cost_rendered_version = -1
            self._update_cost_window(force=True)

    # --------------------------------------------------------
    # Realtime / analytics rendering
    # --------------------------------------------------------

    def _update_cost_window(self, force: bool = False) -> None:
        """
        Update the large cost window only on data changes.
        This keeps the 1-second tariff loop lightweight.
        """
        if self._cost_window is None:
            return

        with self._state_lock:
            state = dict(self._balance_state)
            analytics = dict(self._analytics)
            analytics_error = self._analytics_error
            version = self._balance_version

        if not force and version == self._cost_rendered_version:
            return

        ui = self._cost_ui
        self._cost_rendered_version = version

        total = state.get("total")
        error = state.get("error")

        if total is None:
            ui["status"].config(
                text=self.t("error") if error else self.t("connecting"),
                fg=COLOR_PEAK if error else COLOR_TEXT_DIM,
            )
            ui["balance"].config(text="–")
            ui["sub"].config(text=error or "")
        else:
            currency = state.get("currency", "")
            symbol = currency_symbol(currency)
            avail = (
                self.t("available")
                if state.get("available", True)
                else self.t("unavailable")
            )
            ui["status"].config(text=self.t("realtime"), fg=COLOR_TEXT_DIM)
            ui["balance"].config(
                text=format_money(
                    state.get("total", 0),
                    currency,
                    self.language,
                    decimals=2,
                )
            )
            ui["sub"].config(
                text=(
                    f"{self.t('loaded')} {format_money(state.get('topped', 0), currency, self.language, decimals=2)}   ·   "
                    f"{self.t('granted')} {format_money(state.get('granted', 0), currency, self.language, decimals=2)}   ·   {avail}"
                )
            )

        currency = analytics.get("currency", "")
        lang = self.language

        if analytics_error and not analytics:
            for key in ("total", "peak", "standard", "weekend", "savings"):
                ui["vars"][key].set("–")
            ui["peak_share"].set(
                f"{self.t('proxy_error_hint')} · {analytics_error}"
            )
            ui["note"].config(
                text=f"{self.t('proxy_error_hint')} · {analytics_error}"
            )
        else:
            ui["vars"]["total"].set(format_money(analytics.get("usage", 0.0), currency, lang))
            ui["vars"]["peak"].set(format_money(analytics.get("peak_usage", 0.0), currency, lang))
            ui["vars"]["standard"].set(format_money(analytics.get("standard_usage", 0.0), currency, lang))
            ui["vars"]["weekend"].set(format_money(analytics.get("weekend_usage", 0.0), currency, lang))
            ui["vars"]["savings"].set(format_money(analytics.get("avoidable_savings", 0.0), currency, lang))
            ui["peak_share"].set(
                f"{self.t('peak_share')} {analytics.get('peak_share', 0.0) * 100:.1f}%"
            )
            ui["note"].config(text=self.t("analytics_note", mult=PEAK_MULTIPLIER))

        tree = ui["tree"]
        for item in tree.get_children():
            tree.delete(item)
        for interval in analytics.get("intervals", []):
            for part in interval.get("parts", []):
                tree.insert(
                    "", tk.END,
                    values=(
                        part["start"].strftime("%d.%m %H:%M:%S"),
                        part["end"].strftime("%d.%m %H:%M:%S"),
                        part["status"],
                        format_money(part["amount"], currency, lang),
                        self.t("gap_note") if interval.get("gap") else "",
                    ),
                )

        self._draw_stacked_daily_chart(ui["canvas"], analytics=True)

    def _show_saved_dialog(self, parent: tk.Misc | None = None) -> None:
        """Show a theme-consistent confirmation dialog ("Saved").

        Replaces ``messagebox.showinfo``, whose default look (light/gray) does
        not match the dark color theme. The dialog is modal over ``parent`` and
        is closed via the OK button or ``Escape``.
        """
        owner = parent if parent is not None else self.root
        dlg = tk.Toplevel(owner)
        dlg.title(self.t("saved"))
        dlg.configure(bg=COLOR_BG)
        dlg.resizable(False, False)
        dlg.transient(owner)

        frame = tk.Frame(
            dlg,
            bg=COLOR_CARD,
            highlightbackground=COLOR_CARD_BORDER,
            highlightthickness=1,
        )
        frame.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        tk.Label(
            frame,
            text=self.t("saved"),
            font=("Segoe UI", 9, "bold"),
            fg=COLOR_TEXT_MAIN,
            bg=COLOR_CARD,
        ).pack(padx=24, pady=(16, 12))

        def _close(event: object | None = None) -> None:
            try:
                dlg.grab_release()
            except tk.TclError:
                pass
            try:
                dlg.destroy()
            except tk.TclError:
                pass

        ok_btn = tk.Button(
            frame,
            text="OK",
            command=_close,
            font=("Segoe UI", 8, "bold"),
            fg=COLOR_TEXT_MAIN,
            bg=COLOR_ACCENT,
            activebackground=COLOR_ACCENT,
            activeforeground="#000000",
            relief="flat",
            borderwidth=0,
            padx=18,
            pady=3,
            cursor="hand2",
        )
        ok_btn.pack(pady=(0, 14))

        dlg.bind("<Escape>", _close)
        dlg.bind("<Return>", _close)

        dlg.update_idletasks()
        w = dlg.winfo_reqwidth()
        h = dlg.winfo_reqheight()
        try:
            ox = owner.winfo_rootx() + (owner.winfo_width() - w) // 2
            oy = owner.winfo_rooty() + (owner.winfo_height() - h) // 2
            dlg.geometry(f"+{max(0, ox)}+{max(0, oy)}")
        except tk.TclError:
            pass

        dlg.grab_set()
        ok_btn.focus_set()

    def _show_bar_tooltip(self, text: str) -> None:
        """Zeigt ein kleines, nicht-modales Tooltip-Fenster an der Maus."""
        tip = getattr(self, "_bar_tooltip", None)
        if tip is None:
            tip = tk.Toplevel(self.root)
            tip.withdraw()
            tip.overrideredirect(True)
            try:
                tip.attributes("-topmost", True)
            except tk.TclError:
                pass
            lbl = tk.Label(
                tip,
                text="",
                font=("Segoe UI", 8),
                bg=COLOR_CARD,
                fg=COLOR_TEXT_MAIN,
                relief="solid",
                bd=1,
                padx=6,
                pady=3,
                justify="left",
            )
            lbl.pack()
            self._bar_tooltip = tip
            self._bar_tooltip_label = lbl
        self._bar_tooltip_label.config(text=text)
        try:
            x = self.root.winfo_pointerx() + 14
            y = self.root.winfo_pointery() + 14
            tip.geometry(f"+{x}+{y}")
            tip.deiconify()
            tip.lift()
        except tk.TclError:
            pass

    def _hide_bar_tooltip(self) -> None:
        tip = getattr(self, "_bar_tooltip", None)
        if tip is not None:
            try:
                tip.withdraw()
            except tk.TclError:
                pass

    def _draw_stacked_daily_chart(self, canvas: tk.Canvas, analytics: bool = False) -> None:
        canvas.delete("all")
        with self._state_lock:
            rows = list(self._daily_rows)
        if not rows:
            canvas.create_text(
                max(canvas.winfo_width(), 500) / 2,
                max(canvas.winfo_height(), 160) / 2,
                text=self.t("no_data"), fill=COLOR_TEXT_DIM,
                font=("Segoe UI", 8),
            )
            return

        width = max(canvas.winfo_width(), 500)
        height = max(canvas.winfo_height(), 160)
        data = rows[-ANALYTICS_DAYS:]
        # ``top`` reserves its own header line for the percentage/color hint,
        # so it does not overlap the value labels of the bars.
        left, right, top, bottom = 30, 15, 36, 48
        plot_w = width - left - right
        plot_h = height - top - bottom
        max_value = max(float(r["usage"]) for r in data) or 1.0
        slot = plot_w / len(data)
        bar_w = max(5, slot * 0.62)
        base_y = top + plot_h

        canvas.create_line(left, base_y, width-right, base_y, fill=COLOR_CARD_BORDER)

        # Zone×Host mode: each segment belongs to a tariff zone (color) and
        # einem Host (Abdunkelung). Grundfarbe = Tarif-Farbe; jeder weitere
        # host darkens its base color by 5% (index across all hosts).
        # Stack order: STANDARD (bottom) → WEEKEND → PEAK (top).
        zone_colors = STATUS_COLORS
        zone_order = ("STANDARD", "WEEKEND", "PEAK")

        has_segments = any(r.get("zone_hosts") for r in data)
        host_index: dict[str, int] = {}
        if has_segments:
            all_hosts = sorted(
                {
                    h
                    for r in data
                    for zh in r.get("zone_hosts", {}).values()
                    for h in zh
                }
            )
            for idx, h in enumerate(all_hosts):
                host_index[h] = idx

        def _zone_host_color(zone: str, host: str) -> str:
            base = zone_colors.get(zone, COLOR_STANDARD)
            idx = host_index.get(host, 0)
            return _shade_hex(base, max(0.15, 1.0 - 0.05 * idx))

        for i, row in enumerate(data):
            x = left + slot * i + slot / 2
            if has_segments:
                segments: list[tuple[float, str, str, str]] = []
                for zone in zone_order:
                    for host, amount in row.get("zone_hosts", {}).get(zone, {}).items():
                        segments.append(
                            (float(amount), _zone_host_color(zone, host), host, zone)
                        )
            else:
                segments = [
                    (float(row["standard_usage"]), COLOR_STANDARD, None, "STANDARD"),
                    (float(row["weekend_usage"]), COLOR_WEEKEND, None, "WEEKEND"),
                    (float(row["peak_usage"]), COLOR_PEAK, None, "PEAK"),
                ]

            # Tooltip: host shares of the day (computed once per bar).
            tooltip_text: str | None = None
            if has_segments:
                day_hosts = {
                    h
                    for zh in row.get("zone_hosts", {}).values()
                    for h in zh
                }
                tooltip_lines = []
                for h in sorted(day_hosts):
                    host_total = sum(
                        row.get("zone_hosts", {}).get(z, {}).get(h, 0.0)
                        for z in zone_order
                    )
                    if host_total > 0:
                        pct = (
                            host_total / float(row["usage"]) * 100
                            if row["usage"]
                            else 0.0
                        )
                        tooltip_lines.append(f"{h}: {host_total:.4f} ({pct:.1f}%)")
                if tooltip_lines:
                    tooltip_text = "\n".join(tooltip_lines)

            cursor = base_y
            total_height = float(row["usage"]) / max_value * plot_h
            for amount, color, host, zone in segments:
                segment = amount / max_value * plot_h
                if segment <= 0:
                    continue
                rect = canvas.create_rectangle(
                    x-bar_w/2, cursor-segment, x+bar_w/2, cursor,
                    fill=color, outline="",
                )
                if tooltip_text:
                    canvas.tag_bind(
                        rect, "<Enter>",
                        lambda e, t=tooltip_text: self._show_bar_tooltip(t),
                    )
                    canvas.tag_bind(rect, "<Leave>", lambda e: self._hide_bar_tooltip())
                cursor -= segment

            day = dt.date.fromisoformat(row["date"])
            weekdays = self.t("weekdays")
            weekday_label = weekdays[day.weekday()] if isinstance(weekdays, list) else day.strftime("%a")

            # Two-line date axis: weekday directly above the date.
            canvas.create_text(
                x, base_y + 9,
                text=weekday_label,
                fill=COLOR_TEXT_MAIN,
                font=("Segoe UI", 7, "bold"),
            )
            canvas.create_text(
                x, base_y + 21,
                text=day.strftime("%d.%m."),
                fill=COLOR_TEXT_DIM,
                font=("Segoe UI", 7),
            )
            if total_height > 11:
                canvas.create_text(
                    x, base_y-total_height-7,
                    text=f"{row['usage']:.4f}",
                    fill=COLOR_TEXT_MAIN, font=("Segoe UI", 6),
                )

        # Percentage/color hint at top right of the large analytics window.
        if analytics and self._analytics_ui:
            total = self._analytics.get("usage", 0.0)
            peak = self._analytics.get("peak_usage", 0.0)
            normal = self._analytics.get("standard_usage", 0.0)
            weekend = self._analytics.get("weekend_usage", 0.0)
            if total > 0:
                text = (
                    f"Peak {peak/total*100:.1f}%   "
                    f"Normal {normal/total*100:.1f}%   "
                    f"WE {weekend/total*100:.1f}%"
                )
                canvas.create_text(
                    width-right, 12, anchor="ne",
                    text=text, fill=COLOR_TEXT_DIM,
                    font=("Segoe UI", 7),
                )


    # --------------------------------------------------------
    # Periodic update
    # --------------------------------------------------------

    def _check_zone_warning(self, now_utc: dt.datetime) -> None:
        if not self._warning_enabled:
            return

        boundary = next_status_change(now_utc)
        seconds_left = (boundary - now_utc).total_seconds()

        if 0 < seconds_left <= WARNING_BEFORE_S and boundary != self._warned_boundary:
            self._warned_boundary = boundary
            try:
                self.root.bell()
            except tk.TclError:
                pass

            new_status = status_at(boundary + dt.timedelta(seconds=1))
            localized = {
                "PEAK": self.t("peak_status"),
                "STANDARD": self.t("standard_status"),
                "WEEKEND": self.t("weekend_status"),
            }.get(new_status, new_status)

            self.lbl_warning.config(
                text=self.t(
                    "zone_warning",
                    seconds=int(seconds_left),
                    status=localized,
                )
            )
            if not self._warning_visible:
                self.lbl_warning.pack(pady=(0, 1))
                self._warning_visible = True
        elif seconds_left > WARNING_BEFORE_S + 2:
            self.lbl_warning.config(text="")
            if getattr(self, "_warning_visible", False):
                self.lbl_warning.pack_forget()
                self._warning_visible = False

    def update_loop(self) -> None:
        now_utc = dt.datetime.now(UTC)
        status_text, seconds_left, color, _status_key = calculate_status(now_utc)

        localized_status = {
            "PEAK": self.t("peak_status"),
            "STANDARD": self.t("standard_status"),
            "WEEKEND": self.t("weekend_status"),
        }.get(_status_key, status_text)

        self.lbl_badge.config(text=localized_status, bg=color)

        hours, remainder = divmod(int(seconds_left), 3600)
        minutes, seconds = divmod(remainder, 60)
        self.lbl_timer.config(text=f"{hours:02d}:{minutes:02d}:{seconds:02d}")

        local_now = now_utc.astimezone(self.tz)
        timeline_key = (local_now.date(), local_now.hour, local_now.minute)
        if timeline_key != self._timeline_minute_key:
            self._timeline_minute_key = timeline_key
            self.draw_filigree_timeline(now_utc)

        self._check_zone_warning(now_utc)

        if self._cost_window is not None:
            self._update_cost_window()

        self.root.after(1000, self.update_loop)

    # --------------------------------------------------------
    # Close
    # --------------------------------------------------------

    def _on_close(self) -> None:
        self._stop_event.set()
        if hasattr(self, "_poll_wakeup_event"):
            self._poll_wakeup_event.set()
        self._save_geometry(self.root, TARIFF_POS_FILE)
        self._close_cost_window()
        self.root.destroy()


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    root = tk.Tk()
    app = DeepSeekFiligreeGUI(root)
    root.after(100, app.update_loop)
    root.mainloop()
