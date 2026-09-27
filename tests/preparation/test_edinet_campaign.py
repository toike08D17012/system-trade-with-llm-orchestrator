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


@pytest.mark.parametrize("case", ["success", "timeout", "wrong_entity", "duplicate", "amendment", "bad_zip"])
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
            if case in {"duplicate", "amendment"}:
                other = {**item, "docID": "OTHER", "seqNumber": 2}
                if case == "amendment":
                    other["docTypeCode"] = "130"
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
