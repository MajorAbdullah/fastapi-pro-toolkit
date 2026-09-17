---
name: fastapi-performance
description: Diagnose and improve FastAPI/Python performance and concurrency — async vs sync route rules, avoiding event-loop blocking, running CPU/blocking work in threads or processes, background tasks vs task queues (Celery/ARQ/Dramatiq), caching with Redis, connection pooling, response streaming, profiling, and load testing. Use this whenever the user reports slow endpoints, high latency, timeouts, "the server hangs under load", asks about async/await, concurrency, workers, caching, background jobs, or wants to benchmark or profile a Python service.
---

# FastAPI Performance & Concurrency

Measure first. Most "FastAPI is slow" problems are a blocked event loop, N+1 queries, or missing pooling — not the framework.

## The #1 rule: never block the event loop

| Your code does… | Declare route/dependency as |
|---|---|
| async I/O (asyncpg, httpx.AsyncClient, redis.asyncio, async LLM SDK) | `async def` |
| blocking I/O (requests, psycopg2, boto3, sync SDKs, file I/O) | `def` (FastAPI runs it in a threadpool) |
| CPU-heavy work (image processing, pandas, embeddings on CPU) | offload to a process pool or worker queue |

A single `time.sleep()`, `requests.get()` or heavy loop inside `async def` stalls **every** request on that worker.

Wrapping blocking calls inside async code:

```python
from starlette.concurrency import run_in_threadpool   # or anyio.to_thread.run_sync
result = await run_in_threadpool(boto3_client.get_object, Bucket=b, Key=k)
```

CPU-bound work:

```python
import asyncio
from concurrent.futures import ProcessPoolExecutor

pool = ProcessPoolExecutor(max_workers=2)       # create in lifespan, shut down on exit
result = await asyncio.get_running_loop().run_in_executor(pool, heavy_fn, arg)
```

The default threadpool has ~40 tokens (anyio). If many sync routes/dependencies block for long, they queue. Raise the limit in lifespan only after measuring:

```python
import anyio.to_thread
anyio.to_thread.current_default_thread_limiter().total_tokens = 100
```

Detect blocking in dev: `PYTHONASYNCIODEBUG=1` logs callbacks slower than 100 ms.

## Concurrency inside a request

```python
async with asyncio.TaskGroup() as tg:          # Python 3.11+, cancels siblings on failure
    profile_t = tg.create_task(profile_client.get(uid))
    orders_t = tg.create_task(orders_client.list(uid))
profile, orders = profile_t.result(), orders_t.result()
```

Bound fan-out with `asyncio.Semaphore`. Don't share one `AsyncSession` across these tasks.

Always set timeouts on outbound calls (`httpx.AsyncClient(timeout=httpx.Timeout(10, connect=3))`) — a hung upstream otherwise holds your worker forever.

## Background work: pick the right tool

| Need | Use |
|---|---|
| Tiny fire-and-forget after response (send an email, log an event), losing it on crash is acceptable | `BackgroundTasks` |
| Must not be lost, retries, scheduling, takes > a few seconds, CPU heavy | A queue: ARQ / Dramatiq / Celery / Taskiq with Redis or RabbitMQ |
| Long job the client waits for | Return `202 Accepted` + job id; client polls `GET /jobs/{id}` or gets a webhook/SSE |

`BackgroundTasks` run in the same process after the response — they consume the same worker and vanish on restart.

## Caching

```python
import json
from redis.asyncio import Redis

async def get_product(product_id: int, redis: Redis, repo: ProductRepo) -> ProductRead:
    key = f"product:v1:{product_id}"
    if cached := await redis.get(key):
        return ProductRead.model_validate_json(cached)
    product = ProductRead.model_validate(await repo.get(product_id))
    await redis.set(key, product.model_dump_json(), ex=300)
    return product
```

- Version cache keys (`v1`) so schema changes don't read stale shapes.
- Invalidate on write, and still set a TTL as a safety net.
- Use HTTP caching (`Cache-Control`, `ETag`) for public, cacheable GETs.
- `functools.lru_cache` only for pure, process-local data (e.g. settings).

## Serialization & payloads
- Returning a Pydantic model with `response_model` is fine; for very large payloads, return `Response(content=model.model_dump_json(), media_type="application/json")` to skip double validation.
- Paginate; never return unbounded lists.
- Add `GZipMiddleware(minimum_size=1000)` (or compress at the proxy).
- Stream large files/results with `StreamingResponse`.

## Database
- Fix N+1 with `selectinload`/`joinedload` (see `fastapi-async-database`).
- Add indexes for filter/sort columns; check with `EXPLAIN ANALYZE`.
- Enable `echo=True` or SQL logging locally to count queries per request.
- Size the pool to workers × replicas; use PgBouncer at scale.

## Server configuration
- `uvloop` + `httptools` come with `uvicorn[standard]` — use them.
- Workers ≈ number of CPU cores for async apps (start there, then load test). In Kubernetes prefer 1 worker per pod and scale pods.
- Put a reverse proxy/load balancer in front for TLS, buffering and body-size limits.

## Profiling & load testing

```bash
# where is time spent?
uv run pyinstrument -m uvicorn app.main:app          # or add pyinstrument middleware in dev
py-spy top --pid <worker_pid>                        # sample a live process, no code changes
py-spy dump --pid <pid>                              # see what a "hung" worker is doing

# how does it behave under load?
locust -f loadtests/locustfile.py --host http://localhost:8000
k6 run loadtests/smoke.js
```

Report p50/p95/p99 latency and error rate, not just averages. Change one thing at a time and re-measure.

## Triage checklist for "it's slow"
1. Is it one endpoint or all? (all → blocked loop / pool exhaustion / worker count)
2. Query count and duration per request?
3. Any sync client inside `async def`?
4. Outbound calls with no timeout?
5. Pool size vs concurrency — are requests waiting for connections (`pool_timeout` errors)?
6. Large payloads / missing pagination?
7. py-spy the worker under load to see the actual stack.
