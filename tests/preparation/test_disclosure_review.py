"""Selected assertions must bind to canonical acquisition and raw extraction."""

import json
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path

import pytest

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.task import DetailedAnalysisTaskV1
from stock_research_llm_orchestrator.preparation import disclosure_review as module
from stock_research_llm_orchestrator.preparation.interim_ir_runtime import IrRuntimeManifest, IrRuntimeReceipt
from stock_research_llm_orchestrator.requests.production import CommittedRawReference
from stock_research_llm_orchestrator.requests.storage import initialize_runtime_storage


def fixture_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, bytes], dict[str, bytes], Callable[[], dict[str, object]]]:
    """Isolate review binding; canonical DB verification has real runtime tests."""
    task = DetailedAnalysisTaskV1.model_validate_json(
        (
            Path(__file__).parents[1]
            / "fixtures/contracts/detailed-analysis/v1/detailed-analysis-task/valid/human-selected.json"
        ).read_bytes()
    )
    body = b"synthetic-private-pdf-body"
    raw = tmp_path / "raw" / task.task_id / "acquisitions/logical/physical"
    raw.mkdir(parents=True)
    (raw / "body.bin").write_bytes(body)
    ref = CommittedRawReference(
        publication_id="publication",
        task_id=task.task_id,
        logical_request_id="logical",
        physical_attempt_id="physical",
        relative_path="acquisitions/logical/physical",
        content_sha256=sha256(body).hexdigest(),
        byte_count=len(body),
        raw_schema_id="toyota-ir-pdf-raw",
        raw_schema_version=1,
        publication_generation=1,
    )
    manifest = IrRuntimeManifest(
        kind="toyota-disclosure-runtime-candidates",
        task_id=task.task_id,
        receipts=(IrRuntimeReceipt(key="document", received_at="2026-09-28T15:00:00Z", raw_reference=ref),),
        hashes={},
    )
    monkeypatch.setattr(module, "verify_runtime", lambda *args: manifest)

    def extract(candidate: bytes) -> tuple[str, ...]:
        assert candidate == body
        return ("private source text",)

    monkeypatch.setattr(module, "extract_pages", extract)
    acquisition = {
        "manifest.json": manifest.model_dump_json().encode(),
        "policy.json": b'{"documents":[{"published_on":"2026-09-03"}]}',
    }
    assertion = {
        "task_id": task.task_id,
        "security_code": "7203",
        "reviewed_at": "2026-09-28T16:00:00Z",
        "acquisition_manifest_sha256": sha256(acquisition["manifest.json"]).hexdigest(),
        "entries": [
            {
                "key": "document",
                "content_sha256": ref.content_sha256,
                "published_on": "2026-09-03",
                "extraction_sha256": sha256(
                    json.dumps(("private source text",), ensure_ascii=False, indent=2).encode()
                ).hexdigest(),
                "selected_pages": [1],
                "facts": ["selected_fact"],
            }
        ],
        "limitations": ["selected_sections_only"],
        "unresolved_external_references": ["unacquired-original"],
        "financial_values_adopted": False,
    }
    review = {"review.json": json.dumps(assertion).encode()}
    repin(review, monkeypatch)
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    repository = initialize_runtime_storage(runtime)

    def bind() -> dict[str, object]:
        return module.bind_disclosure_review(review, acquisition, repository, tmp_path / "raw", task, runtime)

    return review, acquisition, bind


def repin(review: dict[str, bytes], monkeypatch: pytest.MonkeyPatch) -> None:
    """Allow synthetic approved assertions to exercise deeper structural failures."""
    monkeypatch.setattr(module, "REVIEW_SHA256", sha256(review["review.json"]).hexdigest())


def test_bound_review_retains_limits_without_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No full acceptance, semantic rerun or raw financial payload is exported."""
    review, acquisition, bind = fixture_review(tmp_path, monkeypatch)
    result = bind()
    assert result["selected_document_count"] == 1
    assert result["semantic_review_reperformed"] is False and result["analysis_ready"] is False
    assert result["review"] == json.loads(review["review.json"])
    assert "private source text" not in json.dumps(result)


@pytest.mark.parametrize("mode", ["hash", "inventory", "manifest", "raw", "kind", "canonical-db"])
def test_untrusted_provenance_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    """Exported review metadata cannot bypass canonical verification or raw bytes."""
    review, acquisition, bind = fixture_review(tmp_path, monkeypatch)
    if mode == "hash":
        review["review.json"] += b" "
    elif mode == "inventory":
        review["other.json"] = b"{}"
    elif mode == "manifest":
        acquisition["manifest.json"] += b" "
    elif mode == "raw":
        next((tmp_path / "raw").rglob("body.bin")).write_bytes(b"tampered")
    elif mode == "kind":
        manifest = IrRuntimeManifest.model_validate_json(acquisition["manifest.json"])
        monkeypatch.setattr(
            module,
            "verify_runtime",
            lambda *args: manifest.model_copy(update={"kind": "toyota-ir-runtime-acquisition"}),
        )
    else:

        def reject(*args: object) -> None:
            raise ValueError("canonical_db_rejected")

        monkeypatch.setattr(module, "verify_runtime", reject)
    with pytest.raises(ValueError):
        bind()


@pytest.mark.parametrize(
    "field,value",
    [
        ("task_id", "other"),
        ("key", "other"),
        ("content_sha256", "0" * 64),
        ("published_on", "2026-09-04"),
        ("reviewed_at", "2026-09-28T14:00:00Z"),
        ("extraction_sha256", "0" * 64),
        ("selected_pages", [2]),
        ("selected_pages", [True]),
    ],
)
def test_assertion_binding_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: object) -> None:
    """Even an approved review must match task, document, extraction and chronology."""
    review, acquisition, bind = fixture_review(tmp_path, monkeypatch)
    assertion = json.loads(review["review.json"])
    target = assertion if field in {"task_id", "reviewed_at"} else assertion["entries"][0]
    target[field] = value
    review["review.json"] = json.dumps(assertion).encode()
    repin(review, monkeypatch)
    with pytest.raises(ValueError):
        bind()
