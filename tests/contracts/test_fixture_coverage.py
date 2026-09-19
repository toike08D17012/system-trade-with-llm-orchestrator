"""Tests for the public contract fixture coverage declaration."""

import json
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).parents[2]
FIXTURE_ROOT = REPOSITORY_ROOT / "tests" / "fixtures" / "contracts" / "detailed-analysis" / "v1"


def test_fixture_coverage_declares_all_generic_and_specialized_failures() -> None:
    """Keep the compact mutation matrix and dedicated semantic cases reviewable."""
    coverage = json.loads((FIXTURE_ROOT / "fixture-coverage.json").read_text(encoding="utf-8"))

    assert coverage["coverage_version"] == 1
    assert {case["expected_code"] for case in coverage["generated_invalid_cases"]} == {
        "required_field_missing",
        "type_mismatch",
        "unknown_field",
        "unsupported_schema_version",
        "constraint_violation",
    }
    assert {case["failure_class"] for case in coverage["specialized_failure_classes"]} == {
        "artifact_or_semantic_conflict",
        "invalid_evidence_or_artifact_reference",
        "state_or_policy_violation",
        "security_violation",
        "markdown_mismatch",
    }

    for case in (*coverage["generated_invalid_cases"], *coverage["specialized_failure_classes"]):
        test_paths = [case["test"]] if "test" in case else case["tests"]
        for test_path in test_paths:
            assert (REPOSITORY_ROOT / test_path.split("::", maxsplit=1)[0]).is_file()
