"""Execute and replay version-2 objective specialists without changing legacy contracts."""

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal, cast

import yaml
from pydantic import JsonValue

from stock_research_llm_orchestrator.contracts.base import StrictContractModel
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.analysis import SynthesisResultV1, WorkerAnalysisV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.evidence import EvidenceSetV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.objectives import (
    ConclusionBody,
    InvestmentConclusionsV1,
    SpecialistAnalysisV1,
    SpecialistBody,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.policies import DetailedAnalysisPolicyV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.review import PrimaryReviewV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.task import DetailedAnalysisTaskV1
from stock_research_llm_orchestrator.research.input import reference, serialize, validate_input
from stock_research_llm_orchestrator.research.scenarios import observed_price, total_return
from stock_research_llm_orchestrator.research.workflow import (
    _TRANSLATION_INSTRUCTIONS,
    ReportTranslation,
    ResearchInvoker,
    ResearchRequest,
    ReviewBody,
    SynthesisBody,
    WorkerBody,
    _display,
    report_texts,
    validate_artifacts,
)


class MainBody(SynthesisBody, ConclusionBody):
    """One main turn produces synthesis and objective-specific conclusions together."""


SPECIALISTS = ("codex_capital", "codex_income", "claude_capital", "claude_income")
STAGES = (*SPECIALISTS, "codex_worker", "claude_worker", "orchestrator", "primary_reviewer", "translation")
INSTRUCTION_NAMES = ("common.md", "capital.md", "income.md", "aggregate.md", "main.md", "review.md")
_MODELS: tuple[type[StrictContractModel], ...] = (
    SpecialistBody,
    SpecialistBody,
    SpecialistBody,
    SpecialistBody,
    WorkerBody,
    WorkerBody,
    MainBody,
    ReviewBody,
    ReportTranslation,
)


def _provider(stage: str) -> Literal["codex", "claude"]:
    return "claude" if stage.startswith("claude") or stage == "primary_reviewer" else "codex"


def _pointer(value: JsonValue, pointer: str) -> None:
    if pointer == "":
        return
    if not pointer.startswith("/"):
        raise ValueError("objective_finding_pointer_invalid")
    try:
        for component in pointer.split("/")[1:]:
            key = component.replace("~1", "/").replace("~0", "~")
            if isinstance(value, list):
                if not key.isdecimal() or str(int(key)) != key:
                    raise ValueError("objective_finding_pointer_invalid")
                value = value[int(key)]
            elif isinstance(value, dict):
                value = value[key]
            else:
                raise ValueError("objective_finding_pointer_invalid")
    except KeyError, IndexError, TypeError:
        raise ValueError("objective_finding_pointer_invalid") from None


def _report(
    task: DetailedAnalysisTaskV1,
    artifacts: Mapping[str, StrictContractModel],
    translation: ReportTranslation,
    calculations: dict[str, JsonValue],
    mode: str,
) -> bytes:
    originals = report_texts(artifacts)
    translated = {item.key: item.text for item in translation.translations}
    if len(translated) != len(translation.translations) or set(translated) != set(originals):
        raise ValueError("research_translation_coverage_invalid")
    if any(not value.strip() for value in translated.values()):
        raise ValueError("research_translation_coverage_invalid")
    if not re.search(r"[\u3040-\u30ff\u3400-\u9fff]", "".join(translated.values())):
        raise ValueError("research_translation_language_invalid")
    review = cast("PrimaryReviewV1", artifacts["primary_reviewer"])
    review_status = (
        "一次レビュー: 通過。ただし正式な最終評価は未完了です。"
        if review.review_passed
        else ("一次レビュー: 未通過。以下の結論は未採用で、指摘への対応が必要です。")
    )
    lines = [
        "# 個別株 目的別投資ストーリー",
        "",
        f"銘柄: `{task.security.security_code}` / `{task.security.mic}`",
        "",
        "状態: 暫定。最終鮮度確認・独立探索の収束・指摘処理は未完了です。",
        "",
        review_status,
        "",
        f"実行方式: `{mode}`。replayは固定応答による検証です。",
        "",
        "中期: 12か月以上24か月未満。長期: 24〜60か月。最終的な投資判断と売買は人間が行います。",
        "",
    ]
    conclusions = cast("InvestmentConclusionsV1", artifacts["investment_conclusions"])
    labels = {
        "candidate": "購入候補",
        "wait": "条件付き候補・待機",
        "reject": "見送り",
        "insufficient_information": "判断保留",
    }
    purposes = {"capital_gain": "キャピタルゲイン", "income_gain": "インカムゲイン", None: "未確定"}
    lines.extend(["## 現在価格での期間別結論", ""])
    for index, item in enumerate(conclusions.conclusions):
        lines.extend(
            [
                f"### {'中期' if item.horizon == 'medium_term' else '長期'}: {labels[item.decision]}",
                "",
                f"主目的: {purposes[item.primary_objective]} / 副目的: {purposes[item.secondary_objective]}",
                "",
            ]
        )
        prefix = f"investment_conclusions/conclusions/{index}"
        for field, label in (
            ("story", "想定ストーリー"),
            ("rationale", "判断理由"),
            ("capital_allocation_consistency", "成長投資と配当の整合性"),
        ):
            lines.extend([f"**{label}**: {_display(translated[prefix + '/' + field])}", ""])
        lines.extend(["**判断を変更する条件**", ""])
        for condition_index in range(len(item.change_conditions)):
            lines.append("- " + _display(translated[f"{prefix}/change_conditions/{condition_index}"]))
        lines.append("")
    headings = {
        "investment_conclusions": "投資ストーリー・判断理由・判断変更条件",
        "codex_capital": "Codex キャピタルゲイン分析",
        "codex_income": "Codex インカムゲイン分析",
        "claude_capital": "Claude キャピタルゲイン分析",
        "claude_income": "Claude インカムゲイン分析",
        "codex_worker": "Codex系の整理",
        "claude_worker": "Claude系の整理",
        "orchestrator": "モデル間の統合・不一致",
        "primary_reviewer": "一次レビュー・未解決指摘",
    }
    for stage, heading in headings.items():
        lines.extend([f"## {heading}", ""])
        for key, original in originals.items():
            if key.startswith(stage + "/"):
                lines.extend([f"- `{key}`: {_display(translated[key])}", f"  - 英語原文: {_display(original)}"])
        body = artifacts[stage].model_dump_json(indent=2).replace("`", "\\u0060").replace("<", "\\u003c")
        lines.extend(["", "```json", body, "```", ""])
    lines.extend(
        [
            "## 条件付き収益計算",
            "",
            "税引前・手数料控除前・配当再投資なしの累計収益です。年率ではありません。",
            "数値は観測価格と将来仮定に基づきます。入力不足は算出不能として残します。",
            "",
            "```json",
            json.dumps(calculations, ensure_ascii=False, indent=2),
            "```",
            "",
        ]
    )
    return "\n".join(lines).encode()


def _execute(
    inputs: Mapping[str, bytes],
    sources: Mapping[str, bytes],
    invoke: ResearchInvoker,
    mode: Literal["live", "replay"],
    stamps: Mapping[str, str] | None = None,
) -> dict[str, bytes]:
    validate_input(inputs)
    definition = json.loads(sources["instruction-set.json"])
    if definition != {
        "instruction_set_version": 1,
        "files": {name: sha256(sources[name]).hexdigest() for name in INSTRUCTION_NAMES},
    }:
        raise ValueError("objective_instruction_hash_invalid")
    task = DetailedAnalysisTaskV1.model_validate_json(inputs["task.json"])
    evidence = EvidenceSetV1.model_validate_json(inputs["evidence-set.json"])
    policy = DetailedAnalysisPolicyV1.model_validate(yaml.safe_load(inputs["policy.yaml"]))
    excerpts = json.loads(inputs["common-input.json"])
    price = observed_price(excerpts)
    files = {
        **{f"input/{key}": value for key, value in inputs.items()},
        **{f"instructions/{key}": value for key, value in sources.items()},
    }
    evidence_ref = reference("evidence_set", evidence.artifact_id, evidence.schema_id, inputs["evidence-set.json"])
    policy_ref = reference("detailed_analysis_policy", policy.artifact_id, policy.schema_id, inputs["policy.yaml"])
    ids = {item.evidence_id for item in evidence.records if item.layer == "raw"}
    common: dict[str, JsonValue] = {
        "task": task.model_dump(mode="json"),
        "policy": policy.model_dump(mode="json"),
        "evidence_set": evidence.model_dump(mode="json"),
        "excerpts": excerpts,
        "observed_price": price,
    }
    artifacts: dict[str, StrictContractModel] = {}
    specialists: dict[str, SpecialistAnalysisV1] = {}
    claim_ids: set[str] = set()
    calculations: dict[str, JsonValue] = {}
    previous_stamp = datetime.fromisoformat(evidence.evidence_frozen_at)
    for stage, model in zip(STAGES, _MODELS, strict=True):
        data = dict(common)
        role = "aggregate.md"
        if stage in SPECIALISTS:
            objective = "capital_gain" if stage.endswith("capital") else "income_gain"
            data.update(objective=objective, claim_prefix=stage + "-")
            role = "capital.md" if objective == "capital_gain" else "income.md"
        elif stage in {"codex_worker", "claude_worker"}:
            data["specialists"] = {
                key: value.model_dump(mode="json")
                for key, value in specialists.items()
                if key.startswith(_provider(stage) + "_")
            }
            data["claim_prefix"] = stage + "-"
        else:
            data["specialists"] = {key: value.model_dump(mode="json") for key, value in specialists.items()}
            data["workers"] = {key: artifacts[key].model_dump(mode="json") for key in ("codex_worker", "claude_worker")}
            role = "main.md" if stage == "orchestrator" else "review.md"
        if stage == "primary_reviewer":
            for key in ("orchestrator", "investment_conclusions"):
                data[key] = artifacts[key].model_dump(mode="json")
            data["synthesis_reference"] = reference(
                "synthesis_result",
                "research-synthesis",
                "detailed-analysis.synthesis-result",
                files["orchestrator.json"],
            ).model_dump(mode="json")
            data["conclusion_reference"] = reference(
                "investment_conclusions",
                "investment-conclusions",
                "detailed-analysis.investment-conclusions",
                files["investment_conclusions.json"],
            ).model_dump(mode="json")
            data["policy_reference"] = policy_ref.model_dump(mode="json")
            data["calculated_returns"] = calculations
        instruction = sources["common.md"].decode() + "\n\n" + sources[role].decode()
        if stage == "translation":
            data = dict(report_texts(artifacts))
            instruction = _TRANSLATION_INSTRUCTIONS
        request = ResearchRequest(stage, _provider(stage), instruction, data, model)
        files[f"requests/{stage}.json"] = (
            json.dumps(
                {
                    "provider": request.provider,
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
        files[f"responses/{stage}.json"] = raw
        content = model.model_validate_json(raw)
        if stage == "translation":
            translation = ReportTranslation.model_validate_json(raw)
            files["report.md"] = _report(task, artifacts, translation, calculations, mode)
            continue
        stamp = (
            stamps[stage]
            if stamps is not None
            else evidence.evidence_frozen_at
            if mode == "replay"
            else datetime.now(UTC).isoformat()
        )
        moment = datetime.fromisoformat(stamp)
        if moment < previous_stamp:
            raise ValueError("objective_artifact_time_invalid")
        previous_stamp = moment
        payload = content.model_dump(mode="json")
        artifact: StrictContractModel
        if stage in SPECIALISTS:
            specialist = SpecialistAnalysisV1.model_validate_json(
                json.dumps(
                    {
                        **payload,
                        "schema_id": "detailed-analysis.specialist-analysis",
                        "schema_version": 1,
                        "task_id": task.task_id,
                        "artifact_id": stage,
                        "created_at": stamp,
                        "provider": _provider(stage),
                        "objective": data["objective"],
                        "evidence_set_reference": evidence_ref.model_dump(mode="json"),
                    }
                )
            )
            for horizon in specialist.horizons:
                for claim in horizon.claims:
                    if not claim.claim_id.startswith(stage + "-") or claim.claim_id in claim_ids:
                        raise ValueError("objective_claim_identity_invalid")
                    if not set((*claim.evidence_ids, *claim.counterevidence_ids)) <= ids:
                        raise ValueError("research_reference_invalid")
                    claim_ids.add(claim.claim_id)
                for scenario in horizon.scenarios:
                    if not set(scenario.evidence_ids) <= ids:
                        raise ValueError("research_reference_invalid")
                    total_return(scenario, price)
            specialists[stage] = specialist
            artifact = specialist
        elif stage.endswith("worker"):
            artifact = WorkerAnalysisV1.model_validate_json(
                json.dumps(
                    {
                        **payload,
                        "schema_id": "detailed-analysis.worker-analysis",
                        "schema_version": 1,
                        "task_id": task.task_id,
                        "artifact_id": f"{stage}-analysis",
                        "agent_run_id": f"{stage}-run",
                        "worker_role": stage,
                        "created_at": stamp,
                        "security": task.security.model_dump(mode="json"),
                        "evidence_set_reference": evidence_ref.model_dump(mode="json"),
                        "evidence_set_version": 1,
                        "evidence_frozen_at": evidence.evidence_frozen_at,
                        "freshness_checked_at": evidence.freshness_checked_at,
                        "evaluation_policy_reference": policy_ref.model_dump(mode="json"),
                    }
                )
            )
        elif stage == "orchestrator":
            conclusions = ConclusionBody.model_validate_json(json.dumps({"conclusions": payload.pop("conclusions")}))
            artifact = SynthesisResultV1.model_validate_json(
                json.dumps(
                    {
                        **payload,
                        "schema_id": "detailed-analysis.synthesis-result",
                        "schema_version": 1,
                        "task_id": task.task_id,
                        "artifact_id": "research-synthesis",
                        "created_at": stamp,
                        "worker_inputs": [
                            {
                                "worker_role": key,
                                "artifact_reference": reference(
                                    "worker_analysis",
                                    f"{key}-analysis",
                                    "detailed-analysis.worker-analysis",
                                    files[f"{key}.json"],
                                ).model_dump(mode="json"),
                            }
                            for key in ("codex_worker", "claude_worker")
                        ],
                        "evidence_set_reference": evidence_ref.model_dump(mode="json"),
                        "evidence_set_version": 1,
                    }
                )
            )
            files["orchestrator.json"] = serialize(artifact)
            conclusion = InvestmentConclusionsV1.model_validate_json(
                json.dumps(
                    {
                        **conclusions.model_dump(mode="json"),
                        "schema_id": "detailed-analysis.investment-conclusions",
                        "schema_version": 1,
                        "task_id": task.task_id,
                        "artifact_id": "investment-conclusions",
                        "created_at": stamp,
                        "evidence_set_reference": evidence_ref.model_dump(mode="json"),
                        "specialist_references": [
                            reference(
                                "specialist_analysis",
                                key,
                                "detailed-analysis.specialist-analysis",
                                files[f"{key}.json"],
                            ).model_dump(mode="json")
                            for key in SPECIALISTS
                        ],
                        "synthesis_reference": reference(
                            "synthesis_result",
                            "research-synthesis",
                            "detailed-analysis.synthesis-result",
                            files["orchestrator.json"],
                        ).model_dump(mode="json"),
                    }
                )
            )
            for index, decision in enumerate(conclusion.conclusions):
                permitted = {
                    claim.claim_id for specialist in specialists.values() for claim in specialist.horizons[index].claims
                }
                selected = set(decision.source_claim_ids)
                if not selected <= permitted or any(
                    not any(value.startswith(key + "-") for value in selected) for key in SPECIALISTS
                ):
                    raise ValueError("objective_conclusion_claims_invalid")
                unavailable = {
                    specialist.objective
                    for specialist in specialists.values()
                    if specialist.horizons[index].evaluability == "not_evaluable"
                }
                if unavailable and not decision.specialist_limitations:
                    raise ValueError("objective_limitations_required")
                synthesized = cast("SynthesisResultV1", artifact).horizon_results[index]
                if decision.decision == "candidate" and synthesized.evaluability == "not_evaluable":
                    raise ValueError("objective_candidate_conflicts_with_synthesis")
                if decision.primary_objective in unavailable:
                    raise ValueError("objective_primary_not_evaluable")
                if (price is None or len(unavailable) == 2) and decision.decision != "insufficient_information":
                    raise ValueError("objective_decision_requires_information")
                for scenario in decision.scenarios:
                    if not set(scenario.evidence_ids) <= ids:
                        raise ValueError("research_reference_invalid")
                calculations[decision.horizon] = [total_return(item, price) for item in decision.scenarios]
            artifacts["investment_conclusions"] = conclusion
            files["investment_conclusions.json"] = serialize(conclusion)
        else:
            artifact = PrimaryReviewV1.model_validate_json(
                json.dumps(
                    {
                        **payload,
                        "schema_id": "detailed-analysis.primary-review",
                        "schema_version": 1,
                        "task_id": task.task_id,
                        "artifact_id": "research-primary-review",
                        "reviewer_agent_run_id": "primary_reviewer-run",
                        "review_round": 1,
                        "created_at": stamp,
                        "reviewed_synthesis_reference": data["synthesis_reference"],
                        "previous_review_reference": None,
                        "reviewed_response_references": [],
                        "reviewer_rechecks": [],
                        "evidence_set_version": 1,
                    }
                )
            )
            all_ids = claim_ids | {
                claim.claim_id
                for key in ("codex_worker", "claude_worker")
                for horizon in cast("WorkerAnalysisV1", artifacts[key]).horizon_analyses
                for claim in horizon.claims
            }
            for finding in cast("PrimaryReviewV1", artifact).findings:
                targets = {
                    "orchestrator": data["synthesis_reference"],
                    "investment_conclusions": data["conclusion_reference"],
                }
                matches = [
                    key
                    for key, ref in targets.items()
                    if ref == finding.target_artifact_reference.model_dump(mode="json")
                ]
                if not matches or finding.lifecycle == "resolved":
                    raise ValueError("objective_review_target_invalid")
                _pointer(artifacts[matches[0]].model_dump(mode="json"), finding.json_pointer)
                if not set(finding.target_claim_ids) <= all_ids or not set(finding.evidence_ids) <= ids:
                    raise ValueError("research_reference_invalid")
                if any(ref != policy_ref for ref in finding.policy_references):
                    raise ValueError("research_finding_policy_invalid")
        artifacts[stage] = artifact
        files[f"{stage}.json"] = serialize(artifact)
        if stage in {"codex_worker", "claude_worker", "orchestrator"}:
            validate_artifacts(task, evidence, policy, files, complete=False)
    files["calculated-returns.json"] = (json.dumps(calculations, indent=2) + "\n").encode()
    review = cast("PrimaryReviewV1", artifacts["primary_reviewer"])
    files["manifest.json"] = (
        json.dumps(
            {
                "kind": "provisional-research-workflow",
                "format_version": 2,
                "mode": mode,
                "analysis_ready": False,
                "analysis_completed": False,
                "review_passed": review.review_passed,
                "unresolved_finding_ids": [item.finding_id for item in review.findings],
                "pending_steps": ["final_freshness", "independent_search_coverage", "finding_processing"],
                "files": {name: sha256(body).hexdigest() for name, body in sorted(files.items())},
            },
            indent=2,
        )
        + "\n"
    ).encode()
    return files


def run_workflow(
    inputs: Mapping[str, bytes],
    invoke: ResearchInvoker,
    instructions: Path,
    *,
    mode: Literal["live", "replay"],
) -> dict[str, bytes]:
    """Run four isolated specialists and two family aggregators before main and review."""
    sources = {name: (instructions / name).read_bytes() for name in (*INSTRUCTION_NAMES, "instruction-set.json")}
    files = _execute(inputs, sources, invoke, mode)
    validate_bundle(files)
    return files


def validate_bundle(files: Mapping[str, bytes]) -> None:
    """Reconstruct all requests, bindings, calculations and display from retained bytes."""
    manifest = json.loads(files["manifest.json"])
    if manifest.get("format_version") != 2 or manifest["mode"] not in {"live", "replay"}:
        raise ValueError("objective_bundle_version_invalid")
    inputs = {key.removeprefix("input/"): value for key, value in files.items() if key.startswith("input/")}
    sources = {name: files[f"instructions/{name}"] for name in (*INSTRUCTION_NAMES, "instruction-set.json")}
    stamps = {stage: json.loads(files[f"{stage}.json"])["created_at"] for stage in STAGES[:-1]}

    def replay(request: ResearchRequest) -> bytes:
        return files[f"responses/{request.stage}.json"]

    expected = _execute(inputs, sources, replay, manifest["mode"], stamps)
    if dict(files) != expected:
        raise ValueError("objective_bundle_binding_invalid")
