"""Configuration-driven provisional research with neutral roles and exact offline replay."""

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Literal, cast

import yaml
from pydantic import JsonValue

from stock_research_llm_orchestrator.contracts.base import ArtifactReferenceV1, StrictContractModel
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.agent_configuration import AgentConfigurationV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.configured_analysis import (
    AggregateBody,
    ConfiguredAnalysisV1,
    ConfiguredMainBody,
    ConfiguredReviewBody,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.evidence import EvidenceSetV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.objectives import SpecialistBody
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.policies import DetailedAnalysisPolicyV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.web_analysis import WebAnalysisV1
from stock_research_llm_orchestrator.research.input import reference, serialize, validate_input
from stock_research_llm_orchestrator.research.objectives import INSTRUCTION_NAMES, _pointer
from stock_research_llm_orchestrator.research.scenarios import observed_price, total_return
from stock_research_llm_orchestrator.research.workflow import (
    _TRANSLATION_INSTRUCTIONS,
    ReportTranslation,
    ResearchInvoker,
    ResearchRequest,
    _display,
)


SPECIALISTS = ("track_a_capital", "track_a_income", "track_b_capital", "track_b_income")
AGGREGATORS = ("track_a_aggregator", "track_b_aggregator")
STAGES = (*SPECIALISTS, *AGGREGATORS, "orchestrator", "primary_reviewer", "translation")
MODELS: tuple[type[StrictContractModel], ...] = (
    SpecialistBody,
    SpecialistBody,
    SpecialistBody,
    SpecialistBody,
    AggregateBody,
    AggregateBody,
    ConfiguredMainBody,
    ConfiguredReviewBody,
    ReportTranslation,
)


def _claims(body: StrictContractModel) -> set[str]:
    if isinstance(body, SpecialistBody):
        return {c.claim_id for h in body.horizons for c in h.claims}
    if isinstance(body, AggregateBody):
        return {c.claim_id for h in body.horizon_analyses for c in h.claims}
    return set()


def _check_evidence(value: JsonValue, allowed: set[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"evidence_ids", "counterevidence_ids"}:
                if not isinstance(child, list) or not set(cast("list[str]", child)) <= allowed:
                    raise ValueError("configured_evidence_reference_invalid")
            else:
                _check_evidence(child, allowed)
    elif isinstance(value, list):
        for child in value:
            _check_evidence(child, allowed)


def _check_main(
    main: ConfiguredMainBody,
    artifacts: Mapping[str, ConfiguredAnalysisV1 | WebAnalysisV1],
    price: dict[str, JsonValue] | None,
) -> dict[str, JsonValue]:
    track_claims = [_claims(artifacts[role].payload) for role in AGGREGATORS]
    all_summaries = track_claims[0] | track_claims[1]
    if not set(main.agreed_claim_ids) <= all_summaries:
        raise ValueError("configured_synthesis_claim_invalid")
    differences = {item.difference_id for item in main.differences}
    for item in main.differences:
        if not set(item.track_a_claim_ids) <= track_claims[0] or not set(item.track_b_claim_ids) <= track_claims[1]:
            raise ValueError("configured_difference_claim_invalid")
    calculations: dict[str, JsonValue] = {}
    for index, conclusion in enumerate(main.conclusions):
        summary = main.horizon_results[index]
        if not set(summary.agreed_claim_ids) <= all_summaries or not set(summary.difference_ids) <= differences:
            raise ValueError("configured_synthesis_reference_invalid")
        permitted: set[str] = set()
        unavailable: set[str] = set()
        for role in SPECIALISTS:
            specialist = cast("SpecialistBody", artifacts[role].payload)
            horizon = specialist.horizons[index]
            permitted.update(c.claim_id for c in horizon.claims)
            if horizon.evaluability == "not_evaluable":
                unavailable.add("capital_gain" if role.endswith("capital") else "income_gain")
        selected = set(conclusion.source_claim_ids)
        if not selected <= permitted or any(
            not any(c.startswith(role + "-") for c in selected) for role in SPECIALISTS
        ):
            raise ValueError("configured_conclusion_claim_invalid")
        if unavailable and not conclusion.specialist_limitations:
            raise ValueError("configured_limitations_required")
        if conclusion.primary_objective in unavailable:
            raise ValueError("configured_primary_not_evaluable")
        if (price is None or len(unavailable) == 2) and conclusion.decision != "insufficient_information":
            raise ValueError("configured_decision_requires_information")
        if conclusion.decision == "candidate" and summary.evaluability == "not_evaluable":
            raise ValueError("configured_candidate_conflicts_with_synthesis")
        calculations[conclusion.horizon] = [total_return(scenario, price) for scenario in conclusion.scenarios]
    return calculations


def _selected_findings(review: ConfiguredReviewBody) -> list[int]:
    """Prioritize blockers while retaining original finding indexes for exact translation keys."""
    severity = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    return sorted(
        range(len(review.findings)),
        key=lambda i: (
            not (
                review.findings[i].materiality.is_material
                or review.findings[i].severity in {"critical", "high"}
                or review.findings[i].classification_pending is not None
            ),
            -severity.get(review.findings[i].severity, 0),
            i,
        ),
    )[:3]


def _report_texts(artifacts: Mapping[str, ConfiguredAnalysisV1 | WebAnalysisV1]) -> dict[str, str]:
    """Translate only a bounded human decision report, never the internal worker corpus."""
    main = cast("ConfiguredMainBody", artifacts["orchestrator"].payload)
    review = cast("ConfiguredReviewBody", artifacts["primary_reviewer"].payload)
    selected: dict[str, str] = {}
    for index, conclusion in enumerate(main.conclusions):
        prefix = f"orchestrator/payload/conclusions/{index}"
        for key in ("story", "rationale", "capital_allocation_consistency"):
            selected[f"{prefix}/{key}"] = str(getattr(conclusion, key))
        for key, limit in (
            ("change_conditions", 2),
            ("missing_information", 2),
            ("unresolved_differences", 1),
            ("specialist_limitations", 1),
        ):
            for item, text in enumerate(getattr(conclusion, key)[:limit]):
                selected[f"{prefix}/{key}/{item}"] = text
        for item, scenario in enumerate(conclusion.scenarios):
            selected[f"{prefix}/scenarios/{item}/story"] = scenario.story
    for index in _selected_findings(review):
        for key in ("summary", "expected_resolution"):
            selected[f"primary_reviewer/payload/findings/{index}/{key}"] = str(getattr(review.findings[index], key))
    return selected


def _report(
    artifacts: Mapping[str, ConfiguredAnalysisV1 | WebAnalysisV1],
    translated: ReportTranslation,
    config: AgentConfigurationV1,
    calculations: dict[str, JsonValue],
    mode: str,
    security_label: str,
) -> bytes:
    texts = _report_texts(artifacts)
    translations = {item.key: item.text for item in translated.translations}
    if len(translations) != len(translated.translations) or set(translations) != set(texts):
        raise ValueError("configured_translation_coverage_invalid")
    if any(not text.strip() for text in translations.values()) or not re.search(
        r"[\u3040-\u30ff\u3400-\u9fff]", "".join(translations.values())
    ):
        raise ValueError("configured_translation_language_invalid")
    review = cast("ConfiguredReviewBody", artifacts["primary_reviewer"].payload)
    main = cast("ConfiguredMainBody", artifacts["orchestrator"].payload)

    def display(text: str) -> str:
        for internal, label in (
            ("capital_gain", "キャピタルゲイン"),
            ("income_gain", "インカムゲイン"),
            ("not_evaluable", "評価不能"),
        ):
            text = text.replace(internal, label)
        for difference in main.differences:
            text = text.replace(difference.difference_id, "")
        return _display(text.lstrip(":： ").replace("評価不能（評価不能）", "評価不能"))

    labels = {
        "candidate": "購入候補",
        "wait": "条件付き候補・待機",
        "reject": "見送り",
        "insufficient_information": "判断保留",
    }
    purposes = {"capital_gain": "キャピタルゲイン", "income_gain": "インカムゲイン", None: "未確定"}
    lines = [
        "# 個別株の投資判断レポート",
        "",
        f"対象銘柄: {security_label}",
        "",
        "暫定結果です。最終判断と売買は人間が行います。",
        "",
        "レビュー方式: 同一系統による開発用レビュー。"
        if config.review_policy.mode == "single_family_development"
        else "レビュー方式: 設定で宣言された異なるモデル系統によるレビュー。",
        "",
        "一次レビュー: 通過（暫定）。" if review.review_passed else "一次レビュー: 未通過。結論は未採用です。",
        "",
    ]
    if mode == "replay":
        lines.extend(["保存応答を再生したレポートです。", ""])
    for index, conclusion in enumerate(main.conclusions):
        prefix = f"orchestrator/payload/conclusions/{index}"
        lines.extend(
            [
                f"## {'中期' if index == 0 else '長期'}: {labels[conclusion.decision]}",
                "",
                f"主目的: {purposes[conclusion.primary_objective]} / "
                f"副目的: {purposes[conclusion.secondary_objective]}",
                "",
            ]
        )
        for key, label in (
            ("story", "想定ストーリー"),
            ("rationale", "判断理由"),
            ("capital_allocation_consistency", "成長投資と配当の整合性"),
        ):
            lines.extend([f"**{label}**: {display(translations[prefix + '/' + key])}", ""])
        lines.extend(
            [
                f"想定期間: {conclusion.scenarios[0].months}か月。"
                "収益率は条件付き・累計・税引前・手数料控除前・配当再投資なし。",
                "",
                "| シナリオ | 将来株価(JPY) | 累計配当(JPY/株) | 値上がり益率 | 配当収益率 | 合計収益率 |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        results = cast("list[dict[str, JsonValue]]", calculations[conclusion.horizon])
        for item, scenario in enumerate(conclusion.scenarios):
            result = results[item]
            rates = [
                f"{Decimal(str(result[key])) * 100:.1f}%" if result["status"] == "conditional" else "算出不能"
                for key in ("capital_return", "income_return", "total_return")
            ]
            label = {"bear": "弱気", "base": "基本", "bull": "強気"}[scenario.case]
            lines.append(
                f"| {label} | {scenario.terminal_price or '未算出'} | {scenario.cumulative_dividends or '未算出'} | "
                + " | ".join(rates)
                + " |"
            )
        lines.append("")
        for item, scenario in enumerate(conclusion.scenarios):
            label = {"bear": "弱気", "base": "基本", "bull": "強気"}[scenario.case]
            lines.extend([f"- {label}: {display(translations[f'{prefix}/scenarios/{item}/story'])}"])
        lines.append("")
        for key, label, limit in (
            ("change_conditions", "判断変更条件", 2),
            ("missing_information", "主な不足情報", 2),
            ("unresolved_differences", "未解決の見解差", 1),
            ("specialist_limitations", "分析上の制約", 1),
        ):
            values = getattr(conclusion, key)
            if values:
                lines.extend([f"**{label}**:", ""])
                lines.extend(
                    "- " + display(translations[f"{prefix}/{key}/{item}"]) for item in range(min(len(values), limit))
                )
                if len(values) > limit:
                    lines.append(
                        f"- ほか{len(values) - limit}件。詳細は[統合分析](orchestrator.json)に保持しています。"
                    )
                lines.append("")
    important_count = sum(
        finding.materiality.is_material or finding.severity in {"high", "critical"} for finding in review.findings
    )
    pending_count = sum(finding.classification_pending is not None for finding in review.findings)
    lines.extend(
        [
            "## レビューで残った事項",
            "",
            f"指摘は全{len(review.findings)}件（重要{important_count}件・分類未確定{pending_count}件）。"
            "優先度の高い最大3件を表示します。",
            "",
        ]
    )
    for index in _selected_findings(review):
        finding = review.findings[index]
        prefix = f"primary_reviewer/payload/findings/{index}"
        important = "重要" if finding.materiality.is_material or finding.severity in {"high", "critical"} else "要確認"
        lines.extend(
            [
                f"- {important}: {display(translations[prefix + '/summary'])}",
                f"  対応: {display(translations[prefix + '/expected_resolution'])}",
            ]
        )
    if len(review.findings) > 3:
        lines.append(f"残り{len(review.findings) - 3}件は[レビュー詳細](primary_reviewer.json)に保持しています。")
    lines.extend(
        [
            "",
            "独立探索・最終鮮度確認・正式な指摘処理は未完了です。",
            "",
            "詳細を確認する場合: [統合分析](orchestrator.json) / [レビュー](primary_reviewer.json) / "
            "[出典と原文](input/common-input.json)。内部分析の通読は前提にしていません。",
            "",
        ]
    )
    return "\n".join(lines).encode()


def _execute(
    inputs: Mapping[str, bytes],
    config: AgentConfigurationV1,
    sources: Mapping[str, bytes],
    invoke: ResearchInvoker,
    mode: Literal["live", "replay"],
    stamps: Mapping[str, str] | None = None,
) -> dict[str, bytes]:
    validate_input(inputs)
    if json.loads(sources["instruction-set.json"]) != {
        "instruction_set_version": 1,
        "files": {name: sha256(sources[name]).hexdigest() for name in INSTRUCTION_NAMES},
    }:
        raise ValueError("configured_instruction_hash_invalid")
    evidence = EvidenceSetV1.model_validate_json(inputs["evidence-set.json"])
    task = json.loads(inputs["task.json"])
    config_bytes = serialize(config)
    config_ref = reference("agent_configuration", config.artifact_id, config.schema_id, config_bytes)
    evidence_ref = reference("evidence_set", evidence.artifact_id, evidence.schema_id, inputs["evidence-set.json"])
    policy = DetailedAnalysisPolicyV1.model_validate(yaml.safe_load(inputs["policy.yaml"]))
    policy_ref = reference(
        "detailed_analysis_policy",
        policy.artifact_id,
        "detailed-analysis.detailed-analysis-policy",
        inputs["policy.yaml"],
    )
    excerpts = json.loads(inputs["common-input.json"])
    price = observed_price(excerpts)
    files = {
        **{f"input/{k}": v for k, v in inputs.items()},
        **{f"instructions/{k}": v for k, v in sources.items()},
        "agent-configuration.json": config_bytes,
    }
    common: dict[str, JsonValue] = {
        "policy": policy.model_dump(mode="json"),
        "task": task,
        "evidence_set": evidence.model_dump(mode="json"),
        "excerpts": excerpts,
        "observed_price": price,
        "policy_reference": policy_ref.model_dump(mode="json"),
    }
    artifacts: dict[str, ConfiguredAnalysisV1] = {}
    references: dict[str, ArtifactReferenceV1] = {}
    ids = {record.evidence_id for record in evidence.records if record.layer == "raw"}
    claimed: set[str] = set()
    calculations: dict[str, JsonValue] = {}
    executions: dict[str, JsonValue] = {}
    assignments = config.roles.assignments()
    previous_time = datetime.fromisoformat(evidence.evidence_frozen_at)
    for role, model in zip(STAGES, MODELS, strict=True):
        dependencies: list[str] = []
        data = dict(common)
        data["claim_prefix"] = role + "-"
        name = "aggregate.md"
        if role in SPECIALISTS:
            data["objective"] = "capital_gain" if role.endswith("capital") else "income_gain"
            name = "capital.md" if role.endswith("capital") else "income.md"
        elif role in AGGREGATORS:
            dependencies = [key for key in SPECIALISTS if key.startswith(role[:7] + "_")]
            data["specialists"] = {key: artifacts[key].model_dump(mode="json") for key in dependencies}
        elif role in {"orchestrator", "primary_reviewer"}:
            dependencies = [*SPECIALISTS, *AGGREGATORS]
            data["specialists"] = {key: artifacts[key].model_dump(mode="json") for key in SPECIALISTS}
            data["summaries"] = {key: artifacts[key].model_dump(mode="json") for key in AGGREGATORS}
            name = "main.md" if role == "orchestrator" else "review.md"
            if role == "primary_reviewer":
                dependencies.append("orchestrator")
                data["main"] = artifacts["orchestrator"].model_dump(mode="json")
                data["artifact_references"] = {key: ref.model_dump(mode="json") for key, ref in references.items()}
                data["calculated_returns"] = calculations
                data["review_policy"] = config.review_policy.model_dump(mode="json")
        instruction = sources["common.md"].decode() + "\n\n" + sources[name].decode()
        if role == "translation":
            data = dict(_report_texts(artifacts))
            instruction = (
                _TRANSLATION_INSTRUCTIONS + " Keep each Japanese value concise, normally one or two sentences. "
                "Translate only these selected report fields; do not add internal analysis."
            )
        alias = assignments[role]
        agent = config.agents[alias]
        provider: Literal["codex", "claude"] = "codex" if agent.adapter == "codex_cli" else "claude"
        request = ResearchRequest(role, provider, instruction, data, model, agent)
        files[f"requests/{role}.json"] = (
            json.dumps(
                {
                    "agent_alias": alias,
                    "agent": agent.model_dump(mode="json"),
                    "provider": provider,
                    "instructions": instruction,
                    "data": data,
                    "output_schema": model.model_json_schema(),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        ).encode()
        raw = invoke(request)
        files[f"responses/{role}.json"] = raw
        body = model.model_validate_json(raw)
        executions[role] = {
            "agent_alias": alias,
            "requested": agent.model_dump(mode="json"),
            "reported_model": None,
            "reported_effort": None,
            "settings_status": "requested_not_independently_verified",
            "family_source": "configuration_declaration",
        }
        if role == "translation":
            security_label = f"{task['security']['security_code']} ({task['security']['mic']})"
            files["report.md"] = _report(
                artifacts, cast("ReportTranslation", body), config, calculations, mode, security_label
            )
            continue
        _check_evidence(body.model_dump(mode="json"), ids)
        claims = _claims(body)
        if any(not claim.startswith(role + "-") for claim in claims) or claimed & claims:
            raise ValueError("configured_claim_identity_invalid")
        claimed.update(claims)
        if isinstance(body, SpecialistBody):
            for horizon in body.horizons:
                for scenario in horizon.scenarios:
                    total_return(scenario, price)
        if isinstance(body, (AggregateBody, ConfiguredMainBody)) and body.entry_exit_context.price_as_of is not None:
            raise ValueError("configured_exact_price_time_unavailable")
        if isinstance(body, ConfiguredMainBody):
            calculations = _check_main(body, artifacts, price)
        if isinstance(body, ConfiguredReviewBody):
            for finding in body.findings:
                targets = [key for key, ref in references.items() if ref == finding.target_artifact_reference]
                if not targets or not set(finding.target_claim_ids) <= claimed:
                    raise ValueError("configured_review_target_invalid")
                _pointer(artifacts[targets[0]].model_dump(mode="json"), finding.json_pointer)
                if any(ref != policy_ref for ref in finding.policy_references):
                    raise ValueError("configured_review_policy_invalid")
        stamp = (
            stamps[role]
            if stamps is not None
            else evidence.evidence_frozen_at
            if mode == "replay"
            else datetime.now(UTC).isoformat()
        )
        moment = datetime.fromisoformat(stamp)
        if moment < previous_time:
            raise ValueError("configured_artifact_time_invalid")
        previous_time = moment
        artifact = ConfiguredAnalysisV1.model_validate_json(
            json.dumps(
                {
                    "schema_id": "detailed-analysis.configured-analysis",
                    "schema_version": 1,
                    "task_id": task["task_id"],
                    "artifact_id": role,
                    "role": role,
                    "created_at": stamp,
                    "agent_alias": alias,
                    "agent": agent.model_dump(mode="json"),
                    "configuration_reference": config_ref.model_dump(mode="json"),
                    "evidence_set_reference": evidence_ref.model_dump(mode="json"),
                    "input_artifact_references": [references[key].model_dump(mode="json") for key in dependencies],
                    "payload": body.model_dump(mode="json"),
                }
            )
        )
        artifacts[role] = artifact
        files[f"{role}.json"] = serialize(artifact)
        references[role] = reference("configured_analysis", role, artifact.schema_id, files[f"{role}.json"])
    files["calculated-returns.json"] = (json.dumps(calculations, indent=2) + "\n").encode()
    review = cast("ConfiguredReviewBody", artifacts["primary_reviewer"].payload)
    files["manifest.json"] = (
        json.dumps(
            {
                "kind": "provisional-research-workflow",
                "format_version": 3,
                "mode": mode,
                "analysis_ready": False,
                "analysis_completed": False,
                "review_policy": config.review_policy.model_dump(mode="json"),
                "review_passed": review.review_passed,
                "unresolved_finding_ids": [finding.finding_id for finding in review.findings],
                "executions": executions,
                "pending_steps": ["final_freshness", "independent_search_coverage", "finding_processing"],
                "files": {key: sha256(value).hexdigest() for key, value in sorted(files.items())},
            },
            indent=2,
        )
        + "\n"
    ).encode()
    return files


def run_workflow(
    inputs: Mapping[str, bytes],
    config: AgentConfigurationV1,
    invoke: ResearchInvoker,
    instructions: Path,
    *,
    mode: Literal["live", "replay"],
) -> dict[str, bytes]:
    """Execute the selected profile with an isolated turn for every role."""
    sources = {name: (instructions / name).read_bytes() for name in (*INSTRUCTION_NAMES, "instruction-set.json")}
    files = _execute(inputs, config, sources, invoke, mode)
    validate_bundle(files)
    return files


def validate_bundle(files: Mapping[str, bytes]) -> None:
    """Reconstruct the complete bundle from its saved configuration, inputs and responses."""
    manifest = json.loads(files["manifest.json"])
    if manifest.get("format_version") != 3 or manifest["mode"] not in {"live", "replay"}:
        raise ValueError("configured_bundle_version_invalid")
    config = AgentConfigurationV1.model_validate_json(files["agent-configuration.json"])
    inputs = {key.removeprefix("input/"): value for key, value in files.items() if key.startswith("input/")}
    sources = {name: files[f"instructions/{name}"] for name in (*INSTRUCTION_NAMES, "instruction-set.json")}
    stamps = {role: json.loads(files[f"{role}.json"])["created_at"] for role in STAGES[:-1]}

    def replay(request: ResearchRequest) -> bytes:
        return files[f"responses/{request.stage}.json"]

    if dict(files) != _execute(inputs, config, sources, replay, manifest["mode"], stamps):
        raise ValueError("configured_bundle_binding_invalid")


def replay_saved_bundle(
    original: Mapping[str, bytes], inputs: Mapping[str, bytes], config: AgentConfigurationV1
) -> dict[str, bytes]:
    """Retain original artifact timestamps so review references remain exact during offline replay."""
    validate_bundle(original)
    expected_inputs = {key.removeprefix("input/"): value for key, value in original.items() if key.startswith("input/")}
    if dict(inputs) != expected_inputs or serialize(config) != original["agent-configuration.json"]:
        raise ValueError("configured_replay_input_mismatch")
    sources = {name: original[f"instructions/{name}"] for name in (*INSTRUCTION_NAMES, "instruction-set.json")}
    stamps = {role: json.loads(original[f"{role}.json"])["created_at"] for role in STAGES[:-1]}

    def replay(request: ResearchRequest) -> bytes:
        return original[f"responses/{request.stage}.json"]

    files = _execute(inputs, config, sources, replay, "replay", stamps)
    validate_bundle(files)
    return files
