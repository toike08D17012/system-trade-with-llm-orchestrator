"""Interim acceptance preserves cumulative periods and exact source bindings."""

import io
import json
from datetime import date
from hashlib import sha256
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from stock_research_llm_orchestrator.preparation import interim_ir
from stock_research_llm_orchestrator.preparation.financial_mapping import Metric
from stock_research_llm_orchestrator.preparation.interim_ir_cli import main
from stock_research_llm_orchestrator.sources.toyota_ir_pdf import (
    MAX_PDF_BYTES,
    InterimDocument,
    extract_pages,
    parse_pages,
)


POLICY = Path("config/financial-mapping/7203-interim-ir-approved.json")


def document() -> InterimDocument:
    """Use a synthetic third-quarter scope with a distinct prior-year end."""
    return InterimDocument(
        key="2024_3q",
        url="https://global.toyota/pages/global_toyota/ir/financial-results/2024_3q_summary_en.pdf",
        fiscal_year=2024,
        quarter=3,
        start_date=date(2023, 4, 1),
        end_date=date(2023, 12, 31),
        published_on=date(2024, 2, 6),
        sha256="0" * 64,
    )


def pages() -> tuple[str, ...]:
    """Synthetic values deliberately differ across adjacent columns."""
    return (
        "TOYOTA MOTOR CORPORATION FY2024\nFebruary 6, 2024",
        "Consolidated Statement of Financial Position\nYen in millions\n"
        "March 31, 2023    December 31, 2023\nTotal assets 10 20\nTotal shareholders' equity 4 8",
        "Consolidated Statement of Income\nYen in millions\nFor the first nine months ended\n"
        "December 31, 2022    December 31, 2023\nTotal sales revenues 1,000 2,000\n"
        "Operating income 3 (4)\nNet income attributable to\nToyota Motor Corporation 5 6",
        "Consolidated Statement of Cash Flows\nYen in millions\nFor the first nine months ended\n"
        "December 31, 2022    December 31, 2023\nNet cash provided by (used in) operating activities 7 8",
    )


def test_period_columns_unit_and_attribution() -> None:
    """Normalize millions while retaining current-column and cumulative provenance."""
    results = {m.metric: m for m in parse_pages(pages(), document())}
    assert all(m.status == "accepted" for m in results.values())
    assert results["revenue"].value == "2000000000"
    assert results["operating_profit"].value == "-4000000"
    assert results["parent_profit"].value == "6000000"
    assert results["assets"].value == "20000000"
    assert results["assets"].start_date is None
    assert results["revenue"].start_date == date(2023, 4, 1)
    assert results["revenue"].period_basis == "fiscal_year_to_date"
    assert results["revenue"].references == ((3, 5, 2),)


def test_reversed_columns_follow_dates() -> None:
    """Column position alone must never select the fiscal period."""
    text = tuple(
        t.replace("December 31, 2022    December 31, 2023", "December 31, 2023    December 31, 2022") for t in pages()
    )
    results = {m.metric: m for m in parse_pages(text, document())}
    assert results["revenue"].value == "1000000000"
    assert results["revenue"].references[0][2] == 1


@pytest.mark.parametrize(
    ("old", "new", "metric"),
    [
        ("Yen in millions", "USD in millions", "revenue"),
        ("nine months", "third quarter", "revenue"),
        ("March 31, 2023", "December 31, 2022", "assets"),
        ("December 31, 2023", "September 30, 2023", "revenue"),
        ("Net income attributable to", "Other income", "parent_profit"),
        ("2,000", "—", "revenue"),
    ],
)
def test_unsupported_or_missing_evidence_stays_unaccepted(old: str, new: str, metric: Metric) -> None:
    """Unknown units, periods, attribution and nil cannot yield accepted values."""
    results = {m.metric: m for m in parse_pages(tuple(t.replace(old, new) for t in pages()), document())}
    assert results[metric].status == "unaccepted"
    assert results[metric].value is None


def test_split_pdf_glyphs_and_conflicting_duplicates() -> None:
    """Tolerate glyph spacing but preserve conflicts and repeated references."""
    text = tuple(t.replace("March 31, 2023", "Ma rch 3 1 , 202 3").replace("millions", "m illio ns") for t in pages())
    assert all(m.status == "accepted" for m in parse_pages(text, document()))
    repeated = parse_pages((*text, text[2]), document())
    assert repeated[0].status == "accepted" and len(repeated[0].references) == 2
    conflict = parse_pages((*text, text[2].replace("2,000", "2,001")), document())
    assert conflict[0].value is None and conflict[0].reasons == ("conflicting_rows",)


@pytest.mark.parametrize("change", [{"quarter": 2}, {"key": "../x"}, {"url": "https://example.com/x"}, {"sha256": "x"}])
def test_document_scope_rejected(change: dict[str, object]) -> None:
    """Prevent unsafe paths and inconsistent document identities."""
    with pytest.raises(ValueError):
        InterimDocument.model_validate({**document().model_dump(), **change})


def test_document_identity_rejected() -> None:
    """A different fiscal report cannot satisfy the pinned scope."""
    with pytest.raises(ValueError, match="identity_mismatch"):
        parse_pages((pages()[0].replace("FY2024", "FY2025"), *pages()[1:]), document())


def pdf(text: str | None = "Synthetic PDF", password: str | None = None, count: int = 1) -> bytes:
    """Generate a small PDF without storing real financial source data."""
    writer = PdfWriter()
    for _ in range(count):
        page = writer.add_blank_page(width=600, height=800)
        if text is not None:
            font = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }
            )
            page[NameObject("/Resources")] = DictionaryObject(
                {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
            )
            stream = DecodedStreamObject()
            stream.set_data(f"BT /F1 12 Tf 30 750 Td ({text}) Tj ET".encode())
            page[NameObject("/Contents")] = stream
    if password is not None:
        writer.encrypt(password)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_real_pdf_subprocess_and_empty_password() -> None:
    """Exercise the isolated parser with readable and public encrypted PDFs."""
    assert "Synthetic PDF" in extract_pages(pdf())[0]
    assert "Synthetic PDF" in extract_pages(pdf(password=""))[0]


@pytest.mark.parametrize(
    "body", [b"invalid", b"%PDF-" + b"x" * MAX_PDF_BYTES, pdf(text=None), pdf(password="required"), pdf(count=65)]
)
def test_pdf_limits_and_unreadable_content(body: bytes) -> None:
    """Reject oversized, scanned, password-protected and excessive-page inputs."""
    with pytest.raises(ValueError, match="ir_pdf_"):
        extract_pages(body)


def fixture_inputs(monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, bytes], bytes]:
    """Replace only the approved hash and PDF extraction, retaining full replay."""
    policy = json.loads(POLICY.read_bytes())
    files: dict[str, bytes] = {}
    for item in policy["documents"]:
        body = b"%PDF-synthetic"
        item["sha256"] = sha256(body).hexdigest()
        files[f"{item['key']}/body.pdf"] = body
        files[f"{item['key']}/receipt.json"] = json.dumps(
            dict(
                url=item["url"], sha256=item["sha256"], bytes=len(body), status=200, retrieved_at="2026-09-27T13:00:00Z"
            )
        ).encode()
    policy["metadata_hashes"] = {k: sha256(v).hexdigest() for k, v in files.items() if k.endswith("receipt.json")}
    body = json.dumps(policy).encode()
    monkeypatch.setattr(interim_ir, "POLICY_SHA256", sha256(body).hexdigest())
    monkeypatch.setattr(interim_ir, "extract_pages", lambda body: ())
    monkeypatch.setattr(interim_ir, "parse_pages", lambda texts, doc: parse_pages(pages(), document()))
    return files, body


def test_pinned_policy() -> None:
    """Only the exact eight-document approved policy may be evaluated."""
    assert sha256(POLICY.read_bytes()).hexdigest() == interim_ir.POLICY_SHA256
    assert len(interim_ir.InterimPolicy.model_validate_json(POLICY.read_bytes()).documents) == 8
    with pytest.raises(ValueError, match="policy_unapproved"):
        interim_ir.evaluate_interim({}, POLICY.read_bytes() + b" ")


def test_replay_counts_and_no_manifest_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replay must verify private values while publishing only coverage metadata."""
    inputs, policy = fixture_inputs(monkeypatch)
    output = interim_ir.evaluate_interim(inputs, policy)
    manifest = json.loads(output["manifest.json"])
    assert manifest["accepted_count"] == 48 and manifest["complete_interim_count"] == 8
    assert not manifest["analysis_ready"] and b'"value"' not in output["manifest.json"]
    interim_ir.validate_interim(output, inputs)
    output["values.json"] += b" "
    with pytest.raises(ValueError, match="replay_mismatch"):
        interim_ir.validate_interim(output, inputs)


@pytest.mark.parametrize("target", ["body.pdf", "receipt.json", "extra"])
def test_source_tamper_rejected(monkeypatch: pytest.MonkeyPatch, target: str) -> None:
    """Reject changed PDFs, receipts and unsolicited input files."""
    inputs, policy = fixture_inputs(monkeypatch)
    key = next(k for k in inputs if k.endswith(target)) if target != "extra" else "unexpected"
    inputs[key] = inputs.get(key, b"") + b" "
    with pytest.raises(ValueError, match="inventory_invalid|metadata_hash_mismatch|receipt_binding_invalid"):
        interim_ir.evaluate_interim(inputs, policy)


def test_partial_coverage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing cash flows must not count as complete interim periods."""
    inputs, policy = fixture_inputs(monkeypatch)
    monkeypatch.setattr(interim_ir, "parse_pages", lambda texts, doc: parse_pages(pages()[:3], document()))
    manifest = json.loads(interim_ir.evaluate_interim(inputs, policy)["manifest.json"])
    assert manifest["accepted_count"] == 40 and manifest["complete_interim_count"] == 0
    assert all(c["missing_metrics"] == ["operating_cash_flow"] for c in manifest["interim_coverage"])


def test_publish_cli_and_overlap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Publish immutable output and reject overwrites or input overlap."""
    inputs, policy = fixture_inputs(monkeypatch)
    source = tmp_path / "source"
    for name, body in inputs.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    policy_path = tmp_path / "policy.json"
    policy_path.write_bytes(policy)
    output = tmp_path / "output"
    assert main(["prepare", "--source", str(source), "--policy", str(policy_path), "--output", str(output)]) == 0
    assert main(["validate", "--source", str(source), "--input", str(output)]) == 0
    assert "2000000000" not in capsys.readouterr().out
    with pytest.raises(FileExistsError):
        interim_ir.prepare_interim(source, policy_path, output)
    with pytest.raises(ValueError, match="overlap"):
        interim_ir.prepare_interim(source, policy_path, source / "nested")
