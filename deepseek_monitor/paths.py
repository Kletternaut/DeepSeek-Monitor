"""DeepSeek Monitor — paths (XDG / files).

Determines the config/state/data directories according to the XDG Base
Directory spec, migrates legacy files from the home directory and provides the
timezone lists. No Tkinter dependency.

Public symbols:
    * :func:`_xdg_base`, :func:`_ensure_dir`
    * directory/file constants (``CONFIG_DIR``, ``POS_FILE`` …)
    * :func:`_migrate_legacy_files`
    * :data:`COMMON_TIMEZONES`, :data:`AVAILABLE_TIMEZONES`
"""

from __future__ import annotations

import os
import zoneinfo


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
LANGUAGE_FILE = os.path.join(CONFIG_DIR, "language")

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
