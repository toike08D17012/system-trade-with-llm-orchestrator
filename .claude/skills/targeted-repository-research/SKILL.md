---
name: targeted-repository-research
description: Investigate a focused topic in the current repository and give a concise, evidence-based answer. Use for implementation location, behavior, symbol usage, configuration, bug context, test location, or change impact. For a broad map of the repository, use repository-overview; for designing changes, use implementation-plan.
---

# Targeted Repository Research

## Goal and boundaries

Answer the user's specific repository question with the **smallest investigation that provides sufficient evidence**. Make the result reusable by a later implementation plan when appropriate.

- Investigation is read-only. Do not edit code, tests, configuration, lockfiles, or documentation; the only permitted write is a requested or warranted research report.
- Do not implement a fix or create a full implementation plan in this skill.
- Write final answers and saved reports in Japanese unless the user requests another language. Preserve paths, identifiers, commands, and quoted source text.
- Do not install dependencies, run formatters, update snapshots, start services, run migrations, or run expensive checks merely to prepare a report. Prefer reading test and CI definitions to running them.

## Choose depth by uncertainty and risk

| Depth | When | Expected output |
| --- | --- | --- |
| Quick | Exact location, setting, or narrow question with a straightforward answer | Brief answer and direct evidence; no report by default |
| Standard (default) | A behavior, call path, relevant tests, or a bounded change-impact question | Focused findings, relevant files, uncertainties, and next step |
| Deep | Cross-component behavior, difficult-to-reconcile evidence, public interfaces, security, data migrations, or consequential changes | Expand only necessary branches; optional independent investigation or review |

Depth is **not determined by a file or symbol count**. Escalate only if uncertainty, coupling, or potential impact justifies it. A larger straightforward search need not become a Deep investigation.

## Investigation workflow

1. **Frame the question.** Identify the target, the decision the answer should enable, and what is out of scope. If a reasonable interpretation exists, state it and proceed.
2. **Reuse context.** If relevant, read `docs/agent-reports/repository-overview.md` and an existing report on the same topic. Treat them as navigation aids, not as proof of the current implementation.
3. **Search narrowly.** Use exact symbol names, errors, config keys, file names, and targeted `rg` / `git ls-files` queries. Read relevant definitions and callers, tests, or configuration only as needed. Avoid exhaustive tree dumps or unrelated architecture exploration.
4. **Verify the answer.** Ground consequential statements in current paths and symbols (and line numbers where available). Distinguish confirmed behavior, reasonable inference, and unknowns. Documentation or a configured command does not prove successful execution.
5. **Stop when sufficient.** Stop after answering the question, identifying the relevant evidence, and recording decision-relevant uncertainty. Do not continue collecting corroboration with negligible value.
6. **Respond or save.** Give the direct conclusion first. Save a report only under the rules below.

A read-only test or command may be run when it resolves material uncertainty, is safe and inexpensive, and its result matters to the question. Otherwise identify the applicable test/command without executing it. Label commands as `設定から確認・未実行` or `実行済み` accurately.

## Delegation: optional, purposeful

The main agent may investigate directly regardless of file count. Delegate only when there is a clearly bounded independent question that would materially improve accuracy or speed (for example, separate subsystem behavior or a substantial cross-component impact trace).

- Give a subagent a narrow question, likely files, read-only constraints, and an expected concise evidence-backed answer.
- Prefer one focused delegate. Use multiple delegates only for genuinely independent investigation branches; do not invoke one agent per report section or investigation label.
- Do not require a strategy designer or quality reviewer for ordinary repository research. Use an independent review only when conflicting evidence or consequential risk remains.
- The main agent verifies and synthesizes findings; never report delegation as performed when it was not.

## Output and persistence

Default to an answer in the conversation. Create or update a persistent report when the user requests one, a subsequent implementation plan would benefit from it, or the investigation is complex enough to avoid repeated research.

- Path: `docs/agent-reports/research/<topic-slug>.md` (short, lowercase kebab-case).
- For an existing same-topic report, inspect it and update only findings needing correction or expansion; verify the current relevant source files. Do not create dated duplicates by default.
- Use `templates/report-template.md` when writing a report. Optional sections should be omitted when not applicable; do not fill empty tables for appearance.
- State the topic, direct answer, confirmed evidence (file paths / symbols / config keys), important unknowns, and next action. Include relevant tests and commands, especially for a planned code change. For a pre-change investigation, name likely change locations and meaningful compatibility or regression concerns without prescribing a full implementation design.
- Put a brief delegation note only if delegation occurred or materially affected the result; no mandatory per-role delegation table.

## Handoff

For follow-up `implementation-plan`, pass the saved report path or concise findings. The planning step should reuse verified findings and investigate only important gaps; it should not repeat this investigation from scratch. Do not start implementation without the user's required approval workflow.
