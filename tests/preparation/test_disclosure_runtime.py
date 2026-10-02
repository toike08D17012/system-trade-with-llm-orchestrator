"""Verify initial disclosure candidates through real persistent publication."""

import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.task import DetailedAnalysisTaskV1
from stock_research_llm_orchestrator.preparation.interim_ir_runtime import acquire_runtime, verify_runtime
from stock_research_llm_orchestrator.preparation.storage import read_bundle
from stock_research_llm_orchestrator.requests.storage import DATABASE_FILENAME, ProductionRequestRepository
from stock_research_llm_orchestrator.sources.toyota_ir import disclosure_policy


ROOT = Path(__file__).parents[2]
POLICY = (ROOT / "config/financial-mapping/7203-latest-disclosure-candidates.json").read_bytes()
TASK = (
    ROOT / "tests/fixtures/contracts/detailed-analysis/v1/detailed-analysis-task/valid/human-selected.json"
).read_bytes()


class Scenario:
    """Inject only network and time; keep real PDF parser, DB and publication."""

    def __init__(self, root: Path) -> None:
        """Build a readable synthetic PDF without externally supplied financial values."""
        self.root = root
        self.now = datetime(2026, 9, 28, 12, tzinfo=UTC)
        writer = PdfWriter()
        page = writer.add_blank_page(width=600, height=800)
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
        content = DecodedStreamObject()
        content.set_data(b"BT /F1 12 Tf 30 750 Td (Synthetic disclosure candidate) Tj ET")
        page[NameObject("/Contents")] = content
        stream = io.BytesIO()
        writer.write(stream)
        self.body = stream.getvalue()
        self.status = 200
        self.sent: list[tuple[str, datetime]] = []

    def clock(self) -> datetime:
        """Return shared deterministic UTC time."""
        return self.now

    def sleep(self, seconds: float) -> None:
        """Advance time while retaining persistent gate checks."""
        self.now += timedelta(seconds=seconds)

    def handler(self, request: httpx.Request) -> httpx.Response:
        """Record the actual URL selected by the pinned transport."""
        self.sent.append((str(request.url), self.now))
        return httpx.Response(self.status, content=self.body, headers={"Content-Type": "application/pdf"})

    def acquire(self) -> Path:
        """Acquire all nine documents once through the production Coordinator."""
        output = self.root / "raw/candidates"
        acquire_runtime(
            config=ROOT / "config",
            runtime=self.root / "runtime",
            raw_root=self.root / "raw",
            output=output,
            task_bytes=TASK,
            policy_bytes=POLICY,
            latest_disclosures=True,
            clock=self.clock,
            sleep=self.sleep,
            transport=httpx.MockTransport(self.handler),
        )
        return output

    def verify(self, files: dict[str, bytes]) -> None:
        """Validate using the canonical DB and original committed raw files."""
        verify_runtime(
            files,
            ProductionRequestRepository(self.root / "runtime" / DATABASE_FILENAME),
            self.root / "raw",
            DetailedAnalysisTaskV1.model_validate_json(TASK),
            self.root / "runtime",
        )


def test_nine_initial_candidates_and_replay(tmp_path: Path) -> None:
    """First observed hashes are recorded without implying prior content adoption."""
    s = Scenario(tmp_path)
    output = s.acquire()
    files = read_bundle(output)
    s.verify(files)
    policy = disclosure_policy.load_policy(POLICY)
    assert [url for url, when in s.sent] == [d.url for d in policy.documents]
    assert all((b[1] - a[1]).total_seconds() >= 60 for a, b in zip(s.sent, s.sent[1:], strict=False))
    manifest = json.loads(files["manifest.json"])
    assert manifest["kind"] == disclosure_policy.KIND and manifest["analysis_ready"] is False
    with pytest.raises(FileExistsError):
        acquire_runtime(
            config=ROOT / "config",
            runtime=tmp_path / "runtime",
            raw_root=tmp_path / "raw",
            output=tmp_path / "raw/other",
            task_bytes=TASK,
            policy_bytes=POLICY,
            latest_disclosures=True,
            clock=s.clock,
            sleep=s.sleep,
            transport=httpx.MockTransport(s.handler),
        )
    assert len(s.sent) == 9
    manifest["kind"] = "toyota-ir-runtime-acquisition"
    files["manifest.json"] = json.dumps(manifest).encode()
    with pytest.raises(ValueError, match="policy_unapproved"):
        s.verify(files)
    files = read_bundle(output)
    ref = json.loads(files["manifest.json"])["receipts"][0]["raw_reference"]
    body = tmp_path / "raw" / ref["task_id"] / ref["relative_path"] / "body.bin"
    body.write_bytes(body.read_bytes() + b"tamper")
    with pytest.raises(ValueError, match="published_raw_mismatch"):
        s.verify(files)


@pytest.mark.parametrize("failure", ["redirect", "cooldown", "invalid-pdf", "corrupt-pdf"])
def test_failure_consumes_campaign_without_retry(tmp_path: Path, failure: str) -> None:
    """Neither HTTP failures nor PDF parsing failures produce an accepted bundle."""
    s = Scenario(tmp_path)
    if failure in {"redirect", "cooldown"}:
        s.status = 302 if failure == "redirect" else 429
    else:
        s.body = b"html" if failure == "invalid-pdf" else b"%PDF-broken"
    with pytest.raises((ValueError, RuntimeError)):
        s.acquire()
    assert len(s.sent) == 1 and not (tmp_path / "raw/candidates").exists()
    with pytest.raises(FileExistsError):
        s.acquire()
    assert len(s.sent) == 1


def test_changed_policy_never_sends(tmp_path: Path) -> None:
    """An arbitrary caller URL cannot extend the initial acquisition cohort."""
    s = Scenario(tmp_path)
    with pytest.raises(ValueError, match="policy_unapproved"):
        acquire_runtime(
            config=ROOT / "config",
            runtime=tmp_path / "runtime",
            raw_root=tmp_path / "raw",
            output=tmp_path / "raw/other",
            task_bytes=TASK,
            policy_bytes=POLICY + b" ",
            latest_disclosures=True,
            clock=s.clock,
            transport=httpx.MockTransport(s.handler),
        )
    assert s.sent == [] and not (tmp_path / "runtime").exists()
