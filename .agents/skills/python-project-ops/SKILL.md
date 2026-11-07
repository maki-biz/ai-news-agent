---
name: python-project-ops
description: Manage this project's Python dependencies with uv and run backend tests, linting, formatting, and runtime checks.
---

# Python Project Operations

## Runtime and working directory

- Support Python **>=3.13.17,<3.14**. Pin the development environment to **3.13.17**; use the same version for baseline tests and any future CI.
- Keep `backend/.python-version` set to `3.13.17` and `backend/pyproject.toml`'s `requires-python` set to `>=3.13.17,<3.14`. Regenerate `backend/uv.lock` with uv after changing the requirement; do not edit the lockfile by hand.
- Run the commands below from `backend/`. Application code is in `src/backend/`, tests in `tests/`, and the virtual environment is `.venv/` relative to that directory.

## Dependencies

- Use uv for installation, synchronization, addition, removal, and updates. Do not use pip, poetry, conda, or manually maintained requirements files.
- Use `uv add <package>` or `uv add --group dev <package>`. Review changes to `pyproject.toml` and `uv.lock`.
- After manually changing dependency metadata, run `uv lock` and `uv sync --locked`. Do not upgrade unrelated dependencies.

```bash
uv sync --locked
uv run python --version
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

- For formatting edits, run `uv run ruff format` on the affected paths, then check the diff.
- Mock external APIs in automated tests. Live OpenAI/NVD checks are separate integration checks; keep credentials out of test fixtures and output.
- Run relevant tests for behavioral changes and the full backend suite before completion when runtime or dependency settings change.
- Mypy, notebook tooling, and a CI workflow are not currently configured. Do not prescribe them as existing required checks; add them only when the task calls for them.
- Root-level `scripts/check_no_raw_data_commit.py` and `scripts/check_no_sensitive_patterns.py` exist. The former only checks tracked raw/external data; the latter may print matching secret values. Do not run it against real `.env` files or sensitive data without first providing safe redaction/exclusion. Neither proves that all data is safe or that CI exists.
