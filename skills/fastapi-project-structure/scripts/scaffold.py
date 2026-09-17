#!/usr/bin/env python3
"""Scaffold a domain-based FastAPI project. Never overwrites existing files."""
from __future__ import annotations

import argparse
from pathlib import Path

CORE_FILES: dict[str, str] = {
    "src/app/__init__.py": "",
    "src/app/main.py": '''from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.router import api_router
from app.core.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.project_name, lifespan=lifespan)
    app.include_router(api_router, prefix="/api/v1")

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
''',
    "src/app/core/__init__.py": "",
    "src/app/core/config.py": '''from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    project_name: str = "my-service"
    environment: str = "local"
    database_url: str = "sqlite+aiosqlite:///./dev.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
''',
    "src/app/core/exceptions.py": '''class DomainError(Exception):
    """Base class for business-rule errors raised by services."""


class NotFoundError(DomainError):
    pass


class ConflictError(DomainError):
    pass
''',
    "src/app/api/__init__.py": "",
    "src/app/api/deps.py": '"""Shared FastAPI dependencies."""\n',
    "src/app/api/v1/__init__.py": "",
    "tests/__init__.py": "",
    "tests/conftest.py": '''import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


@pytest.fixture
async def client():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
''',
    ".env.example": "DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/app\n",
    ".gitignore": ".env\n__pycache__/\n.venv/\n.mypy_cache/\n.ruff_cache/\n.pytest_cache/\n*.db\n",
}

DOMAIN_FILES: dict[str, str] = {
    "__init__.py": "",
    "router.py": '''from fastapi import APIRouter

router = APIRouter()
''',
    "schemas.py": '"""Pydantic request/response schemas for {domain}."""\n',
    "models.py": '"""SQLAlchemy models for {domain}."""\n',
    "service.py": '"""Business logic for {domain}. Raise domain exceptions, not HTTPException."""\n',
    "repository.py": '"""Data access for {domain}."""\n',
}


def write(path: Path, content: str) -> None:
    if path.exists():
        print(f"skip   {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    print(f"create {path}")


def build_router(domains: list[str]) -> str:
    lines = ["from fastapi import APIRouter", ""]
    lines += [f"from app.{d}.router import router as {d}_router" for d in sorted(domains)]
    lines += ["", "api_router = APIRouter()"]
    lines += [f'api_router.include_router({d}_router, prefix="/{d}", tags=["{d}"])' for d in domains]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--domain", action="append", default=[])
    args = parser.parse_args()
    root: Path = args.root
    domains: list[str] = [d.strip().lower() for d in args.domain]

    for rel, content in CORE_FILES.items():
        write(root / rel, content)
    for d in domains:
        if not d.isidentifier():
            raise SystemExit(f"invalid domain name: {d!r}")
        for name, content in DOMAIN_FILES.items():
            write(root / "src/app" / d / name, content.format(domain=d))
        write(root / "tests" / d / "__init__.py", "")

    router_path = root / "src/app/api/v1/router.py"
    if not router_path.exists():
        write(router_path, build_router(domains))
    else:
        print(f"note   {router_path} exists; register new domains manually: {domains}")


if __name__ == "__main__":
    main()
