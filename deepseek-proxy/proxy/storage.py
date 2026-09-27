"""SQLite-Persistenz für Usage-Events, Key-Labels und Einstellungen.

Der Pfad zur Datenbank ist standardmäßig ``~/.local/state/deepseek/usage.db``
und wird bei Bedarf inklusive Verzeichnis angelegt. Es werden ausschließlich
stdlib-Module verwendet.

Öffentliche Symbole:
    * :class:`Storage`
    * :data:`DEFAULT_DB_PATH`
    * :func:`row_to_usage`
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3

from .usage import TokenUsage

logger = logging.getLogger("deepseek_proxy.storage")

__all__ = ["Storage", "DEFAULT_DB_PATH", "row_to_usage"]

#: Standardpfad der SQLite-Datenbank.
DEFAULT_DB_PATH = "~/.local/state/deepseek/usage.db"

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS usage_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        api_key_fingerprint TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
        cache_read_tokens INTEGER NOT NULL DEFAULT 0,
        total_tokens INTEGER NOT NULL DEFAULT 0,
        request_id TEXT NOT NULL DEFAULT '',
        raw_usage TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS key_labels (
        fingerprint TEXT PRIMARY KEY,
        label TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
)


def row_to_usage(row: dict) -> TokenUsage:
    """Mappt ein Storage-Zeilen-Dict auf ein :class:`TokenUsage`.

    Die DB-Spaltennamen weichen teilweise von den Feldnamen des Dataclasses ab:

    * ``cache_creation_tokens``   → ``cache_creation_input_tokens``
    * ``cache_read_tokens``       → ``cache_read_input_tokens``
    * ``api_key_fingerprint``     → ``api_key_fingerprint`` (identisch)

    ``raw_usage`` liegt in der DB als JSON-String vor und wird (best effort) per
    :func:`json.loads` geparst; bei Fehlern ist das Feld ein leeres Dict.
    """

    def _int(key: str) -> int:
        try:
            return int(row.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    raw_usage: dict = {}
    raw = row.get("raw_usage")
    if isinstance(raw, dict):
        raw_usage = raw
    elif isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                raw_usage = parsed
        except (TypeError, ValueError):
            logger.debug("raw_usage ist kein gültiges JSON: %r", raw)

    return TokenUsage(
        timestamp=str(row.get("timestamp") or ""),
        input_tokens=_int("input_tokens"),
        output_tokens=_int("output_tokens"),
        cache_creation_input_tokens=_int("cache_creation_tokens"),
        cache_read_input_tokens=_int("cache_read_tokens"),
        total_tokens=_int("total_tokens"),
        model=str(row.get("model") or ""),
        request_id=str(row.get("request_id") or ""),
        api_key_fingerprint=str(row.get("api_key_fingerprint") or ""),
        raw_usage=raw_usage,
    )


class Storage:
    """Persistente Ablage für Usage-Daten in einer SQLite-Datenbank.

    Parameter:
        db_path: Pfad zur SQLite-Datei. Default: :data:`DEFAULT_DB_PATH`
            (``~/.local/state/deepseek/usage.db``). Das Verzeichnis wird
            automatisch angelegt.
    """

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = os.path.expanduser(db_path or DEFAULT_DB_PATH)
        directory = os.path.dirname(self.db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._init_db()

    # --------------------------------------------------------------------- db
    def _connect(self) -> sqlite3.Connection:
        """Öffnet eine Verbindung mit aktiviertem ``Row``-Factory."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Legt die Tabellen an, falls sie noch nicht existieren."""
        with sqlite3.connect(self.db_path) as conn:
            for statement in _SCHEMA:
                conn.execute(statement)

    # ------------------------------------------------------------------ usage
    def save_usage(self, usage: TokenUsage) -> None:
        """Speichert einen :class:`TokenUsage`-Eintrag als neues Event."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO usage_events (
                    timestamp, api_key_fingerprint, model,
                    input_tokens, output_tokens,
                    cache_creation_tokens, cache_read_tokens, total_tokens,
                    request_id, raw_usage
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    usage.timestamp,
                    usage.api_key_fingerprint,
                    usage.model,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.cache_creation_input_tokens,
                    usage.cache_read_input_tokens,
                    usage.total_tokens,
                    usage.request_id,
                    json.dumps(usage.raw_usage),
                ),
            )

    def get_usage(
        self,
        fingerprint: str | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """Liest Usage-Events mit optionalen Filtern (neueste zuerst).

        Parameter:
            fingerprint: Nur Events mit diesem ``api_key_fingerprint``.
            start: ISO-Timestamp; nur Events mit ``timestamp >= start``.
            end: ISO-Timestamp; nur Events mit ``timestamp <= end``.
            limit: Maximale Anzahl zurückgegebener Zeilen.
        """
        clauses: list[str] = []
        params: list[object] = []
        if fingerprint is not None:
            clauses.append("api_key_fingerprint = ?")
            params.append(fingerprint)
        if start is not None:
            clauses.append("timestamp >= ?")
            params.append(start)
        if end is not None:
            clauses.append("timestamp <= ?")
            params.append(end)

        query = "SELECT * FROM usage_events"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

        with self._connect() as conn:
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def get_daily_usage(
        self,
        days: int = 7,
        fingerprint: str | None = None,
    ) -> list[dict]:
        """Aggregiert die Usage pro Tag.

        Parameter:
            days: Anzahl der jüngsten Tage (nach Datum) in absteigender
                Reihenfolge, die zurückgegeben werden.
            fingerprint: Optionaler Filter auf ``api_key_fingerprint``.

        Returns:
            Liste von Dicts mit ``date``, ``input_tokens``, ``output_tokens``,
            ``cache_read_tokens``, ``cache_creation_tokens``,
            ``total_tokens`` und ``requests``.
        """
        clauses: list[str] = []
        params: list[object] = []
        if fingerprint is not None:
            clauses.append("api_key_fingerprint = ?")
            params.append(fingerprint)

        query = """
            SELECT
                date(timestamp) AS date,
                COALESCE(SUM(input_tokens), 0) AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
                COALESCE(SUM(cache_creation_tokens), 0) AS cache_creation_tokens,
                COALESCE(SUM(total_tokens), 0) AS total_tokens,
                COUNT(*) AS requests
            FROM usage_events
        """
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " GROUP BY date(timestamp) ORDER BY date DESC"
        if days and days > 0:
            query += " LIMIT ?"
            params.append(days)

        with self._connect() as conn:
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def get_distinct_fingerprints(self) -> list[str]:
        """Liefert alle bisher gesehenen API-Key-Fingerprints."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                SELECT DISTINCT api_key_fingerprint
                FROM usage_events
                WHERE api_key_fingerprint != ''
                ORDER BY api_key_fingerprint
                """
            )
            return [row["api_key_fingerprint"] for row in cursor.fetchall()]

    # ----------------------------------------------------------------- labels
    def set_label(self, fingerprint: str, label: str) -> None:
        """Setzt (oder ersetzt) das manuelle Label eines Fingerprints."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO key_labels (fingerprint, label) VALUES (?, ?)",
                (fingerprint, label),
            )

    def get_label(self, fingerprint: str) -> str | None:
        """Liefert das Label eines Fingerprints oder ``None``."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT label FROM key_labels WHERE fingerprint = ?",
                (fingerprint,),
            )
            row = cursor.fetchone()
            return row["label"] if row else None

    def get_labels(self) -> dict[str, str]:
        """Liefert alle Labels als Mapping ``fingerprint -> label``."""
        with self._connect() as conn:
            cursor = conn.execute("SELECT fingerprint, label FROM key_labels")
            return {row["fingerprint"]: row["label"] for row in cursor.fetchall()}

    # --------------------------------------------------------------- settings
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        """Liest einen Settings-Wert oder ``default``."""
        with self._connect() as conn:
            cursor = conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        """Setzt (oder ersetzt) einen Settings-Wert."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )
