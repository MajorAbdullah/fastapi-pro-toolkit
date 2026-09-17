---
name: fastapi-auth-security
description: Secure FastAPI applications — OAuth2 password flow with JWT access/refresh tokens, password hashing with Argon2 (pwdlib), current-user and role/permission dependencies, API keys, CORS, security headers, rate limiting, secrets handling, and OWASP API Top 10 checks. Use this whenever the user adds login/signup, authentication, authorization, JWTs, API keys, permissions, CORS, or asks whether their FastAPI code is secure — even for a quick "protect this route".
---

# FastAPI Authentication & Security

Security defaults matter more than cleverness. Prefer well-maintained libraries: **PyJWT** for tokens and **pwdlib** (Argon2) for passwords — FastAPI's docs moved to these; `python-jose` and `passlib` are no longer actively maintained.

```bash
uv add pyjwt "pwdlib[argon2]"
```

If the organization already has an identity provider (Auth0, Keycloak, Cognito, Entra ID), validate its tokens instead of issuing your own — don't build auth you don't need.

## Password hashing

```python
# app/core/security.py
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import jwt
from pwdlib import PasswordHash

from app.core.config import get_settings

password_hash = PasswordHash.recommended()   # Argon2id

def hash_password(raw: str) -> str:
    return password_hash.hash(raw)

def verify_password(raw: str, hashed: str) -> bool:
    return password_hash.verify(raw, hashed)
```

## JWT tokens

```python
ALGORITHM = "HS256"   # use RS256/ES256 if other services must verify tokens

def create_token(subject: str, *, token_type: str, expires: timedelta, extra: dict[str, Any] | None = None) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": now + expires,
        "jti": str(uuid4()),          # enables revocation lists
        **(extra or {}),
    }
    return jwt.encode(payload, get_settings().jwt_secret.get_secret_value(), algorithm=ALGORITHM)

def decode_token(token: str, *, expected_type: str) -> dict[str, Any]:
    payload = jwt.decode(
        token,
        get_settings().jwt_secret.get_secret_value(),
        algorithms=[ALGORITHM],        # ALWAYS pin; never trust the header's alg
        options={"require": ["exp", "sub", "type"]},
    )
    if payload["type"] != expected_type:
        raise jwt.InvalidTokenError("wrong token type")
    return payload
```

Guidelines: access tokens short-lived (5–15 min); refresh tokens longer, rotated on use, stored server-side (hash/jti) so they can be revoked; secret ≥ 32 random bytes from the environment; keep PII out of token payloads (they're only base64, not encrypted).

## Current-user dependency

```python
# app/api/deps.py
from typing import Annotated
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")

CREDENTIALS_EXC = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)

async def get_current_user(token: Annotated[str, Depends(oauth2_scheme)], session: SessionDep) -> User:
    try:
        payload = decode_token(token, expected_type="access")
    except jwt.PyJWTError:
        raise CREDENTIALS_EXC from None
    user = await session.get(User, UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise CREDENTIALS_EXC
    return user

CurrentUser = Annotated[User, Depends(get_current_user)]
```

(`HTTPException` is fine here — this is the HTTP layer, not a service.)

## Login endpoint

```python
from fastapi.security import OAuth2PasswordRequestForm

@router.post("/token", response_model=TokenPair)
async def login(form: Annotated[OAuth2PasswordRequestForm, Depends()], repo: UserRepoDep) -> TokenPair:
    user = await repo.get_by_email(form.username.lower())
    # verify even when user is missing to keep timing similar (limits user enumeration)
    hashed = user.hashed_password if user else DUMMY_HASH
    if not verify_password(form.password, hashed) or user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Incorrect email or password",
                            headers={"WWW-Authenticate": "Bearer"})
    return TokenPair(
        access_token=create_token(str(user.id), token_type="access", expires=timedelta(minutes=15)),
        refresh_token=create_token(str(user.id), token_type="refresh", expires=timedelta(days=7)),
    )
```

`DUMMY_HASH = hash_password("dummy-password-for-timing")` computed once at import. Use the same generic message for "no user" and "bad password".

## Authorization
- Check **object-level** ownership in the service, not only roles: `if order.user_id != user.id and "admin" not in user.roles: raise PermissionDeniedError(...)`. Broken object-level authorization is the #1 API vulnerability (OWASP API1).
- Return 404 instead of 403 when revealing existence would leak information.
- Use OAuth2 scopes (`SecurityScopes`) for fine-grained third-party access.

## API keys (service-to-service)

```python
from fastapi.security import APIKeyHeader
import hmac, hashlib

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

async def verify_api_key(key: Annotated[str | None, Depends(api_key_header)], repo: ApiKeyRepoDep) -> ApiKey:
    if not key:
        raise HTTPException(401, "Missing API key")
    digest = hashlib.sha256(key.encode()).hexdigest()   # store only hashes
    record = await repo.get_by_digest(digest)
    if record is None or record.revoked:
        raise HTTPException(401, "Invalid API key")
    return record
```

Use `hmac.compare_digest` whenever comparing secrets directly.

## CORS

```python
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,   # explicit list; never ["*"] with credentials
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
```

## Security headers middleware

```python
@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
    return response
```

## Rate limiting
Rate-limit login, signup, password reset and expensive (LLM) endpoints. Use `slowapi` or a Redis-backed limiter keyed by user id / API key (fall back to client IP; behind a proxy, configure trusted forwarded headers so IPs aren't spoofable). Return 429 with `Retry-After`.

## Security checklist
- [ ] Every mutating route has an auth dependency; object ownership verified
- [ ] Passwords hashed with Argon2; never logged; min length ≥ 12
- [ ] JWT `algorithms` pinned; `exp` required; short access TTL
- [ ] Secrets from env via `SecretStr`; `.env` gitignored
- [ ] CORS origins explicit
- [ ] Request body size limited at proxy; list endpoints bounded
- [ ] Response models exclude sensitive fields
- [ ] `/docs` disabled or protected in production if the API is private
- [ ] Dependencies scanned (`pip-audit`) and code scanned (`bandit` / ruff `S` rules)
- [ ] File uploads: validate type & size, store outside web root, random names
- [ ] Outbound URL fetches from user input guarded against SSRF (allow-list hosts)
