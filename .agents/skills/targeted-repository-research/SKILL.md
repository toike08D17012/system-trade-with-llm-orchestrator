---
name: targeted-repository-research
description: Use this skill to investigate the current implementation of a focused repository topic, such as a feature, behavior, symbol, command, configuration, dependency, test area, error, or change impact. Use it for current-implementation checks before planning or modifying code; use repository-overview for broad repository mapping.
---

# Targeted Repository Research Skill

## Purpose

Investigate a user-specified topic inside the repository and produce a grounded answer that explains:

* Where the relevant implementation lives
* How the relevant code, configuration, or behavior works
* What files, symbols, tests, or commands are involved
* What evidence supports the conclusion
* What remains uncertain
* What follow-up actions are recommended

This skill is optimized for focused, user-directed repository investigation.

Use this skill both for:

* direct repository research requests
* current implementation checks before planning or modifying code

For broad repository onboarding or overall repository mapping, use the `repository-overview`
workflow instead.

For implementation planning, use this skill as a prerequisite when the current
implementation is not already understood, then pass the research result to the
implementation planning workflow.

## Language Policy

Final targeted research artifacts MUST be written in Japanese unless the user
explicitly requests another language.

This includes persistent reports under `docs/agent-reports/research/` and
conversation-only final research answers. Keep code identifiers, file paths,
commands, symbols, configuration keys, error messages, and quoted source text
unchanged.

Use English for all main-agent/subagent communication, including delegated prompts,
intermediate investigation reports, review notes, and handoff material.

The main agent MUST synthesize English delegated outputs into a Japanese final
research artifact.

Prefer English for private reasoning where possible, but do not expose private
chain-of-thought.

## When to Use This Skill

Use this skill when the user asks to investigate the current repository
implementation for a focused topic.

Examples:

* "Check the current implementation of X."
* "Understand how X is currently implemented before changing it."
* "Investigate the existing behavior of X."
* "Find where X is implemented and how it works."
* "Trace the current flow for X."
* "Identify what files, tests, and configs are involved in X."
* "What would be affected if we change X?"
* "Which tests cover X?"
* "Where is this setting configured?"
* "What code could explain this error?"

This skill should also be used for equivalent requests written in languages other
than English, including requests that mean:

* Check the current implementation.
* Investigate the existing implementation.
* Understand how something is currently implemented before planning or changing it.
* Find where a behavior is implemented.
* Trace how the current behavior works.
* Identify the impact scope of a potential change.

## When Not to Use This Skill

Do not use this skill when:

* The user asks for a broad repository overview, onboarding map, or architecture map.

  * Use the `repository-overview` workflow instead.
* The user asks for a concrete implementation plan and the current implementation
  is already known.

  * Use the implementation planning workflow instead.
* The user explicitly asks to directly modify files without requesting investigation first.

  * Follow the repository's default planning and editing rules.
* The user asks for general programming advice that does not require grounding in
  this repository.
* The user asks to summarize a file or document already provided in full and no
  repository investigation is needed.

## Default Behavior

Start with read-only investigation.

Do not modify source code, configuration files, tests, documentation, generated
artifacts, or lock files unless the user explicitly asks for edits.

Do not install dependencies, start services, run migrations, update snapshots, or
run expensive commands unless explicitly allowed or clearly safe for the repository
context.

When available, read `docs/agent-reports/repository-overview.md` first to
understand the repository structure and avoid unnecessary exploration.

Do not rely on the overview report blindly. Verify all relevant findings against
current repository files.

By default, answer in the conversation without creating files.

However, create or update a targeted research report when:

* The user explicitly asks for persistent output.
* The investigation is requested as preparation for an implementation plan or code change.
* The result will likely be useful for future coding agents.
* The investigation spans more than a small number of files or symbols.
* The topic is complex enough that future reference would reduce repeated investigation.

Create or update the report under:

* `docs/agent-reports/research/<topic-slug>.md`

Use a short, lowercase, kebab-case topic slug.

Examples:

* `docs/agent-reports/research/dataset-loading-flow.md`
* `docs/agent-reports/research/model-checkpoint-loading.md`
* `docs/agent-reports/research/precommit-ruff-behavior.md`

If a report for the same topic already exists, read it first, verify whether it is
still accurate, and update it in place instead of creating a dated copy.

Use Git history to track previous versions when needed.

## Persistent Report Format

When creating or updating a persistent targeted research report, use
`report-template.md` in this skill directory.

If `report-template.md` exists, it is the source of truth for report structure.

Do not duplicate the full report template in this `SKILL.md`.

If `report-template.md` is missing, create a concise report that satisfies the
completion criteria in this skill.

The report must still satisfy the completion criteria in this skill, including:

* direct conclusion
* scope
* relevant files and symbols
* findings
* behavior or flow, when applicable
* impact scope, when applicable
* relevant tests and validation
* unknowns and assumptions
* recommended next actions
* evidence log
* delegation log, when delegation was required, attempted, or skipped

If `report-template.md` does not include a delegation log section, append one when
delegation was required, attempted, or skipped due to environment limitations.

## Investigation Principles

Follow these principles:

* Focus on the user-specified topic.
* Avoid turning a targeted investigation into a full repository overview.
* Prefer concrete evidence over assumptions.
* Cite file paths, config keys, commands, and symbols when summarizing.
* Distinguish confirmed facts from inferred hypotheses.
* Trace only as far as needed to answer the user's question.
* Prefer precise search terms over broad file dumps.
* Identify unknowns instead of guessing.
* Keep the final output actionable for future implementation, debugging, testing,
  or review.
* Keep small investigations lightweight.
* Use persistent reports when the result is likely to be reused by later agents.

## Research Question Framing

Before deep investigation, identify the investigation type.

Common types:

* Implementation location: "Where is this feature implemented?"
* Symbol trace: "Where is this function/class/setting defined and used?"
* Behavior flow: "How does this behavior work?"
* Impact scope: "What changes if this code/config/spec changes?"
* Bug context: "What code could explain this error or unexpected behavior?"
* Test target: "Which tests cover this behavior?"
* Config/dependency: "Where is this setting/dependency/tool configured?"
* Current implementation check: "How is this currently implemented?"
* Pre-change investigation: "What do we need to understand before changing this?"

If the user's request includes multiple topics, split them into separate research
questions and investigate each one independently.

Do not ask for clarification when a reasonable interpretation exists. State the
assumption and proceed.

If the user asks broadly to check the current implementation without naming a
precise symbol, infer the topic from the nearest available context:

* the feature or change requested in the same user message
* the previous implementation-plan or investigation request
* mentioned files, commands, errors, configs, tests, or symbols
* domain terms in the request
* recently discussed repository work, when clearly relevant

If no reasonable topic can be inferred, perform only routing-level discovery and
ask the user for the missing target.

## Mandatory Delegation Contract

This skill is designed to use the repository's configured custom agents/subagents.

The main agent MUST use the current tool's native separate-agent mechanism when one
is available.

This instruction is platform-neutral. Use the matching custom agent/subagent by role
name. Do not rely on tool-specific invocation syntax in this skill.

The main agent MUST NOT merely role-play these subagents inside the main context.

Small investigations may be completed directly when they do not require tracing more
than two files or symbols.

### Required Delegation Rules

For any investigation that requires tracing more than two files or symbols, the main
agent MUST delegate targeted investigation to the matching custom agent/subagent
role.

The appropriate delegation target depends on the investigation type:

* For implementation location queries, delegate to `implementation-location-researcher`.
* For symbol definition and usage queries, delegate to `symbol-trace-researcher`.
* For runtime behavior queries, delegate to `behavior-flow-researcher`.
* For change impact queries, delegate to `impact-scope-researcher`.
* For error or bug context queries, delegate to `bug-context-researcher`.
* For quality or test coverage queries, delegate to `quality-and-test-researcher`.
* For configuration or dependency queries, delegate to `project-config-researcher`.

For current implementation checks, choose the role based on the main question:

* Use `implementation-location-researcher` when the main goal is to find where the
  implementation lives.
* Use `behavior-flow-researcher` when the main goal is to explain how the behavior
  works at runtime.
* Use `symbol-trace-researcher` when the main goal is to trace a class, function,
  method, setting, or symbol.
* Use `impact-scope-researcher` when the investigation is preparation for a change.
* Use `quality-and-test-researcher` when the user asks how the current behavior is
  tested.
* Use `project-config-researcher` when the topic is primarily configuration,
  dependencies, tooling, or environment behavior.
* Use `bug-context-researcher` when the current implementation is being investigated
  because of an error or suspicious behavior.

For complex investigations that require multiple investigation types, the main agent
MUST delegate to all matching roles and synthesize the results.

The main agent must wait for delegated results before writing the final answer.

The main agent must synthesize delegated outputs into a coherent final answer. Do not
blindly paste delegated output.

### Main Agent Limits Before Delegation

Before delegation, the main agent may perform only routing-level discovery.

Allowed routing-level discovery:

* identify the investigation type from the user request
* infer the target topic when the request is phrased broadly
* check whether repository overview exists
* search for simple keywords to understand scope
* read the first few top-level project files if needed to route correctly

Routing-level discovery does not include:

* tracing multiple symbols or files
* understanding behavior flow
* analyzing call chains or data flow
* evaluating impact scope
* investigating error context
* analyzing test coverage
* examining configuration patterns

If the task requires deeper investigation, delegate it before continuing.

### Fallback Rule

If the current environment does not expose any usable separate-agent mechanism, the
main agent may proceed directly.

When falling back, the main agent MUST record the fallback in the final answer or
persistent report and explain why delegation was not used.

Do not silently skip delegation.

### Delegation Log Requirement

Every persistent research report created by this skill MUST include a `Delegation Log`
section when delegation was required, attempted, or skipped due to environment
limitations.

If delegation was not required because the investigation was small enough to complete
directly, the report may either omit the delegation log or state that delegation was
not required.

When a delegation log is included, use this structure:

| 役割                                 | 委任      | 結果を使用  | メモ  |
| ------------------------------------ | --------- | ----------- | ----- |
| `implementation-location-researcher` | はい / いいえ | はい / いいえ | ... |
| `symbol-trace-researcher`            | はい / いいえ | はい / いいえ | ... |
| `behavior-flow-researcher`           | はい / いいえ | はい / いいえ | ... |
| `impact-scope-researcher`            | はい / いいえ | はい / いいえ | ... |
| `bug-context-researcher`             | はい / いいえ | はい / いいえ | ... |
| `quality-and-test-researcher`        | はい / いいえ | はい / いいえ | ... |
| `project-config-researcher`          | はい / いいえ | はい / いいえ | ... |

If a role was not delegated because the investigation was routing-level only, state
that clearly.

If a role was not delegated because the environment could not invoke separate agents,
state that clearly.

If the answer is only provided in the conversation, include a brief delegation note
only when delegation was required, attempted, or skipped due to fallback.

## High-Level Workflow

### 1. Understand the Request

Extract:

* Target topic
* Relevant names, symbols, files, commands, errors, configs, tests, or behavior
* Expected answer type
* Constraints from the user
* Whether persistent output is needed
* Whether the result is preparation for implementation planning or code changes

State any important assumption before proceeding when the target is inferred.

### 2. Establish Repository Context

Check `docs/agent-reports/repository-overview.md` if it exists.

Use it to identify likely source directories, tests, configs, commands, and runtime
entry points.

Verify findings against current repository files.

Do not turn this step into a full repository overview.

### 3. Search for Relevant Evidence

Use targeted searches based on:

* Exact symbol names
* Error messages
* Config keys
* CLI commands
* Function/class names
* File names
* Test names
* Documentation references
* Domain terms from the user request

Prefer commands such as:

```bash
rg "<exact-term>"
rg "<symbol-name>"
rg "<config-key>"
git ls-files | rg "<path-or-topic>"
```

Use repository-appropriate commands.

Avoid noisy full-tree dumps.

### 4. Delegate When Required

Use custom agent/subagent roles when the investigation requires tracing more than two
files or symbols.

Provide each delegated role with a narrow, explicit research question.

Ask delegated roles to return:

* confirmed files, symbols, configs, commands, or tests
* concise explanation of their findings
* evidence for each important claim
* unknowns and risks
* recommended next checks

Delegated roles must write their returned reports in English.

Do not ask delegated roles to make broad final conclusions outside their assigned
scope.

### 5. Trace the Relevant Context

Depending on the request, trace only what is needed:

* Definition and usage
* Import/export chain
* Call path
* Runtime entrypoint
* Configuration loading
* Data flow
* Test coverage
* CI or command execution
* Documentation references
* Side effects and external interfaces

Stop when the evidence is sufficient to answer the user's question.

### 6. Synthesize the Answer

Produce a concise answer with:

* Direct conclusion
* Relevant files and symbols
* Explanation of behavior or relationships
* Evidence
* Unknowns and assumptions
* Suggested next actions

Write the final answer or persistent report in Japanese unless the user explicitly
requests another language.

For implementation or debugging tasks, include concrete next steps such as:

* Files to edit
* Tests to run
* Additional checks to perform
* Risks to watch

Do not blindly paste delegated output.

Synthesize delegated findings into one coherent answer.

## Output Style

Use concise but useful Markdown.
Write final research output in Japanese unless the user explicitly requests another
language.

Start with the answer, not the investigation log.

Every important claim should be grounded by at least one of:

* File path
* Config key
* Command result
* Symbol name
* Error message
* Test name
* Explicit uncertainty note

Use Japanese labels where helpful:

* `確認済み:` for facts grounded in files
* `推定:` for reasonable conclusions
* `不明:` for unresolved items

Prefer short sections over long narratives.

Avoid generic advice that is not grounded in repository evidence.

## Conversation Answer Template

When answering only in the conversation, use this structure when appropriate:

```markdown
## 結論

...

## 根拠

- `path/to/file.py`: ...
- `path/to/test.py`: ...
- `pyproject.toml`: ...

## 仕組み

...

## 不明点 / 前提

...

## 推奨される次の手順

...
```

For very small investigations, a shorter answer is acceptable.

## Completion Criteria

The investigation is complete when the final output explains:

* What was investigated
* What the conclusion is
* Which files, symbols, configs, tests, or commands are involved
* Why the conclusion is supported
* What remains uncertain
* What should be done next

If the investigation was preparation for implementation planning, the output must also
identify:

* the likely files to edit
* the relevant tests or validation commands
* risks or assumptions that the implementation plan must account for

If creating a persistent report, make sure it is saved under:

* `docs/agent-reports/research/<topic-slug>.md`

Persistent reports must follow `report-template.md` when it exists.
