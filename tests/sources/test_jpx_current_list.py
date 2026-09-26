"""Tests for the approved JPX current-list adapter and transport."""

import hashlib
import io
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from stock_research_llm_orchestrator.sources.jpx.current_list import (
    JpxCurrentListAdapter,
    JpxCurrentListParseError,
)
from stock_research_llm_orchestrator.sources.jpx.httpx_transport import (
    JpxHttpClientPolicy,
    JpxHttpTransportError,
    JpxPhysicalTransport,
)
from stock_research_llm_orchestrator.sources.protocol import BoundedSourceResponse


def _workbook(*rows: tuple[str, ...]) -> bytes:
    strings = (
        "日付",
        "コード",
        "銘柄名",
        "市場・商品区分",
        "33業種コード",
        "33業種区分",
        "17業種コード",
        "17業種区分",
        "規模コード",
        "規模区分",
    ) + tuple(value for row in rows for value in row)
    shared = (
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        + "".join(f"<si><t>{value}</t></si>" for value in strings)
        + "</sst>"
    )
    data_rows = []
    offset = 0
    for number, _row in enumerate((strings[:10], *rows), start=1):
        if number == 1:
            indexes = range(10)
        else:
            indexes = range(10 + offset, 20 + offset)
            offset += 10
        cells = "".join(
            f'<c r="{chr(65 + column)}{number}" t="s"><v>{index}</v></c>' for column, index in enumerate(indexes)
        )
        data_rows.append(f'<row r="{number}">{cells}</row>')
    sheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        + "".join(data_rows)
        + "</sheetData></worksheet>"
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", shared)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return output.getvalue()


def _body() -> bytes:
    return _workbook(
        ("20260831", "1301", "極洋", "プライム（内国株式）", "0050", "水産・農林業", "1", "食品", "6", "TOPIX Small 1"),
        ("20260831", "1305", "ＥＴＦ", "ETF・ETN", "", "", "", "", "", ""),
    )


def test_workbook_member_above_previous_size_limit_is_accepted() -> None:
    """Large valid XML members are not rejected by a resource-capacity ceiling."""
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(_body())) as original,
        zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive,
    ):
        for name in original.namelist():
            body = original.read(name)
            if name == "xl/sharedStrings.xml":
                body += b" " * (16 * 1024 * 1024)
            archive.writestr(name, body)
    body = output.getvalue()
    parsed = JpxCurrentListAdapter().parse(
        BoundedSourceResponse(
            physical_attempt_id="attempt-large",
            body=body,
            sha256=hashlib.sha256(body).hexdigest(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            encoding="binary",
        )
    )
    assert len(parsed.issues) == 2


def test_parses_eligible_domestic_common_equity_without_guessing_other_classes() -> None:
    """Classify only the three explicitly approved domestic-equity categories."""
    body = _body()
    parsed = JpxCurrentListAdapter().parse(
        BoundedSourceResponse(
            physical_attempt_id="attempt-1",
            body=body,
            sha256=hashlib.sha256(body).hexdigest(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            encoding="binary",
        )
    )
    assert parsed.snapshot_on == "2026-08-31"
    assert parsed.issues[0].eligibility == "eligible"
    assert parsed.issues[0].market_segment == "Prime"
    assert parsed.issues[1].eligibility == "ineligible"
    assert parsed.issues[1].security_class == "unknown"


@pytest.mark.parametrize("body", [b"not-xlsx", _workbook(("20260831",) * 10)])
def test_rejects_malformed_or_unrecognized_workbook(body: bytes) -> None:
    """Fail closed without exposing workbook content."""
    with pytest.raises(JpxCurrentListParseError, match="jpx_current_list_invalid"):
        JpxCurrentListAdapter().parse(
            BoundedSourceResponse(
                physical_attempt_id="attempt-1",
                body=body,
                sha256=hashlib.sha256(body).hexdigest(),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                encoding="binary",
            )
        )


def test_five_digit_source_codes_do_not_reject_supported_equities_or_gain_eligibility() -> None:
    """Retain extended source codes without truncating or classifying them as ordinary equity."""
    from stock_research_llm_orchestrator.sources.jpx.verification import verify_jpx_security

    body = _workbook(
        ("20260831", "1234", "Synthetic ordinary", "プライム（内国株式）", "", "", "", "", "", ""),
        ("20260831", "12345", "Synthetic extended", "プライム（内国株式）", "", "", "", "", "", ""),
        ("20260831", "12346", "Synthetic extended second", "プライム（内国株式）", "", "", "", "", "", ""),
    )
    snapshot = JpxCurrentListAdapter().parse(
        BoundedSourceResponse(
            physical_attempt_id="synthetic-extended-codes",
            body=body,
            sha256=hashlib.sha256(body).hexdigest(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            encoding="binary",
        )
    )
    assert tuple(issue.code for issue in snapshot.issues) == ("1234", "12345", "12346")
    assert verify_jpx_security(snapshot, "1234").status == "verified_eligible"
    for issue in snapshot.issues[1:]:
        assert issue.eligibility == "unknown"
        assert issue.security_class == "unknown"
        with pytest.raises(ValueError, match="extended_code"):
            type(issue).model_validate({**issue.model_dump(), "eligibility": "eligible"})
        with pytest.raises(ValueError, match="invalid_jpx_security_code"):
            verify_jpx_security(snapshot, issue.code)


@pytest.mark.parametrize("code", ["123", "123456", "1234A", "12-4"])
def test_unrecognized_code_shapes_remain_rejected(code: str) -> None:
    """Accommodate only the observed source representation, not arbitrary identifiers."""
    body = _workbook(("20260831", code, "Synthetic", "プライム（内国株式）", "", "", "", "", "", ""))
    with pytest.raises(JpxCurrentListParseError):
        JpxCurrentListAdapter().parse(
            BoundedSourceResponse(
                physical_attempt_id="synthetic-invalid-code",
                body=body,
                sha256=hashlib.sha256(body).hexdigest(),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                encoding="binary",
            )
        )


def test_transport_uses_fixed_url_and_rejects_redirect() -> None:
    """Perform one anonymous fixed-resource request with no redirect following."""
    intent = JpxCurrentListAdapter().build_intent("current-listed-issues", ())
    body = _body()
    seen: list[httpx.Request] = []

    def ok(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            headers={"Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
            content=body,
        )

    callback = JpxPhysicalTransport(
        intent,
        JpxHttpClientPolicy(connect_timeout_seconds=1, read_timeout_seconds=2),
        httpx.MockTransport(ok),
    )
    response = callback(intent.to_transport_request("logical-1", "attempt-1"))
    assert response.body == body
    assert seen[0].url == "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xlsx"

    redirect = JpxPhysicalTransport(
        intent,
        JpxHttpClientPolicy(connect_timeout_seconds=1, read_timeout_seconds=2),
        httpx.MockTransport(lambda _request: httpx.Response(302, headers={"Location": "https://example.invalid"})),
    )
    with pytest.raises(JpxHttpTransportError, match="jpx_http_redirect_rejected"):
        redirect(intent.to_transport_request("logical-2", "attempt-2"))


def test_jpx_runs_through_production_and_publishes_exact_raw(tmp_path: Path) -> None:
    """Commit exact XLSX bytes only after strict source parsing succeeds."""
    from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.external_requests import GateScope
    from stock_research_llm_orchestrator.requests.production import (
        GateKeys,
        GateLimit,
        HierarchicalGatePolicy,
        ProductionCachePolicy,
        ProductionLogicalRequest,
        ProductionPhysicalAttempt,
        QueuePolicy,
        RawPublicationIntent,
        RuntimeLeasePolicy,
    )
    from stock_research_llm_orchestrator.requests.raw_artifacts import RawArtifactPublisher
    from stock_research_llm_orchestrator.requests.storage import initialize_runtime_storage
    from stock_research_llm_orchestrator.requests.transport import (
        ProductionTransportCoordinator,
        TransportValidationPolicy,
    )
    from stock_research_llm_orchestrator.sources import publish_source_candidate

    now = datetime(2026, 9, 25, tzinfo=UTC)
    body = _body()
    runtime = tmp_path / ".runtime"
    runtime.mkdir(mode=0o700)
    runs = tmp_path / "runs"
    runs.mkdir(mode=0o700)
    repository = initialize_runtime_storage(runtime)
    lease = repository.acquire_lease("owner-a", now, RuntimeLeasePolicy())
    adapter = JpxCurrentListAdapter()
    intent = adapter.build_intent("current-listed-issues", ())
    logical = ProductionLogicalRequest(
        logical_request_id="logical-jpx-1",
        task_id="task-1",
        source_id="jpx",
        operation="current-listed-issues",
        request_fingerprint=hashlib.sha256(intent.model_dump_json().encode()).hexdigest(),
        source_approval_version=1,
        source_profile_version=1,
        credential_scope_alias=None,
        egress_scope="default-egress",
        created_at=now.isoformat(),
    )
    repository.admit_logical_request(
        logical, "jpx-public-web", ProductionCachePolicy(applicable=False), lease, now, QueuePolicy()
    )
    assert repository.claim_next_queued("jpx-public-web", lease, now + timedelta(seconds=1)) is not None
    attempt = ProductionPhysicalAttempt(
        physical_attempt_id="attempt-jpx-1",
        logical_request_id=logical.logical_request_id,
        sequence_number=1,
        lease_generation=lease.generation,
        created_at=(now + timedelta(seconds=2)).isoformat(),
    )
    callback = JpxPhysicalTransport(
        intent,
        JpxHttpClientPolicy(connect_timeout_seconds=1, read_timeout_seconds=2),
        httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                headers={"Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
                content=body,
            )
        ),
    )
    result = ProductionTransportCoordinator(repository, callback, lambda: "permit-jpx-1").execute(
        intent.to_transport_request(logical.logical_request_id, attempt.physical_attempt_id),
        attempt,
        "reservation-jpx-1",
        GateKeys(
            egress="default-egress",
            provider="jpx-public-web",
            origin="www.jpx.co.jp",
            credential="anonymous",
            operation="current-listed-issues",
            task="task-1",
            role="source-acquisition",
        ),
        HierarchicalGatePolicy(
            limits={
                scope: GateLimit(
                    max_concurrency=1,
                    min_interval_seconds=60,
                    requests_per_window=3,
                    window_seconds=86400,
                )
                for scope in GateScope
            }
        ),
        TransportValidationPolicy(
            allowed_media_types=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",),
            allowed_encodings=("binary",),
        ),
        lease,
        now + timedelta(seconds=2),
        now + timedelta(seconds=3),
    )
    assert result.candidate is not None
    published = publish_source_candidate(
        RawArtifactPublisher(runs, repository),
        result.candidate,
        RawPublicationIntent(
            publication_id="publication-jpx-1",
            task_id="task-1",
            logical_request_id=logical.logical_request_id,
            physical_attempt_id=attempt.physical_attempt_id,
            source_id="jpx",
            operation="current-listed-issues",
            content_sha256=result.candidate.sha256,
            byte_count=len(body),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            encoding="binary",
            raw_schema_id="jpx-current-listed-issues-xlsx",
            raw_schema_version=1,
            publication_generation=lease.generation,
        ),
        lease,
        now + timedelta(seconds=4),
        now + timedelta(seconds=5),
        adapter,
    )
    assert published.value.snapshot_on == "2026-08-31"
    assert published.value.issues[0].eligibility == "eligible"
    stored = runs / "task-1" / published.reference.relative_path / "body.bin"
    assert stored.read_bytes() == body
    assert repository.committed_raw_references(logical.logical_request_id) == (published.reference,)
