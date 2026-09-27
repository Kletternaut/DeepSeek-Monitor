# Contributing

Thank you for your interest in **DeepSeek-Monitor**! This project is an
independent open-source tool and is in no way affiliated with DeepSeek (深度求索).

## Before you start

- Read the [README.md](README.md) and [INSTALLATION.md](INSTALLATION.md).
- Open an issue for larger changes before investing much time.

## Development environment

```bash
git clone --branch main https://github.com/Kletternaut/DeepSeek-Monitor.git
cd DeepSeek-Monitor/deepseek-proxy
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install pytest ruff black
```

## Tests

```bash
cd deepseek-proxy
.venv/bin/python -m pytest
```

## Code style

- Python 3.10+, `from __future__ import annotations`.
- `ruff` and `black` (line length 100, see `pyproject.toml`).
- Type hints and docstrings (rST style).
- Tests for new logic, especially for tariff/cost calculation.

## Pull requests

1. Fork the repository and create a feature branch.
2. Keep commits small and understandable.
3. Keep linters + tests green (`ruff check . && black --check . && pytest`).
4. Describe in the PR what and why.

## License

All contributions are subject to the [MIT license](LICENSE).
