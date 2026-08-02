---
name: implementation-plan
description: Use this skill when the user wants to create an implementation plan for a repository change. This skill assumes that prerequisite investigation results may already exist in files, but it can delegate targeted read-only investigation, strategy design, and quality review to configured custom agents/subagents before producing a concrete implementation plan.
---

# Implementation Plan Skill

## Purpose

Create a concrete, grounded implementation plan for a requested repository change.

This skill bridges the gap between:

1. existing investigation reports, design notes, or repository research files
2. additional missing information discovered through targeted read-only investigation
3. a practical implementation strategy
4. a final implementation plan that future coding agents or developers can follow

The output must make it clear:

* what should be changed
* where it should be changed
* in what order the work should be done
* how the change should be tested
* what risks, assumptions, and open questions remain

This skill does not implement the requested change by default.

## Main Agent Role

The main agent is responsible for orchestration and final synthesis.

The main agent should focus on:

* understanding the user's requested change
* locating likely source investigation reports
* deciding what information is missing
* delegating heavy investigation to configured custom agents/subagents
* delegating implementation strategy design to configured custom agents/subagents
* delegating plan quality review to configured custom agents/subagents
* reviewing and reconciling delegated outputs
* creating the final implementation plan file
* reporting the created plan path to the user

The main agent should avoid doing heavy repository investigation directly.

The main agent is accountable for the final plan, but it is not expected to perform all low-level investigation itself.

## Language Policy

The final implementation plan file is a final artifact and MUST be written in
Japanese unless the user explicitly requests another language.

This requirement applies to visible headings, summaries, requirements, assumptions,
open questions, implementation steps, validation descriptions, risks, rollback
guidance, and done criteria. Keep code identifiers, file paths, commands, symbols,
configuration keys, and quoted source text unchanged.

Use English for all main-agent/subagent communication, including delegated prompts,
context research reports, strategy reports, review reports, and handoff material.

The main agent MUST synthesize English delegated outputs into a Japanese final
implementation plan.

Prefer English for private reasoning where possible, but do not expose private
chain-of-thought.

## Mandatory Delegation Contract

This skill is designed to use the repository's configured custom agents/subagents.

The main agent MUST use the current tool's native separate-agent mechanism when one is available.

This instruction is platform-neutral. Use the matching custom agent/subagent by role name. Do not rely on tool-specific invocation syntax in this skill.

The main agent MUST NOT merely role-play these subagents inside the main context.

### Required Delegation Rules

For any implementation plan that requires inspecting more than two repository files, the main agent MUST delegate targeted repository investigation to the custom agent/subagent whose role name is:

* `implementation-plan-context-researcher`

For any implementation plan that is not clearly small and local, the main agent MUST delegate strategy design to the custom agent/subagent whose role name is:

* `implementation-plan-strategy-designer`

For any plan that touches multiple files, public APIs, tests, CI, build, packaging, deployment, or has meaningful risk, the main agent MUST delegate draft review to the custom agent/subagent whose role name is:

* `implementation-plan-quality-reviewer`

The main agent must wait for delegated results before writing the final plan file.

The main agent must synthesize delegated outputs into a coherent final plan. Do not blindly paste delegated output.

### Main Agent Limits Before Delegation

Before delegation, the main agent may perform only routing-level discovery.

Allowed routing-level discovery:

* list likely report directories
* search filenames and report names
* identify candidate source material paths
* read the implementation-plan template
* check whether the plan output directory exists
* inspect a small number of top-level project files only when needed to route the task

Routing-level discovery does not include:

* broad source-code investigation
* reading many source files
* tracing implementation behavior in detail
* inspecting tests in detail
* designing the implementation strategy
* reviewing the implementation plan draft itself

If the task requires deeper investigation, delegate it before continuing.

### Fallback Rule

If the current environment does not expose any usable separate-agent mechanism, the main agent may proceed directly.

When falling back, the main agent MUST record the fallback in the plan's `Delegation Log` section and explain why delegation was not used.

Do not silently skip delegation.

### Delegation Log Requirement

Every implementation plan created by this skill MUST include a `Delegation Log` section.

Use this structure:

| 役割                                     | 委任      | 結果を使用  | メモ  |
| ---------------------------------------- | --------- | ----------- | ----- |
| `implementation-plan-context-researcher` | はい / いいえ | はい / いいえ | ... |
| `implementation-plan-strategy-designer`  | はい / いいえ | はい / いいえ | ... |
| `implementation-plan-quality-reviewer`   | はい / いいえ | はい / いいえ | ... |

If a role was not delegated because the change was small, local, and low-risk, state that clearly.

If a role was not delegated because the environment could not invoke separate agents, state that clearly.

## Custom Agent/Subagent Roles

Use the following custom agents/subagents when the current environment supports separate-agent delegation.

### `implementation-plan-context-researcher`

Use this role for targeted read-only investigation.

Delegate to this role before inspecting:

* existing investigation reports
* relevant source files
* relevant tests
* project configuration
* CI or pre-commit settings
* examples or documentation
* similar implementations in the repository

The expected output is a factual context research report in English.

The report should include:

* source material used
* files inspected
* confirmed current state
* relevant tests and validation commands
* risks and constraints
* missing or unclear information

### `implementation-plan-strategy-designer`

Use this role to convert investigation results into an implementation strategy.

Delegate to this role when you need:

* proposed implementation approach
* considered options
* phased implementation steps
* file-level change plan
* validation strategy
* risks and mitigations
* rollback plan
* done criteria

The expected output is a strategy report in English that the main agent can merge
into the final plan.

### `implementation-plan-quality-reviewer`

Use this role to review the implementation plan draft.

Delegate to this role before finalizing the plan when:

* the change touches multiple files
* the change affects public APIs
* the change affects tests, CI, build, packaging, or deployment
* the plan contains assumptions or open questions
* the implementation has non-trivial risk

For very small local changes, this review is optional.

The expected output is a review report in English containing:

* required fixes
* missing information
* risk gaps
* validation gaps
* unclear implementation steps
* suggested improvements

## When to Use This Skill

Use this skill when the user asks for things like:

* "Create an implementation plan"
* "Plan how to implement this feature"
* "Turn the investigation result into an implementation plan"
* "Based on this research file, create a plan"
* "Before editing, summarize the implementation steps"
* "追加調査して実装計画に落とし込んで"
* "実装前にplanファイルを作って"

This skill is especially useful when a previous investigation skill has already produced a markdown report, but the implementation path is not yet clear.

## When Not to Use This Skill

Do not use this skill for:

* broad repository overview
* root-cause investigation without a requested implementation
* directly editing source code
* debugging and fixing in the same step
* writing user-facing documentation without implementation planning

For broad repository understanding, use a repository overview or investigation skill first.

For direct code changes, use the appropriate implementation or coding workflow after this plan is created.

## Default Behavior

Start with read-only investigation.

Do not modify source code, configuration files, tests, or documentation, except for creating or updating the implementation plan file.

Do not implement the requested change unless the user explicitly asks to implement immediately or explicitly asks to skip planning.

The allowed write operation for this skill is creating or updating an implementation plan file under:

```text
docs/agent-reports/plans/
```

## Inputs

Use the following inputs when available:

* the user's requested change
* paths to previous investigation reports
* existing design notes
* issue descriptions
* relevant source files
* relevant tests
* project configuration files
* existing coding conventions
* existing architecture patterns

If the user does not provide an investigation report path, look for likely reports in:

* `docs/agent-reports/investigations/`
* `docs/agent-reports/research/`
* `docs/agent-reports/`
* `docs/`
* `reports/`
* `notes/`
* `.agents/`
* `agents/`

Prefer recently created or recently modified reports that clearly relate to the requested change.

## Core Principles

### 1. Main Agent Orchestrates, Custom Agents/Subagents Investigate

The main agent should not manually inspect many files when a matching custom agent/subagent is available.

Use configured custom agents/subagents to gather context, compare options, and review the draft.

The main agent owns final synthesis and final plan quality.

### 2. Ground the Plan in Existing Facts

The plan must be based on repository facts, not guesses.

When possible, reference:

* files inspected
* functions/classes/modules involved
* tests found
* configuration files found
* previous investigation reports used
* custom agent/subagent reports used

### 3. Investigate Only What Is Missing

Assume prior investigation may already contain most of the necessary context.

Do not repeat broad repository investigation unless the existing report is clearly insufficient.

Focus follow-up investigation on gaps that affect implementation planning.

### 4. Prefer Small, Safe, Incremental Changes

The implementation plan should be broken into small, reviewable steps.

Avoid broad rewrites unless clearly justified.

Prefer plans that preserve existing architecture and conventions.

### 5. Make Validation Explicit

Every implementation plan must include concrete validation steps.

Examples:

* unit tests to add or update
* integration tests to run
* lint/type-check commands
* manual verification steps
* expected behavior after implementation

### 6. Separate Facts, Assumptions, and Open Questions

The plan should clearly distinguish:

* confirmed facts from repository investigation
* assumptions made to proceed
* unresolved questions that may need user or maintainer confirmation

When a question is not blocking, proceed with a reasonable assumption and document it.

When a question is blocking, mark it clearly.

### 7. Keep the Plan Executable

The plan should be detailed enough that another coding agent or developer can implement it without repeating the full investigation.

Avoid vague steps such as "update the logic" or "fix the tests" unless they are tied to concrete files and behavior.

## Workflow

### Step 1: Understand the Requested Change

Restate the requested change in practical implementation terms.

Clarify internally:

* what behavior should change
* what new capability should exist
* what existing behavior must be preserved
* whether this is a feature, refactor, bug fix, test improvement, or tooling change

Do not ask the user for clarification unless the request is too ambiguous to produce a useful plan.

When reasonable assumptions are possible, proceed and document them.

### Step 2: Locate Existing Investigation Results

Find likely existing investigation reports or design notes.

Prefer files under:

```text
docs/agent-reports/investigations/
```

Also check:

```text
docs/agent-reports/
docs/
reports/
notes/
.agents/
agents/
```

The main agent may do lightweight routing-level discovery to locate candidate reports.

If candidate reports are found, pass them to the `implementation-plan-context-researcher` role.

### Step 3: Delegate Context Research

Use the custom agent/subagent with the role name `implementation-plan-context-researcher` for targeted read-only investigation when required by the Mandatory Delegation Contract.

Provide the delegated role with:

* the user's requested change
* candidate investigation report paths
* any known relevant files
* specific missing information questions
* instruction to avoid modifying files

Ask the delegated role to return:

* existing reports used
* files inspected
* confirmed current state
* relevant tests and validation commands
* risks and constraints
* missing or unclear information

The delegated role's returned report must be written in English.

The main agent should review the delegated result and decide whether enough information exists to plan.

### Step 4: Identify Remaining Missing Information

Use this checklist:

* Goal and expected behavior are clear
* Relevant entry points are known
* Main files/classes/functions to change are known
* Existing architecture pattern is understood
* Public API impact is understood
* Configuration impact is understood
* Data/model/schema impact is understood, if applicable
* Test locations are known
* Validation commands are known
* Backward compatibility risks are known
* Performance/security risks are considered, if relevant
* Migration or rollout needs are considered, if relevant

If important information is still missing, either:

* send a focused follow-up request to the `implementation-plan-context-researcher` role
* document the unknown as an open question if it is not worth further investigation
* mark it as blocking if the plan cannot safely proceed without it

### Step 5: Delegate Strategy Design

Use the custom agent/subagent with the role name `implementation-plan-strategy-designer` after enough context has been gathered, unless the change is clearly small and local.

Provide the delegated role with:

* the user's requested change
* context research result
* relevant constraints
* assumptions already made
* open questions

Ask the delegated role to return:

* recommended approach
* considered options
* phased implementation steps
* file-level change plan
* validation strategy
* risks and mitigations
* rollback plan
* done criteria

The delegated role's returned strategy report must be written in English.

The main agent should not blindly copy the strategy result.

The main agent must synthesize it into the final implementation plan and resolve inconsistencies.

### Step 6: Draft the Implementation Plan

Create a draft using:

```text
agents/skills/implementation-plan/templates/implementation-plan.md
```

If the template does not exist, use the template structure defined in this skill.

Fill every section.

If a section is not applicable, write `該当なし`.

Write the final visible plan content in Japanese, except for code identifiers, file
paths, commands, symbols, configuration keys, and quoted source text.

The plan must include:

* summary
* user request
* source material used
* delegation log
* confirmed current state
* requirements
* assumptions
* open questions
* proposed approach
* considered options, when useful
* implementation steps
* file-level change plan
* validation plan
* risks and mitigations
* rollback plan
* done criteria

### Step 7: Delegate Quality Review

For medium or large plans, or any plan with meaningful risk, use the custom agent/subagent with the role name `implementation-plan-quality-reviewer`.

Provide the delegated role with:

* the draft implementation plan
* the original user request
* context research result
* strategy result, if available

Ask the delegated role to identify:

* required fixes
* missing information
* risk gaps
* validation gaps
* unclear implementation steps
* suggested improvements

The delegated role's returned review report must be written in English.

Apply required fixes before writing the final plan file.

If suggested improvements are useful but not essential, apply them when they improve clarity without overcomplicating the plan.

### Step 8: Create the Implementation Plan File

Create a markdown file under:

```text
docs/agent-reports/plans/
```

Use the filename format:

```text
YYYY-MM-DD-<short-topic>-implementation-plan.md
```

Examples:

```text
docs/agent-reports/plans/2026-06-10-add-retry-handler-implementation-plan.md
docs/agent-reports/plans/2026-06-10-refactor-dataloader-implementation-plan.md
docs/agent-reports/plans/2026-06-10-update-pre-commit-hooks-implementation-plan.md
```

Filename rules:

* Use the current local date.
* Use lowercase kebab-case for `<short-topic>`.
* Keep the topic short but specific.
* Do not overwrite an existing plan unless the user explicitly asks to update it.
* If a similar plan already exists, either update it with permission or create a new file with a more specific topic name.

Before writing the plan, ensure the output directory exists:

```bash
mkdir -p docs/agent-reports/plans
```

The implementation plan is the primary output of this skill.
The implementation plan file must be written in Japanese unless the user explicitly
requests another language.

Do not implement the change unless the user explicitly asks to proceed with implementation.

### Step 9: Final Response to the User

After creating the plan file, respond with only:

* the created plan file path
* a short summary of the proposed approach
* blocking open questions, if any
* recommended next step

Do not paste the entire plan unless the user asks for it.
Write this final response in Japanese unless the user explicitly requests another
language.

## Implementation Plan Template

Use the external template when available:

```text
agents/skills/implementation-plan/templates/implementation-plan.md
```

If the template file does not exist, use this structure:

`````markdown
# 実装計画: <タイトル>

## 1. 概要

<実装する内容と理由を簡潔に記載する。>

## 2. ユーザー依頼

<ユーザー依頼を実装観点で言い換える。>

## 3. 参照資料

<この計画の作成に使った調査レポート、設計メモ、issue、ソースファイル、テスト、委任レポート、設定ファイルを列挙する。>

## 4. 委任ログ

| 役割 | 委任 | 結果を使用 | メモ |
| --- | --- | --- | --- |
| `implementation-plan-context-researcher` | はい / いいえ | はい / いいえ | ... |
| `implementation-plan-strategy-designer` | はい / いいえ | はい / いいえ | ... |
| `implementation-plan-quality-reviewer` | はい / いいえ | はい / いいえ | ... |

## 5. 確認済みの現状

<調査で確認したリポジトリ上の事実を列挙する。>

## 6. 要件

### 機能要件

- [ ] ...

### 非機能要件

- [ ] ...

### 互換性要件

- [ ] ...

## 7. 前提

- ...

## 8. 未解決の質問

### ブロッキング

- なし

### 非ブロッキング

- なし

## 9. 提案アプローチ

<採用する実装アプローチを説明する。>

## 10. 検討した選択肢

### 選択肢 A: <名前>

利点:

- ...

欠点:

- ...

判断:

- 採用 / 不採用: ...

## 11. 実装手順

### フェーズ 1: 準備

- [ ] ...

### フェーズ 2: コア実装

- [ ] ...

### フェーズ 3: テスト

- [ ] ...

### フェーズ 4: ドキュメントと整理

- [ ] ...

## 12. ファイル単位の変更計画

| ファイル | 予定変更 |
| --- | --- |
| `path/to/file` | ... |

## 13. 検証計画

### 自動チェック

```bash
<commands>
```

期待結果:

* ...

### 手動確認

* ...

## 14. リスクと緩和策

| リスク | 影響 | 緩和策 |
| ---- | ------ | ---------- |
| ...  | ...    | ...        |

## 15. ロールバック計画

<必要に応じて変更を戻す、または無効化する方法を説明する。>

## 16. 完了条件

実装は次の条件を満たすと完了です:

* [ ] 必要な挙動が実装されている。
* [ ] 関連テストが追加または更新されている。
* [ ] 既存テストが通る。
* [ ] lint とフォーマットチェックが通る。
* [ ] 該当する場合、型チェックが通る。
* [ ] 該当する場合、ドキュメントが更新されている。
* [ ] 無関係な変更が含まれていない。

`````

## Plan Creation Rules

When creating an implementation plan:

1. Read or locate existing investigation reports first.
2. Use the repository's configured custom agent/subagent mechanism when available.
3. Delegate targeted read-only investigation to the `implementation-plan-context-researcher` role when required.
4. Delegate implementation strategy design to the `implementation-plan-strategy-designer` role when required.
5. Delegate quality review to the `implementation-plan-quality-reviewer` role for non-trivial plans.
6. Create a plan file under `docs/agent-reports/plans/`.
7. Use `agents/skills/implementation-plan/templates/implementation-plan.md` as the base template when available.
8. Write the final visible plan content in Japanese unless the user explicitly requests another language.
9. Fill every section of the template.
10. If a section is not applicable, write `該当なし` instead of deleting it.
11. Include a `Delegation Log` section in every plan.
12. Do not modify source code during this skill.
13. Do not run formatters or tests unless they are needed for investigation and do not modify files.
14. In the final response, report only:
    - the created plan file path
    - a short summary
    - blocking open questions, if any
    - recommended next step

## Delegation Prompts

Use these prompts with the current environment's native separate-agent mechanism.

These prompts are platform-neutral. Do not include tool-specific invocation syntax in this skill.

### Context Research Prompt

```markdown
You are the custom agent/subagent with the role name `implementation-plan-context-researcher`.

User request:

<user request>

Candidate source material:

- <paths>

Please perform targeted read-only investigation for implementation planning.

Focus on:

- relevant existing reports
- relevant source files
- relevant tests
- project configuration
- existing behavior
- risks and constraints
- missing information

Constraints:

- Do not modify files.
- Do not implement the change.
- Do not create the final implementation plan file.
- Keep the investigation focused on information needed for implementation planning.
- Use English for all output to the main agent.

Return:

- source material used
- files inspected
- confirmed current state
- relevant tests and validation commands
- risks and constraints
- missing or unclear information
````

### Strategy Design Prompt

```markdown
You are the custom agent/subagent with the role name `implementation-plan-strategy-designer`.

User request:

<user request>

Context research result:

<context research result>

Relevant constraints:

<constraints>

Assumptions:

<assumptions>

Open questions:

<open questions>

Please create an implementation strategy.

Include:

- recommended approach
- considered options
- implementation phases
- file-level change plan
- validation strategy
- risks and mitigations
- rollback plan
- done criteria

Constraints:

- Do not modify files.
- Do not implement the change.
- Do not create the final implementation plan file.
- Use English for all output to the main agent.

Return the result as a strategy report that the main agent can synthesize into the final plan.
```

### Quality Review Prompt

```markdown
You are the custom agent/subagent with the role name `implementation-plan-quality-reviewer`.

User request:

<user request>

Context research result:

<context research result>

Strategy result:

<strategy result>

Draft implementation plan:

<draft plan>

Please review the draft for:

- completeness
- grounding in repository facts
- testability
- risk coverage
- rollback clarity
- implementation step clarity
- separation of facts, assumptions, and open questions

Constraints:

- Do not modify files.
- Do not implement the change.
- Do not write the final implementation plan file.
- The draft implementation plan may be written in Japanese.
- Use English for all output to the main agent.

Return:

- required fixes
- suggested improvements
- missing information
- risk gaps
- validation gaps
- unclear implementation steps
```

## Quality Checklist

Before finishing, verify that the plan:

* is based on actual repository investigation
* uses configured custom agent/subagent results when available
* includes a complete Delegation Log
* is written in Japanese unless the user explicitly requested another language
* references the source material used
* identifies concrete files to change
* breaks work into clear phases
* includes tests and validation commands
* separates facts from assumptions
* identifies risks and mitigations
* includes rollback guidance
* avoids unnecessary refactoring
* does not implement the change
* is detailed enough for another coding agent to execute

## Planning Depth Guidelines

### Small Change

For small, localized changes:

* context research may be delegated when more than two files must be inspected
* strategy design is optional if the implementation path is obvious
* quality review is optional
* keep the plan concise
* identify exact file/function targets
* include minimal but sufficient tests
* avoid over-design

### Medium Change

For changes touching multiple files:

* use the `implementation-plan-context-researcher` role
* use the `implementation-plan-strategy-designer` role
* use the `implementation-plan-quality-reviewer` role when risk or ambiguity exists
* include phased implementation
* identify dependencies between steps
* include regression tests
* document compatibility concerns

### Large Change

For architecture-level changes:

* use all three custom agent/subagent roles
* include design alternatives
* include migration or rollout strategy
* include risk analysis
* include incremental delivery milestones
* consider feature flags or compatibility layers if useful

## Handling Insufficient Information

If the existing investigation report is missing important details, delegate targeted read-only investigation to the `implementation-plan-context-researcher` role.

If information is still insufficient after reasonable investigation:

* do not invent details
* document the unknowns
* mark blocking questions clearly
* provide the best safe partial plan
* recommend the next investigation step

## Handling Missing Delegation Support

If a matching custom agent/subagent role is configured but the current environment cannot invoke separate agents:

1. Continue with the smallest necessary direct read-only investigation.
2. Record the fallback in the `Delegation Log`.
3. Keep the direct investigation focused.
4. Avoid broad repository exploration.
5. Do not claim that delegation occurred.

Example `Delegation Log` entry:

| 役割 | 委任 | 結果を使用 | メモ |
| --- | --- | --- | --- |
| `implementation-plan-context-researcher` | いいえ | いいえ | フォールバック: 現在の環境で利用可能な separate-agent 仕組みが公開されていなかったため、main agent が対象を絞った読み取り専用調査を実施した。 |

## Example Final Response

```markdown
実装計画を作成しました:

- `docs/agent-reports/plans/2026-06-10-add-xxx-implementation-plan.md`

概要:

- `src/...` を中心に小さく段階的に変更し、`tests/...` にテストを追加する計画です。

ブロッキングな未解決質問:

- 新しい挙動をデフォルトで有効にするか、オプション配下にするか。

推奨される次の手順:

- ブロッキングな質問を確認してから、計画のフェーズ 1 とフェーズ 2 を実装する。
```
