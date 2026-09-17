---
description: Scaffold a new production-ready FastAPI service (or add domains to an existing one)
argument-hint: "<project-dir> [domain ...]"
allowed-tools: Bash(python3:*), Bash(uv:*), Read, Write, Edit
---

Scaffold a FastAPI project using the `fastapi-project-structure` skill.

Arguments: `$ARGUMENTS` — first value is the project directory, the rest are domain names (e.g. `my-api users orders`).

Steps:
1. Run `python3 ${CLAUDE_PLUGIN_ROOT}/skills/fastapi-project-structure/scripts/scaffold.py <dir> --domain <d1> --domain <d2> ...`.
2. If `pyproject.toml` doesn't exist, create one following the skill's baseline, and merge the tool config from the `python-code-quality` skill's `assets/pyproject-quality.toml`.
3. For each domain, fill in a minimal but complete vertical slice following `fastapi-endpoints`, `fastapi-dependency-injection` and `fastapi-async-database`: schemas (Create/Update/Read), model, repository, service, router, and tests following `fastapi-testing`.
4. Copy `.pre-commit-config.yaml` from `python-code-quality/assets/` and the Dockerfile/.dockerignore from `fastapi-deployment/assets/`.
5. Run `uv sync`, `uv run ruff check --fix .`, `uv run ruff format .` and `uv run pytest -q` if uv is available, and fix any failures.
6. Summarize the created structure and the next commands the user should run.
