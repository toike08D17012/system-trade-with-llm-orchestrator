"""One-time campaign scope, persistent budget and failure boundaries."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.task import DetailedAnalysisTaskV1
from stock_research_llm_orchestrator.preparation.edinet_campaign import CAMPAIGN_ID, TARGETS
from stock_research_llm_orchestrator.preparation.edinet_evidence import acquire_prior_annual_campaign
from stock_research_llm_orchestrator.preparation.financial_disclosure import validate_financial
from stock_research_llm_orchestrator.preparation.fx_evidence import read_bundle

from .test_financial_disclosure import inputs as financial_inputs  # noqa: F401


@pytest.mark.parametrize(
    "case", ["success", "timeout", "wrong_entity", "duplicate", "amendment", "amendment_parent", "bad_zip"]
)
def test_campaign_budget_and_scope(tmp_path: Path, request: pytest.FixtureRequest, case: str) -> None:
    """A fresh task or output path cannot restart the campaign's physical slots."""
    source = request.getfixturevalue("financial_inputs")
    task = DetailedAnalysisTaskV1.model_validate_json((source / "task.json").read_bytes())
    original = json.loads((source / "raw/list/body.bin").read_bytes())
    archive = (source / "raw/document/body.bin").read_bytes()
    credential = tmp_path / "key"
    credential.write_text("synthetic-campaign-key")
    credential.chmod(0o600)
    current = [datetime(2026, 9, 27, tzinfo=UTC)]
    sends = []

    def sleep(seconds: float) -> None:
        current[0] += timedelta(seconds=seconds)

    def handler(req: httpx.Request) -> httpx.Response:
        sends.append(current[0])
        if case == "timeout":
            raise httpx.ReadTimeout("synthetic")
        if req.url.path.endswith("documents.json"):
            day = req.url.params["date"]
            target = next(value for value in TARGETS.values() if value[0] == day)
            listing = json.loads(json.dumps(original))
            listing["metadata"]["parameter"]["date"] = day
            item = listing["results"][0]
            item.update(
                edinetCode="E02144",
                periodStart=target[1],
                periodEnd=target[2],
                legalStatus="1",
                disclosureStatus="0",
                submitDateTime=day + " 10:00",
            )
            listing["results"] = [item]
            if case == "wrong_entity":
                item["edinetCode"] = "E99999"
            if case in {"duplicate", "amendment", "amendment_parent"}:
                other = {**item, "docID": "OTHER", "seqNumber": 2}
                if case in {"amendment", "amendment_parent"}:
                    other["docTypeCode"] = "130"
                if case == "amendment_parent":
                    other.update(periodStart=None, periodEnd=None, parentDocID=item["docID"])
                listing["results"].append(other)
            listing["metadata"]["resultset"]["count"] = len(listing["results"])
            return httpx.Response(200, headers={"Content-Type": "application/json"}, json=listing)
        return httpx.Response(
            200, headers={"Content-Type": "application/octet-stream"}, content=b"bad" if case == "bad_zip" else archive
        )

    def run(selected_task: DetailedAnalysisTaskV1 = task, destination: str = "runs") -> tuple[Path, ...]:
        return acquire_prior_annual_campaign(
            task=selected_task,
            config=Path("config"),
            runtime=tmp_path / "runtime",
            runs=tmp_path / destination,
            credential=credential,
            allow_network=True,
            allow_credential=True,
            clock=lambda: current[0],
            sleep=sleep,
            transport=httpx.MockTransport(handler),
        )

    if case == "success":
        outputs = run()
        assert len(outputs) == 2 and len(sends) == 4
        for output in outputs:
            validate_financial(read_bundle(output))
    else:
        with pytest.raises((ValueError, RuntimeError)):
            run()
        assert len(sends) == (2 if case == "bad_zip" else 1)
    assert all((b - a).total_seconds() >= 60 for a, b in zip(sends, sends[1:], strict=False))
    slots = list((tmp_path / "runtime" / CAMPAIGN_ID).glob("slot-*.json"))
    assert len(slots) == len(sends)
    previous = len(sends)
    with pytest.raises(FileExistsError):
        run(task.model_copy(update={"task_id": "another-task"}), "another-runs")
    assert len(sends) == previous
    for path in (tmp_path / "runtime" / CAMPAIGN_ID).glob("*.json"):
        assert b"synthetic-campaign-key" not in path.read_bytes()


def test_durable_slots_and_exclusive_marker(tmp_path: Path) -> None:
    """An interrupted or concurrent campaign cannot reclaim already spent slots."""
    from stock_research_llm_orchestrator.preparation.edinet_campaign import AnnualCampaign

    now = datetime(2026, 9, 27, tzinfo=UTC)
    campaign = AnnualCampaign(tmp_path, "a" * 64, "task", now)
    with pytest.raises(FileExistsError):
        AnnualCampaign(tmp_path, "a" * 64, "other-task", now)
    with pytest.raises(ValueError, match="slot_invalid"):
        campaign.before_send("2023", 0, now, "out-of-order")
    for target, sequence in (("2024", 0), ("2024", 1), ("2023", 0), ("2023", 1)):
        campaign.before_send(target, sequence, now, "attempt")
    with pytest.raises(ValueError, match="slot_invalid"):
        campaign.before_send("2024", 0, now, "fifth")
    assert len(list(campaign.path.glob("slot-*.json"))) == 4


@pytest.mark.parametrize("case", ["success", "body_tamper", "state_tamper", "already_spent", "bad_zip"])
def test_fixed_continuation_never_resends_list(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """Revalidate pinned failed metadata and use exactly the remaining three slots."""
    from hashlib import sha256

    from stock_research_llm_orchestrator.preparation import edinet_campaign as campaign_module
    from stock_research_llm_orchestrator.preparation import edinet_revalidation as revalidation
    from stock_research_llm_orchestrator.sources.edinet.document_list import EdinetDocumentListAdapter
    from stock_research_llm_orchestrator.sources.protocol import SourceParameter

    source = request.getfixturevalue("financial_inputs")
    task = DetailedAnalysisTaskV1.model_validate_json((source / "task.json").read_bytes())
    original = json.loads((source / "raw/list/body.bin").read_bytes())
    archive = (source / "raw/document/body.bin").read_bytes()
    current = [datetime(2026, 9, 27, tzinfo=UTC)]
    original["metadata"]["parameter"]["date"] = "2024-06-25"
    item = original["results"][0]
    item.update(
        docID="S100TR7I",
        edinetCode="E02144",
        periodStart="2023-04-01",
        periodEnd="2024-03-31",
        submitDateTime="2024-06-25 10:00",
        legalStatus="1",
        disclosureStatus="0",
    )
    original["results"] = [item]
    original["metadata"]["resultset"]["count"] = 1
    original["metadata"]["processDateTime"] = "2026-09-27 08:00"
    retained = tmp_path / "retained"
    retained.mkdir()
    raw = json.dumps(original).encode()
    (retained / "body.bin").write_bytes(raw)
    intent = EdinetDocumentListAdapter().build_intent(
        "document-list", (SourceParameter(name="date", value="2024-06-25"), SourceParameter(name="type", value="2"))
    )
    failure = json.dumps(
        dict(
            reason="source_validation_failed",
            key="list",
            sha256=sha256(raw).hexdigest(),
            received_at=current[0].isoformat(),
            source_intent=intent.model_dump(mode="json"),
        )
    ).encode()
    (retained / "failure.json").write_bytes(failure)
    monkeypatch.setattr(revalidation, "PRIOR_BODY_SHA256", sha256(raw).hexdigest())
    monkeypatch.setattr(revalidation, "PRIOR_FAILURE_SHA256", sha256(failure).hexdigest())
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    old = campaign_module.AnnualCampaign(runtime, revalidation.PRIOR_APPROVAL_SHA256, task.task_id, current[0])
    old.before_send("2024", 0, current[0], "old-attempt")
    original_state = {p.name: p.read_bytes() for p in old.path.iterdir()}
    monkeypatch.setattr(
        campaign_module, "PRIOR_STATE_HASHES", {name: sha256(body).hexdigest() for name, body in original_state.items()}
    )
    if case == "body_tamper":
        (retained / "body.bin").write_bytes(raw + b" ")
    if case == "state_tamper":
        (old.path / "slot-0.json").write_text("{}")
    if case == "already_spent":
        old.before_send("2024", 1, current[0], "already-sent")
    credential = tmp_path / "key"
    credential.write_text("synthetic-continuation-key")
    credential.chmod(0o600)
    sends = []

    def sleep(seconds: float) -> None:
        current[0] += timedelta(seconds=seconds)

    def handler(req: httpx.Request) -> httpx.Response:
        sends.append((current[0], req.url.path))
        if req.url.path.endswith("documents.json"):
            assert req.url.params["date"] == "2023-06-30"
            listing = json.loads(json.dumps(original))
            listing["metadata"]["parameter"]["date"] = "2023-06-30"
            listing["results"][0].update(
                docID="PRIOR2023", periodStart="2022-04-01", periodEnd="2023-03-31", submitDateTime="2023-06-30 10:00"
            )
            return httpx.Response(200, headers={"Content-Type": "application/json"}, json=listing)
        assert req.url.path.endswith("S100TR7I" if len(sends) == 1 else "PRIOR2023")
        return httpx.Response(
            200, headers={"Content-Type": "application/octet-stream"}, content=b"bad" if case == "bad_zip" else archive
        )

    def run() -> tuple[Path, ...]:
        return acquire_prior_annual_campaign(
            task=task,
            config=Path("config"),
            runtime=runtime,
            runs=tmp_path / "runs",
            credential=credential,
            continuation_list=retained,
            allow_network=True,
            allow_credential=True,
            clock=lambda: current[0],
            sleep=sleep,
            transport=httpx.MockTransport(handler),
        )

    if case == "success":
        outputs = run()
        assert len(sends) == 3
        assert json.loads((outputs[0] / "inputs.json").read_bytes())["version"] == 2
        for output in outputs:
            validate_financial(read_bundle(output))
        for name, body in original_state.items():
            assert (old.path / name).read_bytes() == body
        assert len(list(old.path.glob("slot-*.json"))) == 4
    else:
        with pytest.raises(ValueError):
            run()
        assert len(sends) == (1 if case == "bad_zip" else 0)
    previous = len(sends)
    with pytest.raises((ValueError, FileExistsError)):
        run()
    assert len(sends) == previous
    assert all((b[0] - a[0]).total_seconds() >= 60 for a, b in zip(sends, sends[1:], strict=False))
