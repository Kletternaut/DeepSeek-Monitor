"""Asset path resolution and window-icon helper.

The app ships its icons as PNG files under the ``assets/`` directory. When
running from source, that directory sits next to this package; when running as
a PyInstaller onefile bundle, the files are extracted into ``sys._MEIPASS``.
This module resolves the correct base directory in both cases.

``tkinter`` is imported lazily inside :func:`set_window_icon`, so importing this
module does not require a display.

Public symbols:
    * :func:`asset_path`
    * :func:`set_window_icon`
"""

from __future__ import annotations

import os
import sys

__all__ = ["asset_path", "set_window_icon"]


def _base_dir() -> str:
    """Return the directory that contains the ``assets`` folder."""
    if getattr(sys, "frozen", False):
        # PyInstaller onefile: resources are extracted to ``_MEIPASS``.
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    # Source run: repo root = parent of the ``deepseek_monitor`` package dir.
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def asset_path(name: str) -> str:
    """Return the absolute path to ``assets/<name>``."""
    return os.path.join(_base_dir(), "assets", name)


def set_window_icon(root: object, name: str) -> None:
    """Set the window/taskbar icon of a Tk root/Toplevel from ``assets/<name>``.

    Best-effort: silently does nothing if the file is missing or Tk fails.
    """
    path = asset_path(name)
    if not os.path.exists(path):
        return

    import tkinter as tk

    try:
        image = tk.PhotoImage(file=path)
        root.iconphoto(True, image)  # type: ignore[attr-defined]
        # Keep a reference so the image is not garbage-collected.
        root._icon_image = image  # type: ignore[attr-defined]
    except (OSError, tk.TclError):
        pass
