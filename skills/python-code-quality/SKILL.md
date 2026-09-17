---
name: python-code-quality
description: Set up and enforce Python code quality — uv project management, ruff linting and formatting, mypy/pyright strict typing, pre-commit hooks, pyproject.toml configuration, naming and module conventions, docstrings, clean-code refactoring, and conventional commits. Use this whenever the user starts a Python project, asks to "clean up", lint, format or refactor code, configures pyproject.toml, pre-commit, ruff, black, flake8, isort or mypy, or asks for Python best practices or a code review focused on style and maintainability.
---

# Python Code Quality

Automate everything that can be automated so reviews focus on design, not commas. One toolchain: **uv** (env + deps), **ruff** (lint + format, replaces black/isort/flake8/pyupgrade), **mypy** or **pyright** (types), **pre-commit** (runs it all before each commit).

## Project management with uv

```bash
uv init --package my-service      # src/ layout
uv add fastapi pydantic-settings
uv add --dev ruff mypy pytest pre-commit
uv run pytest                      # always run tools through the project env
uv lock --upgrade-package fastapi  # targeted upgrades
```

Commit `uv.lock`. Pin `requires-python`. Don't mix pip installs into a uv-managed venv.

## Tool configuration

Drop-in config: `assets/pyproject-quality.toml` (merge into `pyproject.toml`) and `assets/.pre-commit-config.yaml`. Highlights:

```toml
[tool.ruff]
line-length = 100
target-version = "py312"
src = ["src", "tests"]

[tool.ruff.lint]
select = [
  "E", "W", "F",   # pycodestyle, pyflakes
  "I",             # isort
  "B",             # bugbear: likely bugs
  "UP",            # pyupgrade: modern syntax
  "N",             # naming
  "S",             # bandit security
  "ASYNC",         # blocking calls in async code
  "FAST",          # FastAPI-specific rules
  "SIM", "C4", "RET", "PTH", "RUF", "PL", "TID", "T20", "ERA",
]
ignore = ["PLR0913"]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S101", "PLR2004"]

[tool.mypy]
strict = true
plugins = ["pydantic.mypy"]
```

`ASYNC` and `FAST` rule groups are especially valuable for FastAPI code: they catch blocking calls inside `async def` and redundant/incorrect FastAPI patterns (e.g. missing `Annotated`).

Commands:
```bash
uv run ruff check --fix .
uv run ruff format .
uv run mypy src
uv run pre-commit install        # once per clone
uv run pre-commit run --all-files
```

When adopting on an existing codebase: format first in a single commit (add its hash to `.git-blame-ignore-revs`), then enable lint rules incrementally rather than adding hundreds of `noqa`s.

## Conventions

**Naming** — `snake_case` functions/variables/modules, `PascalCase` classes, `UPPER_SNAKE` constants, `_leading_underscore` for internal. Names describe intent: `active_users` not `lst2`; booleans read as questions: `is_active`, `has_access`.

**Functions** — do one thing; ≤ ~40 lines is a good smell threshold; ≤ 4-5 params (group the rest into a dataclass/model); prefer keyword-only args for flags: `def send(msg, *, retry: bool = False)`. Return early instead of nesting.

**Imports** — absolute imports (`from app.users.service import ...`); no wildcard imports; no import-time side effects (connections, network calls, heavy computation).

**Errors** — catch specific exceptions; never bare `except:`; chain with `raise ... from exc`; define a small exception hierarchy per domain; don't use exceptions for normal control flow.

**Data** — `dataclass(slots=True, frozen=True)` for internal value objects; Pydantic models at I/O boundaries (API, config, external data). Don't pass raw dicts through several layers.

**Mutability** — never use mutable default args (`def f(x=[])`); prefer tuples/frozensets for constants.

**Paths & time** — `pathlib.Path` over `os.path`; timezone-aware datetimes only (`datetime.now(UTC)`).

**Comments & docstrings** — comments explain *why*, code explains *what*. Docstrings (Google style) on public functions/classes whose behavior isn't obvious from the signature:

```python
def allocate(order: Order, batches: Sequence[Batch]) -> BatchRef:
    """Allocate an order line to the earliest available batch.

    Raises:
        OutOfStockError: if no batch can satisfy the quantity.
    """
```

Delete commented-out code — git remembers it.

## Refactoring playbook
When asked to clean up code, work in small, behavior-preserving steps and keep tests green after each:
1. Add/confirm tests around the code first.
2. Run ruff/mypy; fix mechanical issues.
3. Extract functions from long blocks; name them after intent.
4. Replace dict-shaped data with typed models.
5. Move I/O to the edges; keep core logic pure and easy to test.
6. Remove duplication only when the duplicates change for the same reason.
Explain each change briefly and avoid mixing refactors with feature changes in one commit.

## Commits & PRs
Conventional commits: `feat(users): add email verification`, `fix(auth): reject expired refresh tokens`, `refactor:`, `test:`, `docs:`, `chore:`. Keep PRs small and focused; include what/why and how it was tested.
