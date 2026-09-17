---
name: fastapi-endpoints
description: Design and implement clean, RESTful FastAPI endpoints — routers, Pydantic v2 request/response schemas, correct status codes, response_model filtering, pagination, filtering, consistent error responses and global exception handlers. Use this whenever the user writes or reviews a FastAPI route, adds CRUD for a resource, asks about HTTP status codes, error handling, HTTPException, validation errors, pagination, or API design/naming conventions, even if they only say "add an endpoint for X".
---

# FastAPI Endpoints

Endpoints are the public contract of the service. The goals: predictable URLs, explicit input/output schemas, correct status codes, and one consistent error shape.

## Resource & URL conventions
- Plural nouns, no verbs: `GET /users`, `POST /users`, `GET /users/{user_id}`, `PATCH /users/{user_id}`, `DELETE /users/{user_id}`.
- Nested only one level for true ownership: `GET /users/{user_id}/orders`.
- Actions that aren't CRUD become sub-resources: `POST /orders/{id}/cancellation`.
- Version in the prefix (`/api/v1`), not in headers, unless there's a strong reason.

## Status codes
| Situation | Code |
|---|---|
| Read OK | 200 |
| Created | 201 (+ `Location` header when practical) |
| Accepted for async processing | 202 |
| Deleted / no body | 204 |
| Malformed or semantically invalid input | 422 (FastAPI default) / 400 for business-rule failures |
| Not authenticated | 401 |
| Authenticated but forbidden | 403 |
| Not found | 404 |
| Conflict (duplicate, version mismatch) | 409 |
| Rate limited | 429 |

## Separate schemas per direction

Never reuse one model for input and output — it leaks fields (password hashes, internal flags) and lets clients set server-owned fields (id, created_at).

```python
# app/users/schemas.py
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserBase(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=120)


class UserCreate(UserBase):
    password: str = Field(min_length=12, max_length=128)


class UserUpdate(BaseModel):                 # PATCH: everything optional
    full_name: str | None = Field(default=None, min_length=1, max_length=120)


class UserRead(UserBase):
    model_config = ConfigDict(from_attributes=True)   # build from ORM objects
    id: UUID
    created_at: datetime
```

For PATCH, apply only fields the client actually sent: `data = payload.model_dump(exclude_unset=True)`.

## Router pattern

```python
# app/users/router.py
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import CurrentUser
from app.users.deps import UserServiceDep
from app.users.schemas import UserCreate, UserRead, UserUpdate
from app.core.pagination import Page

router = APIRouter()


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user(payload: UserCreate, service: UserServiceDep, response: Response) -> UserRead:
    user = await service.create(payload)
    response.headers["Location"] = f"/api/v1/users/{user.id}"
    return user


@router.get("", response_model=Page[UserRead])
async def list_users(
    service: UserServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> Page[UserRead]:
    items, total = await service.list(limit=limit, offset=offset, q=q)
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/{user_id}", response_model=UserRead)
async def get_user(user_id: UUID, service: UserServiceDep) -> UserRead:
    return await service.get(user_id)          # service raises NotFoundError


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(user_id: UUID, payload: UserUpdate, service: UserServiceDep, _: CurrentUser) -> UserRead:
    return await service.update(user_id, payload.model_dump(exclude_unset=True))


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: UUID, service: UserServiceDep, _: CurrentUser) -> None:
    await service.delete(user_id)
```

Notes:
- Type path params (`UUID`, `int`) so FastAPI validates them for free.
- Always constrain `limit` — unbounded list endpoints are a DoS vector.
- Declare `response_model` so extra attributes on ORM objects are filtered out.
- Give each route a meaningful function name; it becomes the OpenAPI `operationId` that client generators use.

## Generic pagination

```python
# app/core/pagination.py
from pydantic import BaseModel

class Page[T](BaseModel):      # Python 3.12 generic syntax; use Generic[T] on older versions
    items: list[T]
    total: int
    limit: int
    offset: int
```

Use offset pagination for admin/small tables; switch to cursor (keyset) pagination for large or frequently-changing tables, since `OFFSET` gets slow and skips/duplicates rows under concurrent writes.

## Consistent errors

Services raise domain exceptions; one place converts them to HTTP. Use an RFC 9457 "problem details"-style body so clients parse one shape everywhere.

```python
# app/core/exceptions.py
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class DomainError(Exception):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "bad_request"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class NotFoundError(DomainError):
    status_code, code = status.HTTP_404_NOT_FOUND, "not_found"


class ConflictError(DomainError):
    status_code, code = status.HTTP_409_CONFLICT, "conflict"


class PermissionDeniedError(DomainError):
    status_code, code = status.HTTP_403_FORBIDDEN, "forbidden"


def _problem(status_code: int, code: str, detail: str, **extra) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        media_type="application/problem+json",
        content={"type": code, "title": code.replace("_", " ").title(),
                 "status": status_code, "detail": detail, **extra},
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def domain_handler(_: Request, exc: DomainError) -> JSONResponse:
        return _problem(exc.status_code, exc.code, exc.detail)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _problem(422, "validation_error", "Request validation failed",
                        errors=[{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()])

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        # log with traceback here; never echo internals to the client
        return _problem(500, "internal_error", "An unexpected error occurred")
```

Do not include `exc.errors()` "input" values in responses for sensitive endpoints — they may echo passwords back.

## Document the contract
- Add `summary=`/docstrings and `responses={404: {...}}` for non-default errors so OpenAPI is accurate.
- Use `tags` per domain.
- Add `examples` via `Field(examples=[...])` or `model_config = ConfigDict(json_schema_extra=...)`.

## Review checklist
- [ ] Separate Create / Update / Read schemas; no ORM model returned without `response_model`
- [ ] Correct status code (201 on create, 204 on delete)
- [ ] Path/query params typed and bounded
- [ ] No business logic or SQL in the route body
- [ ] No `HTTPException` inside services
- [ ] Auth dependency present on mutating routes
- [ ] Errors follow the shared shape
