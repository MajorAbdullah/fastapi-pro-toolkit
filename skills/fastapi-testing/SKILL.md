---
name: fastapi-testing
description: Write fast, reliable tests for FastAPI and Python services with pytest — async tests with httpx AsyncClient + ASGITransport, fixtures and conftest design, isolated database tests with transaction rollback, dependency_overrides, factories, mocking external HTTP/LLM calls, parametrization, and coverage. Use this whenever the user asks to add tests, write unit/integration tests, fix flaky tests, set up pytest or conftest.py, mock a dependency, or asks "how do I test this endpoint", even if they don't mention pytest by name.
---

# Testing FastAPI Services

Aim for a test pyramid: many fast unit tests of services (no HTTP, no DB), a solid layer of API tests through the ASGI app against a real database, and a few end-to-end smoke tests.

## Setup

```toml
# pyproject.toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "session"
asyncio_default_test_loop_scope = "session"
testpaths = ["tests"]
addopts = "-ra --strict-markers --strict-config"
markers = ["integration: needs external services"]
filterwarnings = ["error"]          # surface deprecations early; relax selectively

[tool.coverage.run]
source = ["app"]
branch = true
[tool.coverage.report]
fail_under = 85
skip_covered = true
```

Dev deps: `pytest pytest-asyncio httpx pytest-cov respx polyfactory` (plus `testcontainers` for a real Postgres).

Test against the **same database engine you run in production**. SQLite hides Postgres-specific behavior (JSONB, constraints, case sensitivity, locking).

## conftest.py — isolated DB per test via rollback

```python
# tests/conftest.py
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine
from testcontainers.postgres import PostgresContainer

from app.api.deps import get_session
from app.core.db import Base
from app.main import create_app


@pytest.fixture(scope="session")
def postgres_url() -> AsyncIterator[str]:
    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as pg:
        yield pg.get_connection_url()


@pytest.fixture(scope="session")
async def engine(postgres_url: str):
    engine = create_async_engine(postgres_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)   # or run Alembic migrations here
    yield engine
    await engine.dispose()


@pytest.fixture
async def connection(engine) -> AsyncIterator[AsyncConnection]:
    async with engine.connect() as conn:
        trans = await conn.begin()
        yield conn
        await trans.rollback()          # every test leaves the DB untouched


@pytest.fixture
async def session(connection: AsyncConnection) -> AsyncIterator[AsyncSession]:
    # savepoint mode: app code may call commit() without ending the outer transaction
    async with AsyncSession(bind=connection, expire_on_commit=False,
                            join_transaction_mode="create_savepoint") as s:
        yield s


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
```

Note `ASGITransport` does **not** run lifespan events. If a test needs them, wrap with `asgi-lifespan`'s `LifespanManager(app)` or override the dependencies that rely on `app.state`.

## Auth helpers

```python
@pytest.fixture
async def user(session) -> User:
    return await UserFactory.create_async(session)

@pytest.fixture
def auth_headers(user) -> dict[str, str]:
    token = create_token(str(user.id), token_type="access", expires=timedelta(minutes=5))
    return {"Authorization": f"Bearer {token}"}
```

For routes where auth is not the subject under test, override `get_current_user` instead.

## Factories instead of hand-built dicts

```python
from polyfactory.factories.pydantic_factory import ModelFactory

class UserCreateFactory(ModelFactory[UserCreate]):
    password = "correct-horse-battery-staple"
```

Factories keep tests focused on the one field that matters.

## API test example

```python
async def test_create_user_returns_201_and_hides_password(client):
    payload = UserCreateFactory.build().model_dump()
    resp = await client.post("/api/v1/users", json=payload)

    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == payload["email"]
    assert "password" not in body and "hashed_password" not in body
    assert resp.headers["location"].endswith(body["id"])


async def test_create_user_duplicate_email_returns_409(client, user):
    resp = await client.post("/api/v1/users", json={**UserCreateFactory.build().model_dump(), "email": user.email})
    assert resp.status_code == 409
    assert resp.json()["type"] == "conflict"


@pytest.mark.parametrize("limit", [0, 101, -1])
async def test_list_users_rejects_bad_limit(client, limit):
    resp = await client.get("/api/v1/users", params={"limit": limit})
    assert resp.status_code == 422
```

What to test for each endpoint: happy path, validation failure, not found, unauthenticated (401), unauthorized/other user's object (403/404), conflict, and that sensitive fields are absent.

## Unit-testing services without a DB

```python
class FakeUserRepo:
    def __init__(self): self.items = {}
    async def get_by_email(self, email): return next((u for u in self.items.values() if u.email == email), None)
    async def add(self, user): self.items[user.id] = user; return user

async def test_service_normalizes_email():
    service = UserService(FakeUserRepo())
    user = await service.create(UserCreateFactory.build(email="Foo@Example.com"))
    assert user.email == "foo@example.com"
```

Hand-written fakes are usually clearer than `MagicMock` for repositories. Use `AsyncMock` for one-off async collaborators.

## Mocking outbound HTTP

```python
import respx, httpx

@respx.mock
async def test_weather_client():
    respx.get("https://api.weather.test/v1/now").mock(return_value=httpx.Response(200, json={"temp": 21}))
    ...
```

Never hit real third-party APIs (including LLM providers) in the default test run; put those behind `@pytest.mark.integration`.

## Anti-flakiness rules
- No `sleep()`; await the real condition or inject a clock.
- Freeze time (`time-machine`) for anything with expiry.
- Don't depend on test order or shared mutable module state.
- Seed randomness; avoid asserting on ordering unless the API guarantees it.
- Run `pytest -p no:randomly` only to debug; normally run randomized (`pytest-randomly`) to catch hidden coupling.

## Commands
```bash
uv run pytest -q
uv run pytest --cov --cov-report=term-missing
uv run pytest -k users -x --lf          # rerun last failures, stop on first
uv run pytest -n auto                   # pytest-xdist parallelism (one DB/container per worker)
```
