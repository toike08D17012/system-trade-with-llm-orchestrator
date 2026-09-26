# Python Instructions

## 1. Scope

Apply these instructions when editing:

- Python source files (`*.py`)
- Python tests
- `pyproject.toml`
- Python packaging, linting, formatting, typing, or test configuration
- Python-related scripts or examples

## 2. Source of Truth

Use the following files as source of truth:

- `pyproject.toml` for Python version, dependencies, Ruff, Mypy, Pytest, and packaging settings
- Existing source files for implementation patterns
- Existing tests for expected behavior and test style
- README and documentation for user-facing behavior

If these instructions conflict with `pyproject.toml` or tool configuration, prefer the tool configuration and report the inconsistency.

## 3. Coding Style

This project follows the [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html).

Exceptions:

- Line length: up to 120 characters

General rules:

- Write readable and Pythonic code.
- Prefer direct functions and control flow over one-use classes, factories, wrappers, registries, or strategy layers.
- Reuse existing modules and helpers before introducing a new abstraction.
- Introduce an abstraction only for a distinct responsibility or concrete repeated behavior, not speculative reuse.
- Keep cohesive behavior together. Extract a module when it has an independently understandable responsibility and the extraction improves reviewability; do not use a numeric size threshold.
- Use type annotations where practical.
- Use built-in generic types such as `list`, `tuple`, and `dict` when supported by the target Python version defined in `pyproject.toml`.
- Write docstrings in Google style.
- Write comments and docstrings in English.
- Avoid redundant comments.
- Explain intent, assumptions, constraints, and non-obvious behavior.
- Do not write comments that merely restate the code.
- Keep public APIs backward compatible unless a breaking change is explicitly requested.
- Avoid unnecessary refactoring, file moves, or formatting-only changes.

## 4. Type Hints

- Add type annotations for new public functions, methods, and classes.
- Prefer precise types over overly broad types such as `Any`.
- Use `Any` only when the type cannot reasonably be expressed.
- Keep annotations compatible with the Python version specified in `pyproject.toml`.
- Avoid introducing runtime imports only for typing when `typing.TYPE_CHECKING` can be used.

## 5. Error Handling

- Use explicit exceptions with clear error messages.
- Do not silently swallow exceptions unless there is a clear reason.
- Validate untrusted or external input at the boundary where it enters trusted code.
- Preserve required invariants and meaningful failure modes without duplicating equivalent validation in each internal layer.
- Do not add fallback branches for failures that the task and repository contracts do not support.
- Preserve existing exception behavior unless the task requires changing it.
- Avoid leaking secrets or sensitive values in exception messages.

## 6. Dependency Management

- Do not use `pip install` to add dependencies.
- Use the repository-defined dependency management workflow, such as `uv add`, when adding dependencies.
- Do not add new dependencies unless necessary.
- Prefer the standard library or existing dependencies when practical.
- When adding a dependency, update the appropriate project configuration.
- Consider license compatibility and supported Python versions.

## 7. Tools

This project uses:

- Ruff for linting and formatting
- Mypy for static type checking
- Pytest for testing

Do not change Ruff, Mypy, Pytest, or CI settings just to make failures disappear.

## 8. Testing Policy

- Add or update tests when changing behavior.
- Test the changed behavior at the narrowest level that exercises the relevant contract.
- Prefer focused unit tests for small logic changes and broader tests only for shared or cross-module behavior.
- Follow the existing test structure and fixture style.
- Keep test names descriptive.
- Do not delete, skip, or weaken tests just to make the test suite pass.
- Do not mock too aggressively when testing real behavior is practical.
- Cover edge cases when changing validation, parsing, error handling, or public APIs.

## 9. Validation

Follow the proportional validation policy in `AGENTS.md`. Start with repository wrappers targeted to the changed files, package, or tests.

Examples:

```bash
./scripts/pre-commit/ruff-check.sh --fix src/package/module.py
./scripts/pre-commit/ruff-format.sh src/package/module.py
./scripts/pre-commit/mypy.sh src/package
./scripts/pre-commit/pytest.sh tests/test_module.py -q
```

Use `./scripts/pre-commit/checks.sh` when the change is broad or high-risk enough to require repository-wide validation.

If checks cannot be run, explain why and state which commands should be run by the developer.

## 10. Checklist

Before finishing Python changes, confirm:

- [ ] Relevant source files and tests were inspected.
- [ ] Existing implementation patterns were followed.
- [ ] Public APIs remain compatible unless a breaking change was requested.
- [ ] Type annotations were added or updated where practical.
- [ ] Comments and docstrings are written in English.
- [ ] Tests were added or updated for behavior changes.
- [ ] Validation covers the changed behavior and is proportional to its scope and risk.
- [ ] Any skipped checks are explained.
