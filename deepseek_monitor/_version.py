"""Single source of truth for the project version.

This module is intentionally dependency-free (no imports), so it can be read
by packaging tools (``pyproject.toml`` via setuptools) without triggering the
side effects of :mod:`deepseek_monitor.config` (which imports ``zoneinfo`` and
instantiates ``ZoneInfo`` objects at import time).

Public symbols:
    * :data:`__version__`
"""

__version__ = "2.4.0"
