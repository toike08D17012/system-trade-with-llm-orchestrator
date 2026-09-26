# AGENTS.md

## 1. Project Overview

This repository is primarily a Python project with Markdown documentation and shell scripts.

Before changing files, inspect the relevant implementation, tests, scripts, documentation, and configuration. Follow existing architecture and prefer small, focused changes.

## 2. Source of Truth

Use repository files as the source of truth:

- `pyproject.toml` for Python dependencies, tooling, and packaging
- existing source files and tests for implementation and behavior
- existing scripts, CI, pre-commit, README, and docs for workflows and user-facing behavior

If this document conflicts with tool configuration, prefer the tool configuration and report the inconsistency.

## 3. Language-Specific Instructions

Language-specific instructions are stored under `.agents/instructions`.

Before editing matching files, read only the relevant instruction file:

- Python / `pyproject.toml` / tests: `.agents/instructions/python.md`
- Markdown: `.agents/instructions/markdown.md`
- Shell scripts: `.agents/instructions/shell.md`

Do not edit instruction files unless explicitly requested.

## 4. General Development Principles

- Inspect relevant files before editing.
- Prefer existing patterns, utilities, naming, and architecture.
- Implement the simplest design that satisfies confirmed requirements.
- Do not add abstractions, extension points, configuration, or defensive branches for hypothetical future needs.
- Prefer direct control flow and existing utilities. Introduce a new abstraction only when it represents a distinct responsibility or removes concrete repetition without hiding behavior.
- Keep changes, changed files, and public surface area minimal and focused.
- Split files by cohesive responsibility when extraction makes the code easier to understand and review. Do not split files to meet arbitrary line or function counts.
- Validate external inputs and required invariants at clear boundaries. Do not repeat equivalent checks across trusted internal layers without a specific failure mode.
- Preserve public APIs unless a breaking change is explicitly requested.
- Do not make unrelated refactors, file moves, or formatting-only changes.
- Do not weaken lint, type-check, test, CI, or test coverage to make checks pass.

## 5. Dependency and Tooling Policy

- Do not add dependencies, tools, formatters, linters, frameworks, or package managers unless necessary and justified.
- Prefer the standard library, existing dependencies, and repository-defined workflows.
- When dependencies change, update the appropriate project configuration.

## 6. Plan-First Workflow

Use the `implementation-plan` skill for non-trivial repository changes that may affect source code, tests, configuration, scripts, CI, dependencies, packaging, public APIs, runtime behavior, or validation behavior.

After creating an implementation plan, stop and wait for user approval before implementation.

Markdown-only changes do not require a separate implementation plan.

If a task includes both Markdown changes and non-Markdown repository changes, apply the plan-first workflow to the non-Markdown change. The Markdown updates may be included in that plan.

## 7. Validation

After editing, run the smallest checks that directly cover the changed behavior and files.
Match validation effort to the change's scope and failure risk, and state why the selected checks are sufficient.

Expand to broader checks when the change affects shared APIs, multiple modules, project configuration, build or distribution behavior, security-sensitive code, or another high-risk boundary. The repository-wide check remains available for broad or high-risk changes:

```bash
./scripts/pre-commit/checks.sh
```

Do not repeat an identical successful check unless relevant files or configuration changed afterward, or the earlier run did not cover the final state.

If checks cannot be run or are not relevant, explain why. In the final response, state which checks were run and which relevant checks were skipped.

## 8. Security and Secrets

- Do not hard-code or expose secrets, credentials, tokens, private keys, local environment files, or machine-specific configuration.
- Use environment variables or existing configuration mechanisms for sensitive values.
- Be careful when printing logs, environment variables, command output, or configuration values.

## 9. Language Policy

- Use Japanese for user-facing communication, including progress updates and final responses, unless otherwise requested.
- Write final plan, research, and repository-overview artifacts in Japanese unless the user explicitly requests another language. This includes files under `docs/agent-reports/plans/`, files under `docs/agent-reports/research/`, and `docs/agent-reports/repository-overview.md`.
- Use English for repository-facing and agent-facing text that is not a final artifact, including code comments, docstrings, test names, commit messages, agent instructions, skill instructions, subagent prompts and responses, investigation notes, TODO lists, checklists, and handoff notes.
- Use English for all main-agent/subagent communication, including delegated prompts, intermediate reports, review notes, and handoff material.
- Follow the existing language style of user-facing documentation.
- Keep agent-facing notes concise and avoid repeating instructions already defined in this file or in language-specific instruction files.
- Prefer English for private reasoning where possible. Do not expose private chain-of-thought. When explaining decisions, provide a concise Japanese summary of the rationale, evidence, changes made, and validation results.
