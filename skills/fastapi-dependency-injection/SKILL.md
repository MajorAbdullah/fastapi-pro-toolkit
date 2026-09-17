---
name: fastapi-dependency-injection
description: Use FastAPI's dependency injection system correctly — Depends with Annotated type aliases, yield dependencies for resources (DB sessions, clients), sub-dependencies, class-based and parametrized dependencies, caching, app.state shared clients, and dependency_overrides for testing. Use this whenever the user writes Depends(), wires services/repositories into routes, manages DB sessions or HTTP clients per request, asks about "global" objects in FastAPI, or needs to mock something in FastAPI tests.
---

# FastAPI Dependency Injection

DI is how FastAPI keeps routes thin and testable. Every external resource (session, client, current user, settings, service) should arrive via a dependency so tests can swap it with `app.dependency_overrides`.

## Use `Annotated` aliases

Define each dependency once and reuse the alias. This removes repetition and keeps signatures readable.

```python
# app/api/deps.py
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import SessionLocal


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
```

## Yield dependencies = request-scoped resources

Code after `yield` runs after the response is produced. Use it for cleanup (close, commit/rollback). Important details:
- Choose **one** transaction strategy: either commit in the dependency (unit-of-work per request, shown above) or commit explicitly in services. Mixing both causes surprise double commits.
- If you need something to finish **before** the response is sent (e.g. commit that could fail), commit in the service so the error becomes a proper HTTP error rather than a failure after the response started.
- Don't swallow exceptions after `yield`; re-raise.

## Layered wiring: session → repository → service

```python
# app/users/deps.py
from typing import Annotated
from fastapi import Depends

from app.api.deps import SessionDep
from app.users.repository import UserRepository
from app.users.service import UserService


def get_user_repository(session: SessionDep) -> UserRepository:
    return UserRepository(session)


def get_user_service(repo: Annotated[UserRepository, Depends(get_user_repository)]) -> UserService:
    return UserService(repo)


UserServiceDep = Annotated[UserService, Depends(get_user_service)]
```

Within a single request FastAPI caches each dependency, so every consumer of `SessionDep` receives the same session. Use `Depends(fn, use_cache=False)` only when you truly want a fresh instance.

## Shared, long-lived clients

Create expensive clients (httpx, Redis, LLM SDK) once in `lifespan`, store them on `app.state`, and expose via a dependency. Never create an `httpx.AsyncClient` per request — it throws away connection pooling.

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    async with httpx.AsyncClient(timeout=10) as http:
        app.state.http = http
        yield

def get_http(request: Request) -> httpx.AsyncClient:
    return request.app.state.http

HttpDep = Annotated[httpx.AsyncClient, Depends(get_http)]
```

Avoid module-level globals that open connections at import time; they break tests and multi-worker startup.

## Parametrized dependencies

Use a callable class (or closure) when the dependency needs configuration:

```python
class RequireRole:
    def __init__(self, *roles: str) -> None:
        self.roles = set(roles)

    def __call__(self, user: CurrentUser) -> User:
        if not self.roles & set(user.roles):
            raise PermissionDeniedError("Insufficient role")
        return user

AdminUser = Annotated[User, Depends(RequireRole("admin"))]
```

## Router- and app-level dependencies

Apply cross-cutting checks without adding a parameter to every route:

```python
router = APIRouter(dependencies=[Depends(verify_api_key)])
```

## Sync vs async dependencies
- `async def` for anything doing I/O with async libraries.
- Plain `def` dependencies run in a threadpool — fine for cheap or blocking-sync work, but don't do blocking I/O inside `async def`.

## Testing with overrides

```python
app.dependency_overrides[get_session] = lambda: test_session
app.dependency_overrides[get_current_user] = lambda: make_user(roles=["admin"])
...
app.dependency_overrides.clear()     # always clear in fixture teardown
```

Override the *original function object* (e.g. `get_session`), not the `Annotated` alias.

## Anti-patterns
- Calling dependency functions directly inside routes (`get_session()`) — bypasses cleanup and overrides.
- Doing work in `__init__` of services that requires the event loop.
- Putting request-specific state in module globals.
- Very deep dependency chains that hide what a route really needs; keep it to ~3 levels.
