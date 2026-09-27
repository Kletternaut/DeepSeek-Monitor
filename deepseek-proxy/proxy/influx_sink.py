"""Optional InfluxDB 2.x sink for token usage events.

This module complements the local SQLite persistence with a **best-effort**
write path into a central InfluxDB 2.x. It is fully decoupled from the actual
streaming: :class:`InfluxSink` is registered as an additional ``usage_callback``
(analogous to ``storage.save_usage``).

Design:

* No hard import dependencies: ``influxdb-client`` is imported **lazily**, so
the proxy also starts without the installed library.
* Writing is buffered (``SYNCHRONOUS`` write_api); errors are logged but never
  break the request stream.
* A ``host`` tag (hostname, overridable via the constructor) makes the usage of
  multiple development hosts distinguishable.

Configuration exclusively via parameters/environment variables -- no credentials
are hardcoded in the code.

Public symbols:
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

#: Measurement name in InfluxDB.
_MEASUREMENT = "usage"


class InfluxSink:
    """Buffer :class:`TokenUsage` events and write them to InfluxDB 2.x.

    Parameters:
        url: InfluxDB 2.x base URL (e.g. ``http://<INFLUX_HOST>:8086``).
        token: API token with write/read permission on ``bucket``.
        org: Organization name.
        bucket: Target bucket.
        host_name: Value for the ``host`` tag. Default: ``socket.gethostname()``.
        pricing: Optional price table; default: from :func:`load_pricing`.
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
                "InfluxSink disabled (incomplete configuration: " "url/token/org/bucket missing)."
            )

    # ------------------------------------------------------------- connection
    def _connect(self) -> tuple[Any, Any]:
        """Open client + buffered synchronous write path (lazy import)."""
        try:
            from influxdb_client import InfluxDBClient
            from influxdb_client.client.write_api import SYNCHRONOUS
        except ImportError as exc:  # pragma: no cover - depends on install
            logger.warning(
                "influxdb-client not installed -- InfluxSink disabled (%s)",
                exc,
            )
            self._enabled = False
            return None, None

        client = InfluxDBClient(url=self.url, token=self.token, org=self.org)
        write_api = client.write_api(write_options=SYNCHRONOUS)
        return client, write_api

    # ------------------------------------------------------------------- write
    def write(self, usage: TokenUsage) -> None:
        """Write a single usage event (best effort, never raise)."""
        if not self._enabled or self._write_api is None:
            return

        try:
            point = self._to_point(usage)
            self._write_api.write(
                bucket=self.bucket,
                org=self.org,
                record=point,
            )
        except Exception:  # noqa: BLE001 - sink must never break the stream
            logger.exception("Error writing to InfluxDB (event discarded)")

    def _to_point(self, usage: TokenUsage) -> Any:
        """Build an InfluxDB point from a :class:`TokenUsage`."""
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
        """Parse the Berlin ISO timestamp to a tz-aware ``datetime``."""
        try:
            parsed = datetime.fromisoformat(timestamp_iso)
            if parsed.tzinfo is None:
                # Interpret naive timestamps as UTC (consistent with pricing).
                from datetime import timezone

                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except (TypeError, ValueError):
            # Fallback: current time (best effort, do not lose the event).
            return datetime.now().astimezone()

    # ------------------------------------------------------------------- close
    def close(self) -> None:
        """Close the client and flush the buffer."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001 - defensive
                logger.debug("Error closing the InfluxDB client", exc_info=True)
