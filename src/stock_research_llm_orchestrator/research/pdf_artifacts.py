"""Export Docling conversions and package their portable assets."""

import json
import re
import shutil
import time
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlparse


if TYPE_CHECKING:
    from docling.document_converter import DocumentConverter
    from docling_core.types.doc import DoclingDocument


def _asset_references(markdown: str, document_json: dict[str, object], root: Path) -> list[dict[str, object]]:
    references = set(re.findall(r"!\[[^\]]*\]\(([^)]+)\)", markdown))

    def collect(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "uri" and isinstance(child, str) and not child.startswith("data:"):
                    references.add(child)
                else:
                    collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(document_json)
    assets = []
    for reference in sorted(references):
        parsed = urlparse(reference)
        if parsed.scheme not in {"", "file"}:
            assets.append({"reference": reference, "local": False, "exists": None})
            continue
        path = Path(unquote(parsed.path))
        resolved = path if path.is_absolute() else root / path
        assets.append({"reference": reference, "local": True, "exists": resolved.is_file()})
    return assets


def _export_docling(document: DoclingDocument, root: Path) -> dict[str, object]:
    from docling_core.types.doc import ImageRefMode

    assets = root / "assets"
    document.save_as_markdown(root / "document.md", artifacts_dir=assets, image_mode=ImageRefMode.REFERENCED)
    document.save_as_json(
        root / "document.json", artifacts_dir=assets, image_mode=ImageRefMode.REFERENCED, ensure_ascii=False
    )
    text = document.export_to_text()
    (root / "document.txt").write_text(text, encoding="utf-8")
    tables = root / "tables"
    figures = root / "figures"
    pages = root / "pages"
    for directory in (tables, figures, pages):
        directory.mkdir()
    for index, table in enumerate(document.tables, 1):
        (tables / f"table-{index}.html").write_text(table.export_to_html(doc=document), encoding="utf-8")
    missing_figures = []
    for index, picture in enumerate(document.pictures, 1):
        image = picture.get_image(document)
        if image is None:
            missing_figures.append(index)
        else:
            image.save(figures / f"figure-{index}.png")
    rendered_pages = []
    for number, page in sorted(document.pages.items()):
        image = page.image.pil_image if page.image is not None else None
        if image is not None:
            image.save(pages / f"page-{number}.png")
            rendered_pages.append(number)
    markdown = (root / "document.md").read_text(encoding="utf-8")
    structured = json.loads((root / "document.json").read_text(encoding="utf-8"))
    references = _asset_references(markdown, structured, root)
    return {
        "text": text,
        "markdown_characters": len(markdown),
        "markdown_utf8_bytes": len(markdown.encode()),
        "table_count": len(document.tables),
        "picture_count": len(document.pictures),
        "heading_count": sum(item.label.value == "section_header" for item in document.texts),
        "rendered_physical_pages": rendered_pages,
        "missing_figure_images": missing_figures,
        "asset_references": references,
        "missing_local_assets": sum(item["local"] is True and item["exists"] is False for item in references),
    }


def convert_and_export(
    converter: DocumentConverter, source: Path, pages: tuple[int, int], root: Path
) -> dict[str, object]:
    """Convert and export the selected physical pages."""
    started = time.perf_counter()
    result = converter.convert(source, page_range=pages, raises_on_error=False)
    converted = time.perf_counter()
    metrics = _export_docling(result.document, root)
    finished = time.perf_counter()
    return {
        **metrics,
        "engine_status": result.status.value,
        "engine_errors": [str(error) for error in result.errors],
        "conversion_seconds": converted - started,
        "export_seconds": finished - converted,
    }


def package_trial(source: Path, target: Path, document: dict, versions: dict) -> dict:
    """Copy one measured result and make its local image references portable."""
    if target.exists():
        raise ValueError("Output already exists")
    shutil.copytree(source, target)

    def relative(value: str) -> str:
        path = Path(value)
        return path.resolve().relative_to(source.resolve()).as_posix() if path.is_absolute() else value

    markdown_path = target / "document.md"
    markdown = markdown_path.read_text()
    markdown = re.sub(r"(!\[[^\]]*\]\()([^\n)]*)(\))", lambda match: match[1] + relative(match[2]) + match[3], markdown)
    markdown_path.write_text(markdown)

    def rewrite(value: object) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("uri"), str) and Path(value["uri"]).is_absolute():
                value["uri"] = relative(value["uri"])
            for child in value.values():
                rewrite(child)
        elif isinstance(value, list):
            for child in value:
                rewrite(child)

    structured_path = target / "document.json"
    structured = json.loads(structured_path.read_text())
    rewrite(structured)
    structured_path.write_text(json.dumps(structured, ensure_ascii=False, indent=2) + "\n")
    references: list[str] = re.findall(r"!\[[^\]]*\]\(([^\n)]*)\)", markdown)

    def collect(value: object) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("uri"), str):
                references.append(value["uri"])
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(structured)
    references = sorted(set(references))
    for reference in references:
        path = Path(reference)
        if path.is_absolute() or ".." in path.parts or not (target / path).is_file():
            raise ValueError(f"Nonportable or missing image: {reference}")
    manifest = {
        "schema_version": 1,
        "document_id": document["id"],
        "source": {key: document[key] for key in ("source_url", "source_sha256", "selected_physical_pages_inclusive")},
        "conversion": {
            "engine": "docling",
            "versions": versions,
            "status": "converted_navigation_not_accuracy_certification",
        },
        "note": "Paths normalized after measurement; timings remain the unmodified engine-export measurements.",
        "relative_image_references": references,
        "files": {
            path.relative_to(target).as_posix(): {
                "bytes": path.stat().st_size,
                "sha256": sha256(path.read_bytes()).hexdigest(),
            }
            for path in sorted(target.rglob("*"))
            if path.is_file()
        },
        "markdown_characters": len(markdown),
    }
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest
