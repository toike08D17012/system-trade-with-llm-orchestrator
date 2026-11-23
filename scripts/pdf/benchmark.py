"""Compare selected physical PDF pages with isolated local conversion engines."""

import argparse
import importlib.metadata
import json
import os
import platform
import re
import resource
import sys
import time
import unicodedata
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from stock_research_llm_orchestrator.research.pdf_artifacts import convert_and_export


REPOSITORY = Path(__file__).resolve().parents[2]
THREADS = 4


def _write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _peak_rss() -> dict[str, object]:
    native = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    unit = "bytes" if sys.platform == "darwin" else "KiB"
    return {
        "native_value": native,
        "native_unit": unit,
        "bytes": int(native if unit == "bytes" else native * 1024),
        "scope": "cumulative process high-water mark; not per-trial memory",
    }


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def _anchors(text: str, anchors: list[dict[str, str]]) -> list[dict[str, object]]:
    normalized = _normalize(text)
    return [
        {"label": item["label"], "text": item["text"], "present": _normalize(item["text"]) in normalized}
        for item in anchors
    ]


def _file_inventory(root: Path) -> list[dict[str, object]]:
    return [
        {"path": str(path.relative_to(root)), "bytes": path.stat().st_size}
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def _pypdf_trial(source: Path, pages: tuple[int, int], root: Path) -> dict[str, object]:
    from pypdf import PdfReader

    started = time.perf_counter()
    reader = PdfReader(source, strict=True)
    if reader.is_encrypted and not reader.decrypt(""):
        raise ValueError("PDF requires a password")
    text = "\n\n".join(
        reader.pages[number - 1].extract_text(extraction_mode="layout") for number in range(pages[0], pages[1] + 1)
    )
    # Apply exactly the existing production extractor's line normalization.
    cleaned = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    converted = time.perf_counter()
    (root / "document.txt").write_text(cleaned, encoding="utf-8")
    (root / "document.md").write_text(cleaned, encoding="utf-8")
    finished = time.perf_counter()
    return {
        "text": cleaned,
        "engine_status": "success" if cleaned else "empty_text",
        "engine_errors": [],
        "conversion_seconds": converted - started,
        "export_seconds": finished - converted,
        "markdown_characters": len(cleaned),
        "markdown_utf8_bytes": len(cleaned.encode()),
        "table_count": None,
        "picture_count": None,
        "heading_count": None,
        "rendered_physical_pages": [],
        "asset_references": [],
        "missing_local_assets": 0,
        "structure_note": "Production-style pypdf text extraction has no table, picture, or heading structure.",
    }


def main() -> int:
    """Run independent selected-page conversions and persist every completed trial."""
    process_started = time.perf_counter()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--engine", choices=("docling", "pypdf"), required=True)
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument(
        "--ocr", action="store_true", help="Enable Docling OCR; optional engine support must be installed."
    )
    parser.add_argument("--artifacts-path", type=Path, help="Use predownloaded Docling model artifacts.")
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be positive")
    if args.ocr and args.engine != "docling":
        parser.error("--ocr is only available for Docling")
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    results_path = args.output / "results.json"
    if results_path.exists():
        parser.error("output already contains results.json; choose a new directory")
    manifest_bytes = args.manifest.read_bytes()
    manifest = json.loads(manifest_bytes)
    for name in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ):
        os.environ[name] = str(THREADS)
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "engine": args.engine,
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "cpu_threads_requested": THREADS,
        "device": "cpu",
        "ocr": args.ocr,
        "image_scale": 1.0 if args.engine == "docling" else None,
        "repeat": args.repeat,
        "manifest_sha256": sha256(manifest_bytes).hexdigest(),
        "versions": {name: importlib.metadata.version(name) for name in ("docling", "docling-core", "pypdf", "torch")},
        "artifacts_path": str(args.artifacts_path.resolve()) if args.artifacts_path else None,
        "memory_note": "ru_maxrss is process-cumulative; Darwin reports bytes, Linux reports KiB.",
        "anchor_note": "NFKC/whitespace-normalized substring presence; not numerical or table accuracy.",
        "documents": [],
    }
    _write_json(results_path, report)
    converter = None
    try:
        import_started = time.perf_counter()
        from pypdf import PdfReader

        if args.engine == "docling":
            import torch
            from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption

        report["import_seconds"] = time.perf_counter() - import_started
        setup_started = time.perf_counter()
        if args.engine == "docling":
            torch.set_num_threads(THREADS)
            torch.set_num_interop_threads(THREADS)
            options = PdfPipelineOptions(
                do_ocr=args.ocr,
                do_table_structure=True,
                generate_picture_images=True,
                generate_page_images=True,
                images_scale=1.0,
                accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU, num_threads=THREADS),
            )
            if args.artifacts_path is not None:
                options.artifacts_path = args.artifacts_path.resolve()
            converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
        report["converter_setup_seconds"] = time.perf_counter() - setup_started
        initialization_started = time.perf_counter()
        if converter is not None:
            print("Initializing Docling pipeline; model loading/downloads are outside conversion timings.", flush=True)
            converter.initialize_pipeline(InputFormat.PDF)
        report["pipeline_initialization_seconds"] = time.perf_counter() - initialization_started
        report["initialization_note"] = (
            "Includes model loading and any uncached downloads; conversions follow explicit initialization."
        )
        report["process_to_ready_seconds"] = time.perf_counter() - process_started
        report["peak_rss_after_setup"] = _peak_rss()
        _write_json(results_path, report)
    except Exception as error:
        report["setup_error"] = {"type": type(error).__name__, "message": str(error)}
        report["process_elapsed_seconds"] = time.perf_counter() - process_started
        report["peak_rss_after_setup"] = _peak_rss()
        _write_json(results_path, report)
        print(f"Setup failed: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        return 1
    any_failure = False
    conversion_count = 0
    for entry in manifest["documents"]:
        result = {"id": entry["id"], "source_url": entry["source_url"], "trials": []}
        report["documents"].append(result)
        validation_started = time.perf_counter()
        try:
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", entry["id"]) is None:
                raise ValueError("Unsafe document id")
            source = (REPOSITORY / entry["path"]).resolve()
            body = source.read_bytes()
            digest = sha256(body).hexdigest()
            if digest != entry["sha256"]:
                raise ValueError("Source SHA-256 mismatch")
            reader = PdfReader(source, strict=True)
            if reader.is_encrypted and not reader.decrypt(""):
                raise ValueError("PDF requires a password")
            start, end = entry["pages"]
            if not (isinstance(start, int) and isinstance(end, int) and 1 <= start <= end <= len(reader.pages)):
                raise ValueError("Invalid inclusive physical page selection")
            result.update(
                {
                    "source_path": entry["path"],
                    "source_sha256": digest,
                    "source_bytes": len(body),
                    "total_physical_pages": len(reader.pages),
                    "selected_physical_pages_inclusive": [start, end],
                    "selected_page_count": end - start + 1,
                    "unevaluated_page_count": len(reader.pages) - (end - start + 1),
                    "validation_seconds": time.perf_counter() - validation_started,
                }
            )
        except Exception as error:
            result["validation_error"] = {"type": type(error).__name__, "message": str(error)}
            any_failure = True
            _write_json(results_path, report)
            print(f"Validation failed for {entry['id']}: {error}", flush=True)
            continue
        for number in range(1, args.repeat + 1):
            conversion_count += 1
            root = args.output / entry["id"] / f"trial-{number}"
            root.mkdir(parents=True, exist_ok=False)
            if args.engine == "docling":
                phase = "first_conversion_after_initialization" if conversion_count == 1 else "model_warm_conversion"
            else:
                phase = "first_conversion_for_document" if number == 1 else "repeat_conversion_with_os_cache"
            trial = {
                "trial": number,
                "phase": phase,
                "converter_call_index": conversion_count,
                "first_conversion_for_this_document": number == 1,
                "output_path": str(root.relative_to(args.output)),
                "peak_rss_before": _peak_rss(),
            }
            started = time.perf_counter()
            print(f"Starting {args.engine} {entry['id']} pages {start}-{end} trial {number}/{args.repeat}", flush=True)
            try:
                if converter is not None:
                    metrics = convert_and_export(converter, source, (start, end), root)
                else:
                    metrics = _pypdf_trial(source, (start, end), root)
                text = metrics.pop("text")
                trial.update(metrics)
                trial.update(
                    {
                        "text_characters": len(text),
                        "text_utf8_bytes": len(text.encode()),
                        "anchors": _anchors(text, entry.get("anchors", [])),
                        "status": "success" if metrics["engine_status"] == "success" else "partial_or_failed",
                    }
                )
                any_failure |= trial["status"] != "success"
            except Exception as error:
                trial.update({"status": "error", "error": {"type": type(error).__name__, "message": str(error)}})
                any_failure = True
            trial["total_trial_seconds"] = time.perf_counter() - started
            trial["peak_rss_after"] = _peak_rss()
            trial["files"] = _file_inventory(root)
            trial["output_total_bytes"] = sum(item["bytes"] for item in trial["files"])
            result["trials"].append(trial)
            report["process_elapsed_seconds"] = time.perf_counter() - process_started
            _write_json(results_path, report)
            print(
                f"Finished {entry['id']} trial {number}: {trial['status']} ({trial['total_trial_seconds']:.3f}s)",
                flush=True,
            )
    report["completed_at"] = datetime.now(UTC).isoformat()
    report["process_elapsed_seconds"] = time.perf_counter() - process_started
    _write_json(results_path, report)
    return int(any_failure)


if __name__ == "__main__":
    raise SystemExit(main())
