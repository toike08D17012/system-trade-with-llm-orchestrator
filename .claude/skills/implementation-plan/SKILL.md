---
name: implementation-plan
description: Create a concise, repository-grounded implementation plan for a requested change. Select planning depth by uncertainty and risk, reuse existing research, and delegate only when it adds value. Do not implement changes as part of planning.
---

# Implementation Plan

## Goal and boundaries

Create an implementation plan that another coding agent can execute without repeating the investigation. Optimize for a **correct decision and a useful handoff**, not a long report or maximum tool use.

The plan should answer: **what changes, where, in what order, and how to verify it**. Separate confirmed facts from assumptions and unknowns. Do not invent file paths, behavior, tests, or commands.

- Planning is read-only except for creating/updating the plan Markdown file.
- Do not edit source code, tests, configurations, or other documentation in this skill.
- Do not begin implementation after producing a plan. Hand it to the user for review; proceed only after explicit approval in the implementation workflow.
- Write the final plan in Japanese unless the user requests another language. Preserve identifiers, paths, and commands as written. Use English for delegated agent communication when applicable.
- Write plans under `docs/agent-reports/plans/`, named `YYYY-MM-DD-<short-topic>-implementation-plan.md` (local date, lowercase kebab-case). Do not overwrite an existing plan unless asked to update it.
- If the user expressly requests only an inline plan, honor that request rather than creating a file.

## 1. Choose the planning depth

Choose by **uncertainty, blast radius, reversibility, and risk**, not the number of files. When in doubt, use Medium; escalate if new evidence changes the risk. The user's explicit request for detailed planning takes precedence.

| Level | Typical case | Output and process |
| --- | --- | --- |
| Small | Local, clear, low-risk change; simple mechanical edits across files | Short plan; main agent inspects only relevant context; no subagents or independent review by default. |
| Medium (default) | Several related changes, moderate ambiguity, or nontrivial test impact | Standard plan; targeted research; delegate context research only when it saves work or resolves uncertainty; no routine reviewer. |
| Large / risky | Architecture, public API/contract, data migration, security, deployment, destructive changes, or difficult rollback | Deeper plan; focused subagents as useful; independent review for material risk; include migration, compatibility, rollback, and staged validation as applicable. |

More files alone do not force delegation or review. A single-file change can still be high-risk.

## 2. Reuse evidence and investigate gaps

1. Understand the request, desired behavior, invariants, and constraints. Prefer reasonable documented assumptions to unnecessary clarification; ask only about genuinely blocking decisions.
2. If the user supplied a report or source path, read that first. Otherwise, look for relevant existing reports, typically `docs/agent-reports/investigations/`, `docs/agent-reports/research/`, `docs/`, or a path named in the request.
3. Inspect only the modules, entry points, interfaces, configurations, and tests necessary to locate the change and make a sound plan. Stop when the implementation path and validation targets are adequately supported.
4. Reuse confirmed findings. Do not repeat broad repository scans to reproduce an existing investigation. If a critical fact is missing, perform one focused follow-up or record a nonblocking unknown.
5. Cite concrete repository paths and symbols in the plan only if actually inspected or supported by an existing report. Mark unverified paths or commands as proposed, not confirmed.

During planning, **do not run test suites, formatters, or exhaustive verification by default**. A targeted, read-only diagnostic is allowed only when it resolves an uncertainty that would materially change the plan. Plan the implementation-time checks instead of executing them now.

## 3. Delegate selectively

The main agent can research, design, and draft directly. Delegation is an **option**, not a quota or a file-count rule. Use the current environment's real separate-agent mechanism, if available; never pretend to invoke one.

- `implementation-plan-context-researcher`: use for broad or unfamiliar code paths, missing facts, or parallel **read-only** research that would materially improve the plan. Give precise questions, known report paths, and scope boundaries. Request a brief factual summary with inspected paths and uncertainties.
- `implementation-plan-strategy-designer`: use only when significant competing architecture/implementation strategies need independent comparison. Not required for ordinary Medium plans.
- `implementation-plan-quality-reviewer`: use for meaningful risk, e.g. external contracts, migration, security, deployment, complex concurrency, or difficult rollback; optionally when the user explicitly requests independent review. Ask for **blocking defects and material gaps**, not stylistic polish or expanded checklists.

Avoid duplicating the same investigation in main and subagent contexts. A subagent should not write or modify the final plan. Reconcile its factual findings before incorporating them. If delegation is unavailable or unnecessary, proceed directly without lengthy fallback boilerplate. Add a brief delegation note only if it matters to the plan's provenance.

## 4. Draft the smallest executable plan

Use `templates/implementation-plan.md` as a starting point, not a form whose every optional field must be filled. Include only information useful to the implementer.

**Always include:**

- Short overview and expected behavior.
- Confirmed current state with relevant paths/symbols, plus important assumptions if any.
- Chosen approach and the reason for it.
- Concrete file-level changes and ordered implementation steps.
- Minimum sufficient implementation-time validation with expected outcomes.
- Blocking unresolved questions, if any (otherwise one short `なし` or omit the section).

**Add only when relevant:** competing options, explicit compatibility requirements, migration/rollout, manual checks, material risks/mitigations, rollback, detailed acceptance criteria, or a delegation note. Do not duplicate the same validation items in both a checklist and a completion-criteria section.

For Small changes, combine sections and use a few actionable bullets. For Medium, normally keep the template's core sections. For Large/risky, expand *only* the areas driving the additional risk. Avoid generic phases such as “preparation/core/tests/docs” if a more direct ordered checklist is clearer.

## 5. Right-size the validation plan

Validation is **proportional to the change**, and occurs mainly during implementation:

- Identify the narrowest relevant existing test(s) and new/updated test cases that exercise changed behavior.
- List necessary lint, formatting, type, or broader regression checks only if the repository requires them or the change warrants them. Reuse the project's established check command instead of inventing commands.
- For behavior untestable automatically, name one concrete manual check and expected result. Otherwise omit manual validation.
- For high-risk changes, include relevant negative/failure paths, integration tests, compatibility, rollout, or rollback verification.
- Avoid redundant tests, speculative test matrices, repeated full-suite runs, and instructions to verify the same fact several ways without a clear risk-based reason.

A useful check has **target + method + expected result**. If the existing validation command is unknown, say so; do not guess.

## 6. Final self-check and handoff

Do one brief self-check before writing the file:

- Is the approach grounded in confirmed repository facts, with assumptions distinguished?
- Could an implementer identify the target and sequence without repeating the investigation?
- Are tests proportional to risk, and are commands known or clearly marked proposed?
- Are open blocking decisions visible? Are unrelated changes excluded?

For risky plans reviewed independently, address the reviewer's material findings; do not run review loops solely to improve wording.

Create the plan Markdown file. The final user reply should give its path, a short approach summary, and any blocking question. **Stop here for human review.**
