---
name: fastapi-deployment
description: Containerize and ship FastAPI apps to production — multi-stage Dockerfile with uv, non-root images, uvicorn/gunicorn worker settings, proxy headers, graceful shutdown, environment config, migrations as a release step, docker-compose for local dev, Kubernetes probes/resources, and GitHub Actions CI/CD. Use this whenever the user asks to dockerize, deploy, write a Dockerfile/compose file/CI pipeline, run in Kubernetes/Cloud Run/ECS, choose worker counts, or prepare a FastAPI service for production.
---

# Deploying FastAPI

## Dockerfile (multi-stage, uv, non-root)

A ready-to-use version lives in `assets/Dockerfile`; copy it into the project root and adjust the module path.

Key choices and why:
- **Multi-stage**: build tools stay out of the runtime image → smaller, fewer CVEs.
- **Dependencies layer before source**: code edits don't reinvalidate the dependency cache.
- **`uv sync --frozen`**: installs exactly the lockfile; build fails if the lock is stale.
- **Non-root user**: limits damage if the app is compromised.
- **Exec-form `CMD`**: uvicorn is PID 1 and receives SIGTERM for graceful shutdown.
- **`PYTHONDONTWRITEBYTECODE` / `PYTHONUNBUFFERED`**: no .pyc clutter; logs flush immediately.

Also add a `.dockerignore` (`assets/.dockerignore`) so `.venv`, `.git`, `.env` and caches never enter the build context — leaking `.env` into an image is a common secret leak.

## Running the server

```bash
# container / Kubernetes (scale by pods, 1-2 workers each)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips="*" \
  --timeout-graceful-shutdown 20

# VM / single big host: multiple workers
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
# or: fastapi run src/app/main.py --workers 4
```

- `--proxy-headers` + `--forwarded-allow-ips` make `request.client` and URL scheme correct behind a load balancer. Restrict `forwarded-allow-ips` to the proxy's addresses when they're known.
- Gunicorn with `uvicorn.workers.UvicornWorker` is still valid if you want gunicorn's process management, but uvicorn's own `--workers` is sufficient for most setups.
- Don't use `--reload` outside development.

## Configuration
- 12-factor: all config via environment variables read by `Settings`.
- Secrets from the platform's secret store (K8s Secrets, AWS Secrets Manager, GCP Secret Manager), never baked into images.
- Set `ENVIRONMENT=production` and disable/guard `/docs` if the API is private.

## Migrations
Run `alembic upgrade head` as a **separate release step** (K8s Job, init container run once, CI deploy step, platform "release command") — not in the app's startup, where N replicas would race. Make migrations backward compatible so old and new pods can run during the rollout (expand → migrate → contract).

## Local dev with docker compose

```yaml
# compose.yaml
services:
  api:
    build: .
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
    env_file: .env
    ports: ["8000:8000"]
    volumes: ["./src:/app/src"]
    depends_on:
      db: { condition: service_healthy }
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: app
      POSTGRES_PASSWORD: app
      POSTGRES_DB: app
    ports: ["5432:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app"]
      interval: 5s
      retries: 10
  redis:
    image: redis:7-alpine
volumes:
  pgdata: {}
```

## Kubernetes essentials

```yaml
containers:
  - name: api
    image: ghcr.io/org/my-service:1.4.2        # immutable tag or digest, never :latest
    ports: [{ containerPort: 8000 }]
    envFrom: [{ secretRef: { name: my-service-env } }]
    resources:
      requests: { cpu: 250m, memory: 256Mi }
      limits:   { memory: 512Mi }
    readinessProbe:
      httpGet: { path: /health/ready, port: 8000 }
      periodSeconds: 5
    livenessProbe:
      httpGet: { path: /health/live, port: 8000 }
      periodSeconds: 10
      failureThreshold: 3
    lifecycle:
      preStop: { exec: { command: ["sleep", "5"] } }   # let the LB drain before SIGTERM
    securityContext:
      runAsNonRoot: true
      readOnlyRootFilesystem: true
      allowPrivilegeEscalation: false
terminationGracePeriodSeconds: 30
```

Add a HorizontalPodAutoscaler and PodDisruptionBudget for real traffic.

## CI/CD (GitHub Actions)

A starter pipeline is in `assets/ci.yml` (copy to `.github/workflows/ci.yml`). It runs: ruff lint + format check → mypy → pytest with a Postgres service and coverage → pip-audit → Docker build, and pushes the image only on `main`.

## Pre-production checklist
- [ ] Image runs as non-root, pinned base image, `.dockerignore` present
- [ ] No secrets in image or repo; config from env
- [ ] Liveness/readiness endpoints wired to probes
- [ ] Graceful shutdown tested (in-flight requests complete)
- [ ] Migrations run as a separate, backward-compatible step
- [ ] Structured logs, metrics and error tracking enabled (see `fastapi-observability`)
- [ ] Timeouts on all outbound calls; body-size limit at proxy
- [ ] CORS, TLS, security headers configured
- [ ] Load-tested at expected peak × 2
- [ ] Rollback plan: previous image tag known and deployable
