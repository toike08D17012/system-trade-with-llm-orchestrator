"""Synthetic EDINET acceptance through real coordinator and credential boundaries."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.task import DetailedAnalysisTaskV1
from stock_research_llm_orchestrator.preparation.edinet_evidence import acquire_edinet_acceptance
from stock_research_llm_orchestrator.preparation.financial_disclosure import validate_financial
from stock_research_llm_orchestrator.preparation.fx_evidence import read_bundle

from .test_financial_disclosure import inputs as financial_inputs  # noqa: F401


@pytest.mark.parametrize(
    "case",
    ["success", "no_optin", "permission", "no_match", "duplicate", "timeout", "401", "429", "redirect", "bad_zip"],
)
def test_acceptance_boundaries(tmp_path: Path, request: pytest.FixtureRequest, case: str) -> None:
    """Respect scope, gate timing, failure stops, and secret-free persistent artifacts."""
    source = request.getfixturevalue("financial_inputs")
    task = DetailedAnalysisTaskV1.model_validate_json((source / "task.json").read_bytes())
    listing = json.loads((source / "raw/list/body.bin").read_bytes())
    listing["metadata"]["parameter"]["date"] = "2026-06-10"
    for item in listing["results"]:
        item.update(csvFlag="1", legalStatus="1")
    if case == "no_match":
        listing["results"][0]["secCode"] = "99990"
    if case == "duplicate":
        duplicate = dict(listing["results"][0])
        duplicate.update(seqNumber=3, docID="SECOND")
        listing["results"].append(duplicate)
        listing["metadata"]["resultset"]["count"] = 3
    body = (source / "raw/document/body.bin").read_bytes()
    key = tmp_path / "key"
    canary = "synthetic-edinet-acceptance-key"
    key.write_text(canary)
    key.chmod(0o644 if case == "permission" else 0o600)
    current = [datetime(2026, 9, 27, tzinfo=UTC)]
    sends: list[datetime] = []

    def sleep(seconds: float) -> None:
        current[0] += timedelta(seconds=seconds)

    def handler(req: httpx.Request) -> httpx.Response:
        sends.append(current[0])
        assert req.url.params["Subscription-Key"] == canary
        if case == "timeout":
            raise httpx.ReadTimeout("synthetic timeout")
        if case in {"401", "429"}:
            return httpx.Response(int(case), headers={"Content-Type": "application/json"}, content=b"{}")
        if case == "redirect":
            return httpx.Response(302, headers={"Location": "https://example.invalid"})
        if req.url.path.endswith("documents.json"):
            assert req.url.params["date"] == "2026-06-10"
            return httpx.Response(200, headers={"Content-Type": "application/json"}, json=listing)
        assert req.url.path.endswith("/SYNTHETIC001")
        return httpx.Response(
            200, headers={"Content-Type": "application/octet-stream"}, content=b"invalid" if case == "bad_zip" else body
        )

    def run() -> Path:
        return acquire_edinet_acceptance(
            task=task,
            config=Path("config"),
            runtime=tmp_path / "runtime",
            runs=tmp_path / "runs",
            credential=key,
            allow_network=case != "no_optin",
            allow_credential=True,
            clock=lambda: current[0],
            sleep=sleep,
            transport=httpx.MockTransport(handler),
        )

    if case == "success":
        output = run()
        validate_financial(read_bundle(output))
        assert len(sends) == 2
        assert (sends[1] - sends[0]).total_seconds() >= 60
    else:
        with pytest.raises((ValueError, RuntimeError)):
            run()
        assert len(sends) == (0 if case in {"no_optin", "permission"} else 2 if case == "bad_zip" else 1)
    for root in (tmp_path / "runs", tmp_path / "runtime"):
        for artifact in root.rglob("*"):
            if artifact.is_file():
                assert canary.encode() not in artifact.read_bytes()
