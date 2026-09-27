"""Eigenständige Tkinter-Detailseite für echte Usage- und Kostendaten.

Dieses Modul ist eine **read-only** Desktop-Ansicht auf dieselbe SQLite-DB, die
der Proxy beschreibt. Es werden ausschließlich die **real aufgezeichneten**
Usage-Events (Token + daraus berechnete Kosten) dargestellt -- es wird **keine**
Balance-/Guthaben-Schätzung verwendet.

Wichtig:

* Die GUI liest die DB nur; geschrieben wird ausschließlich vom Proxy-Server.
* Die GUI startet **keinen** Proxy-Server. Der Server läuft separat (systemd-
  Dienst oder eigenes Terminal).
* Es gibt keinen globalen Zustand: die :class:`~proxy.storage.Storage` wird dem
  Konstruktor explizit übergeben.

Die gesamte Aggregationslogik ist in :func:`build_summary` gebündelt und damit
**ohne** Tkinter testbar. Die GUI-Klasse :class:`UsageDetailApp` ruft nur
:func:`build_summary` auf und rendert das Ergebnis.

Öffentliche Symbole:
    * :func:`build_summary`
    * :class:`UsageDetailApp`
"""

from __future__ import annotations

import logging
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import ttk
from zoneinfo import ZoneInfo

from .cost_calc import calc_usage_cost
from .labels import resolve_label
from .pricing import is_peak, load_pricing, normalize_model
from .storage import Storage, row_to_usage

logger = logging.getLogger("deepseek_proxy.tkinter_view")

__all__ = ["build_summary", "UsageDetailApp"]

_BERLIN = ZoneInfo("Europe/Berlin")

#: Rundung der USD-Kosten (identisch zu ``cost_calc``).
_COST_DECIMALS = 8

# ------------------------------------------------------------------- Farben
_BG = "#0f1115"
_PANEL = "#171a21"
_PANEL2 = "#1e222b"
_BORDER = "#2a2f3a"
_FG = "#e6e9ef"
_MUTED = "#8a93a5"
_ACCENT = "#4f9dff"
_GOOD = "#3ddc97"
_WARN = "#ffb454"


# --------------------------------------------------------------------- Logik
def _window_start(days: int) -> str:
    """ISO-Startzeit ``now - days`` in Europe/Berlin (gleiches Format wie DB)."""
    start = datetime.now(_BERLIN) - timedelta(days=max(0, days))
    return start.isoformat(timespec="seconds")


def build_summary(
    storage: Storage,
    days: int = 7,
    fingerprint: str | None = None,
) -> dict:
    """Aggregiert Kosten und Token über die letzten ``days`` Tage.

    Vorgehen (bewusst analog zu ``dashboard.api_summary``): Es wird über
    :meth:`Storage.get_usage` **pro Event** gerechnet und selbst gruppiert.
    Grund: Die Kosten hängen pro Event vom Zeitstempel (Peak/Off-Peak) und vom
    Modell ab. :meth:`Storage.get_daily_usage` liefert nur Token-Summen pro Tag
    und kann daher keine korrekten Kosten ableiten -- deshalb wird es hier
    **nicht** verwendet.

    Parameter:
        storage: Datenquelle (nur lesend).
        days: Zeitfenster in Tagen (Default 7).
        fingerprint: Optionaler Filter auf ``api_key_fingerprint``.

    Returns:
        Dict mit ``days``, ``start``, ``total_cost``, ``total_input_tokens``,
        ``total_output_tokens``, ``total_cache_read_tokens``,
        ``total_cache_creation_tokens``, ``requests``, ``peak``,
        ``off_peak``, ``daily``, ``per_label`` und ``labels``.
    """
    pricing = load_pricing(storage)
    start = _window_start(days)

    rows = storage.get_usage(fingerprint=fingerprint, start=start, limit=None)
    usages = [row_to_usage(row) for row in rows]

    total_cost = 0.0
    total_input = 0
    total_output = 0
    total_cache_read = 0
    total_cache_creation = 0

    peak = {"cost": 0.0, "requests": 0}
    off_peak = {"cost": 0.0, "requests": 0}
    daily: dict[str, dict] = {}
    per_label: dict[str, dict] = {}

    for usage in usages:
        cost = calc_usage_cost(usage, pricing)
        total_cost += cost
        total_input += int(usage.input_tokens)
        total_output += int(usage.output_tokens)
        total_cache_read += int(usage.cache_read_input_tokens)
        total_cache_creation += int(usage.cache_creation_input_tokens)

        bucket = peak if is_peak(usage.timestamp) else off_peak
        bucket["cost"] = round(bucket["cost"] + cost, _COST_DECIMALS)
        bucket["requests"] += 1

        day = (usage.timestamp or "")[:10] or "unknown"
        day_entry = daily.setdefault(
            day,
            {"date": day, "cost": 0.0, "input_tokens": 0, "output_tokens": 0, "requests": 0},
        )
        day_entry["cost"] = round(day_entry["cost"] + cost, _COST_DECIMALS)
        day_entry["input_tokens"] += int(usage.input_tokens)
        day_entry["output_tokens"] += int(usage.output_tokens)
        day_entry["requests"] += 1

        lbl = resolve_label(storage, usage.api_key_fingerprint)
        lbl_entry = per_label.setdefault(
            lbl,
            {"cost": 0.0, "input_tokens": 0, "output_tokens": 0, "requests": 0},
        )
        lbl_entry["cost"] = round(lbl_entry["cost"] + cost, _COST_DECIMALS)
        lbl_entry["input_tokens"] += int(usage.input_tokens)
        lbl_entry["output_tokens"] += int(usage.output_tokens)
        lbl_entry["requests"] += 1

    labels = [resolve_label(storage, fp) for fp in storage.get_distinct_fingerprints()]

    return {
        "days": days,
        "start": start,
        "total_cost": round(total_cost, _COST_DECIMALS),
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_cache_read_tokens": total_cache_read,
        "total_cache_creation_tokens": total_cache_creation,
        "requests": len(usages),
        "peak": peak,
        "off_peak": off_peak,
        "daily": sorted(daily.values(), key=lambda d: d["date"]),
        "per_label": per_label,
        "labels": labels,
    }


# ----------------------------------------------------------------- Formatter
def _fmt_int(value: int) -> str:
    """Formatiert eine Ganzzahl mit Tausenderpunkten."""
    return f"{int(value):,}".replace(",", ".")


def _fmt_usd(value: float) -> str:
    """Formatiert einen USD-Betrag mit fester Nachkommastellenzahl."""
    return f"${float(value):.6f}"


# ----------------------------------------------------------------------- GUI
class UsageDetailApp:
    """Read-only Tkinter-Detailansicht für echte Usage- und Kostendaten.

    Parameter:
        storage: Datenquelle (nur lesend); wird explizit übergeben.
        refresh_ms: Auto-Refresh-Intervall in Millisekunden (Default 60000).
    """

    def __init__(self, storage: Storage, refresh_ms: int = 60000) -> None:
        self._storage = storage
        self._refresh_ms = max(1000, int(refresh_ms))
        self._days = 7
        self._fingerprint: str | None = None
        self._label_to_fp: dict[str, str] = {}
        self._after_id: str | None = None

        self._root = tk.Tk()
        self._root.title("DeepSeek Kosten & Verbrauch (exakt)")
        self._root.geometry("1000x740")
        self._root.configure(bg=_BG)
        self._root.minsize(760, 560)

        self._last_updated = tk.StringVar(value="noch nicht aktualisiert")
        self._kpi_vars: dict[str, tk.StringVar] = {
            "total_cost": tk.StringVar(value="-"),
            "total_input_tokens": tk.StringVar(value="-"),
            "total_output_tokens": tk.StringVar(value="-"),
            "requests": tk.StringVar(value="-"),
        }
        self._peak_vars: dict[str, tk.StringVar] = {
            "peak_cost": tk.StringVar(value="-"),
            "peak_requests": tk.StringVar(value="-"),
            "off_peak_cost": tk.StringVar(value="-"),
            "off_peak_requests": tk.StringVar(value="-"),
        }
        self._label_var = tk.StringVar(value="alle")
        self._pricing_var = tk.StringVar(value="")
        self._daily_tree: ttk.Treeview | None = None

        self._setup_style()
        self._build_widgets()
        self._refresh()

    # ------------------------------------------------------------------ style
    def _setup_style(self) -> None:
        """Konfiguriert ein dunkles ttk-Theme passend zum Web-Dashboard."""
        style = ttk.Style(self._root)
        try:
            style.theme_use("clam")
        except tk.TclError:  # pragma: no cover - nur bei exotischen Plattformen
            logger.debug("ttk-Theme 'clam' nicht verfügbar, nutze Default.")

        style.configure("TFrame", background=_BG)
        style.configure("Panel.TFrame", background=_PANEL)
        style.configure("Card.TFrame", background=_PANEL2)
        style.configure(
            "TLabel", background=_BG, foreground=_FG, font=("TkDefaultFont", 10)
        )
        style.configure(
            "Panel.TLabel", background=_PANEL, foreground=_FG
        )
        style.configure(
            "Title.TLabel",
            background=_BG,
            foreground=_FG,
            font=("TkDefaultFont", 15, "bold"),
        )
        style.configure(
            "Muted.TLabel", background=_BG, foreground=_MUTED
        )
        style.configure(
            "PanelMuted.TLabel", background=_PANEL, foreground=_MUTED
        )
        style.configure(
            "CardLabel.TLabel", background=_PANEL2, foreground=_MUTED
        )
        style.configure(
            "CardValue.TLabel",
            background=_PANEL2,
            foreground=_FG,
            font=("TkDefaultFont", 16, "bold"),
        )
        style.configure(
            "PeakValue.TLabel",
            background=_PANEL2,
            foreground=_WARN,
            font=("TkDefaultFont", 15, "bold"),
        )
        style.configure(
            "OffPeakValue.TLabel",
            background=_PANEL2,
            foreground=_GOOD,
            font=("TkDefaultFont", 15, "bold"),
        )
        style.configure(
            "Treeview",
            background=_PANEL2,
            fieldbackground=_PANEL2,
            foreground=_FG,
            bordercolor=_BORDER,
        )
        style.configure(
            "Treeview.Heading", background=_PANEL, foreground=_MUTED
        )
        style.map("Treeview", background=[("selected", _ACCENT)])

    # --------------------------------------------------------------- widgets
    def _build_widgets(self) -> None:
        """Baut die statische Widget-Struktur auf."""
        header = ttk.Frame(self._root, style="TFrame", padding=(18, 14, 18, 6))
        header.pack(fill="x")
        ttk.Label(
            header,
            text="DeepSeek Kosten & Verbrauch (exakt)",
            style="Title.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            header,
            textvariable=self._last_updated,
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        body = ttk.Frame(self._root, style="TFrame", padding=(18, 6, 18, 18))
        body.pack(fill="both", expand=True)

        # KPI-Zeile -------------------------------------------------------
        kpi = ttk.Frame(body, style="TFrame")
        kpi.pack(fill="x")
        for idx, (key, title) in enumerate(
            (
                ("total_cost", "Gesamtkosten"),
                ("total_input_tokens", "Input-Token"),
                ("total_output_tokens", "Output-Token"),
                ("requests", "Requests"),
            )
        ):
            kpi.columnconfigure(idx, weight=1)
            self._make_card(kpi, idx, title, self._kpi_vars[key], "CardValue.TLabel")

        # Peak vs. Off-Peak ----------------------------------------------
        ttk.Label(body, text="Peak vs. Off-Peak", style="TLabel").pack(
            anchor="w", pady=(16, 6)
        )
        tariff = ttk.Frame(body, style="TFrame")
        tariff.pack(fill="x")

        tariff.columnconfigure(0, weight=1)
        self._make_tariff_card(
            tariff, 0, "Peak", "PeakValue.TLabel",
            self._peak_vars["peak_cost"], self._peak_vars["peak_requests"],
        )
        tariff.columnconfigure(1, weight=1)
        self._make_tariff_card(
            tariff, 1, "Off-Peak", "OffPeakValue.TLabel",
            self._peak_vars["off_peak_cost"], self._peak_vars["off_peak_requests"],
        )

        # Tagesaufschlüsselung -------------------------------------------
        ttk.Label(body, text="Tagesaufschlüsselung", style="TLabel").pack(
            anchor="w", pady=(16, 6)
        )
        table_frame = ttk.Frame(body, style="TFrame")
        table_frame.pack(fill="both", expand=True)

        columns = ("date", "cost", "input", "output", "requests")
        tree = ttk.Treeview(
            table_frame, columns=columns, show="headings", height=8
        )
        headings = {
            "date": "Datum",
            "cost": "Kosten",
            "input": "Input-Token",
            "output": "Output-Token",
            "requests": "Requests",
        }
        for col in columns:
            tree.heading(col, text=headings[col])
            tree.column(
                col,
                anchor="e" if col != "date" else "w",
                width=160 if col != "date" else 130,
                stretch=True,
            )
        tree.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
        scroll.pack(side="right", fill="y")
        tree.configure(yscrollcommand=scroll.set)
        self._daily_tree = tree

        # Filter + Preis-Info --------------------------------------------
        footer = ttk.Frame(body, style="Panel.TFrame", padding=(12, 10, 12, 10))
        footer.pack(fill="x", pady=(12, 0))

        ttk.Label(footer, text="Label-Filter:", style="PanelMuted.TLabel").pack(
            side="left"
        )
        self._label_combo = ttk.Combobox(
            footer,
            textvariable=self._label_var,
            state="readonly",
            width=28,
        )
        self._label_combo.pack(side="left", padx=(8, 0))
        self._label_combo.bind("<<ComboboxSelected>>", self._on_label_selected)

        ttk.Label(
            footer, textvariable=self._pricing_var, style="PanelMuted.TLabel"
        ).pack(side="right")

    def _make_card(
        self,
        parent: ttk.Frame,
        column: int,
        title: str,
        value_var: tk.StringVar,
        value_style: str,
    ) -> None:
        """Erzeugt eine KPI-Karte in Spalte ``column``."""
        pad = (0, 8, 0, 0) if column == 0 else (8, 8, 0, 0)
        card = ttk.Frame(parent, style="Card.TFrame", padding=(14, 12, 14, 12))
        card.grid(row=0, column=column, sticky="nsew", padx=pad)
        ttk.Label(card, text=title.upper(), style="CardLabel.TLabel").pack(anchor="w")
        ttk.Label(card, textvariable=value_var, style=value_style).pack(
            anchor="w", pady=(4, 0)
        )

    def _make_tariff_card(
        self,
        parent: ttk.Frame,
        column: int,
        title: str,
        value_style: str,
        cost_var: tk.StringVar,
        requests_var: tk.StringVar,
    ) -> None:
        """Erzeugt eine Peak-/Off-Peak-Karte in Spalte ``column``."""
        pad = (0, 8, 0, 0) if column == 0 else (8, 8, 0, 0)
        card = ttk.Frame(parent, style="Card.TFrame", padding=(14, 12, 14, 12))
        card.grid(row=0, column=column, sticky="nsew", padx=pad)
        ttk.Label(card, text=title, style="CardLabel.TLabel").pack(anchor="w")
        ttk.Label(card, textvariable=cost_var, style=value_style).pack(
            anchor="w", pady=(4, 0)
        )
        ttk.Label(
            card, textvariable=requests_var, style="CardLabel.TLabel"
        ).pack(anchor="w", pady=(2, 0))

    # -------------------------------------------------------------- refresh
    def _on_label_selected(self, _event: object | None = None) -> None:
        """Reagiert auf die Label-Auswahl und aktualisiert die Ansicht."""
        label = self._label_var.get()
        if label == "alle":
            self._fingerprint = None
        else:
            self._fingerprint = self._label_to_fp.get(label)
        self._refresh()

    def refresh(self) -> None:
        """Öffentlicher Refresh-Einstieg (auch für Auto-Refresh nutzbar)."""
        self._refresh()

    def _refresh(self) -> None:
        """Liest die Zusammenfassung und aktualisiert alle Widgets."""
        try:
            summary = build_summary(
                self._storage, days=self._days, fingerprint=self._fingerprint
            )
        except Exception:  # noqa: BLE001 - GUI darf nie hart abstürzen
            logger.exception("build_summary fehlgeschlagen")
            self._last_updated.set(
                "Fehler beim Lesen der Datenbank (siehe Log)"
            )
            self._schedule_refresh()
            return

        self._update_label_combo(summary)
        self._update_kpis(summary)
        self._update_tariffs(summary)
        self._update_daily(summary)
        self._update_pricing_info()

        now = datetime.now(_BERLIN).strftime("%Y-%m-%d %H:%M:%S")
        self._last_updated.set(
            f"Letzte Aktualisierung: {now}  ·  Zeitraum: letzte {self._days} Tage"
            f"  ·  {summary['requests']} Requests"
        )
        self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        """Plant den nächsten Auto-Refresh."""
        if self._after_id is not None:
            try:
                self._root.after_cancel(self._after_id)
            except tk.TclError:  # pragma: no cover - Fenster bereits zerstört
                pass
        self._after_id = self._root.after(self._refresh_ms, self._refresh)

    def _update_label_combo(self, summary: dict) -> None:
        """Aktualisiert Dropdown-Werte (Labels + ``alle``) und die Zuordnung."""
        self._label_to_fp = {
            resolve_label(self._storage, fp): fp
            for fp in self._storage.get_distinct_fingerprints()
        }
        values = ["alle", *sorted(self._label_to_fp)]
        current = self._label_var.get()
        self._label_combo.configure(values=values)
        if current not in values:
            self._label_var.set("alle")
            self._fingerprint = None

    def _update_kpis(self, summary: dict) -> None:
        """Setzt die KPI-Werte."""
        self._kpi_vars["total_cost"].set(_fmt_usd(summary["total_cost"]))
        self._kpi_vars["total_input_tokens"].set(
            _fmt_int(summary["total_input_tokens"])
        )
        self._kpi_vars["total_output_tokens"].set(
            _fmt_int(summary["total_output_tokens"])
        )
        self._kpi_vars["requests"].set(_fmt_int(summary["requests"]))

    def _update_tariffs(self, summary: dict) -> None:
        """Setzt die Peak-/Off-Peak-Werte."""
        peak = summary["peak"]
        off_peak = summary["off_peak"]
        self._peak_vars["peak_cost"].set(_fmt_usd(peak["cost"]))
        self._peak_vars["peak_requests"].set(
            f"{_fmt_int(peak['requests'])} Requests"
        )
        self._peak_vars["off_peak_cost"].set(_fmt_usd(off_peak["cost"]))
        self._peak_vars["off_peak_requests"].set(
            f"{_fmt_int(off_peak['requests'])} Requests"
        )

    def _update_daily(self, summary: dict) -> None:
        """Füllt die Tages-Tabelle neu."""
        tree = self._daily_tree
        if tree is None:  # pragma: no cover - defensive
            return
        for item in tree.get_children():
            tree.delete(item)
        for entry in summary["daily"]:
            tree.insert(
                "",
                "end",
                values=(
                    entry["date"],
                    _fmt_usd(entry["cost"]),
                    _fmt_int(entry["input_tokens"]),
                    _fmt_int(entry["output_tokens"]),
                    _fmt_int(entry["requests"]),
                ),
            )

    def _update_pricing_info(self) -> None:
        """Zeigt eine kurze Info zur verwendeten Preis-Tabelle (nur lesend)."""
        pricing = load_pricing(self._storage)
        models = sorted({normalize_model(key) for key in pricing})
        zones = sorted(
            {
                zone
                for entry in pricing.values()
                if isinstance(entry, dict)
                for zone in entry
                if isinstance(entry.get(zone), dict)
            }
        )
        model_txt = ", ".join(models) if models else "keine"
        zone_txt = " / ".join(zones) if zones else "keine"
        self._pricing_var.set(
            f"Preise: {model_txt} ({zone_txt}) – editierbar im Web-Dashboard"
        )

    # ------------------------------------------------------------------ run
    def run(self) -> None:
        """Startet die Tk-Hauptschleife (blockiert bis das Fenster geschlossen wird)."""
        try:
            self._root.mainloop()
        finally:
            if self._after_id is not None:
                try:
                    self._root.after_cancel(self._after_id)
                except tk.TclError:  # pragma: no cover
                    pass
                self._after_id = None
