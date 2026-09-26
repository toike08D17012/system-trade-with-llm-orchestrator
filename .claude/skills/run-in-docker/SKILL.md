---
name: run-in-docker
description: Use this skill when asked to run project commands, tests, linters, type checks, formatters, Python scripts, shell scripts, or development commands inside the repository Docker Compose container. Always route commands through ./docker/run-docker.sh so CPU/GPU switching and UID/GID mapping are handled consistently.
argument-hint: "[command ...]"
------------------------------

# Run project commands in Docker

This skill runs project commands through the repository Docker wrapper script.

Use this skill when the user asks to run or verify commands such as:

* tests
* linters
* type checks
* formatters
* Python scripts
* shell scripts
* build commands
* project maintenance commands

## Core rule

Run project commands through:

```bash
./docker/run-docker.sh [COMMAND ...]
```

Do not run project Python, pytest, ruff, mypy, pre-commit, or shell scripts directly on the host unless the user explicitly asks for host execution or Docker is unavailable.

The wrapper script automatically chooses the GPU container when NVIDIA GPU is available, otherwise it uses the CPU container.

## Before running commands

From the repository root, check that the wrapper exists:

```bash
test -f ./docker/run-docker.sh
```

If it is not executable, make it executable:

```bash
chmod +x ./docker/run-docker.sh
```

## Command examples

Run an interactive shell:

```bash
./docker/run-docker.sh
```

Run pytest:

```bash
./docker/run-docker.sh pytest -q
```

Run a specific test file:

```bash
./docker/run-docker.sh pytest tests/test_example.py -q
```

Run Ruff check:

```bash
./docker/run-docker.sh ruff check .
```

Run Ruff fix only when the user requested automatic fixes:

```bash
./docker/run-docker.sh ruff check --fix .
```

Run mypy:

```bash
./docker/run-docker.sh mypy .
```

Run pre-commit:

```bash
./docker/run-docker.sh pre-commit run --all-files
```

Run a project script:

```bash
./docker/run-docker.sh bash scripts/example.sh
```

Run a Python module:

```bash
./docker/run-docker.sh python -m package.module
```

## Reporting results

After running a command, report:

1. the exact command executed
2. whether it passed or failed
3. the important output only
4. any next action needed

If a command fails, do not immediately rewrite broad parts of the codebase. First summarize the failure, identify the smallest likely cause, then propose or apply a focused fix.

## Safety rules

* Do not run destructive commands such as `rm -rf`, database resets, mass file rewrites, or deployment commands unless the user explicitly requested them.
* Do not run `ruff check --fix`, formatters, migrations, or code generation unless the user asked for changes or fixes.
* Prefer read-only verification commands first when the user asks to investigate.
* If Docker is unavailable, explain that the Docker wrapper could not be used and ask before falling back to host execution, unless the user already allowed fallback behavior.
