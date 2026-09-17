---
name: fastapi-project-structure
description: Scaffold and organize production FastAPI projects — layered/domain-based layout, app factory, lifespan events, typed settings with pydantic-settings, and pyproject/uv setup. Use this whenever the user starts a new FastAPI service, asks "how should I structure my API", wants to reorganize a messy main.py, adds a new domain/module to an existing app, or mentions settings, config, environment variables, or startup/shutdown logic in a FastAPI context.
---

# FastAPI Project Structure

A good layout makes the codebase predictable: anyone (human or agent) should know where a new endpoint, model, or business rule goes without asking. Favor **domain-based** organization once an app has more than ~3 resources; flat "routers/models/schemas" folders scale badly because a single feature ends up spread across the whole tree.

## Recommended layout

```
my-service/
├── pyproject.toml
├── uv.lock
├── .env.example            # committed; real .env is gitignored
├── alembic.ini
├── migrations/
├── src/
│   └── app/
│       ├── __init__.py
│       ├── main.py          # create_app() factory + lifespan
│       ├── core/
│       │   ├── config.py    # Settings (pydantic-settings)
│       │   ├── db.py        # engine / sessionmaker
│       │   ├── security.py
│       │   ├── logging.py
│       │   └── exceptions.py
│       ├── api/
│       │   ├── deps.py      # shared dependencies
│       │   └── v1/
│       │       └── router.py  # includes every domain router
│       └── users/           # one package per domain
│           ├── router.py    # HTTP layer only
│           ├── schemas.py   # Pydantic request/response models
│           ├── models.py    # SQLAlchemy ORM models
│           ├── service.py   # business logic
│           ├── repository.py# data access (optional but recommended)
│           └── exceptions.py
└── tests/
    ├── conftest.py
    └── users/
```

Use the `src/` layout: it stops tests from accidentally importing the working directory instead of the installed package.

### Layer responsibilities

| Layer | Knows about | Must NOT |
|---|---|---|
| `router.py` | HTTP, schemas, dependencies | contain SQL or business rules |
| `service.py` | domain rules, repositories | import `fastapi.Request`/`Response` or raise `HTTPException` |
| `repository.py` | ORM/session | contain business decisions |
| `schemas.py` | Pydantic only | import ORM models at module level |

Keeping `HTTPException` out of services means the same logic is reusable from CLI jobs, workers, and tests. Services raise domain exceptions; a global handler maps them to HTTP (see the `fastapi-endpoints` skill).

## App factory + lifespan

Always use the `lifespan` context manager — `@app.on_event` is deprecated.

```python
# src/app/main.py
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.db import engine
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    # open shared clients here (http client, redis, LLM SDK) and store on app.state
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.project_name,
        version=settings.version,
        lifespan=lifespan,
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url=None,
    )
    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
```

A factory lets tests build a fresh app with overridden settings.

## Typed settings

```python
# src/app/core/config.py
from functools import lru_cache
from typing import Literal

from pydantic import PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_nested_delimiter="__", extra="ignore")

    project_name: str = "my-service"
    version: str = "0.1.0"
    environment: Literal["local", "staging", "production"] = "local"
    enable_docs: bool = True

    database_url: PostgresDsn
    jwt_secret: SecretStr
    cors_origins: list[str] = []


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
```

Rules: never read `os.environ` directly elsewhere; use `SecretStr` for secrets so they don't leak into logs/reprs; fail fast at startup when required config is missing; keep `.env.example` in sync.

## Versioned router aggregation

```python
# src/app/api/v1/router.py
from fastapi import APIRouter
from app.users.router import router as users_router

api_router = APIRouter()
api_router.include_router(users_router, prefix="/users", tags=["users"])
```

## pyproject.toml baseline

Prefer `uv` for dependency management (fast, lockfile-based). Minimal:

```toml
[project]
name = "my-service"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "fastapi[standard]",
  "pydantic-settings",
  "sqlalchemy[asyncio]",
  "asyncpg",
  "alembic",
]

[dependency-groups]
dev = ["pytest", "pytest-asyncio", "httpx", "ruff", "mypy", "pre-commit"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/app"]
```

Run locally with `uv run fastapi dev src/app/main.py`.

## Scaffolding

To generate the skeleton quickly, run the bundled script:

```bash
python ${CLAUDE_PLUGIN_ROOT}/skills/fastapi-project-structure/scripts/scaffold.py <project-dir> [--domain users --domain items]
```

It only creates files that don't exist, so it's safe to rerun to add a new domain.

## Checklist when adding a new domain
1. Create `app/<domain>/` with router, schemas, models, service, (repository).
2. Register the router in `api/v1/router.py`.
3. Import the models where Alembic's `env.py` can see them, then generate a migration.
4. Add `tests/<domain>/`.
