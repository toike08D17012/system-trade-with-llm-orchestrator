"""Value-free mapping reviews preserve source evidence and uncertainty."""

import json
from datetime import date

import pytest

from stock_research_llm_orchestrator.preparation.financial_mapping import (
    EDINET_SCHEME,
    METRICS,
    MappingProposal,
    MappingRule,
    match_entity,
    review_metric,
)
from stock_research_llm_orchestrator.sources.edinet.xbrl_facts import (
    EdinetXbrlContext,
    EdinetXbrlDimension,
    EdinetXbrlFactCandidate,
    EdinetXbrlFactSet,
    EdinetXbrlQName,
    EdinetXbrlUnit,
)


NS = "https://example.invalid/synthetic"


def _qname(name: str, namespace: str = NS) -> EdinetXbrlQName:
    return EdinetXbrlQName(namespace=namespace, local_name=name)


def _proposal() -> MappingProposal:
    return MappingProposal(
        start_date=date(2025, 4, 1),
        end_date=date(2026, 3, 31),
        rules=tuple(
            MappingRule(
                metric=m, concepts=(_qname("Revenue" if m == "revenue" else m),), evidence_references=("synthetic",)
            )
            for m in METRICS
        ),
    )


def _context(**changes: object) -> EdinetXbrlContext:
    original = EdinetXbrlContext(
        source_member_path="XBRL/PublicDoc/synthetic.xbrl",
        context_id="annual",
        entity_identifier_scheme=EDINET_SCHEME,
        entity_identifier="E00001-000",
        period_kind="duration",
        start_date="2025-04-01",
        end_date="2026-03-31",
        instant=None,
        dimensions=(),
    )
    return original.model_copy(update=changes)


def _fact(**changes: object) -> EdinetXbrlFactCandidate:
    original = EdinetXbrlFactCandidate(
        ordinal=1,
        source_member_path="XBRL/PublicDoc/synthetic.xbrl",
        concept_namespace=NS,
        concept_local_name="Revenue",
        context_ref="annual",
        unit_ref="JPY",
        decimals="-6",
        precision=None,
        scale=None,
        language=None,
        value="987654321",
        is_nil=False,
    )
    return original.model_copy(update=changes)


def _facts(
    *facts: EdinetXbrlFactCandidate, context: EdinetXbrlContext | None = None, currency: str = "JPY"
) -> EdinetXbrlFactSet:
    return EdinetXbrlFactSet(
        archive_sha256="a" * 64,
        xbrl_member_paths=("XBRL/PublicDoc/synthetic.xbrl",),
        contexts=(context or _context(),),
        units=(
            EdinetXbrlUnit(
                source_member_path="XBRL/PublicDoc/synthetic.xbrl",
                unit_id="JPY",
                numerator_measures=(_qname(currency, "http://www.xbrl.org/2003/iso4217"),),
                denominator_measures=(),
            ),
        ),
        facts=facts or (_fact(),),
    )


@pytest.mark.parametrize(
    ("scheme", "identifier", "expected"),
    [
        (EDINET_SCHEME, "E00001-000", "matched"),
        ("https://example.invalid/entity", "E00001-000", "unsupported_entity_scheme"),
        (EDINET_SCHEME, "E00002-000", "different_entity"),
        (EDINET_SCHEME, "E00001-001", "unsupported_instance_suffix"),
        (EDINET_SCHEME, "E00001", "unsupported_entity_identifier"),
    ],
)
def test_entity(scheme: str, identifier: str, expected: str) -> None:
    """Do not erase scheme or instance identity."""
    result = match_entity(_context(entity_identifier_scheme=scheme, entity_identifier=identifier), "E00001")
    assert result.reason == expected
    assert result.identifier == identifier and result.scheme == scheme


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"start_date": "2024-04-01"}, "period_mismatch"),
        ({"period_kind": "instant", "instant": "2026-03-31", "start_date": None, "end_date": None}, "period_mismatch"),
        ({"entity_identifier": "E00002-000"}, "different_entity"),
        (
            {
                "dimensions": (
                    EdinetXbrlDimension(
                        location="scenario",
                        dimension=_qname("Axis"),
                        member_kind="explicit",
                        member=_qname("NonConsolidatedMember"),
                        typed_value=None,
                    ),
                )
            },
            "dimensions_outside_proposal",
        ),
    ],
)
def test_context_exclusions(changes: dict[str, object], reason: str) -> None:
    """A context name alone cannot establish period, entity or consolidation."""
    proposal = _proposal()
    result = review_metric(_facts(context=_context(**changes)), proposal.rules[0], proposal, "E00001")
    assert result.status == "excluded" and reason in result.candidates[0].reasons


@pytest.mark.parametrize(
    ("values", "status"),
    [
        (("0",), "candidate"),
        ((None,), "nil"),
        (("1", "1.00"), "duplicate"),
        (("1", "2"), "conflicting"),
        ((None, "0"), "conflicting"),
        (("NaN",), "excluded"),
        (("1e6",), "excluded"),
    ],
)
def test_value_comparison(values: tuple[str | None, ...], status: str) -> None:
    """Nil, zero and disagreements remain distinct without exposing numbers."""
    proposal = _proposal()
    facts = tuple(_fact(ordinal=i + 1, value=v, is_nil=v is None) for i, v in enumerate(values))
    result = review_metric(_facts(*facts), proposal.rules[0], proposal, "E00001")
    assert result.status == status
    assert '"value"' not in result.model_dump_json()
    assert "mapping_not_approved" in result.review_reasons
    assert "consolidation_scope_unconfirmed" in result.review_reasons


def test_unit_scale_and_correction() -> None:
    """Never convert other currencies, apply decimals as scale or adopt corrections."""
    proposal = _proposal()
    for facts, reasons, expected in (
        (_facts(currency="USD"), (), "unsupported_unit"),
        (_facts(_fact(scale="6")), (), "unsupported_scale"),
        (_facts(), ("amendment_selection_unresolved",), "amendment_selection_unresolved"),
    ):
        result = review_metric(facts, proposal.rules[0], proposal, "E00001", reasons)
        assert result.status == "excluded" and expected in result.candidates[0].reasons


def test_unused_context_and_unknown_fact_do_not_block() -> None:
    """Unrelated provider additions cannot invalidate a usable candidate."""
    proposal = _proposal()
    facts = _facts(_fact(), _fact(ordinal=2, concept_local_name="UnknownTextBlock", value="private text"))
    facts = facts.model_copy(
        update={"contexts": (*facts.contexts, _context(context_id="unused", entity_identifier="X"))}
    )
    result = review_metric(facts, proposal.rules[0], proposal, "E00001")
    assert result.status == "candidate" and len(result.candidates) == 1
    assert "987654321" not in result.model_dump_json() and "private text" not in result.model_dump_json()
    missing = review_metric(facts, proposal.rules[1], proposal, "E00001")
    assert missing.status == "missing"


def test_instant_and_member_scoping() -> None:
    """Resolve context and unit IDs within each member, not across an archive."""
    proposal = _proposal()
    facts = _facts(
        _fact(concept_local_name="assets"),
        context=_context(period_kind="instant", instant="2026-03-31", start_date=None, end_date=None),
    )
    foreign_context = _context(source_member_path="other.xbrl", entity_identifier="E99999-000")
    facts = facts.model_copy(update={"contexts": (*facts.contexts, foreign_context)})
    result = review_metric(facts, proposal.rules[3], proposal, "E00001")
    assert result.status == "candidate"


def test_proposal_requires_six_distinct_rules() -> None:
    """Reject ambiguous internal rules while leaving external fields flexible."""
    body = json.loads(_proposal().model_dump_json())
    body["rules"] = body["rules"][:-1]
    with pytest.raises(ValueError):
        MappingProposal.model_validate_json(json.dumps(body))
