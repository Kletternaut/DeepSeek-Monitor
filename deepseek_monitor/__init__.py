"""DeepSeek Monitor — Tkinter desktop app "Cost & Usage".

Independent open-source project; not affiliated with DeepSeek (深度求索).

The pure, Tkinter-free logic is split into submodules:

- :mod:`deepseek_monitor.config` — constants & colors
- :mod:`deepseek_monitor.i18n` — languages & currencies
- :mod:`deepseek_monitor.paths` — XDG paths & timezones
- :mod:`deepseek_monitor.persistence` — persistence (JSON/CSV)
- :mod:`deepseek_monitor.api` — balance API
- :mod:`deepseek_monitor.tariff` — tariff logic
- :mod:`deepseek_monitor.analytics` — usage analysis
"""

from deepseek_monitor.config import APP_NAME, APP_VERSION

__version__ = APP_VERSION
__all__ = ["APP_NAME", "APP_VERSION", "__version__"]
