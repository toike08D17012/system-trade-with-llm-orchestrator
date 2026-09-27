"""Pinned selection and durable two-send budget for a retained amendment pair."""

import json
from collections.abc import Mapping
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from stock_research_llm_orchestrator.preparation.edinet_campaign import _durable_write
from stock_research_llm_orchestrator.preparation.edinet_revalidation import RetainedListFailure
from stock_research_llm_orchestrator.preparation.market_revalidation import _safe_path, _timestamp


PAIR_IDS = ("S100QZHY", "S100RAR0")
PAIR_BODY_SHA256 = "94fef761bca1f9b3c222156c526918ee7f41e3ffa097dfea73545927534c3519"
PAIR_CAMPAIGN_ID = "edinet-2023-pair-20260927"


def validate_pair_list(files: Mapping[str, bytes], now: datetime) -> None:
    """Validate only the pinned pair, without accepting the ambiguous whole list."""
    failure = RetainedListFailure.model_validate_json(files["failure.json"])
    if (
        sha256(files["body.bin"]).hexdigest() != PAIR_BODY_SHA256
        or failure.sha256 != PAIR_BODY_SHA256
        or failure.reason != "source_validation_failed"
        or failure.key != "list"
        or _timestamp(failure.received_at) > now
        or {p.name: p.value for p in failure.source_intent.parameters} != {"date": "2023-06-30", "type": "2"}
    ):
        raise ValueError("edinet_pair_retained_binding_invalid")
    data = json.loads(files["body.bin"])
    if data["metadata"]["parameter"] != {"date": "2023-06-30", "type": "2"}:
        raise ValueError("edinet_pair_list_date_invalid")
    for doc_id, kind, parent in zip(PAIR_IDS, ("120", "130"), (None, PAIR_IDS[0]), strict=True):
        rows = [r for r in data["results"] if r.get("docID") == doc_id]
        if len(rows) != 1:
            raise ValueError("edinet_pair_target_not_unique")
        row = rows[0]
        expected = {
            "edinetCode": "E02144",
            "secCode": "72030",
            "docTypeCode": kind,
            "parentDocID": parent,
            "xbrlFlag": "1",
            "withdrawalStatus": "0",
            "disclosureStatus": "0",
        }
        if any(row.get(k) != v for k, v in expected.items()) or row.get("legalStatus") not in {"1", "2"}:
            raise ValueError("edinet_pair_target_invalid")
        if kind == "120" and (row.get("periodStart"), row.get("periodEnd")) != ("2022-04-01", "2023-03-31"):
            raise ValueError("edinet_pair_period_invalid")


class PairCampaign:
    """Keep a new bounded campaign linked to the spent prior campaign."""

    version = 5

    def __init__(self, runtime: Path, approval_hash: str, task_id: str, now: datetime) -> None:
        """Claim a one-time marker before attempting either document."""
        self.path = _safe_path(runtime) / PAIR_CAMPAIGN_ID
        self.path.mkdir(mode=0o700)
        self.sent = 0
        self.approval_hash = approval_hash
        _durable_write(
            self.path / "started.json",
            {
                "approval_sha256": approval_hash,
                "task_id": task_id,
                "started_at": now.isoformat(),
                "max_sends": 2,
                "document_ids": PAIR_IDS,
                "retained_sha256": PAIR_BODY_SHA256,
                "prior_campaign": "edinet-prior-annual-20260927",
            },
        )

    def before_send(self, target: str, sequence: int, now: datetime, attempt_id: str) -> None:
        """Consume a slot before the physical send; failed sends are not refunded."""
        if self.sent >= 2 or sequence != self.sent or target != PAIR_IDS[sequence]:
            raise ValueError("edinet_pair_slot_invalid")
        _durable_write(
            self.path / f"slot-{sequence}.json",
            {
                "document_id": target,
                "started_at": now.isoformat(),
                "physical_attempt_id": attempt_id,
                "approval_sha256": self.approval_hash,
            },
        )
        self.sent += 1


def validate_pair_bundle(files: Mapping[str, bytes]) -> None:
    """Verify the complete pair inventory and parse both immutable raw archives."""
    from stock_research_llm_orchestrator.preparation.financial_disclosure import FilingInput
    from stock_research_llm_orchestrator.sources.edinet.xbrl_document import EdinetXbrlDocumentAdapter
    from stock_research_llm_orchestrator.sources.protocol import BoundedSourceResponse, SourceParameter

    manifest = json.loads(files["manifest.json"])
    expected = {
        "kind": "edinet-pinned-amendment-pair",
        "analysis_ready": False,
        "document_ids": list(PAIR_IDS),
        "whole_list_accepted": False,
        "hashes": {name: sha256(body).hexdigest() for name, body in files.items() if name != "manifest.json"},
    }
    if manifest != expected:
        raise ValueError("edinet_pair_inventory_mismatch")
    acquisitions = tuple(FilingInput.model_validate_json(json.dumps(a)) for a in json.loads(files["acquisitions.json"]))
    if tuple(a.key for a in acquisitions) != PAIR_IDS:
        raise ValueError("edinet_pair_acquisition_scope_invalid")
    adapter = EdinetXbrlDocumentAdapter()
    for acquisition in acquisitions:
        doc_id = acquisition.key
        if (
            acquisition.source_intent
            != adapter.build_intent(
                "document-retrieval",
                (
                    SourceParameter(name="document_id", value=doc_id),
                    SourceParameter(name="type", value="1"),
                ),
            )
            or acquisition.acquisition_approval_sha256 != sha256(files["approval.yaml"]).hexdigest()
        ):
            raise ValueError("edinet_pair_acquisition_binding_invalid")
        body = files[f"raw/{doc_id}/body.bin"]
        if sha256(body).hexdigest() != acquisition.publication.content_sha256:
            raise ValueError("edinet_pair_archive_mismatch")
        adapter.parse(
            BoundedSourceResponse(
                physical_attempt_id=acquisition.publication.physical_attempt_id,
                body=body,
                sha256=sha256(body).hexdigest(),
                media_type="application/octet-stream",
                encoding="binary",
            )
        )
        validate_pair_list(
            {
                name.removeprefix("retained-list/"): value
                for name, value in files.items()
                if name.startswith("retained-list/")
            },
            _timestamp(acquisition.retrieved_at),
        )
