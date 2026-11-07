---
name: python-style
description: Apply this project's Python conventions when creating, editing, or reviewing backend code, tests, or evaluation scripts.
---

# Python Style

- Target Python >=3.13.17,<3.14; the development environment uses 3.13.17. Runtime and dependency commands are defined in [python-project-ops](../python-project-ops/SKILL.md).
- Current application code lives in `backend/src/backend/`, with tests in `backend/tests/`. Use `backend` imports; the `ai_news_agent` package in `docs/requirements.md` is a proposed layout, not the current package name.
- Add type hints to public function signatures. Use Google-style docstrings for public modules, classes, functions, and methods; document relevant assumptions and exceptions.
- Write inline and explanatory comments in Japanese. Explain intent rather than obvious syntax.
- Prefer small functions and explicit exception handling. Preserve useful failure categories without exposing secrets in error messages.
- Keep external access in tools, deterministic validation and persistence in services, and HTTP handling in API routes. Introduce these modules as their features are implemented.
- Validate external API responses and agent outputs with Pydantic. Do not mark unverified sources or dates as verified.
- Keep DB writes outside LLM-exposed tools; services save validated results.
- Use `pathlib.Path` for filesystem paths; follow [path-and-io](../path-and-io/SKILL.md).
- Follow the project's Ruff configuration. Avoid unrelated formatting changes.
