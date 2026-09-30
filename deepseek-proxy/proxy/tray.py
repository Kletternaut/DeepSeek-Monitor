"""Windows system-tray icon for the DeepSeek proxy (opt-in, Windows only).

Provides a small control surface in the notification area:

    * shows the proxy port and the app version
    * opens the dashboard and the GitHub page in the default browser
    * quits the proxy (sets the shared stop event)

``pystray`` and ``Pillow`` are imported lazily (only when the tray is actually
started on Windows), so importing this module never requires them and Linux
operation is unaffected.

Public symbols:
    * :func:`tray_supported`
    * :func:`start_tray`
"""

from __future__ import annotations

import logging
import os
import threading
import webbrowser

logger = logging.getLogger("deepseek_proxy.tray")

__all__ = ["tray_supported", "start_tray"]

GITHUB_URL = "https://github.com/Kletternaut/DeepSeek-Monitor"

_APP_NAME = "DeepSeek Proxy"


def tray_supported() -> bool:
    """Return ``True`` if a tray icon can be shown (Windows only)."""
    return os.name == "nt"


def _app_version() -> str:
    """Return the app version from the single source of truth
    (``deepseek_monitor.config.APP_VERSION``)."""
    from deepseek_monitor.config import APP_VERSION

    return APP_VERSION


def _open_url(url: str) -> None:
    """Open ``url`` in the default browser (best effort, never raises)."""
    try:
        webbrowser.open(url)
    except Exception:  # pragma: no cover - best effort
        logger.exception("Could not open %s", url)


def _build_icon(image_module, image_draw):
    """Build a simple gauge-style tray icon (64x64) without external assets."""
    size = 64
    img = image_module.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = image_draw.Draw(img)
    # Background disc.
    draw.ellipse([6, 6, size - 6, size - 6], fill=(15, 17, 21, 255))
    # Accent arc (270° gauge).
    draw.arc(
        [12, 12, size - 12, size - 12],
        start=135,
        end=405,
        fill=(79, 157, 255, 255),
        width=5,
    )
    # Center status dot.
    draw.ellipse(
        [size // 2 - 6, size // 2 - 6, size // 2 + 6, size // 2 + 6],
        fill=(61, 220, 151, 255),
    )
    return img


def _on_quit(icon, stop_event: threading.Event) -> None:
    """Handle the "Beenden" menu item: stop the tray loop and the proxy."""
    stop_event.set()
    icon.stop()


def start_tray(
    *,
    port: int,
    base_url: str,
    stop_event: threading.Event,
    on_configure: object | None = None,
) -> threading.Thread | None:
    """Start the tray icon in a background thread (Windows only).

    Parameters:
        port: Proxy port, shown in the menu.
        base_url: Base URL of the running proxy (``http://host:port``).
        stop_event: Shared stop event; set when the user chooses "Beenden".
        on_configure: Optional zero-arg callable invoked when the user chooses
            "InfluxDB konfigurieren…". It runs in the tray thread, so it should
            only set a flag/event (the actual dialog runs elsewhere).

    Returns:
        The started thread, or ``None`` if the tray could not be created
        (non-Windows platform or missing optional dependencies).
    """
    if not tray_supported():
        logger.info("Tray icon is Windows-only; skipping.")
        return None

    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError as exc:  # pragma: no cover - optional deps
        logger.warning("Tray icon skipped (pystray/Pillow not installed): %s", exc)
        return None

    version_text = _app_version()

    menu_items: list = [
        pystray.MenuItem(f"Port {port}", None, enabled=False),
        pystray.MenuItem(f"Version {version_text}", None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "Dashboard öffnen",
            lambda icon, item: _open_url(f"{base_url}/"),
        ),
        pystray.MenuItem(
            "GitHub",
            lambda icon, item: _open_url(GITHUB_URL),
        ),
    ]

    if on_configure is not None:
        menu_items.append(pystray.Menu.SEPARATOR)
        menu_items.append(
            pystray.MenuItem(
                "InfluxDB konfigurieren…",
                lambda icon, item: on_configure(),
            )
        )

    menu_items.append(pystray.Menu.SEPARATOR)
    menu_items.append(
        pystray.MenuItem(
            "Beenden",
            lambda icon, item: _on_quit(icon, stop_event),
        )
    )

    menu = pystray.Menu(*menu_items)

    icon = pystray.Icon(
        "deepseek-proxy",
        _build_icon(Image, ImageDraw),
        _APP_NAME,
        menu,
    )

    def _run() -> None:
        try:
            icon.run()
        except Exception:  # pragma: no cover - defensive
            logger.exception("Tray icon thread crashed")
        finally:
            # If the loop ends unexpectedly (e.g. icon removed), request stop.
            stop_event.set()

    thread = threading.Thread(target=_run, name="deepseek-proxy-tray", daemon=True)
    thread.start()
    logger.info("Tray icon started (port=%s, version=%s)", port, version_text)
    return thread
