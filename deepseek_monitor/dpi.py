"""Windows HiDPI helper for Tkinter apps.

Tkinter is not DPI-aware by default, so on high-DPI displays (e.g. 4K with
150%/200% scaling) Windows up-scales the window via bitmap stretching, which
makes it look blurry. Marking the process as DPI-aware makes Tkinter render
natively and the text size is then corrected via Tk's ``scaling`` option.

Both helpers are no-ops on non-Windows platforms, so Linux/macOS operation is
unaffected.

Public symbols:
    * :func:`enable_dpi_awareness`
    * :func:`apply_tk_scaling`
"""

from __future__ import annotations

import os

__all__ = ["enable_dpi_awareness", "apply_tk_scaling"]


def enable_dpi_awareness() -> None:
    """Make the process DPI-aware (Windows only, no-op elsewhere).

    Must be called **before** the first ``tk.Tk()``. Uses per-monitor V2
    awareness (best for mixed-DPI multi-monitor setups), falling back to the
    legacy system-wide call on older Windows versions.
    """
    if os.name != "nt":
        return

    import contextlib
    import ctypes

    try:
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        with contextlib.suppress(Exception):
            ctypes.windll.user32.SetProcessDPIAware()


def apply_tk_scaling(root: object) -> None:
    """Correct Tk's internal scaling to match the real DPI (Windows only).

    Call **after** the root window is created. ``tk scaling`` defaults to
    96 dpi (``96/72``); on a HiDPI display this would otherwise render text too
    small even with DPI-awareness enabled.
    """
    if os.name != "nt":
        return

    try:
        dpi = root.winfo_fpixels("1i")  # type: ignore[attr-defined]
        if dpi > 0:
            root.tk.call("tk", "scaling", dpi / 72.0)  # type: ignore[attr-defined]
    except Exception:
        pass
