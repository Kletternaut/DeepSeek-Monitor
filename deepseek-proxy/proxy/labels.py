"""API key fingerprints and resolution of manual key labels.

The plaintext API key is never stored or logged. Instead, a SHA-256 fingerprint
of the key is computed per request, which is used to associate and filter the
usage entries.

Public symbols:
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
    """Compute the SHA-256 hex digest of an API key.

    Parameters:
        api_key: The plaintext API key (or an empty/``None`` value).

    Returns:
        Full 64-character hex digest of the UTF-8-encoded key. An empty
        string is returned for an empty or ``None`` key.
    """
    if not api_key:
        return ""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def resolve_label(storage: Storage, fingerprint: str) -> str:
    """Return the stored label for ``fingerprint``.

    If no label is set (or the fingerprint is empty), a stable fallback in the
    form ``key-<first 8 characters>`` is returned.
    """
    if not fingerprint:
        return "key-unknown"
    label = storage.get_label(fingerprint)
    if label:
        return label
    return f"key-{fingerprint[:8]}"
