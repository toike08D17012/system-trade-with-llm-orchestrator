---
name: repository-overview
description: Create or refresh a concise, evidence-based repository map for future coding agents. Prefer incremental updates over repeated full audits.
---

# Repository Overview

## Goal

Create a useful **navigation map**, not an exhaustive audit. A future coding agent should quickly learn what the repository does, where to start reading, how the main pieces connect, and which documented commands are relevant.

The canonical report is `docs/agent-reports/repository-overview.md`. Write the visible report in Japanese unless the user requests another language. Preserve paths, identifiers, configuration keys, and commands verbatim.

## Boundaries

- Investigation is read-only. Writing or updating the canonical overview is the only default write.
- Do not edit source, tests, CI, or documentation during overview creation.
- Do not install dependencies, launch services, run builds, run test suites, or run formatters just to create an overview. Execute validation commands only when explicitly requested or strictly necessary to resolve an important uncertainty and safe to run.
- This skill maps the repository. For a specific feature or bug, use targeted research; for a requested change, create an implementation plan separately.

## Select the smallest useful depth

| Mode | Use when | Scope | Delegation |
| --- | --- | --- | --- |
| Quick refresh (default if a report exists) | Updating an existing overview | Read existing report; inspect relevant changes and stale sections only | None by default |
| Initial overview | No reliable overview exists | Survey root, project metadata, entrypoints, primary modules, test/CI setup | None by default |
| Deep overview | User requests an audit; complex monorepo or architecture is genuinely unclear | Expand only the areas needed to explain the repository | Optional targeted subagent(s) |

Choose by **uncertainty and usefulness**, not a fixed number of files. Do not delegate merely because separate agents are available. When delegation materially helps, assign one bounded question and specific file paths to an agent; avoid five overlapping mandatory roles. The main agent remains responsible for checking and synthesizing findings. Do not fabricate delegation or record an internal delegation log in the report.

## Workflow

1. **Reuse current knowledge.** If the canonical report exists, read it first. Note its baseline commit/date if recorded. Reuse confirmed facts unless affected by changes or clearly stale.
2. **Scope changes.** For refreshes, use Git history/diffs from the recorded baseline when possible and inspect working-tree changes (`git status --short`). If the baseline cannot be compared, examine high-signal files directly; never infer that nothing changed from a failed or incomplete diff.
3. **Inspect source-of-truth files selectively.** Prefer README, package/project metadata, build scripts, top-level source tree, entrypoints, test configuration, CI, and Docker/devcontainer files where present. Use `git ls-files` or shallow directory listings; avoid dumping or reading the entire tree. Follow dependencies only far enough to explain the main flow.
4. **Extract practical facts.** Determine purpose, primary implementation areas, entrypoints, key dependencies/configuration, main flow, and documented install/run/test/lint/build commands. Include only commands supported by actual files; distinguish a command found in configuration from one actually executed.
5. **Separate fact from inference.** Ground important claims in file paths, symbols, config keys, or observed command output. Mark reasonable inference as `推定` and gaps as `未確認`. A missing test or problem is not established merely because it was not found during a shallow scan.
6. **Write concisely.** Use `templates/repository-overview.md`. Fill only useful rows; omit optional sections when not applicable. Usually 1–2 pages is enough for a simple repository. Add Mermaid only if it clarifies component relationships more than prose does.
7. **Check before saving.** Verify cited paths exist in the inspected revision, remove stale statements, avoid duplicate descriptions, and keep important source references near the relevant claims. On refresh, modify only sections affected by the new evidence. Save in place rather than creating dated copies.

## Investigation priorities

- **Always:** purpose, technology/project type, where important code lives, how to navigate entrypoints, source-supported commands, and notable unknowns.
- **When relevant:** shared libraries vs applications, cross-package dependency graph, configuration precedence, CI, test layout, runtime flow, external integrations, Docker/devcontainer.
- **Only on request or clear evidence:** detailed quality/coverage audit, full documentation-consistency audit, exhaustive public-API enumeration, performance/security review, or full call graphs.

For a multi-project monorepo, summarize packages/services in a map and expand only central or requested areas. Do not force every optional topic into the report.

## Refresh policy

- Keep `docs/agent-reports/repository-overview.md` as the current canonical document and use Git history for its previous versions.
- Record an investigation date and, when available, the inspected commit SHA. Mention relevant uncommitted changes when they affect conclusions.
- Update changed facts and references; retain unaffected, still-supported content. If an old claim cannot be verified, mark it unconfirmed or remove it rather than restating it as current.
- Refresh when requested or after meaningful architectural/tooling changes, **not after every trivial edit**.

## Output

Use `templates/repository-overview.md`. The core report should answer:

1. What is the repository for?
2. Which paths/components matter first?
3. What is the main entrypoint/flow, if any?
4. Where are the authoritative setup and validation commands?
5. What is uncertain or important to inspect next?

Finally report the updated path and a short summary of what changed. Do not paste the full report unless asked.
