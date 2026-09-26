"""Task-bound offline requirements, source reuse and preparation publication."""

import csv
import io
import json
import shutil
import socket
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, localcontext
from hashlib import sha256
from pathlib import Path

import httpx
import pytest

from stock_research_llm_orchestrator.preparation.dukascopy_fx import acquire_dukascopy_fx
from stock_research_llm_orchestrator.preparation.fx_evidence import read_bundle
from stock_research_llm_orchestrator.preparation.price_fx_run import (
    PriceFxPreparationManifest,
    prepare_price_fx_run,
    validate_price_fx_run,
)
from stock_research_llm_orchestrator.preparation.price_fx_run_cli import main
from stock_research_llm_orchestrator.preparation.run_requirements import calendar_inputs, resolve_requirements
from stock_research_llm_orchestrator.preparation.task_input import create_human_selected_task

from .test_price_acceptance import AcceptanceScenario, _rehash


CHECKED = datetime.fromisoformat("2026-09-27T12:00:00+09:00")


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """No market data is fetched by the new preparation path."""

    def reject(*args: object, **kwargs: object) -> None:
        pytest.fail("unexpected network access")

    monkeypatch.setattr(socket.socket, "connect", reject)
    monkeypatch.setattr(socket, "getaddrinfo", reject)


@pytest.fixture(scope="module")
def sample(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build reusable synthetic evidence using real validators and mock HTTP."""
    root = tmp_path_factory.mktemp("run-sample")
    scenario = AcceptanceScenario(root)
    scenario.accept()
    calendar = json.loads((root / "calendar.json").read_bytes())
    days = calendar["dates"]
    current = [datetime(2026, 9, 26, 12, tzinfo=UTC)]

    def sleep(seconds: float) -> None:
        current[0] += timedelta(seconds=seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        year = 2026 if request.url.query else int(request.url.path.rsplit("/", 1)[1])
        timestamps = [
            int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp()) * 1000
            for day in days
            if day.startswith(str(year))
        ]
        previous = int(datetime(year, 1, 1, tzinfo=UTC).timestamp()) * 1000
        deltas = []
        for timestamp in timestamps:
            deltas.append((timestamp - previous) // 86400000)
            previous = timestamp
        raw = dict(
            timestamp=int(datetime(year, 1, 1, tzinfo=UTC).timestamp()) * 1000,
            shift=86400000,
            multiplier=0.001,
            open=150,
            high=151,
            low=149,
            close=150,
            times=deltas,
            opens=[0] * len(deltas),
            highs=[0] * len(deltas),
            lows=[0] * len(deltas),
            closes=[0] * len(deltas),
            volumes=[1] * len(deltas),
        )
        return httpx.Response(200, json=raw)

    acquire_dukascopy_fx(
        config=Path("config"),
        runtime=root / "runtime",
        runs=root / "fx",
        destination="accepted",
        task_id="fx-task",
        start=date(2023, 9, 26),
        end=date(2026, 9, 25),
        clock=lambda: current[0],
        sleep=sleep,
        transport=httpx.MockTransport(handler),
    )
    # Explicit synthetic coverage, not a claim about newly retrieved JPX dates.
    calendar["period"]["end_date"] = "2026-09-30"
    calendar["dates"] += ["2026-09-28", "2026-09-29", "2026-09-30"]
    scenario.update_calendar(json.dumps(calendar).encode())
    return root


@pytest.fixture
def case(sample: Path, tmp_path: Path) -> Path:
    """Each test mutates an isolated copy, preserving the reusable source fixture."""
    for name in ("accepted", "prepared", "fx"):
        shutil.copytree(sample / name, tmp_path / name)
    for name in ("calendar.json", "metadata.json", "note.md"):
        shutil.copyfile(sample / name, tmp_path / name)
    return tmp_path


def run(
    root: Path, *, checked: datetime = CHECKED, prices: str = "accepted", code: str = "7203"
) -> PriceFxPreparationManifest:
    """Exercise the public preparation API with a fresh destination task."""
    task = create_human_selected_task(
        code, 1, mic="XTKS", task_id_factory=lambda: "new-task", accepted_at_factory=lambda: checked
    )
    return prepare_price_fx_run(
        task=task,
        checked_at=checked,
        config=Path("config"),
        calendar=root / "calendar.json",
        calendar_metadata=root / "metadata.json",
        research_note=root / "note.md",
        prices=root / prices,
        fx=root / "fx/accepted",
        output=root / "result",
    )


def calendar_files(root: Path) -> dict[str, bytes]:
    """Load hash-bound synthetic calendar inputs."""
    return calendar_inputs(Path("config"), root / "calendar.json", root / "metadata.json", root / "note.md")


@pytest.mark.parametrize(
    "checked,price,fx",
    [
        ("2026-09-27T12:00:00+09:00", "2026-09-25", "2026-09-25"),
        ("2026-09-28T15:29:59+09:00", "2026-09-25", "2026-09-25"),
        ("2026-09-28T15:30:00+09:00", "2026-09-28", "2026-09-25"),
        ("2026-09-29T08:59:59+09:00", "2026-09-28", "2026-09-25"),
        ("2026-09-29T09:00:00+09:00", "2026-09-28", "2026-09-28"),
    ],
)
def test_date_boundaries(case: Path, checked: str, price: str, fx: str) -> None:
    """Keep source arrival separate from schedule and UTC candle completion."""
    result = resolve_requirements(calendar_files(case), checked)
    assert result.required_price_end == price
    assert result.required_fx_end == fx
    assert result.status == "resolved"


def test_ready_reuse_and_replay(case: Path) -> None:
    """Retain exact inputs and reproduce decimals independently of caller context."""
    before = read_bundle(case / "accepted")
    with localcontext() as ctx:
        ctx.prec = 3
        result = run(case)
    assert result.status == "ready_with_limitations"
    assert result.reused_from_task_id is not None
    assert result.reuse_revalidated
    assert result.converted_count == len(result.requirements.fx_dates)
    saved = read_bundle(case / "result")
    assert Decimal(json.loads(saved["conversion.json"])[0]["close_usd"]) == Decimal("0.7033333333333333333333333333")
    validate_price_fx_run(saved)
    assert read_bundle(case / "accepted") == before
    saved["conversion.json"] += b" "
    with pytest.raises(ValueError):
        validate_price_fx_run(saved)


def test_old_terminal_does_not_lower_requirement(case: Path) -> None:
    """Missing Monday data cannot make Friday the required terminal date."""
    result = run(case, checked=datetime.fromisoformat("2026-09-29T10:00:00+09:00"))
    assert result.status == "pending"
    assert result.requirements.required_price_end == "2026-09-28"
    assert result.price.missing_dates == ("2026-09-28",)
    assert result.fx.missing_dates == ("2026-09-28",)


def test_calendar_coverage_pending(case: Path) -> None:
    """An old calendar cannot prove that later dates are holidays."""
    files = calendar_files(case)
    original = json.loads(files["calendar.json"])
    original["period"]["end_date"] = "2026-09-25"
    original["dates"] = original["dates"][:-3]
    files["calendar.json"] = json.dumps(original).encode()
    meta = json.loads(files["calendar-metadata.json"])
    meta["calendar_sha256"] = sha256(files["calendar.json"]).hexdigest()
    files["calendar-metadata.json"] = json.dumps(meta).encode()
    result = resolve_requirements(files, CHECKED.isoformat())
    assert result.required_price_end is None
    assert "calendar_does_not_cover_check_date" in result.reasons


def test_same_day_price_and_unfinished_fx(case: Path) -> None:
    """Accept post-close JPY while leaving the same UTC date unconverted."""
    folder = case / "prepared"
    raw = list(csv.reader(io.StringIO((folder / "prices.csv").read_text())))
    row = raw[-1].copy()
    row[0] = "2026-09-28 00:00:00+09:00"
    raw.append(row)
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(raw)
    (folder / "prices.csv").write_text(out.getvalue())
    normalized = json.loads((folder / "normalized.json").read_text())
    extra = normalized["rows"][-1].copy()
    extra["on"] = "2026-09-28"
    normalized["rows"].append(extra)
    normalized["requested_period"]["end_date"] = "2026-09-28"
    (folder / "normalized.json").write_text(json.dumps(normalized))
    history = json.loads((folder / "history-metadata.json").read_text())
    history["arguments"]["end"] = "2026-09-29"
    history["retrieved_at"] = "2026-09-28T16:00:00+09:00"
    (folder / "history-metadata.json").write_text(json.dumps(history))
    index = json.loads((folder / "index.json").read_text())
    index["prepared_at"] = history["retrieved_at"]
    (folder / "index.json").write_text(json.dumps(index))
    _rehash(folder)
    result = run(case, checked=datetime.fromisoformat("2026-09-28T17:00:00+09:00"), prices="prepared")
    assert result.price.status == "ready_with_limitations", result.price
    assert result.fx.status == "ready_with_limitations"
    assert result.requirements.required_price_end == "2026-09-28"
    assert result.requirements.required_fx_end == "2026-09-25"
    last = json.loads((case / "result/conversion.json").read_text())[-1]
    assert last["close_jpy"] is not None and last["close_usd"] is None
    assert last["reasons"] == ["fx_interval_unfinished"]


def test_identity_and_unsafe_paths(case: Path) -> None:
    """Reject another security before publishing any evidence."""
    with pytest.raises(ValueError, match="security_mismatch"):
        run(case, code="6758")
    assert not (case / "result").exists()
    (case / "fx/accepted/link").symlink_to(case / "fx/accepted/index.json")
    with pytest.raises(ValueError):
        run(case)


def test_cli_plan_and_validate(case: Path) -> None:
    """Plans retain their original evaluation time and task binding."""
    code = main(
        [
            "plan",
            "--task",
            str(case / "prepared/task.json"),
            "--calendar",
            str(case / "calendar.json"),
            "--calendar-metadata",
            str(case / "metadata.json"),
            "--research-note",
            str(case / "note.md"),
            "--checked-at",
            CHECKED.isoformat(),
            "--output",
            str(case / "plan"),
        ]
    )
    assert code == 0
    assert main(["validate", "--input", str(case / "plan")]) == 0


@pytest.mark.parametrize("checked", ["2026-09-26T20:00:00+09:00", "2026-12-27T12:00:00+09:00"])
def test_profile_effective_range(case: Path, checked: str) -> None:
    """Never apply the current-hours profile outside its approved evaluation interval."""
    result = resolve_requirements(calendar_files(case), checked)
    assert result.status == "pending"
    assert "market_profile_outside_effective_period" in result.reasons


def test_calendar_holiday_and_leap_year(case: Path) -> None:
    """Use supplied session dates and calendar-year arithmetic, not weekday guesses."""
    from stock_research_llm_orchestrator.sources.yfinance.normalization import three_year_start

    files = calendar_files(case)
    calendar = json.loads(files["calendar.json"])
    calendar["dates"].remove("2026-09-28")
    files["calendar.json"] = json.dumps(calendar).encode()
    meta = json.loads(files["calendar-metadata.json"])
    meta["calendar_sha256"] = sha256(files["calendar.json"]).hexdigest()
    files["calendar-metadata.json"] = json.dumps(meta).encode()
    req = resolve_requirements(files, "2026-09-28T18:00:00+09:00")
    assert req.required_price_end == "2026-09-25"
    assert three_year_start(date(2028, 2, 29)) == date(2025, 2, 28)


@pytest.mark.parametrize("fault", ["hash", "future", "disabled", "start_coverage"])
def test_bad_calendar_or_profile(case: Path, fault: str) -> None:
    """Distinguish incomplete schedule coverage from untrusted input bytes."""
    files = calendar_files(case)
    if fault == "hash":
        files["research-note.md"] += b"changed"
    elif fault == "future":
        meta = json.loads(files["calendar-metadata.json"])
        meta["checked_at"] = "2026-10-01T00:00:00Z"
        files["calendar-metadata.json"] = json.dumps(meta).encode()
    elif fault == "disabled":
        files["market-profile.yaml"] = files["market-profile.yaml"].replace(
            b"enabled_for_runtime_date_resolution: true", b"enabled_for_runtime_date_resolution: false"
        )
    else:
        calendar = json.loads(files["calendar.json"])
        calendar["period"]["start_date"] = "2024-01-01"
        calendar["dates"] = [day for day in calendar["dates"] if day >= "2024-01-01"]
        files["calendar.json"] = json.dumps(calendar).encode()
        meta = json.loads(files["calendar-metadata.json"])
        meta["calendar_sha256"] = sha256(files["calendar.json"]).hexdigest()
        files["calendar-metadata.json"] = json.dumps(meta).encode()
        result = resolve_requirements(files, CHECKED.isoformat())
        assert "calendar_does_not_cover_required_start" in result.reasons
        return
    with pytest.raises(ValueError):
        resolve_requirements(files, CHECKED.isoformat())


def test_source_suspension_is_pending(case: Path) -> None:
    """A newly suspended approval prevents reuse without changing historical acceptance."""
    import yaml

    from stock_research_llm_orchestrator.preparation.price_fx_run import _evaluate_run

    run(case)
    files = read_bundle(case / "result")
    source = "sources/dukascopy/approval.yaml"
    a = yaml.safe_load(files[source])
    a["status"] = "suspended"
    a["online_use_allowed"] = False
    files[source] = yaml.safe_dump(a).encode()
    p = yaml.safe_load(files["sources/dukascopy/profile.yaml"])
    p["source_approval_status"] = "suspended"
    p["enabled"] = False
    p["source_approval_reference"]["sha256"] = sha256(files[source]).hexdigest()
    files["sources/dukascopy/profile.yaml"] = yaml.safe_dump(p).encode()
    manifest, _ = _evaluate_run(files)
    assert manifest.status == "pending"
    assert manifest.fx.reasons == ("dukascopy_reuse_policy_unavailable",)
    assert manifest.price.status == "ready_with_limitations"


def test_publication_failure_leaves_no_output(case: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed final rename cannot leave a successful visible slice."""
    import stock_research_llm_orchestrator.preparation.storage as storage

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("simulated rename failure")

    monkeypatch.setattr(storage.os, "rename", fail)
    with pytest.raises(OSError):
        run(case)
    assert not (case / "result").exists()


def test_saved_references_and_generation_tampering(case: Path) -> None:
    """Every generated reference and generation is part of replay validation."""
    run(case)
    original = read_bundle(case / "result")
    for field in ("publication_generation", "task_id"):
        files = dict(original)
        manifest = json.loads(files["manifest.json"])
        manifest[field] = "forged"
        files["manifest.json"] = json.dumps(manifest).encode()
        with pytest.raises(ValueError):
            validate_price_fx_run(files)
    with pytest.raises(FileExistsError):
        run(case)


def test_cli_prepare_and_no_network_option(case: Path) -> None:
    """The prepare command persists a replayable slice and exposes no network switch."""
    args = [
        "prepare",
        "--task",
        str(case / "prepared/task.json"),
        "--calendar",
        str(case / "calendar.json"),
        "--calendar-metadata",
        str(case / "metadata.json"),
        "--research-note",
        str(case / "note.md"),
        "--checked-at",
        CHECKED.isoformat(),
        "--prices",
        str(case / "accepted"),
        "--fx",
        str(case / "fx/accepted"),
        "--output",
        str(case / "cli-result"),
    ]
    assert main(args) == 0
    assert main(["validate", "--input", str(case / "cli-result")]) == 0
    assert main(args) == 1


def test_competing_publication_rejected(case: Path) -> None:
    """Concurrent publishers cannot stage under the same exclusively locked root."""
    import fcntl
    import os

    descriptor = os.open(case, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="root_busy"):
            run(case)
        assert not (case / "result").exists()
    finally:
        os.close(descriptor)
