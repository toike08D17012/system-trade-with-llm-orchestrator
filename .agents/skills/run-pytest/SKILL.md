---
name: run-pytest
description: Use this skill when asked to run pytest, run Python tests, verify test failures, investigate failing tests, or check whether the project test suite passes. Always execute pytest through the repository wrapper script so it works consistently from both the host environment and devcontainer/container environments.
argument-hint: "[pytest args ...]"
----------------------------------

# Run pytest

Use this skill when the user asks to:

* run pytest
* run Python tests
* run a specific test file or test case
* investigate failing tests
* verify a fix by running tests
* check whether the project test suite passes

## Core rule

Do not run `pytest` directly.

Always run pytest through the repository wrapper script:

```bash
./scripts/pre-commit/pytest.sh [PYTEST_ARGS ...]
```

This wrapper handles both cases:

* when called from the local host environment, it runs pytest through `./docker/run-docker.sh`
* when called from an environment without Docker, such as a devcontainer or project container, it runs `pytest` directly inside the current container
* when pytest exits with code `5`, meaning no tests were collected, the wrapper treats it as success

## Before running

From the repository root, confirm that the wrapper exists:

```bash
test -f ./scripts/pre-commit/pytest.sh
```

If it is not executable, make it executable:

```bash
chmod +x ./scripts/pre-commit/pytest.sh
```

## Common commands

Run the default test suite:

```bash
./scripts/pre-commit/pytest.sh
```

Run tests quietly:

```bash
./scripts/pre-commit/pytest.sh -q
```

Run a specific test file:

```bash
./scripts/pre-commit/pytest.sh tests/test_example.py -q
```

Run a specific test class or test function:

```bash
./scripts/pre-commit/pytest.sh tests/test_example.py::TestExample::test_case -q
```

Run tests matching a keyword:

```bash
./scripts/pre-commit/pytest.sh -k "keyword" -q
```

Run tests and stop after the first failure:

```bash
./scripts/pre-commit/pytest.sh -x
```

Show captured output:

```bash
./scripts/pre-commit/pytest.sh -s
```

## Exit code handling

The pytest wrapper treats exit code `5` as success.

This means:

```text
No tests collected. Treating pytest exit code 5 as success.
```

should not be reported as a test failure.

Other non-zero exit codes should be treated as failures.

## Error handling

If pytest fails:

1. Report the exact command that was run.
2. Summarize the failing test names.
3. Summarize the important assertion errors or exceptions.
4. Identify whether the issue is likely:

   * a real behavior bug
   * a test expectation mismatch
   * a missing dependency or environment issue
   * a fixture/setup issue
   * an import/path/configuration issue
5. Prefer the smallest focused fix.
6. Re-run the same wrapper command after applying a fix.

## Do not do this

Do not run these commands directly unless the user explicitly asks for host execution:

```bash
pytest
python -m pytest
docker compose run app pytest
docker compose run app-gpu pytest
```

Use the wrapper script instead.

## Reporting format

After execution, report:

```text
Command:
./scripts/pre-commit/pytest.sh ...

Result:
Passed / Failed

Important output:
...

Next action:
...
```

If pytest exits with code `5`, report it as:

```text
Result:
Passed

Important output:
No tests were collected. The wrapper treats pytest exit code 5 as success.
```

If the failure is caused by Docker not being available or the wrapper itself failing, explain the environment issue separately from test failures.
