---
name: run-mypy
description: Use this skill when asked to run mypy, type-check Python code, investigate mypy errors, or verify Python typing. Always execute mypy through the repository wrapper script so it works consistently from both the host environment and devcontainer/container environments.
argument-hint: "[mypy args ...]"
--------------------------------

# Run mypy

Use this skill when the user asks to:

* run mypy
* type-check Python code
* investigate mypy errors
* verify typing after code changes
* check whether a file, package, or project passes static type checking

## Core rule

Do not run `mypy` directly.

Always run mypy through the repository wrapper script:

```bash
./scripts/pre-commit/mypy.sh [MYPY_ARGS ...]
```

This wrapper handles both cases:

* when called from the local host environment, it runs mypy through `./docker/run-docker.sh`
* when called from an environment without Docker, such as a devcontainer or project container, it runs `mypy` directly inside the current container

## Before running

From the repository root, confirm that the wrapper exists:

```bash
test -f ./scripts/pre-commit/mypy.sh
```

If it is not executable, make it executable:

```bash
chmod +x ./scripts/pre-commit/mypy.sh
```

## Common commands

Run mypy with the project default configuration:

```bash
./scripts/pre-commit/mypy.sh
```

Run mypy on a specific package or directory:

```bash
./scripts/pre-commit/mypy.sh src
```

Run mypy on a specific file:

```bash
./scripts/pre-commit/mypy.sh src/package/module.py
```

Run mypy with stricter or diagnostic output:

```bash
./scripts/pre-commit/mypy.sh --show-error-codes --pretty
```

Run mypy using the targets configured in `pyproject.toml`:

```bash
./scripts/pre-commit/mypy.sh
```

## Error handling

If mypy fails:

1. Report the exact command that was run.
2. Summarize the important mypy errors.
3. Identify whether the issue is likely:

   * a real type bug
   * a missing type annotation
   * an incorrect stub or import
   * a missing dependency or environment issue
   * a config/target path issue
4. Prefer the smallest focused fix.
5. Re-run the same wrapper command after applying a fix.

## Do not do this

Do not run these commands directly unless the user explicitly asks for host execution:

```bash
mypy
python -m mypy
docker compose run app mypy
docker compose run app-gpu mypy
```

Use the wrapper script instead.

## Reporting format

After execution, report:

```text
Command:
./scripts/pre-commit/mypy.sh ...

Result:
Passed / Failed

Important output:
...

Next action:
...
```

If the failure is caused by Docker not being available or the wrapper itself failing, explain the environment issue separately from mypy type errors.
