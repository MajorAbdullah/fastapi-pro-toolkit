# fastapi-pro-toolkit

A plugin of **13 agent skills**, **3 slash commands** and **1 reviewer subagent** for building production-grade Python services with FastAPI — plus the AI/LLM layer on top.

## What's inside

| Skill | Covers |
|---|---|
| `fastapi-project-structure` | Domain-based layout, app factory, lifespan, pydantic-settings, uv; includes a scaffold script |
| `fastapi-endpoints` | REST conventions, status codes, Create/Update/Read schemas, pagination, RFC 9457-style errors |
| `fastapi-dependency-injection` | `Annotated` deps, yield resources, session→repo→service wiring, shared clients, overrides |
| `fastapi-async-database` | SQLAlchemy 2.0 async, typed models, repositories, N+1 avoidance, transactions, Alembic safety |
| `fastapi-auth-security` | JWT with PyJWT, Argon2 via pwdlib, current-user/roles, API keys, CORS, headers, OWASP checklist |
| `fastapi-testing` | pytest + httpx ASGITransport, rollback-isolated DB tests, factories, mocking, anti-flakiness |
| `fastapi-performance` | Event-loop blocking, threads/processes, background jobs vs queues, caching, profiling, load tests |
| `fastapi-observability` | structlog JSON logs, request IDs (pure ASGI middleware), OpenTelemetry, metrics, Sentry, health checks |
| `fastapi-deployment` | Multi-stage uv Dockerfile, uvicorn settings, migrations as release step, compose, K8s, GitHub Actions |
| `python-code-quality` | uv, ruff (incl. `ASYNC`/`FAST` rules), mypy strict, pre-commit, conventions, refactoring playbook |
| `python-typing-pydantic` | Modern typing, Protocols, PEP 695 generics, Pydantic v2 validators/unions/aliases, v1→v2 map |
| `llm-api-integration` | Async SDK clients, SSE streaming, validated structured output, tool loops, injection defenses, cost, evals |
| `rag-service` | Chunking, embeddings, pgvector + HNSW, hybrid search with RRF, reranking, citations, retrieval evals |

**Commands**
- `/fastapi-new <dir> [domains...]` — scaffold a service with full vertical slices, tooling and Dockerfile
- `/api-review [path]` — structured review via the `fastapi-reviewer` agent
- `/add-tests <module>` — generate pytest coverage following the testing skill

**Agent**
- `fastapi-reviewer` — prioritized security → correctness → performance → design → tests review

**Bundled assets**
- `fastapi-project-structure/scripts/scaffold.py` (idempotent; never overwrites files)
- `fastapi-deployment/assets/` — `Dockerfile`, `.dockerignore`, `ci.yml`
- `python-code-quality/assets/` — `pyproject-quality.toml`, `.pre-commit-config.yaml`

## Install

### Claude Code
This repository is also a marketplace (`.claude-plugin/marketplace.json` at the root).

```bash
# from a local clone
/plugin marketplace add ./path/to/this-repo
/plugin install fastapi-pro-toolkit@fastapi-pro-marketplace

# or test without installing
claude --plugin-dir ./fastapi-pro-toolkit
```

After pushing to GitHub: `/plugin marketplace add MajorAbdullah/fastapi-pro-toolkit`.

### Other agentic IDEs
This repo ships a root [`AGENTS.md`](./AGENTS.md) — the open, tool-agnostic convention now read natively
by Cursor, GitHub Copilot, Windsurf, Codex CLI, Aider, Zed, Devin, Amp, Factory, Jules and others. Copy
`AGENTS.md` (and the `skills/` folder it links into for full code patterns) into your FastAPI project
root and those tools pick up the same standards automatically — no plugin install needed.

Cline doesn't yet read `AGENTS.md` natively; symlink or copy it to `.clinerules` in that case.

Each skill is also a standard `SKILL.md` folder (YAML frontmatter + Markdown), the format used by the
open Agent Skills convention. For tools that read skills from a directory, copy or symlink
`fastapi-pro-toolkit/skills/*` into that tool's skills folder instead (for example `~/.claude/skills/`
or a project `.claude/skills/`; check your IDE's docs for its path).

## Customize
- Update `author`, `homepage` and `repository` in `.claude-plugin/plugin.json`.
- Run `pre-commit autoupdate` after copying the pre-commit config to pin current hook versions.
- The skill descriptions are intentionally broad so they trigger reliably; narrow them if they fire too often in your workflow.

## Verified
The core code in the skills was exercised during authoring: the scaffold output passes the bundled ruff config; the database, endpoint, DI and testing patterns were assembled into an app and passed an async pytest suite against PostgreSQL 16 (including per-test rollback isolation and 409 conflict handling); the pgvector hybrid-search SQL and tenant filtering were run against a live pgvector database; logging middleware, Pydantic examples, PyJWT/pwdlib and Anthropic SDK streaming APIs were checked.

## License
MIT
