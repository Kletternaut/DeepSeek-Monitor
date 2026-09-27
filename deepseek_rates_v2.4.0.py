#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DeepSeek Monitor + Realtime Balance + Analytics

Verbesserungen gegenüber der ursprünglichen Version:
- Tarifberechnung ohne unnötige 9-Tage-Neuberechnung pro Sekunde
- Timeline wird nur bei Minutenwechsel/Zeitzonenwechsel neu gezeichnet
- History bleibt im Speicher und wird nicht bei jedem Poll erneut eingelesen
- atomisches Schreiben von JSON/CSV
- sauber beendbarer Hintergrund-Thread
- keine Tkinter-Zugriffe aus dem Worker-Thread
- neue Analytics-Ansicht:
    * Peak-/Standard-/Wochenend-Verbrauch
    * Tagesauswertung
    * geschätzte vermeidbare Peak-Kosten
    * letzte Verbrauchsintervalle
    * Transparenz über die zugrunde liegende Annahme
- akustische Warnung 60 Sekunden vor einem echten Tarifwechsel
- API-/Netzwerkfehler behalten den letzten guten Balance-Stand
- Zeitintervalle werden anteilig auf Tarifzonen verteilt
- Wochenendlogik und DST werden bei der Tarifberechnung berücksichtigt

WICHTIG ZUR ANALYTIK:
Die DeepSeek /user/balance API liefert Guthabenstände. Daraus wird Verbrauch als
Guthabenverlust zwischen zwei Messpunkten abgeleitet. Der Zeitraum zwischen zwei
Messpunkten wird dabei zeitanteilig auf die jeweiligen Tarifzonen verteilt.

Das bedeutet:
- Die Kostenanalyse ist eine SCHÄTZUNG, solange nur periodische Balance-Werte vorliegen.
- Ein Verbrauchsintervall kann bei einem Tarifwechsel über mehrere Zonen gehen.
- "Potenzielle Ersparnis" ist die vermiedene Peak-Prämie unter der Annahme,
  dass derselbe Verbrauch im Standardfenster hätte stattfinden können.
- PEAK_MULTIPLIER = 2.0 entspricht der im ursprünglichen Tool verwendeten 2x-Annahme.
  Falls DeepSeek künftig andere Faktoren verwendet, hier anpassen.

Python 3.10+ empfohlen.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import locale
import os
import tempfile
import threading
import time
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
import zoneinfo
from collections import OrderedDict
from tkinter import messagebox, ttk
from typing import Any


# ============================================================
# KONFIGURATION
# ============================================================

UTC = dt.timezone.utc
APP_NAME = "DeepSeek Monitor"
APP_VERSION = "2.4.0"

# Tarifannahme aus der ursprünglichen Version
PEAK_WINDOWS_UTC = (
    (1 * 60, 4 * 60),   # 01:00–04:00 UTC
    (6 * 60, 10 * 60),  # 06:00–10:00 UTC
)

PEAK_MULTIPLIER = 2.0

# Weekend off-peak rule:
# entire Saturday + Sunday in Beijing time
WEEKEND_RULE_START_UTC = dt.datetime(
    2026, 8, 22, 16, 0, tzinfo=UTC
)
BJ_TZ = zoneinfo.ZoneInfo("Asia/Shanghai")

DEFAULT_TZ = "Europe/Berlin"

DEFAULT_POLL_INTERVAL_S = 20
MIN_POLL_INTERVAL_S = 20
MAX_POLL_INTERVAL_S = 3600
POLL_INTERVAL_STEP_S = 10
POLL_INTERVAL_S = DEFAULT_POLL_INTERVAL_S
WARNING_BEFORE_S = 60
ANALYTICS_DAYS = 14
ANALYTICS_INTERVAL_LIMIT = 150

BALANCE_URL = "https://api.deepseek.com/user/balance"

# Proxy (deepseek-proxy/) liefert echte Token-Usage-Daten als Quelle für die
# Detailseite „Kosten & Verbrauch" statt der Guthaben-Delta-Schätzung.
PROXY_BASE_URL = os.environ.get(
    "DEEPSEEK_PROXY_URL",
    "http://127.0.0.1:8000",
).rstrip("/")
PROXY_USAGE_URL = f"{PROXY_BASE_URL}/api/usage"

# Optionale zentrale InfluxDB 2.x als alternative Datenquelle (multi-host).
# Diese Werte können zur Laufzeit über die GUI-Einstellungen geändert werden
# (``_apply_influx_config``) und werden in ``influx_config`` persistiert.
INFLUX_URL = ""
INFLUX_TOKEN = ""
INFLUX_ORG = ""
INFLUX_BUCKET = ""
INFLUX_ENABLED = False


def _apply_influx_config(
    url: str,
    token: str,
    org: str,
    bucket: str,
) -> None:
    """Setzt die InfluxDB-Konfiguration zur Laufzeit (Modul-Globals)."""
    global INFLUX_URL, INFLUX_TOKEN, INFLUX_ORG, INFLUX_BUCKET, INFLUX_ENABLED

    INFLUX_URL = (url or "").strip().rstrip("/")
    INFLUX_TOKEN = (token or "").strip()
    INFLUX_ORG = (org or "").strip()
    INFLUX_BUCKET = (bucket or "").strip()
    INFLUX_ENABLED = bool(
        INFLUX_URL and INFLUX_TOKEN and INFLUX_ORG and INFLUX_BUCKET
    )


MAX_HISTORY = 50_000
MAX_ANALYTICS_GAP_S = POLL_INTERVAL_S * 2.5



# ============================================================
# SPRACHEN / WÄHRUNGEN
# ============================================================

LANGUAGES = {
    "de": "Deutsch", "en": "English", "fr": "Français", "es": "Español",
    "it": "Italiano", "pt": "Português", "nl": "Nederlands",
    "pl": "Polski", "ru": "Русский", "zh": "中文",
}
LANGUAGE_ORDER = tuple(LANGUAGES)


def detect_language() -> str:
    values = [
        os.environ.get("LC_ALL", ""),
        os.environ.get("LC_MESSAGES", ""),
        os.environ.get("LANG", ""),
        os.environ.get("LANGUAGE", ""),
    ]
    try:
        values.append(locale.getlocale()[0] or "")
    except (ValueError, AttributeError):
        pass
    for value in values:
        value = (value or "").lower()
        for prefix in LANGUAGES:
            if value.startswith(prefix):
                return prefix
    return "en"


BASE_TRANSLATIONS = {
    "de": {
        "tariff":" Tarif ", "realtime":" Realtime ", "analytics":" Analytics ", "costs":"Kosten & Verbrauch",
        "tz":"TZ:", "weekend":"Wochenende:", "weekend_text":" Sa 00:00 – Mo 00:00 (Peking-Zeit)",
        "peak":"Peak:", "standard":"Std:", "refresh":"Jetzt aktualisieren",
        "api":"API", "api_key":"API-Key", "api_key_title":"DeepSeek API-Key",
        "api_key_hint":"API-Key eingeben oder neu setzen:", "save":"Speichern",
        "cancel":"Abbrechen", "remove":"Key entfernen",
        "env_hint":"DEEPSEEK_API_KEY wird ebenfalls unterstützt.",
        "connecting":"Verbinde mit DeepSeek API…", "error":"Fehler",
        "available":"verfügbar", "unavailable":"nicht ausreichend",
        "loaded":"Aufgeladen:", "granted":"Geschenkt:", "as_of":"Stand:",
        "analytics_title":"Verbrauchsanalyse",
        "analytics_hint":"Letzte 14 Tage. Verbrauch aus real aufgezeichneten Token-Usage-Daten des Proxys.",
        "total":"Gesamt", "normal":"Normal", "savings":"Pot. Ersparnis",
        "peak_share":"Peak-Anteil am Verbrauch:", "daily":"Tagesanalyse",
        "intervals":"Letzte Requests", "date":"Datum",
        "usage":"Gesamt", "weekend_short":"WE", "from":"Von", "to":"Bis",
        "zone":"Zone", "amount":"Verbrauch", "note":"Hinweis",
        "gap_note":"Schätzung bei größerer Messlücke",
        "analytics_note":"Annahme: Peak = {mult:.1f}× Standard. Die potenzielle Ersparnis zeigt die vermeidbare Peak-Prämie. Verbrauch aus realen Token-Usage-Daten des Proxys.",
        "no_data":"Noch keine Daten – die Auswertung baut sich mit jeder Abfrage auf.",
        "proxy_error_hint":"Keine Proxy-Daten – läuft der Dienst deepseek-proxy?",
        "no_key":"Kein API-Key hinterlegt.",
        "confirm_remove":"Gespeicherten API-Key wirklich löschen?",
        "no_key_error":"Kein API-Key: bitte über den API-Dialog setzen oder DEEPSEEK_API_KEY verwenden.",
        "peak_status":"PEAK", "standard_status":"STANDARD", "weekend_status":"WOCHENENDE OFF-PEAK",
        "zone_warning":"Tarifwechsel in ~{seconds} s → {status}",
        "language":"Sprache", "timezone":"Zeitzone", "auto":"Auto", "settings_title":"Einstellungen", "appearance":"Darstellung", "data":"Daten", "actions":"Aktionen", "on_top":"Immer im Vordergrund",
        "weekdays":["Mo","Di","Mi","Do","Fr","Sa","So"],
        "api_401":"API-Key ungültig oder abgelaufen.",
        "api_403":"Zugriff auf die DeepSeek API verweigert.",
        "api_429":"Zu viele API-Anfragen – bitte später erneut versuchen.",
        "poll_interval":"Balance-Abfrage", "poll_interval_hint":"Sekunden (mind. 20)", "apply":"Übernehmen", "saved":"Gespeichert ✓",
        "data_source":"Datenquelle", "local_proxy":"Lokal (Proxy/SQLite)", "influxdb":"InfluxDB (zentral)",
        "influx_url":"URL", "influx_token":"Token", "influx_org":"Organisation", "influx_bucket":"Bucket",
    },
    "en": {
        "tariff":" Rate ", "realtime":" Realtime ", "analytics":" Analytics ", "costs":"Cost & Usage",
        "tz":"TZ:", "weekend":"Weekend:", "weekend_text":" Sat 00:00 – Mon 00:00 (Beijing time)",
        "peak":"Peak:", "standard":"Std:", "refresh":"Refresh now",
        "api":"API", "api_key":"API key", "api_key_title":"DeepSeek API key",
        "api_key_hint":"Enter or replace API key:", "save":"Save",
        "cancel":"Cancel", "remove":"Remove key",
        "env_hint":"DEEPSEEK_API_KEY is also supported.",
        "connecting":"Connecting to DeepSeek API…", "error":"Error",
        "available":"available", "unavailable":"insufficient",
        "loaded":"Topped up:", "granted":"Granted:", "as_of":"As of:",
        "analytics_title":"Usage analysis",
        "analytics_hint":"Last 14 days. Usage from real recorded token usage data of the proxy.",
        "total":"Total", "normal":"Normal", "savings":"Potential saving",
        "peak_share":"Peak share of usage:", "daily":"Daily analysis",
        "intervals":"Latest requests", "date":"Date",
        "usage":"Total", "weekend_short":"WE", "from":"From", "to":"To",
        "zone":"Zone", "amount":"Usage", "note":"Note",
        "gap_note":"Estimate due to larger measurement gap",
        "analytics_note":"Assumption: Peak = {mult:.1f}× Standard. Potential saving is the avoidable peak premium. Usage from real token usage data of the proxy.",
        "no_data":"No data yet – the analysis builds with each poll.",
        "proxy_error_hint":"No proxy data – is the deepseek-proxy service running?",
        "no_key":"No API key configured.",
        "confirm_remove":"Really delete the saved API key?",
        "no_key_error":"No API key: set it in the API dialog or use DEEPSEEK_API_KEY.",
        "peak_status":"PEAK", "standard_status":"STANDARD", "weekend_status":"WEEKEND OFF-PEAK",
        "zone_warning":"Tariff change in ~{seconds} s → {status}",
        "language":"Language", "timezone":"Timezone", "auto":"Automatic", "settings_title":"Settings", "appearance":"Appearance", "data":"Data", "actions":"Actions", "on_top":"Always on top",
        "weekdays":["Mon","Tue","Wed","Thu","Fri","Sat","Sun"],
        "api_401":"API key invalid or expired.",
        "api_403":"DeepSeek API access denied.",
        "api_429":"Too many API requests – please try again later.",
        "poll_interval":"Balance polling", "poll_interval_hint":"seconds (min. 20)", "apply":"Apply", "saved":"Saved ✓",
        "data_source":"Data source", "local_proxy":"Local (proxy/SQLite)", "influxdb":"InfluxDB (central)",
        "influx_url":"URL", "influx_token":"Token", "influx_org":"Organization", "influx_bucket":"Bucket",
    },
}
TRANSLATIONS = {code: dict(BASE_TRANSLATIONS["en"]) for code in LANGUAGES}
TRANSLATIONS["de"].update(BASE_TRANSLATIONS["de"])
TRANSLATIONS["de"]["costs"] = "Kosten & Verbrauch"
TRANSLATIONS["en"]["costs"] = "Cost & Usage"
TRANSLATIONS["fr"]["costs"] = "Coûts & usage"
TRANSLATIONS["es"]["costs"] = "Costes y consumo"
TRANSLATIONS["it"]["costs"] = "Costi e consumo"
TRANSLATIONS["pt"]["costs"] = "Custos e consumo"
TRANSLATIONS["nl"]["costs"] = "Kosten & verbruik"
TRANSLATIONS["pl"]["costs"] = "Koszty i zużycie"
TRANSLATIONS["ru"]["costs"] = "Расходы и потребление"
TRANSLATIONS["zh"]["costs"] = "成本与用量"
# Wochentage für die Realtime-Grafik
for _lang, _days in {
    "de": ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"],
    "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    "fr": ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"],
    "es": ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"],
    "it": ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"],
    "pt": ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"],
    "nl": ["Ma", "Di", "Wo", "Do", "Vr", "Za", "Zo"],
    "pl": ["Pn", "Wt", "Śr", "Cz", "Pt", "So", "Nd"],
    "ru": ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"],
    "zh": ["一", "二", "三", "四", "五", "六", "日"],
}.items():
    TRANSLATIONS[_lang]["weekdays"] = _days

# Die wichtigsten sichtbaren UI-Begriffe werden für die weiteren Hauptsprachen lokalisiert.
TRANSLATIONS["fr"].update({"costs":"Coûts & usage", "tariff":" Tarif ", "realtime":" Temps réel ", "analytics":" Analyse ", "refresh":"Actualiser", "api_key":"Clé API", "save":"Enregistrer", "cancel":"Annuler", "remove":"Supprimer", "language":"Langue"})
TRANSLATIONS["es"].update({"costs":"Costes y consumo", "tariff":" Tarifa ", "realtime":" Tiempo real ", "analytics":" Análisis ", "refresh":"Actualizar", "api_key":"Clave API", "save":"Guardar", "cancel":"Cancelar", "remove":"Eliminar", "language":"Idioma"})
TRANSLATIONS["it"].update({"costs":"Costi e consumo", "tariff":" Tariffa ", "realtime":" Realtime ", "analytics":" Analisi ", "refresh":"Aggiorna", "api_key":"Chiave API", "save":"Salva", "cancel":"Annulla", "remove":"Rimuovi", "language":"Lingua"})
TRANSLATIONS["pt"].update({"costs":"Custos e consumo", "tariff":" Tarifa ", "realtime":" Tempo real ", "analytics":" Análise ", "refresh":"Atualizar", "api_key":"Chave API", "save":"Guardar", "cancel":"Cancelar", "remove":"Remover", "language":"Idioma"})
TRANSLATIONS["nl"].update({"costs":"Kosten & verbruik", "tariff":" Tarief ", "realtime":" Realtime ", "analytics":" Analyse ", "refresh":"Vernieuwen", "api_key":"API-sleutel", "save":"Opslaan", "cancel":"Annuleren", "remove":"Verwijderen", "language":"Taal"})
TRANSLATIONS["pl"].update({"costs":"Koszty i zużycie", "tariff":" Taryfa ", "realtime":" Na żywo ", "analytics":" Analiza ", "refresh":"Odśwież", "api_key":"Klucz API", "save":"Zapisz", "cancel":"Anuluj", "remove":"Usuń", "language":"Język"})
TRANSLATIONS["ru"].update({"costs":"Расходы и потребление", "tariff":" Тариф ", "realtime":" Баланс ", "analytics":" Аналитика ", "refresh":"Обновить", "api_key":"API-ключ", "save":"Сохранить", "cancel":"Отмена", "remove":"Удалить", "language":"Язык"})
TRANSLATIONS["zh"].update({"tariff":" 费率 ", "realtime":" 实时 ", "analytics":" 分析 ", "costs":"成本与用量", "refresh":"刷新", "api_key":"API 密钥", "save":"保存", "cancel":"取消", "remove":"删除", "language":"语言"})

CURRENCY_SYMBOLS = {
    "USD":"$", "CNY":"¥", "EUR":"€", "GBP":"£", "JPY":"¥",
    "KRW":"₩", "CHF":"CHF ", "CAD":"C$", "AUD":"A$", "PLN":"zł ",
}

# ============================================================
# FARBEN
# ============================================================

COLOR_BG = "#121212"
COLOR_CARD = "#1a1a1a"
COLOR_CARD_BORDER = "#2d2d2d"

COLOR_STANDARD = "#2e7d32"
COLOR_PEAK = "#c62828"
COLOR_WEEKEND = "#f57c00"

COLOR_TEXT_MAIN = "#ffffff"
COLOR_TEXT_DIM = "#78909c"
COLOR_TICK_MINOR = "#404040"
COLOR_ACCENT = "#64b5f6"
COLOR_NEEDLE = "#ffffff"

COLOR_WARNING = "#ffb300"


STATUS_COLORS = {
    "STANDARD": COLOR_STANDARD,
    "PEAK": COLOR_PEAK,
    "WEEKEND": COLOR_WEEKEND,
}


# ============================================================
# XDG / DATEIEN
# ============================================================

def _xdg_base(env_var: str, default_path: str) -> str:
    value = os.environ.get(env_var, "").strip()
    return value if value else os.path.expanduser(default_path)


def _ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


CONFIG_DIR = _ensure_dir(
    os.path.join(_xdg_base("XDG_CONFIG_HOME", "~/.config"), "deepseek")
)
STATE_DIR = _ensure_dir(
    os.path.join(_xdg_base("XDG_STATE_HOME", "~/.local/state"), "deepseek")
)
DATA_DIR = _ensure_dir(
    os.path.join(_xdg_base("XDG_DATA_HOME", "~/.local/share"), "deepseek")
)

POS_FILE = os.path.join(CONFIG_DIR, "tarif_pos")
CONFIG_FILE = os.path.join(CONFIG_DIR, "tarif_config")
API_KEY_FILE = os.path.join(CONFIG_DIR, "api_key")
POLL_INTERVAL_FILE = os.path.join(CONFIG_DIR, "poll_interval")
INFLUX_CONFIG_FILE = os.path.join(CONFIG_DIR, "influx_config")

HISTORY_FILE = os.path.join(STATE_DIR, "balance_history.json")
DAILY_CSV_FILE = os.path.join(DATA_DIR, "daily_usage.csv")


LEGACY_FILES = {
    os.path.expanduser("~/.deepseek_tarif_pos"): POS_FILE,
    os.path.expanduser("~/.deepseek_tarif_config"): CONFIG_FILE,
    os.path.expanduser("~/.deepseek_balance_history.json"): HISTORY_FILE,
    os.path.expanduser("~/.deepseek_daily_usage.csv"): DAILY_CSV_FILE,
}


def _migrate_legacy_files() -> None:
    for src, dst in LEGACY_FILES.items():
        if os.path.exists(dst) or not os.path.exists(src):
            continue
        try:
            os.replace(src, dst)
        except OSError:
            pass


_migrate_legacy_files()


COMMON_TIMEZONES = [
    # UTC
    "UTC",
    # Americas
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Sao_Paulo",
    "America/Argentina/Buenos_Aires",
    # Europe
    "Europe/London",
    "Europe/Berlin",
    "Europe/Paris",
    "Europe/Madrid",
    "Europe/Rome",
    "Europe/Amsterdam",
    "Europe/Stockholm",
    "Europe/Warsaw",
    "Europe/Istanbul",
    "Europe/Moscow",
    # Asia
    "Asia/Dubai",
    "Asia/Kolkata",
    "Asia/Jakarta",
    "Asia/Shanghai",
    "Asia/Tokyo",
    "Asia/Seoul",
    "Asia/Singapore",
    "Asia/Bangkok",
    # Australia / Pacific
    "Australia/Sydney",
    "Australia/Melbourne",
    "Australia/Perth",
    "Pacific/Auckland",
    "Pacific/Fiji",
    # Africa
    "Africa/Cairo",
    "Africa/Johannesburg",
    "Africa/Lagos",
]

AVAILABLE_TIMEZONES = zoneinfo.available_timezones()


# ============================================================
# PERSISTENZ
# ============================================================

def load_timezone() -> str:
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            tz_name = f.read().strip()
        if tz_name in AVAILABLE_TIMEZONES:
            return tz_name
    except OSError:
        pass
    return DEFAULT_TZ


def save_timezone(tz_name: str) -> None:
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write(tz_name)
    except OSError:
        pass


def load_api_key() -> str:
    # Ein im Programm gespeicherter Key hat Vorrang; Environment bleibt Fallback.
    try:
        with open(API_KEY_FILE, "r", encoding="utf-8") as f:
            key = f.read().strip()
            if key:
                return key
    except OSError:
        pass

    return os.environ.get("DEEPSEEK_API_KEY", "").strip()


def save_api_key(key: str) -> None:
    key = key.strip()
    if not key:
        raise ValueError("API-Key darf nicht leer sein.")

    with open(API_KEY_FILE, "w", encoding="utf-8") as f:
        f.write(key + "\n")

    try:
        os.chmod(API_KEY_FILE, 0o600)
    except OSError:
        pass


def remove_saved_api_key() -> None:
    try:
        os.remove(API_KEY_FILE)
    except FileNotFoundError:
        pass


def load_poll_interval() -> int:
    try:
        with open(POLL_INTERVAL_FILE, "r", encoding="utf-8") as f:
            value = int(f.read().strip())
        return min(MAX_POLL_INTERVAL_S, max(MIN_POLL_INTERVAL_S, value))
    except (OSError, ValueError):
        return DEFAULT_POLL_INTERVAL_S


def save_poll_interval(seconds: int) -> None:
    seconds = min(MAX_POLL_INTERVAL_S, max(MIN_POLL_INTERVAL_S, int(seconds)))
    try:
        with open(POLL_INTERVAL_FILE, "w", encoding="utf-8") as f:
            f.write(str(seconds) + "\n")
    except OSError:
        pass


def load_influx_config() -> dict[str, str] | None:
    """Lädt die persistierte InfluxDB-Konfiguration (oder ``None``).

    Eine vorhandene Datei hat Vorrang vor den Env-Variablen, weil sie explizit
    über die GUI gesetzt wurde. Das Token wird nur lokal gespeichert (Datei
    ``0600``), nie im Code.
    """
    try:
        with open(INFLUX_CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {
                "url": str(data.get("url", "") or ""),
                "token": str(data.get("token", "") or ""),
                "org": str(data.get("org", "") or ""),
                "bucket": str(data.get("bucket", "") or ""),
            }
    except (OSError, json.JSONDecodeError):
        pass
    return None


def save_influx_config(cfg: dict[str, str]) -> None:
    """Persistiert die InfluxDB-Konfiguration (Datei ``0600``)."""
    try:
        with open(INFLUX_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, separators=(",", ":"))
        os.chmod(INFLUX_CONFIG_FILE, 0o600)
    except OSError:
        pass


def remove_influx_config() -> None:
    """Entfernt die persistierte InfluxDB-Konfiguration."""
    try:
        os.remove(INFLUX_CONFIG_FILE)
    except FileNotFoundError:
        pass


def _init_influx_config() -> None:
    """Initialisiert die Influx-Konfiguration: Datei > Env > deaktiviert."""
    cfg = load_influx_config()
    if cfg is not None:
        _apply_influx_config(
            cfg["url"],
            cfg["token"],
            cfg["org"],
            cfg["bucket"],
        )
    else:
        _apply_influx_config(
            os.environ.get("DEEPSEEK_INFLUX_URL", ""),
            os.environ.get("DEEPSEEK_INFLUX_TOKEN", ""),
            os.environ.get("DEEPSEEK_INFLUX_ORG", ""),
            os.environ.get("DEEPSEEK_INFLUX_BUCKET", ""),
        )


def load_history() -> list[dict[str, Any]]:
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError, TypeError):
        return []


def save_history(records: list[dict[str, Any]]) -> None:
    """
    Atomisches Schreiben, damit bei einem Programm-/Systemabsturz
    nicht die komplette History-Datei beschädigt wird.
    """
    trimmed = records[-MAX_HISTORY:]

    try:
        directory = os.path.dirname(HISTORY_FILE) or "."
        fd, tmp_name = tempfile.mkstemp(
            prefix=".balance_history.",
            dir=directory,
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(
                    trimmed,
                    f,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                f.write("\n")
            os.replace(tmp_name, HISTORY_FILE)
        finally:
            if os.path.exists(tmp_name):
                try:
                    os.remove(tmp_name)
                except OSError:
                    pass
    except OSError:
        pass


def write_daily_csv(rows: list[dict[str, Any]]) -> None:
    try:
        with open(
            DAILY_CSV_FILE,
            "w",
            newline="",
            encoding="utf-8",
        ) as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "date",
                    "consumption_total",
                    "peak_consumption",
                    "standard_consumption",
                    "weekend_consumption",
                    "avoidable_peak_cost",
                ]
            )
            for row in rows:
                writer.writerow(
                    [
                        row["date"],
                        row["usage"],
                        row["peak_usage"],
                        row["standard_usage"],
                        row["weekend_usage"],
                        row["avoidable_savings"],
                    ]
                )
    except OSError:
        pass


# Influx-Konfiguration beim Import initialisieren (Datei > Env > deaktiviert).
_init_influx_config()


# ============================================================
# API
# ============================================================

def fetch_balance(api_key: str) -> dict[str, Any]:
    request = urllib.request.Request(
        BALANCE_URL,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="GET",
    )

    with urllib.request.urlopen(request, timeout=15) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw)


def parse_balance(data: dict[str, Any]) -> dict[str, Any]:
    def as_float(obj: dict[str, Any], field: str) -> float:
        try:
            return float(obj.get(field, 0))
        except (TypeError, ValueError):
            return 0.0

    is_available = bool(data.get("is_available", True))

    infos = data.get("balance_infos") or []
    if infos and isinstance(infos[0], dict):
        info = infos[0]
        return {
            "total": as_float(info, "total_balance"),
            "topped": as_float(info, "topped_up_balance"),
            "granted": as_float(info, "granted_balance"),
            "available": is_available,
            "currency": str(info.get("currency", "") or ""),
        }

    return {
        "total": as_float(data, "total_balance"),
        "topped": as_float(data, "topped_up_balance"),
        "granted": as_float(data, "granted_balance"),
        "available": is_available,
        "currency": str(data.get("currency", "") or ""),
    }


def currency_symbol(currency: str) -> str:
    return CURRENCY_SYMBOLS.get(
        (currency or "").upper(),
        currency or "",
    )


# ============================================================
# TARIFLOGIK
# ============================================================

def status_at(now_utc: dt.datetime) -> str:
    """
    Liefert STANDARD, PEAK oder WEEKEND.
    Der Status ist allein aus UTC-Zeit + Beijing-Wochenende ableitbar.
    """
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=UTC)

    bj_now = now_utc.astimezone(BJ_TZ)

    if (
        now_utc >= WEEKEND_RULE_START_UTC
        and bj_now.weekday() >= 5
    ):
        return "WEEKEND"

    current_minute = now_utc.hour * 60 + now_utc.minute

    for start_minute, end_minute in PEAK_WINDOWS_UTC:
        if start_minute <= current_minute < end_minute:
            return "PEAK"

    return "STANDARD"


def _all_tariff_boundaries_around(
    now_utc: dt.datetime,
    days: int = 3,
) -> list[dt.datetime]:
    """
    Erstellt mögliche Tarifgrenzen in einem kleinen Fenster.
    Diese Liste ist klein und wird nicht sekündlich vollständig analysiert.
    """
    boundaries: set[dt.datetime] = set()

    start_date = now_utc.date() - dt.timedelta(days=days)
    end_date = now_utc.date() + dt.timedelta(days=days)

    cursor = start_date
    while cursor <= end_date:
        for hour in (1, 4, 6, 10):
            boundaries.add(
                dt.datetime(
                    cursor.year,
                    cursor.month,
                    cursor.day,
                    hour,
                    tzinfo=UTC,
                )
            )
        cursor += dt.timedelta(days=1)

    bj_date = now_utc.astimezone(BJ_TZ).date() - dt.timedelta(days=days)

    while bj_date <= now_utc.astimezone(BJ_TZ).date() + dt.timedelta(days=days):
        local_midnight = dt.datetime(
            bj_date.year,
            bj_date.month,
            bj_date.day,
            tzinfo=BJ_TZ,
        )
        boundaries.add(local_midnight.astimezone(UTC))
        bj_date += dt.timedelta(days=1)

    boundaries.add(WEEKEND_RULE_START_UTC)

    return sorted(boundaries)


def next_status_change(now_utc: dt.datetime) -> dt.datetime:
    """
    Sucht die nächste Grenze, an der sich der tatsächlich sichtbare Status
    ändert. Dadurch gibt es am Wochenende keine falschen Warnungen an Peak-Kanten,
    die durch WEEKEND ohnehin maskiert sind.
    """
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=UTC)

    before_delta = dt.timedelta(milliseconds=10)

    for boundary in _all_tariff_boundaries_around(now_utc):
        if boundary <= now_utc:
            continue

        before = status_at(boundary - before_delta)
        after = status_at(boundary + before_delta)

        if before != after:
            return boundary

    return now_utc + dt.timedelta(days=3)


def get_rate_periods(
    start_utc: dt.datetime,
    end_utc: dt.datetime,
) -> list[tuple[dt.datetime, dt.datetime, str]]:
    """
    Lückenlose Tarifperioden innerhalb [start_utc, end_utc].
    Wird primär von Analytics benutzt, nicht vom 1-Sekunden-GUI-Loop.
    """
    if start_utc.tzinfo is None:
        start_utc = start_utc.replace(tzinfo=UTC)
    if end_utc.tzinfo is None:
        end_utc = end_utc.replace(tzinfo=UTC)

    if end_utc <= start_utc:
        return []

    boundaries = {
        start_utc,
        end_utc,
    }

    for boundary in _all_tariff_boundaries_around(start_utc, days=2):
        if start_utc < boundary < end_utc:
            boundaries.add(boundary)

    points = sorted(boundaries)
    periods: list[tuple[dt.datetime, dt.datetime, str]] = []

    for a, b in zip(points, points[1:]):
        if b <= a:
            continue

        mid = a + (b - a) / 2
        periods.append((a, b, status_at(mid)))

    if not periods:
        return [(start_utc, end_utc, status_at(start_utc))]

    merged: list[tuple[dt.datetime, dt.datetime, str]] = []
    for a, b, status in periods:
        if merged and merged[-1][2] == status:
            merged[-1] = (merged[-1][0], b, status)
        else:
            merged.append((a, b, status))

    return merged


def weekend_segments_utc(
    day_start_utc: dt.datetime,
    day_end_utc: dt.datetime,
) -> list[tuple[dt.datetime, dt.datetime]]:
    return [
        (a, b)
        for a, b, status in get_rate_periods(
            day_start_utc,
            day_end_utc,
        )
        if status == "WEEKEND"
    ]


def calculate_status(
    now_utc: dt.datetime,
) -> tuple[str, float, str, str]:
    key = status_at(now_utc)
    next_change = next_status_change(now_utc)
    seconds_left = max(
        0.0,
        (next_change - now_utc).total_seconds(),
    )

    text = (
        "WOCHENENDE OFF-PEAK"
        if key == "WEEKEND"
        else key
    )

    return text, seconds_left, STATUS_COLORS[key], key


# ============================================================
# ANALYTICS
# ============================================================

def _parse_history(
    records: list[dict[str, Any]],
) -> list[tuple[dt.datetime, float, str]]:
    parsed: list[tuple[dt.datetime, float, str]] = []

    for record in records:
        try:
            timestamp = dt.datetime.fromisoformat(record["ts"])
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)

            total = float(record.get("total", 0.0))
            currency = str(record.get("currency", "") or "")

            parsed.append(
                (
                    timestamp.astimezone(UTC),
                    total,
                    currency,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue

    parsed.sort(key=lambda item: item[0])
    return parsed


def _split_at_local_midnights(
    start_utc: dt.datetime,
    end_utc: dt.datetime,
    tz: zoneinfo.ZoneInfo,
) -> list[tuple[dt.datetime, dt.datetime]]:
    """Teilt ein Intervall zusätzlich an lokalen Mitternächten."""
    if end_utc <= start_utc:
        return []

    boundaries = {start_utc, end_utc}

    local_start = start_utc.astimezone(tz).date() - dt.timedelta(days=1)
    local_end = end_utc.astimezone(tz).date() + dt.timedelta(days=1)

    cursor = local_start
    while cursor <= local_end:
        local_midnight = dt.datetime(
            cursor.year,
            cursor.month,
            cursor.day,
            tzinfo=tz,
        )
        boundary_utc = local_midnight.astimezone(UTC)

        if start_utc < boundary_utc < end_utc:
            boundaries.add(boundary_utc)

        cursor += dt.timedelta(days=1)

    points = sorted(boundaries)
    return list(zip(points, points[1:]))


def analyze_history(
    records: list[dict[str, Any]],
    tz_name: str,
    days: int = ANALYTICS_DAYS,
    max_gap_s: float | None = None,
) -> dict[str, Any]:
    """
    Analysiert Verbrauch als Balance-Verlust.

    Annahme:
    Ein Verbrauch zwischen zwei Messungen wird zeitanteilig auf die
    Tarifintervalle zwischen den Messpunkten verteilt.
    """
    tz = zoneinfo.ZoneInfo(tz_name)
    max_gap_s = float(max_gap_s) if max_gap_s is not None else MAX_ANALYTICS_GAP_S
    now_utc = dt.datetime.now(UTC)
    start_utc = now_utc - dt.timedelta(days=days)

    parsed = _parse_history(records)

    daily: OrderedDict[str, dict[str, float]] = OrderedDict()
    intervals: list[dict[str, Any]] = []

    total_usage = 0.0
    peak_usage = 0.0
    standard_usage = 0.0
    weekend_usage = 0.0

    currency = ""

    for prev, curr in zip(parsed, parsed[1:]):
        prev_ts, prev_total, prev_currency = prev
        curr_ts, curr_total, curr_currency = curr

        if curr_currency:
            currency = curr_currency

        if curr_ts <= prev_ts:
            continue

        raw_usage = prev_total - curr_total

        # Aufladungen / positive Kontobewegungen nicht als Verbrauch werten.
        if raw_usage <= 0:
            continue

        interval_start = max(prev_ts, start_utc)
        interval_end = min(curr_ts, now_utc)

        if interval_end <= interval_start:
            continue

        raw_duration_s = (
            curr_ts - prev_ts
        ).total_seconds()

        clipped_duration_s = (
            interval_end - interval_start
        ).total_seconds()

        if raw_duration_s <= 0:
            continue

        # Anteiliger Verbrauch nur für den sichtbaren Analytics-Zeitraum.
        visible_usage = (
            raw_usage
            * clipped_duration_s
            / raw_duration_s
        )

        if visible_usage <= 0:
            continue

        segments = get_rate_periods(
            interval_start,
            interval_end,
        )

        segment_total_s = sum(
            (b - a).total_seconds()
            for a, b, _ in segments
        )

        if segment_total_s <= 0:
            continue

        interval_rows: list[dict[str, Any]] = []

        for seg_start, seg_end, status in segments:
            for day_start, day_end in _split_at_local_midnights(
                seg_start,
                seg_end,
                tz,
            ):
                duration_s = (
                    day_end - day_start
                ).total_seconds()

                amount = (
                    visible_usage
                    * duration_s
                    / segment_total_s
                )

                local_start = day_start.astimezone(tz)
                local_end = day_end.astimezone(tz)

                day_key = local_start.date().isoformat()

                row = daily.setdefault(
                    day_key,
                    {
                        "usage": 0.0,
                        "peak_usage": 0.0,
                        "standard_usage": 0.0,
                        "weekend_usage": 0.0,
                        "avoidable_savings": 0.0,
                    },
                )

                row["usage"] += amount

                if status == "PEAK":
                    peak_usage += amount
                    row["peak_usage"] += amount

                    # Balanceverlust = tatsächliche Peak-Kosten.
                    # Bei 2x wäre das vermeidbare Premium 50 % davon.
                    savings = (
                        amount
                        * (1.0 - 1.0 / PEAK_MULTIPLIER)
                        if PEAK_MULTIPLIER > 0
                        else 0.0
                    )

                    row["avoidable_savings"] += savings
                elif status == "WEEKEND":
                    weekend_usage += amount
                    row["weekend_usage"] += amount
                else:
                    standard_usage += amount
                    row["standard_usage"] += amount

                interval_rows.append(
                {
                    "start": local_start,
                    "end": local_end,
                    "status": status,
                    "amount": amount,
                }
            )

        gap = raw_duration_s > max_gap_s

        # Für die Detailansicht nur echte sichtbare Verbrauchsintervalle.
        if interval_rows:
            intervals.append(
                {
                    "start": interval_rows[0]["start"],
                    "end": interval_rows[-1]["end"],
                    "duration_s": clipped_duration_s,
                    "amount": visible_usage,
                    "gap": gap,
                    "parts": interval_rows,
                }
            )

        total_usage += visible_usage

    daily_rows: list[dict[str, Any]] = []

    for date_key, row in daily.items():
        daily_rows.append(
            {
                "date": date_key,
                "usage": round(row["usage"], 6),
                "peak_usage": round(row["peak_usage"], 6),
                "standard_usage": round(row["standard_usage"], 6),
                "weekend_usage": round(row["weekend_usage"], 6),
                "avoidable_savings": round(
                    row["avoidable_savings"],
                    6,
                ),
            }
        )

    # Lückenlose Tagesreihe: Auch Tage ohne Verbrauch bekommen einen
    # leeren Eintrag (usage = 0), damit sie in den Charts als leeres
    # Fach mit Datum erscheinen.
    rows_by_date = {
        row["date"]: row
        for row in daily_rows
    }

    local_today = now_utc.astimezone(tz).date()
    daily_rows = []
    for offset in range(days, -1, -1):
        day_key = (
            local_today
            - dt.timedelta(days=offset)
        ).isoformat()

        row = rows_by_date.get(day_key)
        if row is None:
            row = {
                "date": day_key,
                "usage": 0.0,
                "peak_usage": 0.0,
                "standard_usage": 0.0,
                "weekend_usage": 0.0,
                "avoidable_savings": 0.0,
            }
        daily_rows.append(row)

    avoidable_savings = (
        peak_usage
        * (1.0 - 1.0 / PEAK_MULTIPLIER)
        if PEAK_MULTIPLIER > 0
        else 0.0
    )

    peak_share = (
        peak_usage / total_usage
        if total_usage > 0
        else 0.0
    )

    intervals.sort(
        key=lambda item: item["end"],
        reverse=True,
    )

    return {
        "days": days,
        "currency": currency,
        "usage": round(total_usage, 6),
        "peak_usage": round(peak_usage, 6),
        "standard_usage": round(standard_usage, 6),
        "weekend_usage": round(weekend_usage, 6),
        "peak_share": peak_share,
        "avoidable_savings": round(
            avoidable_savings,
            6,
        ),
        "daily": daily_rows,
        "intervals": intervals[:ANALYTICS_INTERVAL_LIMIT],
    }


def fetch_proxy_usage(days: int = ANALYTICS_DAYS) -> list[dict[str, Any]]:
    """Ruft die real aufgezeichneten Usage-Events des Proxys ab.

    Der Proxy persistiert pro Request einen Usage-Eintrag und stellt ihn unter
    ``GET /api/usage`` bereit. Der ``start``-Parameter wird in Europe/Berlin
    formatiert, weil der Storage-Zeitstempelvergleich auf Berlin-ISO-Strings
    läuft (gleiches Format wie die Datenbank).
    """
    berlin = zoneinfo.ZoneInfo("Europe/Berlin")
    start = (
        dt.datetime.now(berlin)
        - dt.timedelta(days=max(0, int(days)))
    ).isoformat(timespec="seconds")

    url = f"{PROXY_USAGE_URL}?start={urllib.parse.quote(start)}"

    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json"},
        method="GET",
    )

    with urllib.request.urlopen(request, timeout=10) as response:
        raw = response.read().decode("utf-8")
        data = json.loads(raw)

    if not isinstance(data, list):
        return []

    return [item for item in data if isinstance(item, dict)]


def _parse_flux_annotated_csv(raw: str) -> list[dict[str, str]]:
    """Parst das annotated-CSV-Antwortformat der InfluxDB-2.x-Query-API.

    Kommentarzeilen (``#``) werden übersprungen; die erste reguläre Zeile ist
    der Header. Jede Datenzeile wird zu einem Dict ``Spalte -> Wert``.
    """
    rows: list[dict[str, str]] = []
    header: list[str] | None = None

    for line in raw.splitlines():
        if not line:
            continue
        if line.startswith("#"):
            continue
        fields = next(csv.reader([line]))
        if header is None:
            header = fields
            continue
        if len(fields) != len(header):
            continue
        rows.append(dict(zip(header, fields)))

    return rows


def _flux_row_to_event(row: dict[str, str]) -> dict[str, Any] | None:
    """Mappt eine pivotierte Flux-Zeile auf ein Event-Dict analog zu ``/api/usage``.

    Das Format entspricht der Proxy-/api/usage-Struktur, damit
    :func:`analyze_proxy_usage` unverändert weiterverwendet werden kann.
    """
    def as_int(key: str) -> int:
        try:
            return int(float(row.get(key) or 0.0))
        except (TypeError, ValueError):
            return 0

    def as_float(key: str) -> float:
        try:
            return float(row.get(key) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    timestamp = row.get("_time", "")
    if timestamp.endswith("Z"):
        timestamp = timestamp[:-1] + "+00:00"

    return {
        "timestamp": timestamp,
        "model": row.get("model", "") or "unknown",
        "tariff": row.get("tariff", "") or "off_peak",
        "fingerprint": row.get("fingerprint", "") or "",
        "host": row.get("host", "") or "",
        "input_tokens": as_int("input_tokens"),
        "output_tokens": as_int("output_tokens"),
        "cache_read_tokens": as_int("cache_read_tokens"),
        "cache_creation_tokens": as_int("cache_creation_tokens"),
        "total_tokens": as_int("total_tokens"),
        "cost_usd": as_float("cost_usd"),
    }


def _influx_query(flux: str) -> list[dict[str, str]]:
    """Führt eine Flux-Query gegen die InfluxDB-2.x-Query-API aus.

    Liefert die geparsten annotated-CSV-Zeilen als Liste von Dicts.
    """
    url = f"{INFLUX_URL}/api/v2/query?org={urllib.parse.quote(INFLUX_ORG)}"

    body = json.dumps(
        {
            "query": flux,
            "dialect": {
                "header": True,
                "delimiter": ",",
                "annotations": ["datatype"],
            },
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Accept": "application/csv",
            "Content-Type": "application/json",
            "Authorization": f"Token {INFLUX_TOKEN}",
        },
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=15) as response:
        raw = response.read().decode("utf-8")

    return _parse_flux_annotated_csv(raw)


def fetch_influx_usage(
    days: int = ANALYTICS_DAYS,
    host: str | None = None,
) -> list[dict[str, Any]]:
    """Liest die Usage-Events direkt aus der zentralen InfluxDB 2.x.

    Nutzt die HTTP-Query-API (``POST /api/v2/query``) mit Flux und ``pivot``,
    damit jede Event-Zeile als eine Zeile zurückkommt. Keine zusätzliche
    Dependency -- reines ``urllib`` + stdlib-CSV-Parsing.

    Parameter:
        host: Optionaler Filter auf den ``host``-Tag (leer/None = alle Hosts).

    Liefert eine leere Liste, wenn InfluxDB nicht konfiguriert ist
    (``DEEPSEEK_INFLUX_*`` fehlt).
    """
    if not INFLUX_ENABLED:
        return []

    query = (
        f'from(bucket: "{INFLUX_BUCKET}")'
        f"  |> range(start: -{max(0, int(days))}d)"
        '  |> filter(fn: (r) => r._measurement == "usage")'
    )
    if host:
        query += f'\n  |> filter(fn: (r) => r.host == "{host}")'
    query += '  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")'

    rows = _influx_query(query)

    events: list[dict[str, Any]] = []
    for row in rows:
        event = _flux_row_to_event(row)
        if event:
            events.append(event)

    return events


def fetch_influx_hosts() -> list[str]:
    """Liefert alle bisher gesehenen ``host``-Tag-Werte aus InfluxDB."""
    if not INFLUX_ENABLED:
        return []

    flux = (
        'import "influxdata/influxdb/schema"'
        f'\nschema.tagValues(bucket: "{INFLUX_BUCKET}", tag: "host")'
    )

    rows = _influx_query(flux)

    hosts: list[str] = []
    for row in rows:
        value = (row.get("_value") or "").strip()
        if value and value not in hosts:
            hosts.append(value)

    return sorted(hosts)


def fetch_usage(
    days: int = ANALYTICS_DAYS,
    host: str | None = None,
) -> list[dict[str, Any]]:
    """Dispatcher: InfluxDB (falls konfiguriert) sonst lokaler Proxy ``/api/usage``."""
    if INFLUX_ENABLED:
        return fetch_influx_usage(days, host=host)
    return fetch_proxy_usage(days)


def analyze_proxy_usage(
    events: list[dict[str, Any]],
    tz_name: str,
    days: int = ANALYTICS_DAYS,
) -> dict[str, Any]:
    """Aggregiert echte Usage-Events des Proxys zu derselben Struktur wie
    :func:`analyze_history`, damit die bestehende Darstellung unverändert
    weiterverwendet werden kann.

    Im Gegensatz zur Guthaben-Delta-Schätzung wird hier pro Request die real
    berechneten Kosten (``cost_usd``) herangezogen. Die Zone wird pro Event
    über die vorhandene :func:`status_at` bestimmt (PEAK/STANDARD/WEEKEND).
    """
    tz = zoneinfo.ZoneInfo(tz_name)
    now_utc = dt.datetime.now(UTC)
    start_utc = now_utc - dt.timedelta(days=days)

    daily: OrderedDict[str, dict[str, float]] = OrderedDict()
    intervals: list[dict[str, Any]] = []

    total_usage = 0.0
    peak_usage = 0.0
    standard_usage = 0.0
    weekend_usage = 0.0

    for event in events:
        try:
            timestamp = dt.datetime.fromisoformat(
                str(event.get("timestamp") or "")
            )
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            ts_utc = timestamp.astimezone(UTC)
        except (TypeError, ValueError):
            continue

        # Exakt im sichtbaren Fenster filtern (schützt vor DST-Unschärfe des
        # reinen String-Vergleichs im Storage).
        if not (start_utc <= ts_utc <= now_utc):
            continue

        try:
            amount = float(event.get("cost_usd") or 0.0)
        except (TypeError, ValueError):
            amount = 0.0

        if amount <= 0:
            continue

        status = status_at(ts_utc)
        local = ts_utc.astimezone(tz)
        day_key = local.date().isoformat()

        row = daily.setdefault(
            day_key,
            {
                "usage": 0.0,
                "peak_usage": 0.0,
                "standard_usage": 0.0,
                "weekend_usage": 0.0,
                "avoidable_savings": 0.0,
            },
        )

        row["usage"] += amount

        if status == "PEAK":
            peak_usage += amount
            row["peak_usage"] += amount
            savings = (
                amount * (1.0 - 1.0 / PEAK_MULTIPLIER)
                if PEAK_MULTIPLIER > 0
                else 0.0
            )
            row["avoidable_savings"] += savings
        elif status == "WEEKEND":
            weekend_usage += amount
            row["weekend_usage"] += amount
        else:
            standard_usage += amount
            row["standard_usage"] += amount

        total_usage += amount

        intervals.append(
            {
                "start": local,
                "end": local,
                "duration_s": 0.0,
                "amount": amount,
                "gap": False,
                "parts": [
                    {
                        "start": local,
                        "end": local,
                        "status": status,
                        "amount": amount,
                    }
                ],
            }
        )

    daily_rows: list[dict[str, Any]] = []

    for date_key, row in daily.items():
        daily_rows.append(
            {
                "date": date_key,
                "usage": round(row["usage"], 6),
                "peak_usage": round(row["peak_usage"], 6),
                "standard_usage": round(row["standard_usage"], 6),
                "weekend_usage": round(row["weekend_usage"], 6),
                "avoidable_savings": round(row["avoidable_savings"], 6),
            }
        )

    rows_by_date = {row["date"]: row for row in daily_rows}

    local_today = now_utc.astimezone(tz).date()
    daily_rows = []
    for offset in range(days, -1, -1):
        day_key = (local_today - dt.timedelta(days=offset)).isoformat()
        row = rows_by_date.get(day_key)
        if row is None:
            row = {
                "date": day_key,
                "usage": 0.0,
                "peak_usage": 0.0,
                "standard_usage": 0.0,
                "weekend_usage": 0.0,
                "avoidable_savings": 0.0,
            }
        daily_rows.append(row)

    avoidable_savings = (
        peak_usage * (1.0 - 1.0 / PEAK_MULTIPLIER)
        if PEAK_MULTIPLIER > 0
        else 0.0
    )

    peak_share = (
        peak_usage / total_usage
        if total_usage > 0
        else 0.0
    )

    intervals.sort(key=lambda item: item["end"], reverse=True)

    return {
        "days": days,
        "currency": "USD",
        "usage": round(total_usage, 6),
        "peak_usage": round(peak_usage, 6),
        "standard_usage": round(standard_usage, 6),
        "weekend_usage": round(weekend_usage, 6),
        "peak_share": peak_share,
        "avoidable_savings": round(avoidable_savings, 6),
        "daily": daily_rows,
        "intervals": intervals[:ANALYTICS_INTERVAL_LIMIT],
    }


def format_money(
    value: float,
    currency: str,
    language: str = "en",
    decimals: int = 4,
) -> str:
    symbol = currency_symbol(currency)

    if language in {"de", "fr", "es", "it", "pt", "nl", "pl", "ru"}:
        number = (
            f"{value:,.{decimals}f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )
    else:
        number = f"{value:,.{decimals}f}"

    return f"{symbol}{number}".strip() if symbol else number


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
        self._language_auto = True
        self.language = detect_language()
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
    # Basis / Fenster
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
    # Tarif-Tab
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
            fg=COLOR_TEXT_MAIN,
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
            text="Wochenende:",
            font=("Segoe UI", 7, "bold"),
            fg=COLOR_WEEKEND,
            bg=COLOR_BG,
        ).pack(side=tk.LEFT)

        tk.Label(
            weekend_frame,
            text=" Sa 00:00 – Mo 00:00 (Peking-Zeit)",
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
    # Realtime-Tab
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
            text="Tagesauswertung (14 Tage)",
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
            (COLOR_STANDARD, "Normal"),
            (COLOR_WEEKEND, "Wochenende"),
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
    # Analytics-Tab
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
            ("Gesamt", "total"),
            ("Peak", "peak"),
            ("Normal", "standard"),
            ("Wochenende", "weekend"),
            ("Pot. Ersparnis", "savings"),
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
            "date": "Datum",
            "usage": "Gesamt",
            "peak": "Peak",
            "standard": "Normal",
            "weekend": "WE",
            "saving": "Ersparnis",
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
            "from": "Von",
            "to": "Bis",
            "status": "Zone",
            "amount": "Verbrauch",
            "note": "Hinweis",
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
    # Sprache / API-Key
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
        if hasattr(self, "lang_btn"):
            self.lang_btn.config(text=language.upper())
        self._retranslate_ui()
        self._timeline_minute_key = None

    def _reset_language_auto(self) -> None:
        self._language_auto = True
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
    # Zeitzone
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
                    "Kein API-Key: DEEPSEEK_API_KEY setzen "
                    "oder ~/.config/deepseek/api_key anlegen"
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

                # Persistenz bewusst außerhalb des Locks.
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
                        "API-Key ungültig oder abgelaufen."
                    )
                elif exc.code == 403:
                    message = (
                        "Zugriff auf die DeepSeek API verweigert."
                    )
                elif exc.code == 429:
                    message = (
                        "Zu viele API-Anfragen – bitte später erneut versuchen."
                    )
                else:
                    message = (
                        f"DeepSeek API HTTP-Fehler {exc.code}"
                    )

                self._set_balance_error(message)

            except urllib.error.URLError as exc:
                self._set_balance_error(
                    f"Netzwerkfehler: {exc.reason}"
                )

            except Exception as exc:
                self._set_balance_error(
                    f"API-Fehler: {exc}"
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
    # Analytics-State
    # --------------------------------------------------------

    def _refresh_analytics_state(self) -> None:
        """Lädt die echten Usage-Daten des Proxys (nicht-blockierend) und
        aktualisiert daraus die Analytics-Struktur.

        Bei einem Fehler (Proxy nicht erreichbar, keine Daten) wird die
        Analytics-Struktur geleert und ein Hinweis gesetzt -- es wird bewusst
        NICHT auf die alte Guthaben-Delta-Schätzung zurückgefallen.
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
                source = "InfluxDB" if INFLUX_ENABLED else "Proxy"
                error_message = f"Keine {source}-Daten"
        except urllib.error.HTTPError as exc:
            error_message = f"Datenquelle HTTP-Fehler {exc.code}"
        except urllib.error.URLError as exc:
            error_message = f"Datenquelle nicht erreichbar ({exc.reason})"
        except Exception as exc:
            error_message = f"Datenquellen-Fehler: {exc}"

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
                    "Fehler"
                    if error
                    else "Verbinde mit DeepSeek API…"
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
                    "Stand: "
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
                    "Noch keine Daten – "
                    "die Auswertung baut sich mit jeder Abfrage auf."
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

            # Gestapelte Segmente:
            # unten Normal (grün), Mitte Wochenende (orange),
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
                f"Peak-Anteil am Verbrauch: "
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
                    "Schätzung bei größerer Messlücke"
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
    # Akustische Warnung
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

            # Tkinter bell() ist plattformübergreifend die einfachste
            # Lösung und nutzt das System-/Fensterglockensignal.
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

            text = (
                f"Tarifwechsel in ~{int(seconds_left)} s → "
                f"{'WOCHENENDE OFF-PEAK' if new_status == 'WEEKEND' else new_status}"
            )

            self.lbl_warning.config(
                text=text
            )

        elif seconds_left > WARNING_BEFORE_S + 2:
            self.lbl_warning.config(
                text=""
            )

    # --------------------------------------------------------
    # Legende
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
    # Haupt-Loop
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
            "WOCHENENDE OFF-PEAK": self.t("weekend_status"),
        }.get(status_text, status_text)

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

        # Teures Canvas-Redraw nur bei Minute/Zeitzone/Tag-Wechsel.
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
    Neue Oberfläche:
      - Root = minimalistisches Tarif-Widget
      - Kosten & Verbrauch = ein gemeinsames, frei skalierbares Toplevel-Fenster
      - Tarif bleibt ein minimalistisches Hauptfenster
      - Fensterpositionen/-größen separat gespeichert
      - Tarif-Fenster kann optional always-on-top sein

    Die vorhandene Tarif-, Balance- und Analytics-Logik aus der Legacy-Klasse
    wird wiederverwendet; nur die GUI-Schicht ist neu organisiert.
    """

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_NAME)
        self.root.configure(bg=COLOR_BG)
        self.root.resizable(False, False)
        self._set_app_icon()

        self.tz_name = load_timezone()
        self.tz = zoneinfo.ZoneInfo(self.tz_name)
        self.language = detect_language()

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
        """Setzt das DeepSeek-Logo als Fenster-/Taskleisten-Icon."""
        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            png_path = os.path.join(base_dir, "deepseek_logo.png")
            ico_path = os.path.join(base_dir, "deepseek_logo.ico")

            if os.path.exists(png_path):
                self._icon_image = tk.PhotoImage(file=png_path)
                self.root.iconphoto(True, self._icon_image)

            if os.name == "nt" and os.path.exists(ico_path):
                try:
                    self.root.iconbitmap(ico_path)
                except tk.TclError:
                    pass
        except (OSError, tk.TclError):
            pass

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
        """Lädt nur X/Y aus settings_pos; Breite/Höhe werden ignoriert."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                value = f.read().strip()
            if "+" in value:
                # Alte Form: 320x265+X+Y -> nur X/Y übernehmen.
                _, x, y = value.rsplit("+", 2)
                return int(x), int(y)
            if "," in value:
                # Neue Form: X,Y
                x, y = value.split(",", 1)
                return int(x), int(y)
        except (OSError, ValueError):
            pass
        return None

    def _save_window_position(self, window: tk.Toplevel | tk.Tk, path: str) -> None:
        """Speichert bei positionsgebundenen Fenstern ausschließlich X/Y."""
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
    # Mini-Tarif-Fenster
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

        # Farbband für jede lokale Stunde.
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

        # Uhrzeit-Strahlen: jede Stunde ein Tick; alle 6 Stunden länger.
        # Die sichtbaren Beschriftungen bleiben kompakt und stehen direkt darunter.
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

        # Aktueller Zeitpunkt: dezentes kleines gelbes Dreieck.
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
    # Fenster-Toggle
    # --------------------------------------------------------

    def _sync_window_buttons(self) -> None:
        cost_open = self._cost_window is not None
        self.btn_cost.config(
            text="×" if cost_open else "C",
            bg=COLOR_ACCENT if cost_open else COLOR_CARD,
            fg="#000000" if cost_open else COLOR_TEXT_MAIN,
        )

    # --------------------------------------------------------
    # Kosten & Verbrauch
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

        # Host-Filter (nur bei aktivem InfluxDB-Modus sinnvoll).
        if INFLUX_ENABLED:
            host_lbl = tk.Label(
                header, text="Host:",
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
            ("Gesamt", "total"),
            ("Peak", "peak"),
            ("Normal", "standard"),
            ("Wochenende", "weekend"),
            ("Pot. Ersparnis", "savings"),
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

        # Kompakte Legende unter dem Graphen (minimale Höhe, zentriert).
        legend = tk.Frame(win, bg=COLOR_BG)
        legend.pack(fill=tk.X, padx=15, pady=(0, 1))
        legend_inner = tk.Frame(legend, bg=COLOR_BG)
        legend_inner.pack(anchor=tk.CENTER)
        for legend_color, legend_label in (
            (COLOR_PEAK, "Peak"),
            (COLOR_STANDARD, "Normal"),
            (COLOR_WEEKEND, "Wochenende"),
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
            ("from", "Von", 145), ("to", "Bis", 145),
            ("status", "Zone", 110), ("amount", "Verbrauch", 110),
            ("note", "Hinweis", 240),
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
        """Befüllt das Host-Filter-Dropdown (nur bei aktivem InfluxDB-Modus)."""
        combo = getattr(self, "_host_combo", None)
        if combo is None or not INFLUX_ENABLED:
            return

        try:
            hosts = fetch_influx_hosts()
        except Exception:
            hosts = []

        values = ["Alle Hosts"] + hosts
        current = self._host_filter if self._host_filter in hosts else "Alle Hosts"
        combo["values"] = values
        combo.set(current)

    def _on_host_filter_selected(self, _event: object | None = None) -> None:
        """Reagiert auf die Host-Auswahl und lädt die Daten neu."""
        combo = getattr(self, "_host_combo", None)
        if combo is None:
            return

        value = combo.get().strip()
        if value in ("", "Alle Hosts"):
            self._host_filter = ""
        else:
            self._host_filter = value

        # Daten mit neuem Filter neu laden.
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

        # Analytics-Gap-Erkennung ebenfalls sofort anpassen.
        global MAX_ANALYTICS_GAP_S
        MAX_ANALYTICS_GAP_S = seconds * 2.5
        self._refresh_analytics_state()

        # Ein laufendes Warten sofort auf das neue Intervall umstellen.
        self._poll_wakeup_event.set()

        if settings_window is not None:
            settings_window.focus_set()

        messagebox.showinfo(self.t("saved"), self.t("saved"))

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

        # Breite des Einstellungsfensters immer exakt wie das Hauptfenster.
        # settings_pos speichert ausschließlich die Position, niemals die Größe.
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
        # Position wird beim tatsächlichen Schließen gespeichert; kein Configure-Write nötig.

        # Kopfbereich
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

        # Gemeinsamer Stil für Eingabefelder/Buttons
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

        # Einheitliches 2-Spalten-Layout: Bezeichnung links, Auswahlfeld rechts.
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

        # ---- Datenquelle (Lokal/Proxy vs. InfluxDB) ----
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
            value=self.t("influxdb") if INFLUX_ENABLED else self.t("local_proxy")
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

        # Token mit verdeckter Anzeige.
        token_entry.config(show="•")

        url_entry.insert(0, INFLUX_URL)
        token_entry.insert(0, INFLUX_TOKEN)
        org_entry.insert(0, INFLUX_ORG)
        bucket_entry.insert(0, INFLUX_BUCKET)

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

            # Daten mit neuer Quelle neu laden.
            self._refresh_analytics_state()
            if self._cost_window is not None:
                self._cost_rendered_version = -1
                self._update_cost_window(force=True)

            messagebox.showinfo(self.t("saved"), self.t("saved"))

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
        self._retranslate_ui()
        button.set(f"{LANGUAGES[language]} ({language.upper()})")

    def _reset_language_from_settings(self, button: ttk.Combobox) -> None:
        self._language_auto = True
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
        # Rückwärtskompatibler Helfer: Geometrie sichern und Referenz bereinigen.
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
        Aktualisiert das große Kostenfenster nur bei Datenänderungen.
        Der 1-Sekunden-Tarifloop bleibt dadurch leichtgewichtig.
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
        # ``top`` reserviert eine eigene Kopfzeile für den Prozent-/Farbhinweis,
        # damit sich dieser nicht mit den Wertlabels der Balken überlagert.
        left, right, top, bottom = 30, 15, 36, 48
        plot_w = width - left - right
        plot_h = height - top - bottom
        max_value = max(float(r["usage"]) for r in data) or 1.0
        slot = plot_w / len(data)
        bar_w = max(5, slot * 0.62)
        base_y = top + plot_h

        canvas.create_line(left, base_y, width-right, base_y, fill=COLOR_CARD_BORDER)

        for i, row in enumerate(data):
            x = left + slot * i + slot / 2
            values = (
                (float(row["standard_usage"]), COLOR_STANDARD),
                (float(row["weekend_usage"]), COLOR_WEEKEND),
                (float(row["peak_usage"]), COLOR_PEAK),
            )
            cursor = base_y
            total_height = float(row["usage"]) / max_value * plot_h
            for value, color in values:
                segment = value / max_value * plot_h
                if segment <= 0:
                    continue
                canvas.create_rectangle(
                    x-bar_w/2, cursor-segment, x+bar_w/2, cursor,
                    fill=color, outline="",
                )
                cursor -= segment

            day = dt.date.fromisoformat(row["date"])
            weekdays = self.t("weekdays")
            weekday_label = weekdays[day.weekday()] if isinstance(weekdays, list) else day.strftime("%a")

            # Zweizeilige Datumsachse: Wochentag direkt über dem Datum.
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

        # Prozent-/Farbhinweis oben rechts im großen Analytics-Fenster.
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
            "WOCHENENDE OFF-PEAK": self.t("weekend_status"),
        }.get(status_text, status_text)

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
