"""Cross-platform path resolution for the DeepSeek proxy.

On Linux/macOS the XDG Base Directory spec is used (``~/.config``,
``~/.local/state``). On Windows the native locations ``%APPDATA%`` and
``%LOCALAPPDATA%`` are used, so the config/API key and the state/DB land in
idiomatic locations.

The API-key path must stay identical to the one used by ``deepseek_monitor``
(``<config>/deepseek/api_key``), so the monitor and proxy share the same key.

Public symbols:
    * :func:`config_dir`, :func:`state_dir`, :func:`data_dir`
    * :data:`API_KEY_FILE`, :data:`DB_FILE`, :data:`CHECKPOINT_FILE`
"""

from __future__ import annotations

import os


def _is_windows() -> bool:
    return os.name == "nt"


def _env_or_fallback(env_var: str, fallback: str) -> str:
    value = os.environ.get(env_var, "").strip()
    return value if value else os.path.expanduser(fallback)


def config_dir() -> str:
    if _is_windows():
        return os.path.join(_env_or_fallback("APPDATA", "~/.config"), "deepseek")
    return os.path.join(_env_or_fallback("XDG_CONFIG_HOME", "~/.config"), "deepseek")


def state_dir() -> str:
    if _is_windows():
        return os.path.join(_env_or_fallback("LOCALAPPDATA", "~/.local/state"), "deepseek")
    return os.path.join(_env_or_fallback("XDG_STATE_HOME", "~/.local/state"), "deepseek")


def data_dir() -> str:
    if _is_windows():
        return os.path.join(_env_or_fallback("LOCALAPPDATA", "~/.local/share"), "deepseek")
    return os.path.join(_env_or_fallback("XDG_DATA_HOME", "~/.local/share"), "deepseek")


API_KEY_FILE = os.path.join(config_dir(), "api_key")
DB_FILE = os.path.join(state_dir(), "usage.db")
CHECKPOINT_FILE = os.path.join(state_dir(), "checkpoints.json")
