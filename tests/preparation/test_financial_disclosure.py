"""Offline candidate preparation with synthetic acquisition exports."""

import hashlib
import io
import json
import socket
import zipfile
from pathlib import Path

import pytest

from stock_research_llm_orchestrator.preparation.financial_disclosure import prepare_financial, validate_financial
from stock_research_llm_orchestrator.preparation.financial_disclosure_cli import main
from stock_research_llm_orchestrator.preparation.financial_mapping import METRICS, prepare_mapping, validate_mapping
from stock_research_llm_orchestrator.preparation.financial_mapping_cli import main as mapping_main
from stock_research_llm_orchestrator.preparation.fx_evidence import read_bundle
from stock_research_llm_orchestrator.sources.edinet.document_list import EdinetDocumentListAdapter
from stock_research_llm_orchestrator.sources.edinet.xbrl_document import EdinetXbrlDocumentAdapter
from stock_research_llm_orchestrator.sources.protocol import SourceParameter


ROOT = Path(__file__).resolve().parents[2]


def _save(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2))


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the offline evidence boundary with synthetic data."""

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("network forbidden")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr("stock_research_llm_orchestrator.credentials.edinet.use_edinet_api_key_for_send", refuse)


@pytest.fixture
def inputs(tmp_path: Path) -> Path:
    """Exercise the offline evidence boundary with synthetic data."""
    source = tmp_path / "inputs"
    source.mkdir()
    task = json.loads(
        (
            ROOT / "tests/fixtures/contracts/detailed-analysis/v1/detailed-analysis-task/valid/human-selected.json"
        ).read_bytes()
    )
    task["task_accepted_at"] = "2026-09-22T00:00:00+00:00"
    task["security"]["security_code"] = "7203"
    _save(source / "task.json", task)
    approval = (ROOT / "config/source-approvals/edinet/v1.yaml").read_bytes()
    (source / "approval.yaml").write_bytes(approval)
    (source / "evaluation-policy.yaml").write_bytes((ROOT / "config/policies/detailed-analysis/v1.yaml").read_bytes())
    (source / "acquisition-approvals").mkdir()
    listing = (ROOT / "tests/fixtures/sources/edinet/document-list.json").read_bytes()
    xml = b"""<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
 xmlns:iso4217="http://www.xbrl.org/2003/iso4217" xmlns:s="https://example.invalid/synthetic">
 <xbrli:context id="annual"><xbrli:entity><xbrli:identifier scheme="https://example.invalid/entity">E00001</xbrli:identifier></xbrli:entity>
 <xbrli:period><xbrli:startDate>2025-04-01</xbrli:startDate><xbrli:endDate>2026-03-31</xbrli:endDate></xbrli:period></xbrli:context>
 <xbrli:unit id="JPY"><xbrli:measure>iso4217:JPY</xbrli:measure></xbrli:unit>
 <s:Revenue contextRef="annual" unitRef="JPY" decimals="-6">123000000</s:Revenue></xbrli:xbrl>"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("XBRL/PublicDoc/synthetic.xbrl", xml)
    acquisitions = []
    for key, body, schema, media, encoding, adapter, params in (
        (
            "list",
            listing,
            "edinet-document-list-raw",
            "application/json",
            "utf-8",
            EdinetDocumentListAdapter(),
            (SourceParameter(name="date", value="2026-09-22"), SourceParameter(name="type", value="2")),
        ),
        (
            "document",
            buffer.getvalue(),
            "edinet-xbrl-zip-raw",
            "application/octet-stream",
            "binary",
            EdinetXbrlDocumentAdapter(),
            (SourceParameter(name="document_id", value="SYNTHETIC001"), SourceParameter(name="type", value="1")),
        ),
    ):
        operation = "document-list" if key == "list" else "document-retrieval"
        intent = adapter.build_intent(operation, params)
        publication = dict(
            publication_id=f"pub-{key}",
            task_id="original-task",
            logical_request_id=f"logical-{key}",
            physical_attempt_id=f"attempt-{key}",
            source_id="edinet",
            operation=operation,
            content_sha256=hashlib.sha256(body).hexdigest(),
            byte_count=len(body),
            media_type=media,
            encoding=encoding,
            raw_schema_id=schema,
            raw_schema_version=1,
            publication_generation=1,
        )
        directory = source / "raw" / key
        directory.mkdir(parents=True)
        (directory / "body.bin").write_bytes(body)
        _save(
            directory / "request.json",
            {
                k: publication[k]
                for k in (
                    "publication_id",
                    "task_id",
                    "logical_request_id",
                    "physical_attempt_id",
                    "source_id",
                    "operation",
                    "publication_generation",
                )
            },
        )
        _save(
            directory / "response.json",
            {
                k: publication[k]
                for k in ("byte_count", "media_type", "encoding", "raw_schema_id", "raw_schema_version")
            },
        )
        _save(directory / "receipt.json", {"body_sha256": publication["content_sha256"], "byte_count": len(body)})
        (source / "acquisition-approvals" / f"{key}.yaml").write_bytes(approval)
        acquisitions.append(
            dict(
                key=key,
                publication=publication,
                source_intent=intent.model_dump(mode="json"),
                retrieved_at="2026-09-22T10:00:00+00:00",
                acquisition_approval_sha256=hashlib.sha256(approval).hexdigest(),
            )
        )
    _save(
        source / "inputs.json",
        dict(
            version=1,
            checked_at="2026-09-27T00:00:00+00:00",
            survey_period=dict(start_date="2026-09-22", end_date="2026-09-23"),
            issuer=dict(
                security_code="7203",
                edinet_code="E00001",
                provider_security_code="72030",
                applicable_period=dict(start_date="2026-09-22", end_date="2026-09-27"),
                list_key="list",
                list_sha256=hashlib.sha256(listing).hexdigest(),
            ),
            acquisitions=acquisitions,
        ),
    )
    return source


def test_prepare_replay_preserves_candidates_and_missing_periods(inputs: Path) -> None:
    """Exercise the offline evidence boundary with synthetic data."""
    before = read_bundle(inputs)
    output = inputs.parent / "prepared"
    result = prepare_financial(inputs, output)
    assert result.status == "pending" and not result.analysis_ready
    assert result.annual_periods == (("2025-04-01", "2026-03-31"),)
    assert result.missing_list_dates == ("2026-09-23",)
    assert "issuer_ir_unchecked" in result.reasons
    candidates = json.loads((output / "candidates.json").read_bytes())
    assert candidates["document"]["facts"]["facts"][0]["value"] == "123000000"
    validate_financial(read_bundle(output))
    assert read_bundle(inputs) == before
    with pytest.raises(FileExistsError):
        prepare_financial(inputs, output)


@pytest.mark.parametrize("change", ["body", "receipt", "future", "issuer", "missing_archive"])
def test_invalid_or_incomplete_inputs(inputs: Path, change: str) -> None:
    """Exercise the offline evidence boundary with synthetic data."""
    data = json.loads((inputs / "inputs.json").read_bytes())
    if change == "body":
        (inputs / "raw/list/body.bin").write_bytes(b"{}")
    elif change == "receipt":
        _save(inputs / "raw/list/receipt.json", {})
    elif change == "future":
        data["acquisitions"][0]["retrieved_at"] = "2027-01-01T00:00:00+00:00"
    elif change == "issuer":
        data["issuer"]["edinet_code"] = "E99999"
    else:
        import shutil

        data["acquisitions"].pop()
        shutil.rmtree(inputs / "raw/document")
        (inputs / "acquisition-approvals/document.yaml").unlink()
    _save(inputs / "inputs.json", data)
    if change in {"issuer", "missing_archive"}:
        result = prepare_financial(inputs, inputs.parent / "out")
        assert result.annual_periods == ()
        assert result.filings[0].reasons
    else:
        with pytest.raises(ValueError):
            prepare_financial(inputs, inputs.parent / "out")


def test_cli_and_tampering(inputs: Path) -> None:
    """Exercise the offline evidence boundary with synthetic data."""
    output = inputs.parent / "out"
    assert main(["prepare", "--input", str(inputs), "--output", str(output)]) == 0
    assert main(["validate", "--input", str(output)]) == 0
    (output / "candidates.json").write_bytes(b"{}")
    assert main(["validate", "--input", str(output)]) == 1


def test_symlink_refused(inputs: Path) -> None:
    """Exercise the offline evidence boundary with synthetic data."""
    link = inputs.parent / "link"
    link.symlink_to(inputs, target_is_directory=True)
    with pytest.raises(ValueError):
        prepare_financial(link, inputs.parent / "out")


def _replace_list(source: Path, listing: dict) -> None:
    """Update a synthetic raw observation and all its explicit acquisition bindings."""
    body = json.dumps(listing).encode()
    digest = hashlib.sha256(body).hexdigest()
    (source / "raw/list/body.bin").write_bytes(body)
    data = json.loads((source / "inputs.json").read_bytes())
    data["issuer"]["list_sha256"] = digest
    data["acquisitions"][0]["publication"].update(content_sha256=digest, byte_count=len(body))
    _save(source / "inputs.json", data)
    response = json.loads((source / "raw/list/response.json").read_bytes())
    response["byte_count"] = len(body)
    _save(source / "raw/list/response.json", response)
    _save(source / "raw/list/receipt.json", dict(body_sha256=digest, byte_count=len(body)))


@pytest.mark.parametrize(
    "case,reason",
    [
        ("withdrawn", "withdrawn"),
        ("amended", "amendment_parent_missing"),
        ("cycle", "amendment_cycle"),
        ("other", "other_or_unresolved_issuer"),
    ],
)
def test_filing_exclusion(inputs: Path, case: str, reason: str) -> None:
    """Retain unsafe selections and their concrete reasons rather than accepting periods."""
    listing = json.loads((inputs / "raw/list/body.bin").read_bytes())
    doc = listing["results"][0]
    if case == "withdrawn":
        doc["withdrawalStatus"] = "1"
    elif case in {"amended", "cycle"}:
        doc["docTypeCode"] = "130"
        doc["parentDocID"] = "MISSING" if case == "amended" else doc["docID"]
    else:
        import copy

        other = copy.deepcopy(doc)
        other.update(seqNumber=3, docID="OTHER", edinetCode="E00002", secCode="99990")
        listing["results"].append(other)
        listing["metadata"]["resultset"]["count"] = 3
    _replace_list(inputs, listing)
    result = prepare_financial(inputs, inputs.parent / "out")
    assert any(reason in filing.reasons for filing in result.filings)
    if case != "other":
        assert not result.annual_periods


def test_publication_failure_and_competing_root(inputs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject competing publishers and leave no partial destination on rename failure."""
    import fcntl
    import os

    from stock_research_llm_orchestrator.preparation import storage

    output = inputs.parent / "out"
    descriptor = os.open(inputs.parent, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="root_busy"):
            prepare_financial(inputs, output)
    finally:
        os.close(descriptor)

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("synthetic rename failure")

    monkeypatch.setattr(storage.os, "rename", fail)
    with pytest.raises(OSError):
        prepare_financial(inputs, output)
    assert not output.exists()


from .test_price_fx_run import sample as price_sample  # noqa: E402, F401


def test_price_fx_connection_requires_same_task_and_time(inputs: Path, request: pytest.FixtureRequest) -> None:
    """Connect a fully validated price bundle and reject a later financial evaluation."""
    import shutil

    from .test_price_fx_run import CHECKED, run

    root = inputs.parent / "price-case"
    shutil.copytree(request.getfixturevalue("price_sample"), root)
    run(root)
    shutil.copytree(root / "result", inputs / "price-fx")
    (inputs / "task.json").write_bytes((root / "result/task.json").read_bytes())
    data = json.loads((inputs / "inputs.json").read_bytes())
    data["checked_at"] = CHECKED.isoformat()
    _save(inputs / "inputs.json", data)
    result = prepare_financial(inputs, inputs.parent / "connected")
    assert result.price_fx_status == "ready_with_limitations"
    validate_financial(read_bundle(inputs.parent / "connected"))
    data["checked_at"] = "2026-09-27T13:00:00+09:00"
    _save(inputs / "inputs.json", data)
    with pytest.raises(ValueError, match="task_or_time"):
        prepare_financial(inputs, inputs.parent / "mismatch")


def test_repeated_list_observation_does_not_add_financial_periods(inputs: Path) -> None:
    """Two snapshots of one filing count as one observed accounting period."""
    import copy
    import shutil

    data = json.loads((inputs / "inputs.json").read_bytes())
    duplicate = copy.deepcopy(data["acquisitions"][0])
    duplicate["key"] = "list2"
    for name in ("publication_id", "logical_request_id", "physical_attempt_id"):
        duplicate["publication"][name] += "2"
    data["acquisitions"].append(duplicate)
    _save(inputs / "inputs.json", data)
    shutil.copytree(inputs / "raw/list", inputs / "raw/list2")
    request = json.loads((inputs / "raw/list2/request.json").read_bytes())
    for name in ("publication_id", "logical_request_id", "physical_attempt_id"):
        request[name] = duplicate["publication"][name]
    _save(inputs / "raw/list2/request.json", request)
    shutil.copyfile(inputs / "acquisition-approvals/list.yaml", inputs / "acquisition-approvals/list2.yaml")
    result = prepare_financial(inputs, inputs.parent / "out")
    assert len(result.filings) == 2
    assert len(result.annual_periods) == 1
    assert "annual_periods_insufficient" in result.reasons


def test_sidecar_replay_and_legacy_compatibility(inputs: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Retain old synthetic bare identifiers and detect every artifact mutation."""
    source = inputs.parent / "financial"
    prepare_financial(inputs, source)
    before = read_bundle(source)
    proposal = inputs.parent / "proposal.json"
    proposal.write_text(
        json.dumps(
            {
                "version": 1,
                "status": "draft",
                "start_date": "2025-04-01",
                "end_date": "2026-03-31",
                "rules": [
                    {
                        "metric": metric,
                        "concepts": [
                            {
                                "namespace": "https://example.invalid/synthetic",
                                "local_name": "Revenue" if metric == "revenue" else metric,
                            }
                        ],
                        "evidence_references": ["synthetic"],
                    }
                    for metric in METRICS
                ],
            }
        )
    )
    output = inputs.parent / "mapping"
    result = prepare_mapping(source, proposal, output)
    assert result.analysis_ready is False and result.status == "pending"
    assert result.archives[0].metrics[0].status == "excluded"
    assert result.archives[0].entities[0].reason == "unsupported_entity_scheme"
    assert read_bundle(source) == before
    validate_financial(before)
    saved = read_bundle(output)
    validate_mapping(saved, before)
    assert set(saved) == {"proposal.json", "review.json"}
    assert "123000000" not in saved["review.json"].decode()
    with pytest.raises((FileExistsError, ValueError)):
        prepare_mapping(source, proposal, output)
    with pytest.raises(ValueError):
        prepare_mapping(source, proposal, source / "child")
    for name in ("review.json", "proposal.json"):
        altered = {**saved, name: saved[name] + b" "}
        with pytest.raises(ValueError):
            validate_mapping(altered, before)
    damaged_source = {**before, "raw/document/body.bin": b"bad"}
    with pytest.raises(ValueError):
        validate_mapping(saved, damaged_source)
    assert mapping_main(["validate", "--source", str(source), "--input", str(output)]) == 0
    assert "analysis_ready=false" in capsys.readouterr().out
    (output / "review.json").write_text("{}")
    assert mapping_main(["validate", "--source", str(source), "--input", str(output)]) == 1
    assert "123000000" not in capsys.readouterr().out
