"""Verify the offline EDINET physical transport boundary with dummy data."""

from pathlib import Path

import pytest

from stock_research_llm_orchestrator.requests.transport import UntrustedTransportResponse
from stock_research_llm_orchestrator.sources import (
    EdinetDocumentListAdapter,
    EdinetDocumentRetrievalAdapter,
    EdinetHttpTarget,
    EdinetPhysicalTransport,
    EdinetTransportError,
    SourceParameter,
)


CANARY = "dummy-edinet-transport-key-do-not-log"


def test_sends_document_list_with_key_only_at_wire_boundary(tmp_path: Path) -> None:
    """Keep the dummy key out of all credential-free request representations."""
    intent = EdinetDocumentListAdapter().build_intent(
        "document-list",
        (SourceParameter(name="date", value="2026-09-24"), SourceParameter(name="type", value="2")),
    )
    request = intent.to_transport_request("logical-1", "attempt-1")
    credential = _credential(tmp_path)
    observed: list[tuple[EdinetHttpTarget, str]] = []

    def wire_send(target: EdinetHttpTarget, subscription_key: str) -> UntrustedTransportResponse:
        observed.append((target, subscription_key))
        return _response()

    transport = EdinetPhysicalTransport(intent, credential, wire_send)
    assert transport(request) == _response()
    assert observed == [
        (
            EdinetHttpTarget(
                origin="api.edinet-fsa.go.jp",
                path="/api/v2/documents.json",
                query=(
                    SourceParameter(name="date", value="2026-09-24"),
                    SourceParameter(name="type", value="2"),
                ),
            ),
            CANARY,
        )
    ]
    for value in (intent, request, transport, observed[0][0]):
        assert CANARY not in repr(value)


def test_renders_document_retrieval_without_document_id_query(tmp_path: Path) -> None:
    """Render the document ID in the approved path and retain only public type query."""
    intent = EdinetDocumentRetrievalAdapter().build_intent(
        "document-retrieval",
        (SourceParameter(name="document_id", value="SYNTHETIC001"), SourceParameter(name="type", value="1")),
    )
    targets: list[EdinetHttpTarget] = []

    def wire_send(target: EdinetHttpTarget, _subscription_key: str) -> UntrustedTransportResponse:
        targets.append(target)
        return _response()

    EdinetPhysicalTransport(intent, _credential(tmp_path), wire_send)(
        intent.to_transport_request("logical-2", "attempt-2")
    )
    assert targets[0].path == "/api/v2/documents/SYNTHETIC001"
    assert targets[0].query == (SourceParameter(name="type", value="1"),)


def test_rejects_mismatched_request_before_credential_access(tmp_path: Path) -> None:
    """Do not inspect a credential for a physical request with changed identity."""
    intent = EdinetDocumentListAdapter().build_intent(
        "document-list",
        (SourceParameter(name="date", value="2026-09-24"), SourceParameter(name="type", value="2")),
    )
    missing = tmp_path / "must-not-be-read"
    request = intent.to_transport_request("logical-3", "attempt-3").model_copy(update={"operation": "other"})

    with pytest.raises(EdinetTransportError, match="^edinet_transport_request_mismatch$"):
        EdinetPhysicalTransport(intent, missing, _unexpected_send)(request)


def test_rejects_insecure_credential_before_wire_send_without_leak(tmp_path: Path) -> None:
    """Apply the approved owner-only preflight before opening or sending."""
    intent = EdinetDocumentListAdapter().build_intent(
        "document-list",
        (SourceParameter(name="date", value="2026-09-24"), SourceParameter(name="type", value="2")),
    )
    credential = _credential(tmp_path)
    credential.chmod(0o640)

    with pytest.raises(EdinetTransportError, match="^edinet_credential_invalid_permissions$") as captured:
        EdinetPhysicalTransport(intent, credential, _unexpected_send)(
            intent.to_transport_request("logical-4", "attempt-4")
        )
    assert CANARY not in str(captured.value)
    assert CANARY not in repr(captured.value)


def test_sanitizes_wire_exception_containing_key(tmp_path: Path) -> None:
    """Do not allow a wire-client exception to carry the key outward."""
    intent = EdinetDocumentListAdapter().build_intent(
        "document-list",
        (SourceParameter(name="date", value="2026-09-24"), SourceParameter(name="type", value="2")),
    )

    def fail(_target: EdinetHttpTarget, subscription_key: str) -> UntrustedTransportResponse:
        raise RuntimeError(subscription_key)

    with pytest.raises(EdinetTransportError, match="^edinet_physical_send_failed$") as captured:
        EdinetPhysicalTransport(intent, _credential(tmp_path), fail)(
            intent.to_transport_request("logical-5", "attempt-5")
        )
    assert CANARY not in str(captured.value)
    assert CANARY not in repr(captured.value)


def _credential(tmp_path: Path) -> Path:
    path = tmp_path / "edinet-api-key"
    path.write_text(CANARY)
    path.chmod(0o600)
    return path


def _response() -> UntrustedTransportResponse:
    return UntrustedTransportResponse(
        status_code=200,
        body=b"{}",
        media_type="application/json",
        encoding="utf-8",
        final_origin="api.edinet-fsa.go.jp",
        redirected=False,
    )


def _unexpected_send(_target: EdinetHttpTarget, _subscription_key: str) -> UntrustedTransportResponse:
    raise AssertionError("wire send must not run")
