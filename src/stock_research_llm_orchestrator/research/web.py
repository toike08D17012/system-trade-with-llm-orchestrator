"""Independent native-Web research with isolated specialists and offline-replayable provenance."""

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal, cast
from zoneinfo import ZoneInfo

import yaml
from pydantic import JsonValue

from stock_research_llm_orchestrator.agents.research_provider import WebTurnResult, native_web_calls
from stock_research_llm_orchestrator.contracts.base import ArtifactReferenceV1, StrictContractModel
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.agent_configuration import AgentConfigurationV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.evidence import EvidenceSetV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.policies import DetailedAnalysisPolicyV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.web_analysis import (
    WebAggregateBody,
    WebAnalysisV1,
    WebMainBody,
    WebReviewBody,
    WebSource,
    WebSpecialistBody,
)
from stock_research_llm_orchestrator.research.configured import (
    AGGREGATORS,
    INSTRUCTION_NAMES,
    SPECIALISTS,
    STAGES,
    _check_evidence,
    _claims,
    _report,
    _report_texts,
)
from stock_research_llm_orchestrator.research.input import reference, serialize, validate_input
from stock_research_llm_orchestrator.research.objectives import _pointer
from stock_research_llm_orchestrator.research.scenarios import observed_price, total_return
from stock_research_llm_orchestrator.research.workflow import (
    _TRANSLATION_INSTRUCTIONS,
    ReportTranslation,
    ResearchRequest,
    _display,
)


WebInvoker = Callable[[ResearchRequest], WebTurnResult]
MODELS: tuple[type[StrictContractModel], ...] = (
    WebSpecialistBody,
    WebSpecialistBody,
    WebSpecialistBody,
    WebSpecialistBody,
    WebAggregateBody,
    WebAggregateBody,
    WebMainBody,
    WebReviewBody,
    ReportTranslation,
)


def _check_web(value: JsonValue, sources: Mapping[str, WebSource]) -> None:
    """Reject invented references and search snippets presented as read original pages."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "web_source_ids":
                if not isinstance(child, list) or any(
                    not isinstance(i, str) or i not in sources or sources[i].retrieval_status != "page_read"
                    for i in child
                ):
                    raise ValueError("web_source_reference_invalid")
            else:
                _check_web(child, sources)
    elif isinstance(value, list):
        for child in value:
            _check_web(child, sources)


def _check_main(
    main: WebMainBody,
    artifacts: Mapping[str, WebAnalysisV1],
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
            specialist = cast("WebSpecialistBody", artifacts[role].payload)
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
        if price is None and conclusion.decision != "insufficient_information":
            raise ValueError("configured_decision_requires_information")
        if conclusion.decision == "candidate" and summary.evaluability == "not_evaluable":
            raise ValueError("configured_candidate_conflicts_with_synthesis")
        calculations[conclusion.horizon] = [
            total_return(scenario, price, source_references_available=bool(scenario.web_source_ids))
            for scenario in conclusion.scenarios
        ]
    return calculations


def _execute(
    inputs: Mapping[str, bytes],
    config: AgentConfigurationV1,
    sources: Mapping[str, bytes],
    invoke: WebInvoker,
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
    artifacts: dict[str, WebAnalysisV1] = {}
    references: dict[str, ArtifactReferenceV1] = {}
    ids = {record.evidence_id for record in evidence.records if record.layer == "raw"}
    claimed: set[str] = set()
    web_sources: dict[str, WebSource] = {}
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
        if role not in SPECIALISTS and role != "translation":
            source_roles = dependencies if role in AGGREGATORS else list(artifacts)
            data["previous_web_sources"] = {
                source.source_id: source.model_dump(mode="json")
                for key in source_roles
                for source in artifacts[key].payload.research.sources
            }
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
        turn = invoke(request)
        raw = turn.body
        files[f"provider-traces/{role}.jsonl"] = turn.trace
        files[f"responses/{role}.json"] = raw
        observed_calls = native_web_calls(turn.trace)
        if role in SPECIALISTS and observed_calls == 0:
            raise ValueError("independent_native_web_search_not_observed")
        body = model.model_validate_json(raw)
        executions[role] = {
            "agent_alias": alias,
            "requested": agent.model_dump(mode="json"),
            "reported_model": None,
            "reported_effort": None,
            "settings_status": "requested_not_independently_verified",
            "family_source": "configuration_declaration",
            "native_web_tool_events": observed_calls,
        }
        if role == "translation":
            security_label = f"{task['security']['security_code']} ({task['security']['mic']})"
            files["report.md"] = _report(
                artifacts, cast("ReportTranslation", body), config, calculations, mode, security_label
            )
            notice = (
                "Web出典・株価は各担当の調査報告です。原本との機械照合・最終鮮度確認は未実施で、"
                "収益率は記載した価格とシナリオを前提にした条件付き試算です。\n\n"
            )
            heading, remainder = files["report.md"].decode().split("\n", 1)
            files["report.md"] = (heading + "\n\n" + notice + remainder.lstrip("\n")).encode()
            main = cast("WebMainBody", artifacts["orchestrator"].payload)
            appendix = [
                "\n## 調査の出典\n",
                "各担当が独立してWeb調査を実施しました。Web引用と株価は担当の報告値で、原本との機械照合・最終鮮度確認は未実施です。\n",
            ]
            if main.web_observed_price is not None:
                quote = main.web_observed_price
                appendix.append(f"計算の基準株価: {quote.price} JPY（{quote.observed_on}、Web調査の報告値）。\n")
            unique = {source.url: source for source in web_sources.values() if source.retrieval_status == "page_read"}
            for source in list(unique.values())[:5]:
                appendix.append(f"- [{_display(source.title)}](<{source.url}>)")
            if len(unique) > 5:
                appendix.append(f"ほか{len(unique) - 5}件。全出典と検索記録は各分析JSONに保存しています。")
            files["report.md"] += ("\n".join(appendix) + "\n").encode()
            continue
        researched = cast("WebSpecialistBody | WebAggregateBody | WebMainBody | WebReviewBody", body)
        own_sources = {source.source_id: source for source in researched.research.sources}
        if any(not key.startswith(role + "-web-") for key in own_sources) or web_sources.keys() & own_sources.keys():
            raise ValueError("web_source_identity_invalid")
        permitted_web = dict(own_sources) if role in SPECIALISTS else {**web_sources, **own_sources}
        _check_web(body.model_dump(mode="json"), permitted_web)
        web_sources.update(own_sources)
        _check_evidence(body.model_dump(mode="json"), ids)
        claims = _claims(body)
        if any(not claim.startswith(role + "-") for claim in claims) or claimed & claims:
            raise ValueError("configured_claim_identity_invalid")
        claimed.update(claims)
        if isinstance(body, WebSpecialistBody):
            for horizon in body.horizons:
                for scenario in horizon.scenarios:
                    total_return(scenario, price, source_references_available=bool(scenario.web_source_ids))
        if isinstance(body, (WebAggregateBody, WebMainBody)) and body.entry_exit_context.price_as_of is not None:
            raise ValueError("configured_exact_price_time_unavailable")
        if isinstance(body, WebMainBody):
            if body.web_observed_price is not None:
                quote = body.web_observed_price
                if (
                    quote.source_id not in permitted_web
                    or permitted_web[quote.source_id].retrieval_status != "page_read"
                ):
                    raise ValueError("web_price_source_invalid")
                if (
                    quote.observed_on
                    > datetime.fromisoformat(evidence.evidence_frozen_at).astimezone(ZoneInfo("Asia/Tokyo")).date()
                ):
                    raise ValueError("web_price_future_observation")
                if quote.price not in quote.quote_excerpt.replace(",", ""):
                    raise ValueError("web_price_excerpt_mismatch")
                price = {
                    "price": quote.price,
                    "currency": quote.currency,
                    "on": quote.observed_on.isoformat(),
                    "per_share_basis": quote.per_share_basis,
                    "source_id": quote.source_id,
                    "provenance": "agent_reported_native_web_not_machine_verified",
                }
            calculations = _check_main(body, artifacts, price)
        if isinstance(body, WebReviewBody):
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
        artifact = WebAnalysisV1.model_validate_json(
            json.dumps(
                {
                    "schema_id": "detailed-analysis.web-analysis",
                    "schema_version": 1,
                    "task_id": task["task_id"],
                    "artifact_id": role,
                    "role": role,
                    "created_at": stamp,
                    "agent_alias": alias,
                    "agent": agent.model_dump(mode="json"),
                    "configuration_reference": config_ref.model_dump(mode="json"),
                    "initial_evidence_set_reference": evidence_ref.model_dump(mode="json"),
                    "input_artifact_references": [references[key].model_dump(mode="json") for key in dependencies],
                    "payload": body.model_dump(mode="json"),
                }
            )
        )
        artifacts[role] = artifact
        files[f"{role}.json"] = serialize(artifact)
        references[role] = reference("configured_analysis", role, artifact.schema_id, files[f"{role}.json"])
    files["calculated-returns.json"] = (json.dumps(calculations, indent=2) + "\n").encode()
    review = cast("WebReviewBody", artifacts["primary_reviewer"].payload)
    files["manifest.json"] = (
        json.dumps(
            {
                "kind": "provisional-research-workflow",
                "format_version": 4,
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
    invoke: WebInvoker,
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
    if manifest.get("format_version") != 4 or manifest["mode"] not in {"live", "replay"}:
        raise ValueError("configured_bundle_version_invalid")
    config = AgentConfigurationV1.model_validate_json(files["agent-configuration.json"])
    inputs = {key.removeprefix("input/"): value for key, value in files.items() if key.startswith("input/")}
    sources = {name: files[f"instructions/{name}"] for name in (*INSTRUCTION_NAMES, "instruction-set.json")}
    stamps = {role: json.loads(files[f"{role}.json"])["created_at"] for role in STAGES[:-1]}

    def replay(request: ResearchRequest) -> WebTurnResult:
        return WebTurnResult(files[f"responses/{request.stage}.json"], files[f"provider-traces/{request.stage}.jsonl"])

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

    def replay(request: ResearchRequest) -> WebTurnResult:
        return WebTurnResult(
            original[f"responses/{request.stage}.json"], original[f"provider-traces/{request.stage}.jsonl"]
        )

    files = _execute(inputs, config, sources, replay, "replay", stamps)
    validate_bundle(files)
    return files
