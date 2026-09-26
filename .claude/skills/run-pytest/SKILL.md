---
name: run-pytest
description: Run Python tests and investigate pytest failures through the repository pytest wrapper, preserving requested scope and avoiding redundant verification.
argument-hint: "[pytest args ...]"
---

# Run pytest

Use this skill for pytest execution, failure investigation, and test verification.

## Execution

- From the repository root, run `./scripts/pre-commit/pytest.sh [PYTEST_ARGS ...]`. Never invoke `pytest`, `python -m pytest`, or Docker Compose directly for this purpose unless the user explicitly requests another execution method.
- Preserve user-supplied targets and options. Do not silently broaden or replace the requested scope.
- The wrapper selects Docker execution on the host and direct execution inside the project container/devcontainer. Do not wrap it again in `./docker/run-docker.sh`.
- Check the wrapper exists only when needed. If it lacks executable permission, use `bash ./scripts/pre-commit/pytest.sh [PYTEST_ARGS ...]` rather than changing repository permissions.
- If the wrapper or Docker is unavailable, distinguish the environment failure from a test failure. Do not silently fall back to host pytest.

## Verification budget

- Run only the requested or task-relevant tests; do not run the whole suite by default when a narrower check suffices.
- For failures, report failing test nodes and the essential assertion/exception. Investigate the smallest likely cause; do not broaden edits or checks automatically.
- After a fix, rerun the smallest failing node/file first. Return to the originally requested scope when needed to establish that result; expand further only for shared fixtures, public behavior, configuration, or cross-module changes.
- Stop once the necessary scope passes. Repeat a successful run only after relevant code, tests, fixtures, or configuration changes.
- Do not modify code, fixtures, snapshots, or configuration merely to execute a verification request.

## Exit status and reporting

- The wrapper maps pytest exit code `5` (no tests collected) to success. Report **wrapper succeeded; no tests collected**, not **tests passed**. Other nonzero wrapper exit statuses are failures.
- Report the exact command, outcome, relevant failures or counts, and next action only if necessary. Keep environment/wrapper failures separate from actual test failures.
