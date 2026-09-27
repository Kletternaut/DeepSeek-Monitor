"""Gemeinsame pytest-Konfiguration: beide Pakete importierbar machen.

Das Repository enthält zwei Pakete mit je eigener Test-Suite:

- ``deepseek_monitor/`` (Root) — Tkinter-App-Logik
- ``deepseek-proxy/proxy/`` (``deepseek-proxy/``) — Streaming-Proxy

Damit ``pytest`` vom Repository-Root aus beide importieren kann, werden hier
beide Verzeichnisse in ``sys.path`` eingefügt.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PROXY = os.path.join(ROOT, "deepseek-proxy")

for _path in (ROOT, PROXY):
    if _path not in sys.path:
        sys.path.insert(0, _path)
