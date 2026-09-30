"""Standalone entry point for the DeepSeek-Anthropic proxy.

Starts the streaming proxy including token monitoring and (optionally) the web
dashboard, and waits for ``SIGINT``/``SIGTERM`` to shut the server down cleanly.

Invocation (from the project root, so the ``proxy`` package is importable)::

    .venv/bin/python main.py --port 8000

Start only the (read-only) Tkinter detail view -- without the proxy server::

    .venv/bin/python main.py --view

Public symbols:
    * :func:`main`
    * :func:`parse_args`
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import threading

from proxy.dashboard import add_dashboard_routes
from proxy.server import DEFAULT_UPSTREAM_URL, DeepSeekProxyServer
from proxy.storage import Storage

logger = logging.getLogger("deepseek_proxy.main")

__all__ = ["main", "parse_args"]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command-line arguments of the standalone server."""
    parser = argparse.ArgumentParser(
        prog="main.py",
        description=(
            "Streaming proxy between VS Code (Anthropic Messages API) and "
            "DeepSeek with token usage monitoring and web dashboard."
        ),
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address of the proxy server (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Bind port of the proxy server (default: 8000).",
    )
    parser.add_argument(
        "--upstream",
        default=DEFAULT_UPSTREAM_URL,
        help=f"Upstream URL of the DeepSeek-Anthropic API (default: {DEFAULT_UPSTREAM_URL}).",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Path to the SQLite database (default: storage default path).",
    )
    parser.add_argument(
        "--no-dashboard",
        action="store_true",
        help="Do not register the web dashboard.",
    )
    parser.add_argument(
        "--view",
        action="store_true",
        default=False,
        help=(
            "Start only the Tkinter detail view (Cost & Usage) -- "
            "NO proxy server. The server runs separately."
        ),
    )
    parser.add_argument(
        "--tray",
        action="store_true",
        default=False,
        help=(
            "Show a system-tray icon (Windows only) to monitor the proxy and "
            "quit it. Requires the optional 'pystray'/'Pillow' packages."
        ),
    )
    parser.add_argument(
        "--influx-url",
        default=None,
        help=(
            "Optional InfluxDB 2.x base URL (e.g. http://<INFLUX_HOST>:8086). "
            "When set, usage is additionally written to InfluxDB."
        ),
    )
    parser.add_argument(
        "--influx-token",
        default=None,
        help="API token for InfluxDB (default: environment variable DEEPSEEK_INFLUX_TOKEN).",
    )
    parser.add_argument(
        "--influx-org",
        default=None,
        help="InfluxDB organization (default: environment variable DEEPSEEK_INFLUX_ORG).",
    )
    parser.add_argument(
        "--influx-bucket",
        default=None,
        help="InfluxDB bucket (default: environment variable DEEPSEEK_INFLUX_BUCKET).",
    )
    parser.add_argument(
        "--host-name",
        default=None,
        help="Hostname for the InfluxDB `host` tag (default: socket.gethostname()).",
    )
    return parser.parse_args(argv)


def _load_shared_influx_config() -> dict[str, str]:
    """Load the shared InfluxDB config (the same file the monitor writes).

    The monitor persists its data-source settings as JSON (url/token/org/bucket)
    in the platform config dir. The proxy reads this as a fallback so both
    components can share a single configuration.
    """
    try:
        from deepseek_monitor.persistence import load_influx_config

        cfg = load_influx_config()
        if cfg and cfg.get("url"):
            return cfg
    except Exception:  # pragma: no cover - optional cross-package import
        logger.debug("Shared InfluxDB config unavailable", exc_info=True)
    return {}


def _build_usage_callback(args: argparse.Namespace, storage: Storage):
    """Build the ``usage_callback`` including the optional InfluxDB sink.

    SQLite always stays active. If InfluxDB is configured (``--influx-url``
    or ``DEEPSEEK_INFLUX_URL``), usage is additionally written to InfluxDB.
    An error in the Influx sink must not impair the SQLite persistence and
    must never break the stream.
    """
    influx_url = args.influx_url or os.environ.get("DEEPSEEK_INFLUX_URL")
    influx_token = args.influx_token or os.environ.get("DEEPSEEK_INFLUX_TOKEN")
    influx_org = args.influx_org or os.environ.get("DEEPSEEK_INFLUX_ORG")
    influx_bucket = args.influx_bucket or os.environ.get("DEEPSEEK_INFLUX_BUCKET")
    host_name = args.host_name or os.environ.get("DEEPSEEK_INFLUX_HOST_NAME")

    # Fallback: shared config file (written by the monitor/tray dialog).
    if not influx_url:
        shared = _load_shared_influx_config()
        influx_url = shared.get("url", "")
        influx_token = influx_token or shared.get("token", "")
        influx_org = influx_org or shared.get("org", "")
        influx_bucket = influx_bucket or shared.get("bucket", "")

    if not influx_url:
        return storage.save_usage

    from proxy.influx_sink import InfluxSink

    sink = InfluxSink(
        url=influx_url,
        token=influx_token or "",
        org=influx_org or "",
        bucket=influx_bucket or "",
        host_name=host_name,
    )

    def combined_callback(usage) -> None:
        # Local persistence first (critical), then best-effort Influx.
        storage.save_usage(usage)
        try:
            sink.write(usage)
        except Exception:  # noqa: BLE001 - Influx is purely optional
            logger.exception("InfluxDB write failed (ignored)")

    logger.info(
        "InfluxDB sink enabled: %s (org=%s, bucket=%s, host=%s)",
        influx_url,
        influx_org,
        influx_bucket,
        sink.host_name,
    )
    return combined_callback


def _build_server(args: argparse.Namespace, storage: Storage) -> DeepSeekProxyServer:
    """Create and configure a proxy server from ``args`` (not yet started)."""
    usage_callback = _build_usage_callback(args, storage)
    server = DeepSeekProxyServer(
        usage_callback=usage_callback,
        host=args.host,
        port=args.port,
        upstream_url=args.upstream,
    )
    if not args.no_dashboard:
        add_dashboard_routes(server.app, storage)
        logger.info("Dashboard enabled at %s/", server.base_url)
    else:
        logger.info("Dashboard disabled (--no-dashboard)")
    return server


def main(argv: list[str] | None = None) -> int:
    """Start the proxy and wait until a termination signal arrives."""
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    storage = Storage(args.db)

    if args.view:
        # Start only the desktop detail view -- no proxy server, no dashboard.
        # tkinter is imported here (lazily), so server operation does not
        # require a tkinter dependency.
        from proxy.tkinter_view import UsageDetailApp

        logger.info("Starting Tkinter detail view (read-only, no proxy server)")
        UsageDetailApp(storage).run()
        return 0

    server = _build_server(args, storage)

    stop_event = threading.Event()

    def _handle_signal(signum: int, frame: object | None) -> None:
        logger.info("Signal %s received -- shutting down", signal.Signals(signum).name)
        stop_event.set()

    # SIGINT works everywhere. SIGTERM is a no-op on Windows (it exists as a
    # constant but is never delivered by the OS); SIGBREAK is the Windows
    # equivalent of Ctrl+Break. Register only what the platform supports.
    _signals = [signal.SIGINT]
    for _name in ("SIGTERM", "SIGBREAK"):
        _sig = getattr(signal, _name, None)
        if _sig is not None:
            _signals.append(_sig)
    for sig in _signals:
        try:
            signal.signal(sig, _handle_signal)
        except (ValueError, OSError):
            logger.warning("Could not register signal handler for %s", sig)

    server.start()
    logger.info("Proxy ready at %s", server.base_url)

    # A tray "configure" request is raised from the tray thread; the actual
    # dialog + restart run in this (main) thread, which is required for Tkinter.
    configure_event = threading.Event()

    def _configure_influx() -> None:
        """Open the InfluxDB dialog, persist, and restart the server."""
        nonlocal server

        from proxy.influx_dialog import open_influx_dialog

        try:
            from deepseek_monitor.persistence import (
                load_influx_config,
                save_influx_config,
            )

            current = load_influx_config() or {}
        except Exception:  # pragma: no cover - optional cross-package import
            current = {}

        result = open_influx_dialog(current)
        if not result:
            logger.info("InfluxDB configuration cancelled.")
            return

        try:
            save_influx_config(
                {
                    "url": result.get("url", ""),
                    "token": result.get("token", ""),
                    "org": result.get("org", ""),
                    "bucket": result.get("bucket", ""),
                }
            )
        except Exception:  # pragma: no cover - defensive
            logger.exception("Could not persist InfluxDB configuration")

        # Restart so the new Influx config takes effect (sink is built at start).
        logger.info("Restarting proxy to apply InfluxDB configuration ...")
        server.stop()
        server = _build_server(args, storage)
        server.start()
        logger.info("Proxy ready at %s", server.base_url)

    # System-tray icon (opt-in, Windows only, lazy pystray+Pillow import).
    if args.tray:
        from proxy.tray import start_tray, tray_supported

        if tray_supported():
            start_tray(
                port=args.port,
                base_url=server.base_url,
                stop_event=stop_event,
                on_configure=configure_event.set,
            )
        else:
            logger.info("--tray requested but unsupported on this platform; ignored.")

    try:
        # Poll instead of a single blocking wait: on Windows a blocking
        # ``Event.wait()`` (native wait) is not interruptible by console
        # control signals (Ctrl+C/SIGINT, SIGBREAK), so the signal handler
        # would never run. A short timeout returns periodically and lets the
        # interpreter process the pending signal (which sets ``stop_event``).
        while not stop_event.is_set():
            if configure_event.is_set():
                configure_event.clear()
                try:
                    _configure_influx()
                except Exception:  # pragma: no cover - defensive
                    logger.exception("InfluxDB configuration failed")
            stop_event.wait(timeout=0.5)
    finally:
        server.stop()
        logger.info("Proxy stopped")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
