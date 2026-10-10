# Contributing

Thank you for your interest in **DeepSeek API Proxy & Token Monitor**. This is
an independent open-source project and is not affiliated with DeepSeek
(深度求索).

## Before you start

- Read the [project README](../README.md) and [installation guide](INSTALLATION.md).
- Open an issue before investing significant time in a larger change.
- Keep changes focused and include regression tests for behavior changes.

## Development environment

Run these commands from the repository root:

```bash
git clone --branch main https://github.com/Kletternaut/DeepSeek-Monitor.git
cd DeepSeek-Monitor
python3 -m venv .venv
.venv/bin/python -m pip install -r deepseek-proxy/requirements.txt
.venv/bin/python -m pip install pytest ruff black
```

## Tests

From the repository root:

```bash
.venv/bin/python -m pytest
```

## Code style

- Python 3.10+; use `from __future__ import annotations` where appropriate.
- Use Ruff and Black; line length is 100 (see `pyproject.toml`).
- Prefer type hints and clear docstrings.
- Add regression coverage for API usage parsing, pricing/cost calculation,
  timezone/holiday boundaries, storage filters, and dashboard responses where
  relevant.

## Pull requests

1. Use a feature branch and keep commits focused.
2. Explain the user-visible behavior and any compatibility implications.
3. Include test results and note any limitations.
4. Do not merge a change until CI and review have passed.

## License

Contributions are subject to the [MIT license](../LICENSE).
