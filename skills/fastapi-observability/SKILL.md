---
name: fastapi-observability
description: Make FastAPI services observable — structured JSON logging with structlog, request/correlation IDs via middleware and contextvars, OpenTelemetry tracing and metrics, Prometheus endpoints, Sentry error tracking, and liveness/readiness health checks. Use this whenever the user sets up logging, asks about print() vs logging, needs request tracing, monitoring, metrics, dashboards, alerting, health endpoints for Kubernetes, or is debugging production issues they can't reproduce locally.
---

# Observability for FastAPI

You can't fix what you can't see. Every production service needs: structured logs with a request ID, traces across service boundaries, a few key metrics, error tracking, and honest health checks.

## Structured logging (structlog)

```python
# app/core/logging.py
import logging
import sys
import structlog

def configure_logging(json_logs: bool = True, level: str = "INFO") -> None:
    shared = [
        structlog.contextvars.merge_contextvars,     # pulls request_id etc. into every log line
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]
    renderer = structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,                    # also formats uvicorn/sqlalchemy stdlib logs
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    structlog.processors.format_exc_info, renderer],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
    logging.getLogger("uvicorn.access").disabled = True   # replaced by our access log below
```

Usage: `log = structlog.get_logger(__name__)` then `log.info("order_created", order_id=str(order.id), total=order.total)`.

Logging rules:
- Log **events with key/value fields**, not interpolated sentences — they become queryable.
- Never log secrets, tokens, passwords, full request bodies, or raw PII. Add a redaction processor if bodies must be logged.
- Use `log.exception(...)` inside `except` to keep tracebacks.
- JSON to stdout in containers; human-readable console locally.
- No `print()` in application code.

## Request ID + access log middleware

```python
# app/core/middleware.py
import time
import uuid
import structlog
from starlette.types import ASGIApp, Receive, Scope, Send, Message

log = structlog.get_logger("access")

class RequestContextMiddleware:
    """Pure ASGI middleware (cheaper and streaming-safe compared to BaseHTTPMiddleware)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        headers = dict(scope["headers"])
        request_id = headers.get(b"x-request-id", b"").decode()[:64] or uuid.uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        start = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            log.info("request", method=scope["method"], path=scope["path"],
                     status=status_code, duration_ms=round((time.perf_counter() - start) * 1000, 2))
```

Register with `app.add_middleware(RequestContextMiddleware)`. Pass `X-Request-ID` on outbound calls so logs join up across services.

## OpenTelemetry tracing

```bash
uv add opentelemetry-distro opentelemetry-exporter-otlp \
       opentelemetry-instrumentation-fastapi opentelemetry-instrumentation-sqlalchemy \
       opentelemetry-instrumentation-httpx
```

Zero-code option (good default):
```bash
OTEL_SERVICE_NAME=my-service \
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317 \
opentelemetry-instrument uvicorn app.main:app
```

Manual spans around important business steps:

```python
from opentelemetry import trace
tracer = trace.get_tracer(__name__)

with tracer.start_as_current_span("pricing.calculate") as span:
    span.set_attribute("cart.items", len(cart.items))
    ...
```

Add the trace id to logs (a structlog processor reading `trace.get_current_span().get_span_context().trace_id`) so you can jump from a log line to its trace. Exclude `/health*` and `/metrics` from tracing to cut noise (`OTEL_PYTHON_FASTAPI_EXCLUDED_URLS`).

## Metrics

Track the RED metrics per route — **R**ate, **E**rrors, **D**uration — plus saturation (DB pool usage, queue depth). Options: OpenTelemetry metrics via the collector, or `prometheus-fastapi-instrumentator`:

```python
from prometheus_fastapi_instrumentator import Instrumentator
Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
```

Keep label cardinality low: use the route template (`/users/{user_id}`), never raw paths or user IDs as labels. Protect `/metrics` from public access.

## Error tracking

```python
import sentry_sdk
sentry_sdk.init(dsn=settings.sentry_dsn, environment=settings.environment,
                traces_sample_rate=0.1, send_default_pii=False)
```

Initialize before creating the app. Sentry auto-detects FastAPI.

## Health checks

```python
from sqlalchemy import text

@router.get("/health/live", include_in_schema=False)
async def live() -> dict[str, str]:
    return {"status": "ok"}             # process is up; no dependencies checked

@router.get("/health/ready", include_in_schema=False)
async def ready(session: SessionDep, response: Response) -> dict[str, str]:
    try:
        await asyncio.wait_for(session.execute(text("SELECT 1")), timeout=2)
    except Exception:
        response.status_code = 503
        return {"status": "unavailable", "database": "down"}
    return {"status": "ok", "database": "up"}
```

Liveness must not depend on downstream services — otherwise a database blip makes Kubernetes restart every pod. Readiness may check critical dependencies with short timeouts.

## Alerting starting points
- 5xx rate > 1% for 5 min
- p95 latency above SLO for 10 min
- readiness failing on > 50% of pods
- DB pool wait time rising / saturation > 80%
Alert on symptoms users feel; use dashboards for causes.
