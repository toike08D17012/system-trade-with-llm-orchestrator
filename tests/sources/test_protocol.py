"""Tests for the pure credential-free source adapter boundary."""

import hashlib

import pytest
from pydantic import ValidationError

from stock_research_llm_orchestrator.requests.transport import TemporaryRawCandidate
from stock_research_llm_orchestrator.sources import (
    BoundedSourceResponse,
    CredentialFreeSourceIntent,
    SourceParameter,
)


def test_intent_maps_only_non_secret_transport_identity() -> None:
    """Keep canonical parser parameters out of the physical transport request."""
    intent = CredentialFreeSourceIntent(
        source_id="fixture",
        operation="history",
        origin="example-com",
        resource_key="fixture-history",
        parameters=(
            SourceParameter(name="from_date", value="2026-01-01"),
            SourceParameter(name="ticker", value="7203.T"),
        ),
    )

    request = intent.to_transport_request("logical-1", "attempt-1")

    assert request.model_dump() == {
        "logical_request_id": "logical-1",
        "physical_attempt_id": "attempt-1",
        "origin": "example-com",
        "operation": "history",
        "resource_key": "fixture-history",
    }


@pytest.mark.parametrize(
    "name",
    ["api_key", "access-token", "Authorization", "session.cookie", "credential_alias", "password"],
)
def test_intent_rejects_credential_shaped_parameter_names(name: str) -> None:
    """Reject common credential channels from durable source parameters."""
    with pytest.raises(ValidationError, match="credential_parameter_forbidden"):
        SourceParameter(name=name, value="dummy-canary")


@pytest.mark.parametrize(
    "parameters",
    [
        (
            SourceParameter(name="ticker", value="7203.T"),
            SourceParameter(name="from_date", value="2026-01-01"),
        ),
        (
            SourceParameter(name="ticker", value="7203.T"),
            SourceParameter(name="ticker", value="6758.T"),
        ),
    ],
)
def test_intent_requires_sorted_unique_parameters(parameters: tuple[SourceParameter, ...]) -> None:
    """Reject unstable or ambiguous canonical parameter sets."""
    with pytest.raises(ValidationError, match="source_parameters_not_canonical"):
        CredentialFreeSourceIntent(
            source_id="fixture",
            operation="history",
            origin="example-com",
            resource_key="fixture-history",
            parameters=parameters,
        )


def test_bounded_response_rechecks_exact_candidate_hash_and_hides_body() -> None:
    """Reject modified bytes and avoid exposing accepted raw bytes in repr output."""
    body = b'{"value":1}'
    digest = hashlib.sha256(body).hexdigest()
    candidate = TemporaryRawCandidate("attempt-1", body, digest, "application/json", "utf-8")

    response = BoundedSourceResponse.from_candidate(candidate)

    assert response.body == body
    assert body.decode() not in repr(response)
    changed = TemporaryRawCandidate("attempt-1", b"changed", digest, "application/json", "utf-8")
    with pytest.raises(ValueError, match="source_candidate_hash_mismatch"):
        BoundedSourceResponse.from_candidate(changed)
