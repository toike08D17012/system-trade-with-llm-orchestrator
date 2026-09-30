"""Synthetic tests for the public EDINET code-list boundary."""

import io
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zipfile import ZipFile

import pytest

from stock_research_llm_orchestrator.preparation import edinet_code_list
from stock_research_llm_orchestrator.preparation.edinet_code_list import acquire_code_list, validate_code_list_receipt
from stock_research_llm_orchestrator.preparation.fx_evidence import read_bundle
from stock_research_llm_orchestrator.requests.transport import UntrustedTransportResponse
from stock_research_llm_orchestrator.sources.edinet.code_list import parse_code_list


def _zip(rows: list[str], *, as_of: str = "2026年09月29日現在") -> bytes:
    content = (
        f"ダウンロード実行日,{as_of},件数,{len(rows)}件\n"
        "ＥＤＩＮＥＴコード,提出者種別,上場区分,連結の有無,資本金,決算日,提出者名,提出者名（英字）,提出者名（ヨミ）,所在地,提出者業種,証券コード,提出者法人番号\n"
        + "\n".join(rows)
        + "\n"
    ).encode("cp932")
    stream = io.BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("EdinetcodeDlInfo.csv", content)
    return stream.getvalue()


TOYOTA = "E02144,,,,,,トヨタ自動車株式会社,,,,,72030,"


def test_parser_requires_unique_matching_issuer_and_nonfuture_date() -> None:
    """Reject ambiguous, wrong, and future-dated public lists."""
    assert parse_code_list(_zip([TOYOTA]), date(2026, 9, 30)).security_code == "72030"
    for body, received in (
        (_zip([TOYOTA, TOYOTA]), date(2026, 9, 30)),
        (_zip([TOYOTA.replace("72030", "99990")]), date(2026, 9, 30)),
        (_zip([TOYOTA]), date(2026, 9, 28)),
    ):
        with pytest.raises(ValueError):
            parse_code_list(body, received)


def test_acquisition_uses_one_coordinated_send_and_replays_raw(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One permit-bound send creates a replayable committed raw receipt."""
    body = _zip([TOYOTA])
    sends = []
    now = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
    ticks = iter(now + timedelta(milliseconds=i) for i in range(50))

    def fake_send(request: object, received: list[datetime], clock: object) -> UntrustedTransportResponse:
        sends.append(request)
        received.append(now + timedelta(milliseconds=6))
        return UntrustedTransportResponse(
            status_code=200,
            body=body,
            media_type="application/octet-stream",
            encoding="binary",
            final_origin="disclosure2dl.edinet-fsa.go.jp",
            redirected=False,
        )

    monkeypatch.setattr(edinet_code_list, "_send_once", fake_send)
    runs = tmp_path / "runs"
    runs.mkdir(mode=0o700)
    runtime = tmp_path / "runtime"
    root = Path(__file__).resolve().parents[2]
    receipt = acquire_code_list(
        config=root / "config",
        runtime=runtime,
        runs=runs,
        destination="code-list-test",
        task_id="code-list-task",
        clock=lambda: next(ticks),
    )
    assert len(sends) == 1
    assert receipt.observation.edinet_code == "E02144"
    assert validate_code_list_receipt(read_bundle(runs / "code-list-test"), runtime=runtime, runs=runs) == receipt
    raw = runs / receipt.raw_reference.task_id / receipt.raw_reference.relative_path / "body.bin"
    raw.write_bytes(body + b"altered")
    with pytest.raises(ValueError, match="raw_mismatch"):
        validate_code_list_receipt(read_bundle(runs / "code-list-test"), runtime=runtime, runs=runs)
