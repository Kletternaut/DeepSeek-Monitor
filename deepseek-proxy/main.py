"""Standalone-Einstiegspunkt für den DeepSeek-Anthropic-Proxy.

Startet den Streaming-Proxy samt Token-Überwachung und (optional) Web-Dashboard
und wartet auf ``SIGINT``/``SIGTERM``, um den Server sauber herunterzufahren.

Aufruf (aus dem Projekt-Root, damit das Paket ``proxy`` importierbar ist)::

    .venv/bin/python main.py --port 8000

Nur die (read-only) Tkinter-Detailansicht starten -- ohne Proxy-Server::

    .venv/bin/python main.py --view

Öffentliche Symbole:
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
    """Parst die Kommandozeilenargumente des Standalone-Servers."""
    parser = argparse.ArgumentParser(
        prog="main.py",
        description=(
            "Streaming-Proxy zwischen VS Code (Anthropic Messages API) und "
            "DeepSeek mit Token-Usage-Monitoring und Web-Dashboard."
        ),
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind-Adresse des Proxy-Servers (Default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Bind-Port des Proxy-Servers (Default: 8000).",
    )
    parser.add_argument(
        "--upstream",
        default=DEFAULT_UPSTREAM_URL,
        help=f"Upstream-URL der DeepSeek-Anthropic-API (Default: {DEFAULT_UPSTREAM_URL}).",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Pfad zur SQLite-Datenbank (Default: Storage-Standardpfad).",
    )
    parser.add_argument(
        "--no-dashboard",
        action="store_true",
        help="Web-Dashboard nicht registrieren.",
    )
    parser.add_argument(
        "--view",
        action="store_true",
        default=False,
        help=(
            "Nur die Tkinter-Detailansicht (Kosten & Verbrauch) starten -- "
            "KEIN Proxy-Server. Der Server läuft separat."
        ),
    )
    parser.add_argument(
        "--influx-url",
        default=None,
        help=(
            "Optionale InfluxDB-2.x-Basis-URL (z. B. http://192.168.0.32:8086). "
            "Wenn gesetzt, wird die Usage zusätzlich in InfluxDB geschrieben."
        ),
    )
    parser.add_argument(
        "--influx-token",
        default=None,
        help="API-Token für InfluxDB (Default: Umgebungsvariable DEEPSEEK_INFLUX_TOKEN).",
    )
    parser.add_argument(
        "--influx-org",
        default=None,
        help="InfluxDB-Organisation (Default: Umgebungsvariable DEEPSEEK_INFLUX_ORG).",
    )
    parser.add_argument(
        "--influx-bucket",
        default=None,
        help="InfluxDB-Bucket (Default: Umgebungsvariable DEEPSEEK_INFLUX_BUCKET).",
    )
    parser.add_argument(
        "--host-name",
        default=None,
        help="Hostname für das InfluxDB-`host`-Tag (Default: socket.gethostname()).",
    )
    return parser.parse_args(argv)


def _build_usage_callback(args: argparse.Namespace, storage: Storage):
    """Erzeugt den ``usage_callback`` inklusive optionalem InfluxDB-Sink.

    SQLite bleibt immer aktiv. Ist InfluxDB konfiguriert (``--influx-url``
    bzw. ``DEEPSEEK_INFLUX_URL``), wird zusätzlich in InfluxDB geschrieben.
    Ein Fehler im Influx-Sink darf die SQLite-Persistenz nicht beeinträchtigen
    und den Stream niemals brechen.
    """
    influx_url = args.influx_url or os.environ.get("DEEPSEEK_INFLUX_URL")
    influx_token = args.influx_token or os.environ.get("DEEPSEEK_INFLUX_TOKEN")
    influx_org = args.influx_org or os.environ.get("DEEPSEEK_INFLUX_ORG")
    influx_bucket = args.influx_bucket or os.environ.get("DEEPSEEK_INFLUX_BUCKET")
    host_name = args.host_name or os.environ.get("DEEPSEEK_INFLUX_HOST_NAME")

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
        # Lokale Persistenz zuerst (kritisch), dann best-effort Influx.
        storage.save_usage(usage)
        try:
            sink.write(usage)
        except Exception:  # noqa: BLE001 - Influx ist rein optional
            logger.exception("InfluxDB-Schreiben fehlgeschlagen (ignoriert)")

    logger.info(
        "InfluxDB-Sink aktiviert: %s (org=%s, bucket=%s, host=%s)",
        influx_url,
        influx_org,
        influx_bucket,
        sink.host_name,
    )
    return combined_callback


def main(argv: list[str] | None = None) -> int:
    """Startet den Proxy und wartet, bis ein Beendigungssignal eintrifft."""
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    storage = Storage(args.db)

    if args.view:
        # Nur die Desktop-Detailansicht starten -- kein Proxy-Server, kein
        # Dashboard. tkinter wird erst hier (lazy) importiert, damit der
        # Server-Betrieb keine tkinter-Abhängigkeit benötigt.
        from proxy.tkinter_view import UsageDetailApp

        logger.info("Starte Tkinter-Detailansicht (read-only, ohne Proxy-Server)")
        UsageDetailApp(storage).run()
        return 0

    usage_callback = _build_usage_callback(args, storage)

    server = DeepSeekProxyServer(
        usage_callback=usage_callback,
        host=args.host,
        port=args.port,
        upstream_url=args.upstream,
    )

    if not args.no_dashboard:
        add_dashboard_routes(server.app, storage)
        logger.info("Dashboard aktiviert unter %s/", server.base_url)
    else:
        logger.info("Dashboard deaktiviert (--no-dashboard)")

    stop_event = threading.Event()

    def _handle_signal(signum: int, frame: object | None) -> None:
        logger.info("Signal %s empfangen -- fahre herunter", signal.Signals(signum).name)
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _handle_signal)

    server.start()
    logger.info("Proxy bereit unter %s", server.base_url)

    try:
        # Warten auf SIGINT/SIGTERM; nutzt keinen Busy-Wait.
        stop_event.wait()
    finally:
        server.stop()
        logger.info("Proxy gestoppt")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
