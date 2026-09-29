"""Private assembly preserves identities and rejects conflicting graph edges."""

import json
from hashlib import sha256

import pytest

from stock_research_llm_orchestrator.preparation.evidence_assembly import DRAFT_CATEGORIES, build_evidence_assembly


def encoded(value: object) -> bytes:
    """Use the canonical draft encoding."""
    return json.dumps(value, sort_keys=True, indent=2).encode()


def fixture() -> dict[str, bytes]:
    """Share one source/raw identity across all four replayed categories."""
    reference = {
        "artifact_type": "source_approval",
        "artifact_id": "approval",
        "schema_id": "detailed-analysis.source-approval",
        "schema_version": 1,
        "sha256": "a" * 64,
    }
    source = {
        "evidence_id": "source",
        "layer": "source_metadata",
        "source_id": "provider",
        "source_approval_reference": reference,
        "provider": "Provider",
        "service": "Service",
        "dataset": "Dataset",
        "published_at": None,
        "first_available_at": None,
        "updated_at": None,
        "retrieved_at": "2026-09-28T12:00:00Z",
        "applicable_period": {"start_date": "2026-09-28", "end_date": "2026-09-28"},
        "timezone": "UTC",
        "reference": "synthetic",
        "content_sha256": "b" * 64,
        "data_version": "original",
    }
    raw = {
        "evidence_id": "raw",
        "layer": "raw",
        "source_metadata_evidence_id": "source",
        "payload_reference": {
            **reference,
            "artifact_type": "raw_evidence_payload",
            "artifact_id": "original",
            "sha256": "b" * 64,
        },
        "content_sha256": "b" * 64,
        "data_version": "original",
    }
    result = {}
    for category in DRAFT_CATEGORIES:
        payload = {"value": "selected assertion", "input_evidence_ids": ["raw"], "provenance": category}
        normalized = {
            "evidence_id": category,
            "layer": "normalized",
            "input_evidence_ids": ["raw"],
            "value": "selected assertion",
            "missing_reason": None,
            "unit": "qualitative_review",
            "content_sha256": sha256(encoded(payload)).hexdigest(),
            "data_version": "original",
            "normalization_logic_version": 1,
        }
        result[category] = encoded(
            {
                "kind": f"internal-{category}-p1-evidence-draft",
                "version": 1,
                "task_id": "task",
                "status": "pending",
                "analysis_ready": False,
                "records": [source, raw, normalized],
                "payloads": {"source": {"metadata": source}, category: payload},
                "artifact_locators": {"original": [{"dependency": category, "path": "raw.bin", "sha256": "b" * 64}]},
                "limitations": [category + "_limited"],
                **(
                    {"context": {"coverage_complete": False}, "context_sha256": "c" * 64}
                    if category == "narrative"
                    else {}
                ),
            }
        )
    return result


def test_assembly_preserves_payloads_context_and_deduplicates() -> None:
    """Retain all original provenance and collapse only identical records."""
    drafts = fixture()
    body, metadata = build_evidence_assembly(drafts)
    assert build_evidence_assembly(dict(reversed(list(drafts.items())))) == (body, metadata)
    assembled = json.loads(body)
    assert len(assembled["records"]) == 6
    assert metadata == {
        "sha256": sha256(body).hexdigest(),
        "record_counts": {"source_metadata": 1, "raw": 1, "normalized": 4},
    }
    assert assembled["status"] == "pending" and assembled["analysis_ready"] is False
    assert "evidence_frozen_at" not in assembled and "freshness_assessments" not in assembled
    for category, original in drafts.items():
        draft = json.loads(original)
        provenance = assembled["draft_provenance"][category]
        assert provenance["draft_sha256"] == sha256(original).hexdigest()
        assert provenance["artifact_locators"] == draft["artifact_locators"]
        assert provenance["limitations"] == draft["limitations"]
        for key, payload in draft["payloads"].items():
            assert assembled["payloads"][key] == payload
    assert assembled["draft_provenance"]["narrative"]["context"] == {"coverage_complete": False}


@pytest.mark.parametrize(
    "change", ["task", "state", "record", "payload", "duplicate", "missing", "layer", "unknown_payload", "kind"]
)
def test_assembly_rejects_incompatible_drafts(change: str) -> None:
    """Reject incompatible states, identities and graph edges."""
    drafts = fixture()
    draft = json.loads(drafts["narrative"])
    if change == "task":
        draft["task_id"] = "other"
    elif change == "state":
        draft["analysis_ready"] = True
    elif change == "record":
        draft["records"][0]["reference"] = "changed"
    elif change == "payload":
        draft["payloads"]["source"] = {"changed": True}
    elif change == "duplicate":
        draft["records"].append(draft["records"][0])
    elif change == "missing":
        draft["records"][2]["input_evidence_ids"] = ["missing"]
    elif change == "layer":
        draft["records"][2]["input_evidence_ids"] = ["source"]
    elif change == "unknown_payload":
        draft["payloads"]["unknown"] = {}
    elif change == "kind":
        draft["kind"] = "other"
    drafts["narrative"] = encoded(draft)
    with pytest.raises(ValueError, match="evidence_assembly"):
        build_evidence_assembly(drafts)


def test_assembly_requires_all_four_categories() -> None:
    """Require the complete explicit draft set."""
    drafts = fixture()
    del drafts["annual"]
    with pytest.raises(ValueError, match="draft_set_invalid"):
        build_evidence_assembly(drafts)


@pytest.mark.parametrize("record_value", [True, False])
@pytest.mark.parametrize("boundary", ["record", "payload"])
def test_assembly_distinguishes_json_types(record_value: bool, boundary: str) -> None:
    """Do not collapse boolean and integer values that compare equal in Python."""
    drafts = fixture()
    annual = json.loads(drafts["annual"])
    narrative = json.loads(drafts["narrative"])
    if boundary == "payload":
        annual["payloads"]["source"]["flag"] = record_value
        narrative["payloads"]["source"]["flag"] = int(record_value)
    else:
        annual["records"][2]["value"] = record_value
        duplicate = {**annual["records"][2], "value": int(record_value)}
        narrative["records"].append(duplicate)
        narrative["payloads"]["annual"] = annual["payloads"]["annual"]
    drafts["annual"] = encoded(annual)
    drafts["narrative"] = encoded(narrative)
    with pytest.raises(ValueError, match=boundary + "_conflict"):
        build_evidence_assembly(drafts)
