"""Run the hybrid converter in a subprocess and collect its reusable reader files."""

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def build_pdf_reader(raw: bytes, cache: Path) -> dict[str, bytes]:
    """Convert once using the current Python environment, or record why it was unavailable."""
    if importlib.util.find_spec("docling") is None:
        return {"status.json": b'{"status":"unavailable","reason":"docling_environment_missing"}'}
    with tempfile.TemporaryDirectory(prefix="research-pdf-") as temporary:
        source = Path(temporary) / "source.pdf"
        source.write_bytes(raw)
        command = [
            sys.executable,
            "-m",
            "stock_research_llm_orchestrator.research.pdf_conversion",
            str(source),
            "--cache",
            str(cache.resolve()),
        ]
        try:
            completed = subprocess.run(command, capture_output=True, check=True, timeout=900)
            result = json.loads(completed.stdout.splitlines()[-1])
            reader = Path(result["reader"])
            return {
                path.relative_to(reader).as_posix(): path.read_bytes() for path in reader.rglob("*") if path.is_file()
            }
        except (OSError, ValueError, IndexError, subprocess.SubprocessError) as error:
            return {"status.json": json.dumps({"status": "failed", "reason": type(error).__name__}).encode()}
