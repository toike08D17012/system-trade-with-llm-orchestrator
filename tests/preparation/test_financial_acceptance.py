"""Local normalization preserves policy boundaries, uncertainty and provenance."""

from datetime import date
from hashlib import sha256
from pathlib import Path

import pytest

from stock_research_llm_orchestrator.preparation.financial_acceptance import (
    APPROVED_POLICY_SHA256,
    FinancialAdoptionPolicy,
    adopt_metric,
    evaluate_acceptance,
)
from stock_research_llm_orchestrator.preparation.financial_mapping import (
    METRICS,
    MappingProposal,
    MappingRule,
    review_metric,
)
from stock_research_llm_orchestrator.sources.edinet.xbrl_facts import (
    EdinetXbrlFactSet,
    EdinetXbrlQName,
    _parse_xbrl_member,
)
from stock_research_llm_orchestrator.sources.edinet.xbrl_taxonomy import TaxonomyProof


START, END = date(2025, 4, 1), date(2026, 3, 31)


def _facts(values: tuple[str | None, ...], *, unit: str = "JPY", scale: str = "") -> EdinetXbrlFactSet:
    items = "".join(
        f'<s:revenue contextRef="c" unitRef="u" decimals="-6" {scale} '
        + ('xsi:nil="true"/>' if value is None else f">{value}</s:revenue>")
        for value in values
    )
    body = f"""<x:xbrl xmlns:x="http://www.xbrl.org/2003/instance" xmlns:s="urn:synthetic"
 xmlns:iso="http://www.xbrl.org/2003/iso4217" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
 <x:context id="c"><x:entity><x:identifier scheme="http://disclosure.edinet-fsa.go.jp">E00001-000</x:identifier></x:entity>
 <x:period><x:startDate>2025-04-01</x:startDate><x:endDate>2026-03-31</x:endDate></x:period></x:context>
 <x:unit id="u"><x:measure>iso:{unit}</x:measure></x:unit>{items}</x:xbrl>""".encode()
    parsed = _parse_xbrl_member("facts.xbrl", body, first_ordinal=1)
    return EdinetXbrlFactSet(
        archive_sha256="a" * 64,
        xbrl_member_paths=("facts.xbrl",),
        contexts=parsed.contexts,
        units=parsed.units,
        facts=parsed.facts,
    )


def _proof() -> TaxonomyProof:
    return TaxonomyProof(
        verified=True,
        reasons=(),
        concept=EdinetXbrlQName(namespace="urn:synthetic", local_name="revenue"),
        period_type="duration",
        member_hashes={},
        arcs=(),
    )


def _proposal() -> MappingProposal:
    return MappingProposal(
        start_date=START,
        end_date=END,
        rules=tuple(
            MappingRule(
                metric=m,
                concepts=(EdinetXbrlQName(namespace="urn:synthetic", local_name=m),),
                evidence_references=("synthetic",),
            )
            for m in METRICS
        ),
    )


@pytest.mark.parametrize(
    ("values", "accepted", "normalized"),
    [
        (("0",), True, "0"),
        (("1000000", "1000000.00"), True, "1000000"),
        ((None,), False, None),
        (("1", "2"), False, None),
        ((None, "0"), False, None),
    ],
)
def test_adopt_only_unique_non_nil_values(
    values: tuple[str | None, ...], accepted: bool, normalized: str | None
) -> None:
    """Do not average, fill nil or use decimals as a unit multiplier."""
    facts = _facts(values)
    proposal = _proposal()
    review = review_metric(facts, proposal.rules[0], proposal, "E00001")
    result = adopt_metric(facts, review, _proof(), START, END)
    assert (result.status == "accepted") == accepted and result.value == normalized
    assert len(result.references) == len(values)


@pytest.mark.parametrize(
    ("unit", "scale", "reasons"),
    [
        ("USD", "", ()),
        ("JPY", 'scale="6"', ()),
        ("JPY", "", ("amendment_selection_unresolved",)),
    ],
)
def test_unsupported_candidates_remain_unaccepted(unit: str, scale: str, reasons: tuple[str, ...]) -> None:
    """Currency, scale and correction restrictions survive normalization."""
    facts = _facts(("123",), unit=unit, scale=scale)
    proposal = _proposal()
    review = review_metric(facts, proposal.rules[0], proposal, "E00001", reasons)
    result = adopt_metric(facts, review, _proof(), START, END)
    assert result.status == "unaccepted" and result.value is None


def test_scope_and_concept_proofs_are_required() -> None:
    """Candidate availability cannot replace verified taxonomy identity and scope."""
    facts = _facts(("123",))
    proposal = _proposal()
    review = review_metric(facts, proposal.rules[0], proposal, "E00001")
    for proof in (
        _proof().model_copy(update={"verified": False}),
        _proof().model_copy(update={"period_type": "instant"}),
        _proof().model_copy(update={"concept": EdinetXbrlQName(namespace="urn:wrong", local_name="revenue")}),
    ):
        result = adopt_metric(facts, review, proof, START, END)
        assert result.status == "unaccepted" and result.value is None


def test_only_exact_owner_approved_policy_is_loaded() -> None:
    """Changing draft status or adding policy whitespace cannot authorize adoption."""
    path = Path(__file__).resolve().parents[2] / "config/financial-mapping/7203-2026-approved.json"
    body = path.read_bytes()
    assert sha256(body).hexdigest() == APPROVED_POLICY_SHA256
    policy = FinancialAdoptionPolicy.model_validate_json(body)
    assert len(policy.rules) == 6
    for candidate in (body + b" ", body.replace(b'"approved"', b'"draft"'), b"{}"):
        with pytest.raises(ValueError, match="not_approved"):
            evaluate_acceptance({}, {}, candidate)
