---
name: python-typing-pydantic
description: Write precise Python type hints and Pydantic v2 models — modern typing syntax (X | None, generics, TypeVar/PEP 695, Protocol, Literal, TypedDict, Annotated), Pydantic v2 validators, computed fields, model_config, discriminated unions, serialization aliases, and migrating from Pydantic v1. Use this whenever the user defines data models or schemas, adds validation, sees mypy/pyright errors, asks about type hints, Protocols, generics, or hits Pydantic errors like "validator is deprecated", "orm_mode", "parse_obj", or ".dict()".
---

# Python Typing & Pydantic v2

Types are documentation that the tooling checks. Pydantic turns those types into runtime validation at the system's boundaries.

## Modern typing essentials (Python 3.12+)

```python
from collections.abc import Callable, Iterable, Mapping, Sequence, AsyncIterator
from typing import Annotated, Literal, Protocol, Self, TypedDict, overload

def first(items: Sequence[str]) -> str | None: ...          # not Optional[...], not List[...]

type UserId = int                                            # PEP 695 type alias

def chunk[T](items: Sequence[T], size: int) -> list[list[T]]:  # PEP 695 generic function
    return [list(items[i:i + size]) for i in range(0, len(items), size)]

Status = Literal["pending", "paid", "cancelled"]
```

Guidelines:
- **Accept abstract, return concrete**: parameters as `Sequence`/`Mapping`/`Iterable`; return `list`/`dict`.
- Avoid `Any`; use `object` when you truly accept anything, and narrow with `isinstance`.
- `Protocol` for structural interfaces — perfect for repositories and external clients, so tests can pass fakes without inheritance:

```python
class UserRepo(Protocol):
    async def get(self, user_id: UUID) -> User | None: ...
    async def add(self, user: User) -> User: ...
```

- `TypedDict` for dict-shaped data you don't own (JSON from a library); Pydantic when you need validation.
- `Self` for fluent/builder returns and alternate constructors.
- Use `typing.cast` sparingly and `# type: ignore[code]` only with a specific error code and a reason.
- Run mypy/pyright in strict mode on new code (config in `python-code-quality`).

## Pydantic v2 models

```python
from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import (BaseModel, ConfigDict, Field, computed_field,
                      field_validator, model_validator, AfterValidator)

def _strip_lower(v: str) -> str:
    return v.strip().lower()

NormalizedEmail = Annotated[str, AfterValidator(_strip_lower), Field(max_length=320)]
PositiveMoney = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]


class OrderLine(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    sku: str = Field(pattern=r"^[A-Z0-9-]{3,32}$")
    quantity: int = Field(ge=1, le=1000)
    unit_price: PositiveMoney

    @computed_field  # included in serialization and JSON schema
    @property
    def total(self) -> Decimal:
        return self.unit_price * self.quantity


class OrderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_email: NormalizedEmail
    lines: list[OrderLine] = Field(min_length=1, max_length=100)
    deliver_after: datetime | None = None
    deliver_before: datetime | None = None

    @field_validator("lines")
    @classmethod
    def unique_skus(cls, lines: list[OrderLine]) -> list[OrderLine]:
        skus = [line.sku for line in lines]
        if len(skus) != len(set(skus)):
            raise ValueError("duplicate SKUs; merge quantities instead")
        return lines

    @model_validator(mode="after")
    def check_window(self) -> "OrderCreate":
        if self.deliver_after and self.deliver_before and self.deliver_after >= self.deliver_before:
            raise ValueError("deliver_after must be earlier than deliver_before")
        return self
```

Tips:
- `extra="forbid"` on request models catches client typos (`"quantitiy"`) instead of silently ignoring them.
- Prefer reusable `Annotated` types over repeating validators.
- Validators must raise `ValueError` (or `AssertionError`), not `HTTPException`.
- Use `Decimal` for money, never `float`.
- `from_attributes=True` to build from ORM objects.

## Discriminated unions

```python
class CardPayment(BaseModel):
    method: Literal["card"]
    token: str

class BankPayment(BaseModel):
    method: Literal["bank"]
    iban: str

Payment = Annotated[CardPayment | BankPayment, Field(discriminator="method")]
```

Faster validation and much clearer error messages than plain unions.

## Aliases (camelCase APIs)

```python
from pydantic.alias_generators import to_camel

class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
```

FastAPI serializes `response_model` output by alias by default, so clients see camelCase while Python stays snake_case.

## Serialization
- `model.model_dump(mode="json", exclude_unset=True, by_alias=True)`
- `model.model_dump_json()` — fastest path to JSON
- `Model.model_validate(obj)` / `Model.model_validate_json(raw)`
- `TypeAdapter(list[Item]).validate_python(data)` for non-model types
- `@field_serializer` for custom output formats

## v1 → v2 migration map

| v1 | v2 |
|---|---|
| `class Config: orm_mode = True` | `model_config = ConfigDict(from_attributes=True)` |
| `@validator` | `@field_validator` (+ `@classmethod`) |
| `@root_validator` | `@model_validator(mode="before"/"after")` |
| `.dict()` / `.json()` | `.model_dump()` / `.model_dump_json()` |
| `.parse_obj()` / `.parse_raw()` | `.model_validate()` / `.model_validate_json()` |
| `.copy(update=...)` | `.model_copy(update=...)` |
| `Field(regex=...)` | `Field(pattern=...)` |
| `BaseSettings` from pydantic | `pydantic-settings` package |
| `const=True` | `Literal[...]` |

`bump-pydantic` can automate much of this; review its output.
