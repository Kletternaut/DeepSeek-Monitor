"""Tkinter dialog for configuring the InfluxDB data source (Windows only).

Opens a small modal window with URL/Token/Org/Bucket fields. ``tkinter`` is
imported lazily (only when the dialog is actually shown), so importing this
module never requires a display.

The dialog returns a ``dict`` with the entered (stripped) values, or ``None``
if the user cancelled.

Public symbols:
    * :func:`open_influx_dialog`
"""

from __future__ import annotations

import logging

logger = logging.getLogger("deepseek_proxy.influx_dialog")

__all__ = ["open_influx_dialog"]

#: Field order and labels shown in the dialog.
_FIELDS = (
    ("url", "URL", "http://<HOST>:8086"),
    ("token", "Token", ""),
    ("org", "Organisation", ""),
    ("bucket", "Bucket", ""),
)

_BG = "#121212"
_PANEL = "#1a1a1a"
_FG = "#ffffff"
_DIM = "#78909c"


def open_influx_dialog(initial: dict[str, str] | None = None) -> dict[str, str] | None:
    """Show the InfluxDB configuration dialog and return the entered values.

    Parameters:
        initial: Current values to pre-fill the fields (may be empty/missing).

    Returns:
        ``{"url", "token", "org", "bucket"}`` or ``None`` on cancel.
    """
    import tkinter as tk

    from deepseek_monitor.dpi import apply_tk_scaling, enable_dpi_awareness

    enable_dpi_awareness()

    initial = initial or {}
    root = tk.Tk()
    apply_tk_scaling(root)
    root.title("DeepSeek Proxy — InfluxDB konfigurieren")
    root.configure(bg=_BG)
    root.resizable(False, False)

    entries: dict[str, tk.StringVar] = {}

    for i, (key, label, _placeholder) in enumerate(_FIELDS):
        tk.Label(
            root,
            text=label + ":",
            bg=_BG,
            fg=_DIM,
            anchor="w",
        ).grid(row=i, column=0, sticky="w", padx=(14, 8), pady=(10 if i == 0 else 5, 2))

        # Mask the token while typing; existing value stays intact on save.
        show = "*" if key == "token" else None
        var = tk.StringVar(value=initial.get(key, ""))
        entry = tk.Entry(
            root,
            textvariable=var,
            show=show,
            width=52,
            bg=_PANEL,
            fg=_FG,
            insertbackground=_FG,
            relief="flat",
        )
        entry.grid(row=i, column=1, sticky="ew", padx=(0, 14), pady=(10 if i == 0 else 5, 2))
        entries[key] = var

    result: dict[str, str] = {}

    def _save() -> None:
        result.update({key: var.get().strip() for key, var in entries.items()})
        root.destroy()

    def _cancel() -> None:
        root.destroy()

    buttons = tk.Frame(root, bg=_BG)
    buttons.grid(row=len(_FIELDS), column=0, columnspan=2, sticky="ew", padx=14, pady=(14, 14))
    tk.Button(
        buttons,
        text="Abbrechen",
        command=_cancel,
        bg=_PANEL,
        fg=_FG,
        relief="flat",
        padx=12,
        pady=4,
    ).pack(side="right", padx=(8, 0))
    tk.Button(
        buttons,
        text="Speichern",
        command=_save,
        bg=_PANEL,
        fg=_FG,
        relief="flat",
        padx=12,
        pady=4,
    ).pack(side="right")

    root.update_idletasks()
    root.mainloop()
    return result if result else None
