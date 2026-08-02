---
name: run-ruff-format
description: Use this skill when asked to run Ruff format, format Python files, check Python formatting, or keep Python formatting clean. By default, run Ruff format through the repository wrapper script so formatting is applied automatically.
argument-hint: "[ruff format args ...]"
---------------------------------------

# Run Ruff format

Use this skill when the user asks to:

* run Ruff format
* format Python files
* check Python formatting
* keep Python formatting clean
* verify formatting after code changes

## Core rule

Do not run `ruff format` directly.

Always run Ruff format through the repository wrapper script:

```bash
./scripts/pre-commit/ruff-format.sh [RUFF_FORMAT_ARGS ...]
```

By default, run Ruff format in modifying mode:

```bash
./scripts/pre-commit/ruff-format.sh .
```

The project should normally be kept in a state where running `ruff format` produces no additional changes.

This wrapper handles both cases:

* when called from the local host environment, it runs Ruff through `./docker/run-docker.sh`
* when called from an environment without Docker, such as a devcontainer or project container, it runs `ruff format` directly inside the current container

## Before running

From the repository root, confirm that the wrapper exists:

```bash
test -f ./scripts/pre-commit/ruff-format.sh
```

If it is not executable, make it executable:

```bash
chmod +x ./scripts/pre-commit/ruff-format.sh
```

## Common commands

Format the whole repository:

```bash
./scripts/pre-commit/ruff-format.sh .
```

Format a specific directory:

```bash
./scripts/pre-commit/ruff-format.sh src
```

Format a specific file:

```bash
./scripts/pre-commit/ruff-format.sh src/package/module.py
```

Check formatting without modifying files only when read-only verification is specifically useful:

```bash
./scripts/pre-commit/ruff-format.sh --check .
```

Show formatting differences without modifying files:

```bash
./scripts/pre-commit/ruff-format.sh --diff .
```

## Workflow

When working on Python code:

1. Make the requested code changes.

2. Run Ruff format:

   ```bash
   ./scripts/pre-commit/ruff-format.sh .
   ```

3. Run Ruff check with fixes:

   ```bash
   ./scripts/pre-commit/ruff-check.sh --fix .
   ```

4. Re-run Ruff format if Ruff check changed imports or code layout.

5. Continue until both commands produce no additional changes.

## Error handling

If Ruff format fails:

1. Report the exact command that was run.
2. Summarize the important formatting or parser errors.
3. Identify whether the issue is likely:

   * invalid Python syntax
   * an unsupported file
   * an environment/configuration issue
4. Prefer the smallest focused fix.
5. Re-run the same wrapper command after applying a fix.

## Do not do this

Do not run these commands directly unless the user explicitly asks for host execution:

```bash
ruff format
python -m ruff format
docker compose run app ruff format
docker compose run app-gpu ruff format
```

Use the wrapper script instead.

## Reporting format

After execution, report:

```text
Command:
./scripts/pre-commit/ruff-format.sh ...

Result:
Passed / Failed

Important output:
...

Next action:
...
```

If formatting was applied, briefly summarize the changed files based on command output or `git diff --stat`.
