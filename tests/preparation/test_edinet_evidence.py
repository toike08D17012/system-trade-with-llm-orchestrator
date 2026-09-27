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
    [
        "success",
        "resume",
        "retained_tamper",
        "no_optin",
        "permission",
        "no_match",
        "legal_unknown",
        "duplicate",
        "timeout",
        "401",
        "429",
        "redirect",
        "bad_zip",
    ],
)
def test_acceptance_boundaries(tmp_path: Path, request: pytest.FixtureRequest, case: str) -> None:
    """Respect scope, gate timing, failure stops, and secret-free persistent artifacts."""
    source = request.getfixturevalue("financial_inputs")
    task = DetailedAnalysisTaskV1.model_validate_json((source / "task.json").read_bytes())
    listing = json.loads((source / "raw/list/body.bin").read_bytes())
    listing["metadata"]["parameter"]["date"] = "2026-06-10"
    for item in listing["results"]:
        item.update(csvFlag="1", legalStatus="1")
    if case == "legal_unknown":
        listing["results"][0]["legalStatus"] = "unknown"
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

    retained = None
    if case in {"resume", "retained_tamper"}:
        from hashlib import sha256

        from stock_research_llm_orchestrator.sources.edinet.document_list import EdinetDocumentListAdapter
        from stock_research_llm_orchestrator.sources.protocol import SourceParameter

        retained = tmp_path / "retained"
        retained.mkdir()
        list_body = json.dumps(listing).encode()
        (retained / "body.bin").write_bytes(list_body)
        intent = EdinetDocumentListAdapter().build_intent(
            "document-list", (SourceParameter(name="date", value="2026-06-10"), SourceParameter(name="type", value="2"))
        )
        (retained / "failure.json").write_text(
            json.dumps(
                dict(
                    reason="source_validation_failed",
                    key="list",
                    sha256=sha256(list_body).hexdigest(),
                    received_at="2026-09-26T12:00:00+00:00",
                    source_intent=intent.model_dump(mode="json"),
                )
            )
        )
        if case == "retained_tamper":
            (retained / "body.bin").write_bytes(b"{}")

    def run() -> Path:
        return acquire_edinet_acceptance(
            task=task,
            config=Path("config"),
            runtime=tmp_path / "runtime",
            runs=tmp_path / "runs",
            credential=key,
            retained_list=retained,
            allow_network=case != "no_optin",
            allow_credential=True,
            clock=lambda: current[0],
            sleep=sleep,
            transport=httpx.MockTransport(handler),
        )

    if case in {"success", "resume"}:
        output = run()
        validate_financial(read_bundle(output))
        assert len(sends) == (1 if case == "resume" else 2)
        if case == "success":
            assert (sends[1] - sends[0]).total_seconds() >= 60
        else:
            assert retained is not None
            assert (output / "retained-list/failure.json").read_bytes() == (retained / "failure.json").read_bytes()
    else:
        with pytest.raises((ValueError, RuntimeError)):
            run()
        assert len(sends) == (
            0 if case in {"no_optin", "permission", "retained_tamper"} else 2 if case == "bad_zip" else 1
        )
    for root in (tmp_path / "runs", tmp_path / "runtime"):
        for artifact in root.rglob("*"):
            if artifact.is_file():
                assert canary.encode() not in artifact.read_bytes()


@pytest.mark.parametrize("case", ["success", "bad_zip", "tamper", "wrong_parent", "duplicate"])
def test_pinned_pair_boundaries(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """Use real transport coordination while replacing only the retained body pin."""
    from hashlib import sha256

    from stock_research_llm_orchestrator.preparation import edinet_pair
    from stock_research_llm_orchestrator.sources.edinet.document_list import EdinetDocumentListAdapter
    from stock_research_llm_orchestrator.sources.protocol import SourceParameter

    source = request.getfixturevalue("financial_inputs")
    task = DetailedAnalysisTaskV1.model_validate_json((source / "task.json").read_bytes())
    listing = json.loads((source / "raw/list/body.bin").read_bytes())
    listing["metadata"]["parameter"] = {"date": "2023-06-30", "type": "2"}
    rows = []
    for doc_id, kind, parent in zip(edinet_pair.PAIR_IDS, ("120", "130"), (None, "S100QZHY"), strict=True):
        row = dict(listing["results"][0])
        row.update(
            docID=doc_id,
            docTypeCode=kind,
            parentDocID=parent,
            edinetCode="E02144",
            secCode="72030",
            periodStart="2022-04-01",
            periodEnd="2023-03-31",
            legalStatus="1",
            disclosureStatus="0",
            withdrawalStatus="0",
            xbrlFlag="1",
        )
        rows.append(row)
    if case == "wrong_parent":
        rows[1]["parentDocID"] = "OTHER"
    if case == "duplicate":
        rows.append(dict(rows[0]))
    listing["results"] = rows
    body = json.dumps(listing).encode()
    pinned = sha256(body).hexdigest()
    monkeypatch.setattr(edinet_pair, "PAIR_BODY_SHA256", pinned)
    retained = tmp_path / "retained"
    retained.mkdir()
    (retained / "body.bin").write_bytes(body + b" " if case == "tamper" else body)
    intent = EdinetDocumentListAdapter().build_intent(
        "document-list", (SourceParameter(name="date", value="2023-06-30"), SourceParameter(name="type", value="2"))
    )
    (retained / "failure.json").write_text(
        json.dumps(
            dict(
                reason="source_validation_failed",
                key="list",
                sha256=pinned,
                received_at="2026-09-26T12:00:00Z",
                source_intent=intent.model_dump(mode="json"),
            )
        )
    )
    key = tmp_path / "key"
    key.write_text("synthetic-pair-key")
    key.chmod(0o600)
    now = [datetime(2026, 9, 27, tzinfo=UTC)]
    sends: list[datetime] = []

    def sleep(seconds: float) -> None:
        now[0] += timedelta(seconds=seconds)

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path.endswith("/" + edinet_pair.PAIR_IDS[len(sends)])
        sends.append(now[0])
        return httpx.Response(
            200,
            headers={"Content-Type": "application/octet-stream"},
            content=b"invalid" if case == "bad_zip" else (source / "raw/document/body.bin").read_bytes(),
        )

    def run() -> Path:
        return acquire_edinet_acceptance(
            task=task,
            config=Path("config"),
            runtime=tmp_path / "runtime",
            runs=tmp_path / "runs",
            credential=key,
            retained_list=retained,
            amendment_pair=True,
            allow_network=True,
            allow_credential=True,
            clock=lambda: now[0],
            sleep=sleep,
            transport=httpx.MockTransport(handler),
        )

    if case == "success":
        output = run()
        assert json.loads((output / "manifest.json").read_bytes())["whole_list_accepted"] is False
        saved_pair = read_bundle(output)
        edinet_pair.validate_pair_bundle(saved_pair)
        with pytest.raises(ValueError, match="inventory_mismatch"):
            edinet_pair.validate_pair_bundle({**saved_pair, "acquisitions.json": b"[]"})
        assert len(sends) == 2 and (sends[1] - sends[0]).total_seconds() >= 60
        with pytest.raises(FileExistsError):
            run()
        assert len(sends) == 2
    else:
        with pytest.raises(ValueError):
            run()
        assert len(sends) == (1 if case == "bad_zip" else 0)
        if case == "bad_zip":
            with pytest.raises(FileExistsError):
                run()
            assert len(sends) == 1
