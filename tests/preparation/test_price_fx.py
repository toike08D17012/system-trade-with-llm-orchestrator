"""Offline conversion from complete retained synthetic source evidence."""

import json
import socket
from datetime import date
from decimal import Decimal, localcontext
from pathlib import Path

import httpx
import pytest

from stock_research_llm_orchestrator.preparation.fx_evidence import acquire_fx_evidence, read_bundle
from stock_research_llm_orchestrator.preparation.price_fx import join_price_fx, validate_price_fx

from .test_fx_evidence import FIXTURE, NOW
from .test_price_acceptance import AcceptanceScenario


def prepare(root: Path, *, pending: bool = False) -> None:
    """Construct accepted synthetic price and FX evidence with independent missing-value cases."""
    scenario = AcceptanceScenario(root)
    if pending:
        scenario.edit_prices("negative")
    scenario.accept()
    days = json.loads((root / "calendar.json").read_bytes())["dates"]
    body = json.loads(FIXTURE.read_bytes())
    body["PARAMETER"].update(STARTDATE="202309", ENDDATE="202609")
    series = body["RESULTSET"][0]
    series["VALUES"] = {"SURVEY_DATES": [int(day.replace("-", "")) for day in days], "VALUES": [150] * len(days)}
    # Keep independent absence, explicit null and invalid value cases.
    series["VALUES"]["SURVEY_DATES"].pop(3)
    series["VALUES"]["VALUES"].pop(3)
    series["VALUES"]["VALUES"][1] = None
    series["VALUES"]["VALUES"][2] = 0
    series["VALUES"]["SURVEY_DATES"].insert(0, 20230901)
    series["VALUES"]["VALUES"].insert(0, 145)
    acquire_fx_evidence(
        config=Path("config"),
        runtime=root / ".fx-runtime",
        runs=root / "runs",
        destination="fx",
        task_id="task-fx",
        start=date(2023, 9, 26),
        end=date(2026, 9, 25),
        clock=lambda: NOW,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200, headers={"Content-Type": "application/json"}, content=json.dumps(body).encode()
            )
        ),
    )


@pytest.fixture(autouse=True)
def deny_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail immediately if offline conversion attempts a physical connection."""

    def reject(*args: object, **kwargs: object) -> None:
        pytest.fail("offline join attempted network I/O")

    monkeypatch.setattr(socket.socket, "connect", reject)
    monkeypatch.setattr(socket, "getaddrinfo", reject)


def test_join_reproduces_decimals_missing_reasons_and_unchanged_inputs(tmp_path: Path) -> None:
    """Fix arithmetic context independently from the caller and retain original source bytes."""
    prepare(tmp_path)
    original = read_bundle(tmp_path / "accepted")
    with localcontext() as context:
        context.prec = 3
        result = join_price_fx(tmp_path / "accepted", tmp_path / "runs/fx", tmp_path / "joined")
    assert result.status == "incomplete"
    assert result.rows[0].close_usd == Decimal("0.7033333333333333333333333333")
    assert [row.missing_reasons for row in result.rows[1:4]] == [("fx_null",), ("fx_invalid",), ("fx_date_missing",)]
    assert result.excluded_fx_dates["2023-09-01"] == "outside_fixed_period"
    assert read_bundle(tmp_path / "accepted") == original
    files = read_bundle(tmp_path / "joined")
    validate_price_fx(files)
    files["index.json"] = files["index.json"].replace(b'"converted_count":', b'"converted_count": 0, "forged":')
    with pytest.raises(ValueError):
        validate_price_fx(files)


def test_pending_prices_are_not_joinable(tmp_path: Path) -> None:
    """Require an accepted price receipt even when its contents are internally consistent."""
    prepare(tmp_path, pending=True)
    with pytest.raises(ValueError, match="requires_accepted"):
        join_price_fx(tmp_path / "accepted", tmp_path / "runs/fx", tmp_path / "joined")


def test_existing_overlap_symlink_and_modified_inputs_are_rejected(tmp_path: Path) -> None:
    """Reject unsafe destinations and tampered source bundles before publication."""
    prepare(tmp_path)
    price, fx = tmp_path / "accepted", tmp_path / "runs/fx"
    with pytest.raises(ValueError, match="overlap"):
        join_price_fx(price, fx, price / "joined")
    output = tmp_path / "joined"
    output.mkdir()
    with pytest.raises(FileExistsError):
        join_price_fx(price, fx, output)
    output.rmdir()
    (fx / "alias").symlink_to(fx / "body.bin")
    with pytest.raises(ValueError):
        join_price_fx(price, fx, output)
    (fx / "alias").unlink()
    (fx / "body.bin").write_bytes(b"{}")
    with pytest.raises(ValueError):
        join_price_fx(price, fx, output)
    assert not output.exists()
