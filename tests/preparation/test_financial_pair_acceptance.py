"""Pinned pair acceptance rejects unreviewed scope and unresolved comparisons."""

from hashlib import sha256
from pathlib import Path

import pytest

from stock_research_llm_orchestrator.preparation.financial_pair_acceptance import (
    PAIR_POLICY_SHA256,
    PairAdoptionPolicy,
    compare_pair_records,
    evaluate_pair_acceptance,
)

from .test_financial_comparative import _record


def test_exact_pair_policy_and_period() -> None:
    """No arbitrary policy may add a period or replace a filing."""
    body = Path("config/financial-mapping/7203-2022-pair-approved.json").read_bytes()
    assert sha256(body).hexdigest() == PAIR_POLICY_SHA256
    policy = PairAdoptionPolicy.model_validate_json(body)
    assert policy.adopted_period.end_date.isoformat() == "2022-03-31"
    assert policy.amended.document_id == "S100RAR0" and policy.original.document_id == "S100QZHY"
    with pytest.raises(ValueError, match="policy_unapproved"):
        evaluate_pair_acceptance({}, body + b" ")


@pytest.mark.parametrize("mode", ["conflict", "nil", "missing", "period"])
def test_pair_comparison_fails_closed(mode: str) -> None:
    """Agreement requires every accepted metric in both periods."""
    original = _record(6)
    compare_pair_records((original,), (original,))
    if mode == "period":
        with pytest.raises(ValueError, match="period_mismatch"):
            compare_pair_records((original,), ())
        return
    changed = original.values[0].model_copy(
        update={
            "value": "1" if mode == "conflict" else None,
            "status": "unaccepted" if mode == "missing" else "accepted",
        }
    )
    revised = original.model_copy(update={"values": (changed, *original.values[1:])})
    with pytest.raises(ValueError, match="values_unresolved"):
        compare_pair_records((original,), (revised,))
