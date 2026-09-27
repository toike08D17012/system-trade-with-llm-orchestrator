"""Durable one-shot budget for the owner-approved prior annual campaign."""

import json
import os
from datetime import datetime
from pathlib import Path

from stock_research_llm_orchestrator.preparation.market_revalidation import _safe_path


TARGETS = {
    "2024": ("2024-06-25", "2023-04-01", "2024-03-31"),
    "2023": ("2023-06-30", "2022-04-01", "2023-03-31"),
}
CAMPAIGN_ID = "edinet-prior-annual-20260927"


def _durable_write(path: Path, data: dict[str, object]) -> None:
    """Create an immutable record and sync it before any provider send."""
    with path.open("x") as handle:
        json.dump(data, handle, sort_keys=True, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class AnnualCampaign:
    """An exclusive invocation; every subsequent invocation refuses all sends."""

    def __init__(self, runtime: Path, approval_hash: str, task_id: str, now: datetime) -> None:
        """Persist an exclusive campaign marker before any attempt is possible."""
        self.path = _safe_path(runtime) / CAMPAIGN_ID
        self.path.mkdir(mode=0o700)
        descriptor = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self.approval_hash = approval_hash
        self.sent = 0
        _durable_write(
            self.path / "started.json",
            {
                "campaign_id": CAMPAIGN_ID,
                "approval_sha256": approval_hash,
                "task_id": task_id,
                "started_at": now.isoformat(),
                "max_sends": 4,
            },
        )

    def before_send(self, target: str, sequence: int, now: datetime, attempt_id: str) -> None:
        """Consume slots in order; crashes and failures never refund a slot."""
        expected = (("2024", 0), ("2024", 1), ("2023", 0), ("2023", 1))
        if self.sent >= 4 or expected[self.sent] != (target, sequence):
            raise ValueError("edinet_campaign_slot_invalid")
        _durable_write(
            self.path / f"slot-{self.sent}.json",
            {
                "target": target,
                "sequence": sequence,
                "started_at": now.isoformat(),
                "physical_attempt_id": attempt_id,
                "approval_sha256": self.approval_hash,
            },
        )
        self.sent += 1

    def record_result(self, target: str, output: Path) -> None:
        """Record a completed offline-prepared bundle without enabling a restart."""
        from hashlib import sha256

        _durable_write(
            self.path / f"result-{target}.json",
            {
                "output": str(output),
                "manifest_sha256": sha256((output / "manifest.json").read_bytes()).hexdigest(),
            },
        )
