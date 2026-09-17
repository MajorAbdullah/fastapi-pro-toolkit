---
name: fastapi-async-database
description: Async database access for FastAPI with SQLAlchemy 2.0 (typed Mapped models, AsyncSession, select() queries), the repository pattern, avoiding N+1 and lazy-load errors, transactions, connection pooling, and Alembic migrations. Use this whenever the user defines ORM models, writes queries, sets up Postgres/MySQL/SQLite with FastAPI, sees "MissingGreenlet" or "greenlet_spawn" errors, creates or reviews migrations, or asks about SQLModel/SQLAlchemy/Alembic.
---

# Async Database with SQLAlchemy 2.0

## Engine & session factory

```python
# app/core/db.py
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, MappedAsDataclass
from sqlalchemy import MetaData

from app.core.config import get_settings

NAMING = {  # deterministic constraint names -> clean Alembic diffs
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)

engine = create_async_engine(
    str(get_settings().database_url),   # e.g. postgresql+asyncpg://...
    pool_size=10,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=1800,
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
```

`expire_on_commit=False` is essential in async code: otherwise touching attributes after commit triggers an implicit lazy load, which raises `MissingGreenlet`.

Pool sizing: total connections ≈ `(pool_size + max_overflow) × workers × replicas`. Keep this below the database's `max_connections`; use PgBouncer when it isn't.

## Typed models

```python
# app/users/models.py
import uuid
from datetime import datetime
from sqlalchemy import ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120))
    hashed_password: Mapped[str]
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    orders: Mapped[list["Order"]] = relationship(back_populates="user", lazy="raise")
```

`lazy="raise"` turns accidental lazy loads into loud errors during development instead of silent N+1 queries (or `MissingGreenlet` in async). Load relationships explicitly.

## Repository pattern

```python
# app/users/repository.py
from collections.abc import Sequence
from uuid import UUID
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.users.models import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, user_id: UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        return await self.session.scalar(select(User).where(User.email == email))

    async def list(self, *, limit: int, offset: int, q: str | None = None) -> tuple[Sequence[User], int]:
        stmt = select(User)
        if q:
            stmt = stmt.where(User.full_name.ilike(f"%{q}%"))   # bound parameter, safe
        total = await self.session.scalar(select(func.count()).select_from(stmt.subquery()))
        rows = await self.session.scalars(stmt.order_by(User.created_at.desc()).limit(limit).offset(offset))
        return rows.all(), total or 0

    async def get_with_orders(self, user_id: UUID) -> User | None:
        stmt = select(User).where(User.id == user_id).options(selectinload(User.orders))
        return await self.session.scalar(stmt)

    async def add(self, user: User) -> User:
        self.session.add(user)
        await self.session.flush()     # assigns PKs, surfaces constraint errors early
        return user
```

### Loading strategy cheat-sheet
- `selectinload` — one-to-many / many-to-many collections (default choice).
- `joinedload` — many-to-one / one-to-one scalars.
- Never loop over parents and query children one at a time.

## Service with integrity handling

```python
from sqlalchemy.exc import IntegrityError

async def create(self, data: UserCreate) -> User:
    user = User(email=data.email.lower(), full_name=data.full_name,
                hashed_password=hash_password(data.password))
    try:
        return await self.repo.add(user)
    except IntegrityError as exc:
        raise ConflictError("Email already registered") from exc
```

Rely on the database's unique constraint rather than "check then insert" — the check-first approach races under concurrency.

## Transactions
- One session per request (see `fastapi-dependency-injection`).
- For multi-step atomic work inside a request use `async with session.begin_nested():` (savepoint).
- Never share an `AsyncSession` across concurrent tasks (`asyncio.gather`) — sessions aren't concurrency-safe. Give each task its own session.
- For optimistic locking, add a `version_id` column with `__mapper_args__ = {"version_id_col": version}`.

## Raw SQL
Use `text()` with bound params only: `await session.execute(text("... where id = :id"), {"id": x})`. Never f-string user input into SQL.

## Alembic (async)

```bash
alembic init -t async migrations
```

In `migrations/env.py`, set `target_metadata = Base.metadata` and make sure **every models module is imported** (e.g. an `app/models.py` that imports all domain models) — otherwise autogenerate thinks tables were deleted. Read the URL from `get_settings()` instead of `alembic.ini`.

Workflow:
```bash
alembic revision --autogenerate -m "add users table"
# ALWAYS read the generated file: autogenerate misses renames (sees drop+add),
# server defaults, enum changes and some index changes.
alembic upgrade head
alembic downgrade -1   # verify downgrade works before merging
```

Migration safety rules for production:
- Add columns as nullable (or with server default), backfill, then add NOT NULL in a later migration.
- Create indexes on big Postgres tables with `postgresql_concurrently=True` inside `with op.get_context().autocommit_block():`.
- Never edit a migration that has already run in a shared environment; add a new one.
- Run migrations as a separate deploy step, not in app startup (multiple workers would race).

## SQLModel note
SQLModel is fine for small apps, but for larger services prefer plain SQLAlchemy models + separate Pydantic schemas; it keeps persistence and API contracts independent.
