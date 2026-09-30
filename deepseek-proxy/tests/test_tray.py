"""Tests for the Windows system-tray integration (``proxy.tray``).

These tests only exercise the platform-independent, dependency-free parts of
the module: the lazy import guard and the version resolution. The actual tray
icon requires a Windows session and the optional ``pystray``/``Pillow``
packages, so it is not covered here.
"""

from __future__ import annotations

import sys
import threading

from proxy import tray


def test_tray_supported_returns_bool() -> None:
    assert isinstance(tray.tray_supported(), bool)


def test_tray_module_imports_pystray_lazily() -> None:
    # Importing proxy.tray must never import pystray/Pillow eagerly; they are
    # optional and only imported inside start_tray() on Windows.
    assert "pystray" not in sys.modules


def test_start_tray_returns_none_when_unsupported() -> None:
    # On non-Windows platforms (or without optional deps) start_tray must
    # return None instead of raising.
    if tray.tray_supported():
        import pytest

        pytest.skip("tray is supported on this platform")
    stop_event = threading.Event()
    result = tray.start_tray(
        port=8000,
        base_url="http://127.0.0.1:8000",
        stop_event=stop_event,
    )
    assert result is None


def test_app_version_returns_nonempty_string() -> None:
    version = tray._app_version()
    assert isinstance(version, str)
    assert version.strip()


def test_influx_dialog_module_imports_and_exposes_callable() -> None:
    # The dialog module must import without a display and expose the function.
    from proxy import influx_dialog

    assert callable(influx_dialog.open_influx_dialog)
