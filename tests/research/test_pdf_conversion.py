"""Exercise correction boundaries and reuse without a model or Docling dependency."""

import json
from hashlib import sha256
from pathlib import Path
from types import ModuleType

import pytest

from stock_research_llm_orchestrator.research import pdf_conversion


@pytest.fixture
def hybrid(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Provide a deterministic package version for the cache test."""
    monkeypatch.setattr(pdf_conversion.importlib.metadata, "version", lambda name: "test")
    return pdf_conversion


def response(edits: list[dict[str, str]]) -> dict[str, object]:
    """Build one model result with separate concise navigation."""
    return {"edits": edits, "summary": "Overview", "contents": ["Purpose"], "limitations": []}


def test_correct_missing_text_and_table_without_rewriting_unchanged_content(hybrid: ModuleType) -> None:
    """Retain untouched content while applying body and table corrections."""
    files = {"document.md": "# Purpose\nRetained text\n", "tables/table-1.html": "<td>13</td>"}
    result = hybrid.apply_edits(
        files,
        response(
            [
                {"file": "document.md", "old": "# Purpose", "new": "# Purpose\nRecovered paragraph"},
                {"file": "tables/table-1.html", "old": "<td>13</td>", "new": "<td>136</td>"},
            ]
        ),
    )
    assert result["document.md"] == "# Purpose\nRecovered paragraph\nRetained text\n"
    assert result["tables/table-1.html"] == "<td>136</td>"
    assert files["document.md"] == "# Purpose\nRetained text\n"


@pytest.mark.parametrize(
    "edits",
    [
        [{"file": "../outside", "old": "abc", "new": "x"}],
        [{"file": "document.md", "old": "a", "new": "x"}],
        [{"file": "document.md", "old": "", "new": "x"}],
        [{"file": "document.md", "old": "abc", "new": "x"}, {"file": "document.md", "old": "bc", "new": "y"}],
    ],
)
def test_reject_ambiguous_or_overlapping_edits(hybrid: ModuleType, edits: list[dict[str, str]]) -> None:
    """Reject edits that cannot be applied unambiguously to supplied originals."""
    with pytest.raises(ValueError):
        hybrid.apply_edits({"document.md": "abc a"}, response(edits))


def test_reuse_completed_package_without_docling_or_llm(hybrid: ModuleType, tmp_path: Path) -> None:
    """Call the worker once and reject damaged completed packages."""
    source = tmp_path / "source.pdf"
    source.write_bytes(b"test PDF identity")
    identity = {
        "source_sha256": sha256(source.read_bytes()).hexdigest(),
        "pages": (1, 1),
        "model": "test",
        "effort": "medium",
        "versions": {"docling": "test", "docling-core": "test"},
        "implementation_sha256": sha256(Path(pdf_conversion.__file__ or "").read_bytes()).hexdigest(),
    }
    key = sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    root = tmp_path / "cache" / key
    draft = root / "draft"
    (draft / "pages").mkdir(parents=True)
    (draft / "pages/page-1.png").write_bytes(b"page")
    for name in ("assets", "figures", "tables"):
        (draft / name).mkdir()
    (draft / "document.md").write_text("Original")
    (root / "docling-complete.json").write_text("{}")
    calls: list[tuple[object, ...]] = []

    def invoke(*args: object) -> dict[str, object]:
        calls.append(args)
        return response([{"file": "document.md", "old": "Original", "new": "Corrected"}])

    reader, hit = hybrid.build_package(source, tmp_path / "cache", (1, 1), "test", "medium", invoke=invoke)
    assert not hit
    assert (reader / "document.md").read_text() == "Corrected"
    assert not (reader / "pages").exists()
    second, hit = hybrid.build_package(source, tmp_path / "cache", (1, 1), "test", "medium", invoke=invoke)
    assert hit and second == reader and len(calls) == 1
    (reader / "document.md").write_text("Changed")
    with pytest.raises(ValueError, match="integrity"):
        hybrid.build_package(source, tmp_path / "cache", (1, 1), "test", "medium", invoke=invoke)
