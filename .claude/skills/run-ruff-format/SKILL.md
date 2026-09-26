---
name: run-ruff-format
description: Use for formatting Python code or checking Ruff formatting. Always use the repository wrapper; distinguish read-only checks from modifying format operations and avoid unnecessary whole-repository runs.
argument-hint: "[ruff format args ...]"
---

# Run Ruff format

Run Ruff through the repository wrapper from the repository root:

```bash
./scripts/pre-commit/ruff-format.sh [RUFF_FORMAT_ARGS ...]
```

Do not invoke `ruff format`, `python -m ruff format`, or Docker Compose directly. The wrapper handles host/Docker and container/devcontainer execution. Preserve user-specified targets and options; do not silently broaden their scope.

## Choose the mode and scope

- **Check / verify / investigate formatting:** use `--check` (or `--diff` when a diff is requested), without modifying files.
- **Format / fix formatting:** use modifying mode without `--check` or `--diff`. Formatting is a write operation; perform it only when changes were requested or authorized as part of the coding task.
- In an implementation workflow with no explicit target, prefer changed Python files. For an explicit project-wide request, use `.`. For a standalone unscoped request to run Ruff format, use `.` as the original skill's default; do not turn a check request into a formatting operation.
- Use the user's explicit targets and options unchanged. If targets cannot be identified reliably for a scoped operation, explain the limitation rather than guessing an unrelated scope.

Examples:

```bash
./scripts/pre-commit/ruff-format.sh --check src/package/module.py
./scripts/pre-commit/ruff-format.sh --diff src/package/module.py
./scripts/pre-commit/ruff-format.sh src/package/module.py
./scripts/pre-commit/ruff-format.sh .  # explicitly requested whole-project formatting
```

## Execution and follow-up

1. Check the wrapper exists if not already confirmed in this session: `test -f ./scripts/pre-commit/ruff-format.sh`. If it is not executable, invoke `bash ./scripts/pre-commit/ruff-format.sh ...`; do not change file permissions just to run it.
2. Run the selected command once. Do not automatically run Ruff lint, mypy, pytest, or a second format check. Run other checks only when requested or required by the implementation plan.
3. On failure, distinguish a formatting difference (`--check`) from syntax, configuration, or environment errors. Apply only a focused authorized fix, then rerun the smallest relevant scope. Recheck the originally requested scope when needed to support the final result.
4. Stop after the required scope passes; rerun only if relevant Python files or Ruff configuration subsequently change.

Report the exact command, passed/failed result, important output, and changed files when formatting applied (using wrapper output or a focused `git diff`). If the wrapper or Docker environment fails, report that separately from Ruff findings. Do not fall back to direct host execution without user permission.
