---
description: Review FastAPI code for correctness, security, performance and maintainability
argument-hint: "[path-or-glob] (defaults to changed files)"
allowed-tools: Bash(git:*), Bash(uv:*), Bash(ruff:*), Read, Grep, Glob
---

Review the FastAPI/Python code at `$ARGUMENTS`. If no argument is given, review files changed relative to the default branch (`git diff --name-only origin/HEAD...` falling back to `git diff --name-only HEAD`).

Delegate the detailed review to the `fastapi-reviewer` agent, then present its findings.

If ruff and mypy are available, run `ruff check` and `mypy` on the targets first and include notable results.
