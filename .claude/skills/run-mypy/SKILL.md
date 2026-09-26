---
name: run-mypy
description: Run or diagnose Python static type checks through the repository mypy wrapper. Preserve the requested arguments and scope; do not invoke mypy or Docker directly.
argument-hint: "[mypy args ...]"
---

# Run mypy

Use this skill for mypy execution, type-check verification, or investigation of mypy errors.

## Execution contract

- Run from the repository root through `./scripts/pre-commit/mypy.sh [MYPY_ARGS ...]`.
- Preserve user-supplied arguments, targets, and scope exactly. Do not silently expand a file check into a project-wide check or weaken configured semantics.
- The wrapper selects execution through `./docker/run-docker.sh` on the host or within the current project container, as implemented by the wrapper. Do not add another Docker invocation or bypass the wrapper.
- If the user specifies no target, use the wrapper's default behavior; do not assume which targets it checks without inspecting its implementation when that distinction matters.
- Do not run `mypy`, `python -m mypy`, or `docker compose ... mypy` directly unless the user explicitly requests a different execution method.

## Run

Confirm the wrapper exists. If it lacks executable permission, use `bash ./scripts/pre-commit/mypy.sh ...` instead of modifying file permissions. If it is missing or fails due to the environment, report that separately from type errors; do not silently fall back to host execution.

```bash
./scripts/pre-commit/mypy.sh
./scripts/pre-commit/mypy.sh src/package/module.py
./scripts/pre-commit/mypy.sh --show-error-codes --pretty
```

## Failures and rechecks

- Report the exact command, pass/fail result, and only the actionable diagnostics. Distinguish typing errors from wrapper, dependency, configuration, or environment failures.
- For investigation-only requests, do not edit code. Apply a focused fix only when changes are authorized.
- After an authorized fix, recheck the smallest relevant failing scope without changing mypy's configured semantics. Recheck the originally requested scope if needed to establish the user's requested result, especially for shared types, imports, or configuration changes.
- Stop after a sufficient successful check. Do not repeat unchanged checks or run unrelated lint/test suites merely because mypy was requested.

Keep the final response brief: command, result, key diagnostics, and next action if any.
