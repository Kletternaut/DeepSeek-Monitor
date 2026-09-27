"""Optionaler InfluxDB-2.x-Sink für Token-Usage-Events.

Dieses Modul ergänzt die lokale SQLite-Persistenz um einen **best-effort**
Schreibweg in eine zentrale InfluxDB 2.x. Es ist vollständig entkoppelt vom
eigentlichen Streaming: :class:`InfluxSink` wird (analog zu
``storage.save_usage``) als zusätzlicher ``usage_callback`` registriert.

Design:

* Keine harten Abhängigkeiten beim Import: ``influxdb-client`` wird **lazy**
  importiert, damit der Proxy auch ohne installierte Library startet.
* Schreiben erfolgt gepuffert (``SYNCHRONOUS`` write_api); Fehler werden
  geloggt, brechen aber niemals den Request-Stream.
* Ein ``host``-Tag (Hostname, per Konstruktor überschreibbar) macht die
  Usage mehrerer Entwicklungs-Hosts unterscheidbar.

Konfiguration ausschließlich über Parameter/Umgebungsvariablen -- es werden
keine Zugangsdaten im Code hinterlegt.

Öffentliche Symbole:
    * :class:`InfluxSink`
"""

from __future__ import annotations

import logging
import socket
from datetime import datetime
from typing import Any

from .cost_calc import calc_usage_cost
from .pricing import is_peak, load_pricing
from .usage import TokenUsage

logger = logging.getLogger("deepseek_proxy.influx_sink")

__all__ = ["InfluxSink"]

#: Measurement-Name in InfluxDB.
_MEASUREMENT = "usage"


class InfluxSink:
    """Puffert :class:`TokenUsage`-Events und schreibt sie in InfluxDB 2.x.

    Parameter:
        url: InfluxDB-2.x-Basis-URL (z. B. ``http://192.168.0.32:8086``).
        token: API-Token mit Schreib-/Leserecht auf ``bucket``.
        org: Organisationsname.
        bucket: Ziel-Bucket.
        host_name: Wert für das ``host``-Tag. Default: ``socket.gethostname()``.
        pricing: Optionale Preis-Tabelle; Default: aus :func:`load_pricing`.
    """

    def __init__(
        self,
        url: str,
        token: str,
        org: str,
        bucket: str,
        host_name: str | None = None,
        pricing: dict | None = None,
    ) -> None:
        self.url = (url or "").rstrip("/")
        self.token = token or ""
        self.org = org
        self.bucket = bucket
        self.host_name = host_name or socket.gethostname()
        self.pricing = pricing if pricing is not None else load_pricing()

        self._client: Any = None
        self._write_api: Any = None
        self._enabled = bool(self.url and self.token and self.org and self.bucket)

        if self._enabled:
            self._client, self._write_api = self._connect()
        else:
            logger.warning(
                "InfluxSink deaktiviert (unvollständige Konfiguration: "
                "url/token/org/bucket fehlt)."
            )

    # ------------------------------------------------------------- connection
    def _connect(self) -> tuple[Any, Any]:
        """Öffnet Client + gepufferten synchronen Write-Weg (lazy import)."""
        try:
            from influxdb_client import InfluxDBClient
            from influxdb_client.client.write_api import SYNCHRONOUS
        except ImportError as exc:  # pragma: no cover - abhängig von Install
            logger.warning(
                "influxdb-client nicht installiert -- InfluxSink deaktiviert (%s)",
                exc,
            )
            self._enabled = False
            return None, None

        client = InfluxDBClient(url=self.url, token=self.token, org=self.org)
        write_api = client.write_api(write_options=SYNCHRONOUS)
        return client, write_api

    # ------------------------------------------------------------------- write
    def write(self, usage: TokenUsage) -> None:
        """Schreibt ein einzelnes Usage-Event (best effort, nie werfen)."""
        if not self._enabled or self._write_api is None:
            return

        try:
            point = self._to_point(usage)
            self._write_api.write(
                bucket=self.bucket,
                org=self.org,
                record=point,
            )
        except Exception:  # noqa: BLE001 - Sink darf den Stream nie brechen
            logger.exception("Fehler beim InfluxDB-Schreiben (Event verworfen)")

    def _to_point(self, usage: TokenUsage) -> Any:
        """Baut aus einem :class:`TokenUsage` einen InfluxDB-Point."""
        from influxdb_client import Point

        tariff = "peak" if is_peak(usage.timestamp) else "off_peak"
        cost = calc_usage_cost(usage, self.pricing)

        timestamp = self._parse_timestamp(usage.timestamp)

        return (
            Point(_MEASUREMENT)
            .tag("host", self.host_name)
            .tag("model", (usage.model or "").strip() or "unknown")
            .tag("fingerprint", usage.api_key_fingerprint or "")
            .tag("tariff", tariff)
            .field("input_tokens", int(usage.input_tokens))
            .field("output_tokens", int(usage.output_tokens))
            .field("cache_read_tokens", int(usage.cache_read_input_tokens))
            .field("cache_creation_tokens", int(usage.cache_creation_input_tokens))
            .field("total_tokens", int(usage.total_tokens))
            .field("cost_usd", float(cost))
            .field("requests", 1)
            .time(timestamp)
        )

    @staticmethod
    def _parse_timestamp(timestamp_iso: str) -> datetime:
        """Parst den Berlin-ISO-Zeitstempel zu einem tz-aware ``datetime``."""
        try:
            parsed = datetime.fromisoformat(timestamp_iso)
            if parsed.tzinfo is None:
                # Naive Zeitstempel als UTC interpretieren (konsistent zu pricing).
                from datetime import timezone

                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except (TypeError, ValueError):
            # Fallback: aktuelle Zeit (best effort, Event nicht verlieren).
            return datetime.now().astimezone()

    # ------------------------------------------------------------------- close
    def close(self) -> None:
        """Schließt den Client und flusht den Puffer."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001 - defensiv
                logger.debug("Fehler beim Schließen des InfluxDB-Clients", exc_info=True)
