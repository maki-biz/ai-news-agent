---
name: path-and-io
description: Handle local file paths and I/O for this project's backend settings, SQLite storage, evaluation datasets, and generated results.
---

# Path and I/O

- Use `pathlib.Path`; avoid hard-coded machine-specific absolute paths.
- Current code lives in `backend/src/backend/`.
- Backend commands run from `backend/`. The existing `backend/src/backend/config.py` loads `.env` relative to the working directory, so use `backend/` when invoking the app. Do not silently change this behavior.
- For new persistent storage, define and document a base directory through application settings. Resolve relative paths against that explicit base rather than an incidental working directory.
- SQLite storage is planned, not yet configured. When implementing it, document the chosen database location and exclude runtime databases and SQLite WAL/SHM files from Git. Use SQLAlchemy's URL facilities rather than treating a database URL as a filesystem path.
- The planned evaluation dataset location is `evals/datasets/` at repository root. Keep generated evaluation results separate from input cases, for example under `evals/results/`; document and create the chosen directory when needed.
- Create output parent directories explicitly with `path.parent.mkdir(parents=True, exist_ok=True)`. Use UTF-8 for text and descriptive filenames with run IDs or dates for repeated outputs.
- Avoid overwriting existing artifacts unless the task authorizes replacement. Keep raw/external inputs immutable if such directories are introduced.
- Follow [safe-data-handling](../safe-data-handling/SKILL.md) for article bodies, API responses, logs, databases, and evaluation data. Do not create placeholder directories just to match the planned architecture.
