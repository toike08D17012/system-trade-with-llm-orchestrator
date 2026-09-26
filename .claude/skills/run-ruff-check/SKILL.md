---
name: run-ruff-check
description: Run or fix Ruff lint checks through the repository wrapper script, preserving requested options and scope. Use for lint verification, diagnosis, and focused automatic fixes.
argument-hint: "[ruff check args ...]"
---

# Run Ruff check

## Execution

Run from the repository root through the wrapper, never directly through host Ruff or Docker Compose:

```bash
./scripts/pre-commit/ruff-check.sh [RUFF_CHECK_ARGS ...]
```

The wrapper selects the appropriate host/Docker/devcontainer execution path. Do not wrap it again with `docker/run-docker.sh`. If the script is not executable, invoke it with `bash ./scripts/pre-commit/ruff-check.sh ...` rather than modifying permissions. If it is missing or fails before Ruff starts, report the environment issue separately; do not silently fall back to direct Ruff execution.

Preserve all user-specified targets and options. Do not silently broaden the scope or add `--fix` to an explicitly read-only request.

## Fix policy and scope

- For verification or investigation, run **without** `--fix` (read-only).
- For a request to fix lint errors, or as part of an authorized code-editing task, use `--fix` on the relevant targets. Check the resulting diff. Do not add `--unsafe-fixes` without explicit authorization.
- When no target is provided, prefer the files affected by the current task, if known; otherwise use the project's configured default target or `.`. Never replace an explicit user target.
- Do not run additional whole-repository checks merely for reassurance. After a fix, rerun the smallest meaningful failing target; expand to the original requested scope when needed to establish the requested outcome.
- Stop after a successful required check unless relevant files or Ruff configuration subsequently change.

## Examples

```bash
./scripts/pre-commit/ruff-check.sh src/package/module.py
./scripts/pre-commit/ruff-check.sh --fix src/package/module.py
./scripts/pre-commit/ruff-check.sh --diff src
```

## Reporting

Report the exact command, pass/fail result, relevant rule violations, and any fixes applied. For automatic fixes, summarize the changed files using the output or `git diff` and distinguish remaining errors from wrapper/environment failures. Avoid unrelated refactoring.
