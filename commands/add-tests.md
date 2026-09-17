---
description: Generate pytest tests for a FastAPI router, service, or module
argument-hint: "<path-to-module>"
allowed-tools: Bash(uv:*), Bash(python3:*), Read, Write, Edit, Grep, Glob
---

Write tests for `$ARGUMENTS` following the `fastapi-testing` skill.

1. Read the target and its dependencies; list the behaviors to cover (happy path, validation, not found, 401/403, conflict, sensitive-field leakage, edge cases).
2. Reuse existing fixtures in `tests/conftest.py`; add missing fixtures there rather than duplicating them.
3. Unit-test services with fakes; API-test routers through `httpx.AsyncClient` + `ASGITransport`.
4. Run the new tests, fix failures that come from the tests themselves, and report any real bugs you found in the code instead of changing test expectations to match them.
