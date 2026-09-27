"""API-Key-Fingerprints und Auflösung von manuellen Key-Labels.

Es wird niemals der Klartext-API-Key gespeichert oder geloggt. Stattdessen
wird pro Request ein SHA-256-Fingerprint des Keys berechnet, der zur
Zuordnung und Filterung der Usage-Einträge dient.

Öffentliche Symbole:
    * :func:`fingerprint_key`
    * :func:`resolve_label`
"""

from __future__ import annotations

import hashlib
import logging

from .storage import Storage

logger = logging.getLogger("deepseek_proxy.labels")

__all__ = ["fingerprint_key", "resolve_label"]


def fingerprint_key(api_key: str) -> str:
    """Berechnet den SHA-256-Hexdigest eines API-Keys.

    Parameter:
        api_key: Der Klartext-API-Key (oder ein leerer/``None``-Wert).

    Returns:
        Voller 64-stelliger Hexdigest des UTF-8-kodierten Keys. Für einen
        leeren oder ``None``-Key wird ein leerer String zurückgegeben.
    """
    if not api_key:
        return ""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def resolve_label(storage: Storage, fingerprint: str) -> str:
    """Liefert das gespeicherte Label für ``fingerprint``.

    Ist kein Label gesetzt (oder ist der Fingerprint leer), wird ein
    stabiler Fallback in der Form ``key-<erste 8 Zeichen>`` zurückgegeben.
    """
    if not fingerprint:
        return "key-unknown"
    label = storage.get_label(fingerprint)
    if label:
        return label
    return f"key-{fingerprint[:8]}"
