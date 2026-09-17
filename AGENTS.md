# AGENTS.md

> This file mirrors the standards packaged as Claude Code skills under [`skills/`](./skills/) so they're
> usable by *any* AI coding agent, not just Claude Code. If you're using Claude Code, install this
> repo as a plugin instead (see [`README.md`](./README.md)) — the skills auto-trigger with full code
> patterns and a scaffold script. This AGENTS.md exists so the same standards get enforced when a
> developer is working with Cursor, Windsurf, Copilot, Codex CLI, Aider, Zed, Devin, Amp, Factory,
> Jules, or any other tool that reads `AGENTS.md` natively.

## Project Context

This repo's skills are written to be dropped into a **production-grade FastAPI service** (Python
3.12+, async-first, SQLAlchemy 2.0, Pydantic v2). Whether this specific repo *is* that service or a
toolkit meant to be applied to one, treat any FastAPI code touched here as production code: typed,
tested, secured, and observable from the first commit — not a prototype to harden later.

## Core Principles

These recur across nearly every skill below — internalize them before the topic-specific rules:

- **Layer cleanly and keep each layer honest.** Routes/controllers handle HTTP only; services hold
  business logic; repositories hold persistence. Routers never contain SQL or business rules;
  services never import `fastapi.Request`/`Response` or raise `HTTPException`; repositories never
  make business decisions. Services raise domain exceptions, one global handler maps them to HTTP.
- **Depend on abstractions, not concretions.** Every external resource (DB session, HTTP client,
  current user, service) arrives via a FastAPI dependency (`Depends`) or a `Protocol`, so it can be
  swapped or faked in tests. Don't call dependency functions directly or reach for module-level
  globals.
- **Never block the event loop.** `async def` only for real async I/O; blocking calls go in a
  threadpool or process pool; CPU-heavy work goes to a worker queue. This single rule underlies most
  "FastAPI is slow" incidents.
- **Validate and type at every boundary.** Pydantic models for API/config/external data, typed
  SQLAlchemy models for persistence, strict mypy/pyright. Never pass raw dicts between layers.
- **Treat every external and AI-generated input as untrusted.** Parameterized SQL only; validate and
  sanitize at the API boundary; wrap retrieved/LLM content in explicit delimiters and never treat it
  as instructions; validate AI-generated output (schema, tool inputs) before acting on it.
- **Fail safe and least-privilege by default.** Auth/authz checked server-side on every mutating
  route, including object-level ownership; secrets only from env/secret managers via typed
  `SecretStr`; agents/tools get the minimum access needed, with iteration caps and confirmation
  before irreversible actions.
- **Measure before you optimize, and log everything.** Structured logs, request IDs, traces, and RED
  metrics (rate/errors/duration) are baseline, not an afterthought. Diagnose "it's slow" with
  profiling data, not guesses.
- **Small, focused, replaceable units.** Functions do one thing (~40 lines is a smell threshold);
  one prompt = one task; new behavior extends via a new handler/dependency/router rather than editing
  tested code in place.

## Quick Reference

### Project Structure
- Use domain-based folders (`app/users/`, `app/orders/`) once an app exceeds ~3 resources, not flat `routers/models/schemas` folders — a single feature shouldn't be spread across the whole tree.
- Use the `src/` layout so tests import the installed package, not the working directory.
- Each domain package holds `router.py` (HTTP only), `schemas.py` (Pydantic only), `models.py` (ORM), `service.py` (business logic), and optionally `repository.py` (data access).
- Build the app via a `create_app()` factory using the `lifespan` context manager — `@app.on_event` is deprecated. Tests should build a fresh app from the factory with overridden settings.
- Typed settings via `pydantic-settings`, loaded once and cached (`lru_cache`); never read `os.environ` directly elsewhere; use `SecretStr` for secrets; fail fast on missing required config.
- Aggregate domain routers into a versioned `api_router` mounted at `/api/v1`.
- Prefer `uv` for dependency management; commit the lockfile.

Full patterns & code: `skills/fastapi-project-structure/SKILL.md`

### API Endpoints
- URLs are plural nouns, no verbs (`GET /users`, `POST /users/{id}`); nest only one level for true ownership; non-CRUD actions become sub-resources (`POST /orders/{id}/cancellation`); version in the path prefix (`/api/v1`).
- Status codes: 200 read, 201 create (+ `Location` header), 202 async-accepted, 204 delete/no body, 422 validation / 400 business-rule failure, 401 unauthenticated, 403 forbidden, 404 not found, 409 conflict, 429 rate-limited.
- Never reuse one schema for input and output — separate `Create` / `Update` (all-optional, for PATCH) / `Read` models; always declare `response_model` so extra ORM attributes are filtered out.
- For PATCH, apply only `payload.model_dump(exclude_unset=True)`.
- Always bound `limit`/`offset` query params — unbounded list endpoints are a DoS vector; use cursor/keyset pagination for large or fast-changing tables instead of `OFFSET`.
- Return one consistent error shape (RFC 9457 problem-details style) from a small set of domain exceptions mapped by a global exception handler; never leak internals (tracebacks, raw DB errors) to clients.
- Type and constrain path/query params (`UUID`, `int`, `Query(ge=..., le=...)`); give routes meaningful function names (they become the OpenAPI `operationId`).

Full patterns & code: `skills/fastapi-endpoints/SKILL.md`

### Dependency Injection
- Define each dependency once as an `Annotated` type alias (e.g. `SessionDep`) and reuse it everywhere instead of repeating `Depends(...)`.
- Use `yield` dependencies for request-scoped resources (DB sessions, etc.); commit either in the dependency or in the service — never both, and never swallow an exception after `yield`.
- Wire dependencies in layers: session → repository → service, each its own small `Depends`-returning function. FastAPI caches a dependency per request, so all consumers share one instance.
- Create expensive long-lived clients (httpx, Redis, LLM SDK) once in `lifespan`, store on `app.state`, expose via a dependency — never create them per request.
- Use a parametrized callable class (e.g. `RequireRole("admin")`) for dependencies that need configuration; apply cross-cutting checks at the router level with `APIRouter(dependencies=[...])`.
- `async def` dependencies for async I/O; plain `def` (runs in threadpool) for cheap/blocking work — never blocking I/O inside `async def`.
- In tests, override the *original function object* via `app.dependency_overrides`, and always clear it in teardown.

Full patterns & code: `skills/fastapi-dependency-injection/SKILL.md`

### Async Database
- Use SQLAlchemy 2.0 typed `Mapped`/`mapped_column` models; set `expire_on_commit=False` on the async sessionmaker — otherwise post-commit attribute access triggers a `MissingGreenlet` lazy load.
- Mark relationships `lazy="raise"` so accidental N+1/lazy loads fail loudly instead of silently; load explicitly with `selectinload` (collections) or `joinedload` (scalars).
- Put persistence behind a repository class; services handle business rules (e.g. catching `IntegrityError` to raise a domain `ConflictError`) rather than "check then insert," which races under concurrency — rely on DB unique constraints instead.
- One `AsyncSession` per request; never share a session across concurrent tasks (`asyncio.gather`/`TaskGroup`) — give each task its own session.
- Raw SQL only via `text()` with bound parameters — never f-string user input into SQL.
- Alembic: import every model module so autogenerate sees all tables; always read the generated migration (it misses renames, server defaults, some index/enum changes); add columns nullable/with defaults then tighten in a later migration; create indexes on big tables with `postgresql_concurrently=True`; never edit a migration that already ran in a shared environment; run migrations as a separate deploy step, not at app startup.

Full patterns & code: `skills/fastapi-async-database/SKILL.md`

### Auth & Security
- Use maintained libraries: **PyJWT** for tokens, **pwdlib (Argon2)** for password hashing — not `python-jose`/`passlib`. If an identity provider already exists (Auth0, Keycloak, Cognito, Entra ID), validate its tokens rather than building your own auth.
- Hash passwords with Argon2 (`PasswordHash.recommended()`); minimum length ≥ 12; never log passwords.
- JWTs: pin the `algorithms` list on decode (never trust the token header's `alg`); require `exp`/`sub`/`type`; short-lived access tokens (5–15 min), longer rotated/revocable refresh tokens; keep PII out of payloads (JWTs are base64, not encrypted).
- Check **object-level authorization** in the service layer (can this user act on this specific resource?), not just role checks — this is OWASP API1, the #1 API vulnerability. Return 404 instead of 403 when existence itself is sensitive.
- Login: verify a password hash even when the user doesn't exist (dummy hash) and return the same generic error for "no user" and "bad password" to resist enumeration/timing attacks.
- API keys: store only a hash (e.g. SHA-256 digest) server-side, never the raw key; compare secrets with `hmac.compare_digest`.
- CORS: explicit origin list, never `"*"` with credentials. Add security headers (`X-Content-Type-Options`, `X-Frame-Options`, `Strict-Transport-Security`, etc.). Rate-limit login/signup/password-reset and expensive (LLM) endpoints, returning 429 with `Retry-After`.
- Checklist highlights: auth dependency + ownership check on every mutating route; `.env` gitignored; response models exclude sensitive fields; dependencies/code scanned (`pip-audit`, `bandit`/ruff `S` rules); uploaded files validated and stored outside the web root; outbound URL fetches from user input guarded against SSRF.

Full patterns & code: `skills/fastapi-auth-security/SKILL.md`

### Testing
- Follow the test pyramid: many fast unit tests of services (no HTTP/DB), a solid layer of API tests through the ASGI app against a real database, a few end-to-end smoke tests.
- Test against the **same database engine used in production** (e.g. real Postgres via `testcontainers`) — SQLite hides Postgres-specific behavior.
- Isolate each test with a transaction/savepoint rollback fixture, not a shared or recreated database.
- Drive API tests with `httpx.AsyncClient` + `ASGITransport`; note `ASGITransport` does not run `lifespan` events by default. Override dependencies (`get_session`, `get_current_user`) rather than hitting real infrastructure.
- Use factories (e.g. `polyfactory`) instead of hand-built dicts; use hand-written fakes for repositories in unit tests rather than heavy mocking.
- Never call real external/LLM APIs in the default test run — mock outbound HTTP (`respx`) and mark real-integration tests `@pytest.mark.integration`.
- For every endpoint, cover: happy path, validation failure, not found, unauthenticated (401), unauthorized/other user's object (403/404), conflict, and absence of sensitive fields in the response.
- Avoid flakiness: no `sleep()`, freeze time for expiry logic, don't depend on test order or shared mutable state, seed randomness.

Full patterns & code: `skills/fastapi-testing/SKILL.md`

### Performance & Concurrency
- The #1 rule: never block the event loop. Async I/O → `async def`; blocking I/O → plain `def` (threadpool) or `run_in_threadpool`; CPU-heavy work → a process pool or worker queue.
- Always set timeouts on outbound calls — a hung upstream otherwise holds a worker forever indefinitely.
- Pick the right background-work tool: `BackgroundTasks` only for tiny fire-and-forget work where loss on crash is acceptable; a real queue (ARQ/Dramatiq/Celery/Taskiq) for anything that must not be lost, retries, or is slow/CPU-heavy; `202 Accepted` + job polling/webhook for long jobs the client is waiting on.
- Cache with versioned keys (`product:v1:{id}`), invalidate on write, and still set a TTL as a safety net.
- Paginate every list endpoint; never return unbounded lists; stream large payloads with `StreamingResponse`; use `GZipMiddleware` for large responses.
- Fix N+1 queries with `selectinload`/`joinedload`; size the DB pool to `workers × replicas`, use PgBouncer at scale.
- Diagnose "it's slow" with data, not guesses: check whether it's one endpoint or all, query count/duration, sync clients inside `async def`, missing timeouts, pool exhaustion, payload size — then profile (`py-spy`, `pyinstrument`) and load-test (`locust`/`k6`), reporting p50/p95/p99, not just averages.

Full patterns & code: `skills/fastapi-performance/SKILL.md`

### Observability
- Structured JSON logging (e.g. `structlog`) with key/value event fields, not interpolated sentences; no `print()` in application code; never log secrets, tokens, passwords, full request bodies, or raw PII.
- Attach a request/correlation ID via middleware (contextvars) to every log line, and propagate it on outbound calls so logs join across services.
- Instrument OpenTelemetry tracing across service boundaries; add the trace ID to logs; exclude `/health*` and `/metrics` from tracing noise.
- Track RED metrics (Rate, Errors, Duration) per route plus saturation (DB pool usage, queue depth); keep metric label cardinality low (route templates, never raw IDs); protect `/metrics` from public access.
- Separate liveness (process is up, no dependency checks — never fails due to a DB blip) from readiness (may check critical dependencies with short timeouts) health endpoints.
- Alert on user-facing symptoms tied to SLOs (5xx rate, p95 latency, readiness failures, pool saturation), not raw CPU.

Full patterns & code: `skills/fastapi-observability/SKILL.md`

### Deployment
- Multi-stage Dockerfile: build tools excluded from the runtime image; dependencies layer installed before source copy; `uv sync --frozen`; run as a non-root user; exec-form `CMD` so uvicorn is PID 1 and receives `SIGTERM` for graceful shutdown. Always add a `.dockerignore` so `.venv`/`.git`/`.env` never enter the build context.
- In containers/Kubernetes, run 1–2 uvicorn workers per pod and scale by pods; use `--proxy-headers` + a restricted `--forwarded-allow-ips` behind a load balancer; never use `--reload` outside development.
- All configuration via environment variables from the platform's secret store — never baked into images.
- Run `alembic upgrade head` as a separate release step (not app startup, which would race across replicas); design migrations backward-compatible (expand → migrate → contract) so old and new pods can coexist during rollout.
- Kubernetes: pin an immutable image tag or digest (never `:latest`); wire distinct liveness/readiness probes; set resource requests/limits; use a `preStop` hook to drain connections before `SIGTERM`.
- CI/CD should run lint → typecheck → test (with a real Postgres service) → dependency scan → build, and build the image once to promote across environments.
- Pre-production checklist highlights: non-root pinned image, no secrets in image/repo, probes wired, graceful shutdown tested, migrations backward-compatible, structured logs/metrics/error-tracking enabled, timeouts everywhere, load-tested at peak × 2, rollback plan ready.

Full patterns & code: `skills/fastapi-deployment/SKILL.md`

### Python Code Quality
- One toolchain: **uv** (env/deps), **ruff** (lint + format, replacing black/isort/flake8), **mypy**/**pyright** (strict typing), **pre-commit** (enforces all of it before commit). Commit `uv.lock`.
- Enable ruff's `ASYNC` and `FAST` rule groups especially for FastAPI code — they catch blocking calls inside `async def` and incorrect FastAPI patterns.
- Naming: `snake_case` functions/variables/modules, `PascalCase` classes, `UPPER_SNAKE` constants; names describe intent; booleans read as questions (`is_active`).
- Functions do one thing, ~40 lines is a smell threshold, ≤4–5 params (group the rest into a model); return early instead of nesting; no mutable default arguments.
- Absolute imports only, no wildcard imports, no import-time side effects (network calls, connections).
- Catch specific exceptions, never bare `except:`, chain with `raise ... from exc`; use `Decimal` for money, `pathlib.Path` for paths, timezone-aware `datetime.now(UTC)` — never naive datetimes.
- When cleaning up existing code: add/confirm tests first, run ruff/mypy, extract and name functions, replace dict-shaped data with typed models, move I/O to the edges, and don't mix refactors with feature changes in one commit. Use conventional commits (`feat(scope): ...`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`).

Full patterns & code: `skills/python-code-quality/SKILL.md`

### Typing & Pydantic
- Use modern typing: `X | None` (not `Optional`), `list`/`dict` builtins (not `List`/`Dict`), `Sequence`/`Mapping`/`Iterable` for parameters (accept abstract, return concrete). Avoid `Any`.
- Use `Protocol` for structural interfaces (repositories, external clients) so tests can pass plain fakes without inheritance.
- Pydantic v2 request models: `extra="forbid"` to catch client typos instead of silently ignoring unknown fields; reusable `Annotated` types over repeated validators; validators raise `ValueError`, never `HTTPException`; `Decimal` for money, not `float`; `from_attributes=True` to build models from ORM objects.
- Use discriminated unions (`Field(discriminator=...)`) for tagged variant payloads — faster validation and clearer errors than plain unions.
- Use `alias_generator=to_camel` + `populate_by_name=True` for camelCase APIs while keeping snake_case in Python.
- Know the v1 → v2 migration map: `orm_mode` → `from_attributes`, `@validator` → `@field_validator`, `.dict()`/`.parse_obj()` → `.model_dump()`/`.model_validate()`, `BaseSettings` now lives in `pydantic-settings`.

Full patterns & code: `skills/python-typing-pydantic/SKILL.md`

### LLM API Integration
- Treat the model as an unreliable, slow, expensive remote dependency: isolate it behind your own `Protocol`/adapter so provider SDK types never leak into business code; keep model names/max tokens/temperature in `Settings`, not scattered literals.
- Use the provider's **async** client inside `async def` routes — the sync client blocks the event loop; create the client once in `lifespan`, not per request.
- Set `max_tokens` on every call; check `stop_reason` (e.g. `max_tokens` means truncation); set timeouts and rely on/configure SDK retry-with-backoff for transient failures.
- Stream responses over SSE; once streaming starts the HTTP status is already sent, so report errors as an event, not a status code; stop generating if the client disconnects (`request.is_disconnected()`).
- Prefer the provider's native structured-output/tool-schema features, then always validate the result against a Pydantic model; on validation failure, feed the error back to the model for a corrective retry (bounded attempts).
- Cap agentic tool-calling loops with a hard `max_steps`; validate tool inputs with Pydantic; enforce the **calling user's** permissions inside every tool (never trust model-supplied identity/scope); require explicit confirmation before irreversible actions.
- Wrap untrusted content in explicit delimiters (e.g. `<document>...</document>`) and tell the model it's data, not instructions; never render model output as raw HTML; never put secrets or other users' data in prompts; validate/allow-list any URL, SQL, or shell command the model proposes before executing.
- Log model, input/output tokens, latency, and `prompt_version` for every call; enforce per-user rate limits and token budgets; store prompts as versioned files in the repo and run evals before merging prompt/model changes.
- Never call the real LLM API in the default test run — inject a fake client implementing your `Protocol`.

Full patterns & code: `skills/llm-api-integration/SKILL.md`

### RAG Service
- RAG quality is mostly retrieval quality: get ingestion, chunking, and search right and measure them before tuning prompts. Run ingestion in a background worker, never inside the upload request.
- Start with Postgres + pgvector (transactions, joins, and access-control filters for free); move to a dedicated vector DB only when scale/features demand it.
- Chunk on structure first (headings/paragraphs/pages), then size (~300–800 tokens, 10–15% overlap); keep headings/breadcrumbs inside each chunk; store `document_id`/`ordinal`/section for citation and neighbor expansion; deduplicate by content hash.
- Record the embedding model per row; changing embedding models requires re-embedding as a migration (new column/table, backfill, switch reads); batch embedding calls and rate-limit/retry on 429; run local embedding models in a worker/process pool, not the API event loop.
- Use hybrid search (dense + keyword, fused with Reciprocal Rank Fusion) since vector search misses exact IDs/codes/names and keyword search misses paraphrases; retrieve wide (20–50) then rerank with a cross-encoder down to 5–8 chunks — usually the single biggest quality win after hybrid search.
- **Filter by tenant/ACL inside the retrieval query itself, never after generation** — post-hoc filtering can leak one customer's data into another's answer.
- Ground generation strictly in retrieved sources: instruct the model to answer only from provided sources, say "I don't know" when they're insufficient, and cite sources inline; treat all retrieved content as untrusted data.
- Evaluate retrieval (recall@k, MRR) and answer quality (faithfulness, correctness, citation accuracy) separately, with a real eval set of 30–100 questions; change one variable at a time.

Full patterns & code: `skills/rag-service/SKILL.md`

## Reference: Claude Code-Specific Agent & Commands

The items below are Claude Code mechanics (a subagent and slash commands) that only run inside Claude
Code. They're documented here so an agent on another tool understands the *intent* behind them — on
other tools, ask your agent to follow the equivalent skill doc(s) directly instead of invoking these.

### `fastapi-reviewer` agent (`agents/fastapi-reviewer.md`)
A senior-reviewer persona that audits FastAPI/async Python code against this repo's skills, in
priority order:
1. **Security** — missing auth on mutating routes, object-level authorization gaps, secrets in
   code/logs, f-string SQL, unpinned JWT algorithms, `CORS *` with credentials, sensitive fields in
   responses, SSRF, prompt-injection paths with tool access.
2. **Correctness** — wrong status codes, shared input/output schemas, PATCH without
   `exclude_unset`, check-then-insert races, transaction handling, async lazy-loading errors, shared
   `AsyncSession` across concurrent tasks, unhandled LLM `stop_reason`/invalid output.
3. **Performance** — blocking calls in `async def`, per-request client creation, N+1 queries,
   unbounded list endpoints, missing outbound timeouts, work that belongs in a queue.
4. **Design & maintainability** — business logic/SQL in routes, `HTTPException` in services, missing
   `Annotated` dependencies, module-level side effects, weak typing.
5. **Tests** — missing error-path/auth coverage, tests hitting real external APIs, flaky patterns.

It reports findings grouped as Critical / Should fix / Suggestions, each with file:line, why it
matters, and a concrete fix, plus a "what's good" section. On another tool, ask your agent to run
this same five-point review manually, citing the relevant skill file per finding.

### `/fastapi-new` (`commands/fastapi-new.md`)
Scaffolds a new production-ready FastAPI service (or adds a domain to an existing one): generates the
layout from `fastapi-project-structure`, fills in a complete vertical slice per domain (schemas,
model, repository, service, router, tests) using the endpoints/DI/database/testing skills, wires up
`pyproject.toml`, pre-commit config, and the Dockerfile, then runs `uv sync`, lint, format, and the
test suite. On another tool, ask your agent to follow `skills/fastapi-project-structure/SKILL.md`
(plus the endpoints/DI/database/testing skills for each domain) to scaffold equivalently.

### `/api-review` (`commands/api-review.md`)
Reviews FastAPI/Python code (a given path/glob, or files changed vs. the default branch) for
correctness, security, performance, and maintainability — runs `ruff`/`mypy` first when available,
then delegates the detailed review to the `fastapi-reviewer` agent above. On another tool, ask your
agent to run lint/type-checks and then perform the same review priorities described above.

### `/add-tests` (`commands/add-tests.md`)
Generates pytest tests for a given module (router, service, etc.) following `fastapi-testing`:
identifies behaviors to cover (happy path, validation, not found, 401/403, conflict, sensitive-field
leakage, edge cases), reuses/extends fixtures in `tests/conftest.py`, unit-tests services with fakes,
API-tests routers via `httpx.AsyncClient` + `ASGITransport`, then runs the new tests — fixing test
bugs but reporting real code bugs rather than adjusting expectations to match them. On another tool,
ask your agent to apply the same process using `skills/fastapi-testing/SKILL.md`.
