"""Package measured first-trial outputs without rerunning document conversion."""

import argparse
import json
from pathlib import Path

from stock_research_llm_orchestrator.research.pdf_artifacts import package_trial


def main() -> None:
    """Create one portable example per measured document and check all image references."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    results = json.loads(args.results.read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    summary = []
    for document in results["documents"]:
        source = Path(document["trials"][0]["output_path"])
        if not source.is_absolute():
            source = args.results.parent / source
        manifest = package_trial(source, args.output / document["id"], document, results["versions"])
        summary.append(
            {
                "document_id": document["id"],
                "markdown_characters": manifest["markdown_characters"],
                "image_references": len(manifest["relative_image_references"]),
            }
        )
    (args.output / "index.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
