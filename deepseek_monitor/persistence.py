"""DeepSeek Monitor — persistence (timezone, language, key, poll, Influx, history).

Loads and saves all persistent settings (atomically, ``0600`` for sensitive
values). No Tkinter dependency.

Public symbols:
    * ``load_*`` / ``save_*`` / ``remove_*`` for timezone, language, api_key,
      poll_interval, influx_config
    * :func:`_init_influx_config`
    * :func:`load_history`, :func:`save_history`, :func:`write_daily_csv`
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
from typing import Any

from deepseek_monitor.config import (
    DEFAULT_POLL_INTERVAL_S,
    DEFAULT_TZ,
    MAX_HISTORY,
    MAX_POLL_INTERVAL_S,
    MIN_POLL_INTERVAL_S,
    _apply_influx_config,
)
from deepseek_monitor.i18n import LANGUAGES
from deepseek_monitor.paths import (
    API_KEY_FILE,
    AVAILABLE_TIMEZONES,
    CONFIG_FILE,
    DAILY_CSV_FILE,
    FIRST_RUN_FILE,
    HISTORY_FILE,
    INFLUX_CONFIG_FILE,
    LANGUAGE_FILE,
    POLL_INTERVAL_FILE,
)


def _chmod_private(path: str) -> None:
    """Restrict file permissions to the owner (``0600``).

    No-op on Windows, where ``os.chmod`` only toggles the read-only flag and
    per-owner permissions are not applicable.
    """
    if os.name == "nt":
        return
    os.chmod(path, 0o600)


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


def load_language() -> str | None:
    """Load the manually set language (or ``None`` if not set).

    A manual selection takes precedence over automatic system detection.
    """
    try:
        with open(LANGUAGE_FILE, "r", encoding="utf-8") as f:
            code = f.read().strip()
        if code in LANGUAGES:
            return code
    except OSError:
        pass
    return None


def save_language(code: str) -> None:
    try:
        with open(LANGUAGE_FILE, "w", encoding="utf-8") as f:
            f.write(code)
    except OSError:
        pass


def remove_language() -> None:
    try:
        os.remove(LANGUAGE_FILE)
    except FileNotFoundError:
        pass


def load_api_key() -> str:
    # A stored key takes precedence; the environment is the fallback.
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
        raise ValueError("API key must not be empty.")

    with open(API_KEY_FILE, "w", encoding="utf-8") as f:
        f.write(key + "\n")

    try:
        _chmod_private(API_KEY_FILE)
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


def load_first_run_done() -> bool:
    """Return ``True`` once the first-run notice has been acknowledged."""
    try:
        with open(FIRST_RUN_FILE, "r", encoding="utf-8") as f:
            return f.read().strip() != ""
    except OSError:
        return False


def mark_first_run_done() -> None:
    """Persist that the first-run notice has been acknowledged."""
    try:
        with open(FIRST_RUN_FILE, "w", encoding="utf-8") as f:
            f.write("done\n")
    except OSError:
        pass


def load_influx_config() -> dict[str, str] | None:
    """Load the persisted InfluxDB configuration (or ``None``).

    An existing file takes precedence over the environment variables, because
    it was set explicitly via the GUI. The token is stored locally only
    (``0600`` file), never in code.
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
    """Persist the InfluxDB configuration (``0600`` file)."""
    try:
        with open(INFLUX_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, separators=(",", ":"))
        _chmod_private(INFLUX_CONFIG_FILE)
    except OSError:
        pass


def remove_influx_config() -> None:
    """Remove the persisted InfluxDB configuration."""
    try:
        os.remove(INFLUX_CONFIG_FILE)
    except FileNotFoundError:
        pass


def _init_influx_config() -> None:
    """Initialize the Influx configuration: file > env > disabled."""
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
    Atomic write so a program/system crash does not corrupt the entire
    history file.
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


# Initialize the Influx configuration on import (file > env > disabled).
_init_influx_config()
