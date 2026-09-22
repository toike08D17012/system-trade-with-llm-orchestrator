"""Pure EDINET document-list intent and parser."""

import json
from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, ValidationError, field_validator, model_validator

from stock_research_llm_orchestrator.contracts.base import Identifier, StrictContractModel
from stock_research_llm_orchestrator.sources.protocol import (
    BoundedSourceResponse,
    CredentialFreeSourceIntent,
    SourceParameter,
)


_DOCUMENT_LIST_OPERATION = "document-list"
_DOCUMENT_LIST_ORIGIN = "api.edinet-fsa.go.jp"
_DOCUMENT_LIST_RESOURCE = "api-v2-documents"
_DOCUMENT_LIST_MEDIA_TYPE = "application/json"
_DOCUMENT_LIST_ENCODING = "utf-8"


class EdinetDocumentType(StrEnum):
    """Approved EDINET filing types for the initial implementation."""

    ANNUAL_REPORT = "120"
    AMENDED_ANNUAL_REPORT = "130"
    QUARTERLY_REPORT = "140"
    AMENDED_QUARTERLY_REPORT = "150"
    SEMIANNUAL_REPORT = "160"
    AMENDED_SEMIANNUAL_REPORT = "170"


class EdinetDocumentListParseError(ValueError):
    """Sanitized failure raised for an invalid EDINET document list."""


class EdinetDocument(StrictContractModel):
    """Selected source-native metadata for one approved filing."""

    sequence_number: int = Field(ge=1)
    document_id: Identifier
    edinet_code: Identifier | None
    security_code: Identifier | None
    filer_name: Annotated[str, StringConstraints(min_length=1, max_length=512)]
    document_type: EdinetDocumentType
    period_start: str | None
    period_end: str | None
    submitted_at: str
    description: Annotated[str, StringConstraints(min_length=1, max_length=1024)]
    parent_document_id: Identifier | None
    withdrawal_status: Literal["0", "1"]
    xbrl_available: bool

    @field_validator("period_start", "period_end")
    @classmethod
    def validate_optional_date(cls, value: str | None) -> str | None:
        """Require real ISO dates when a filing period boundary is present."""
        if value is not None:
            date.fromisoformat(value)
        return value

    @field_validator("submitted_at")
    @classmethod
    def validate_submitted_at(cls, value: str) -> str:
        """Require the EDINET local submission timestamp shape."""
        datetime.strptime(value, "%Y-%m-%d %H:%M")
        return value


class EdinetDocumentList(StrictContractModel):
    """Validated source-native EDINET document-list result."""

    requested_date: str
    processed_at: str
    provider_status: str
    provider_message: str
    provider_result_count: int = Field(ge=0)
    documents: tuple[EdinetDocument, ...]

    @field_validator("requested_date")
    @classmethod
    def validate_requested_date(cls, value: str) -> str:
        """Require a real ISO request date."""
        date.fromisoformat(value)
        return value

    @field_validator("processed_at")
    @classmethod
    def validate_processed_at(cls, value: str) -> str:
        """Require the EDINET processing timestamp shape."""
        datetime.strptime(value, "%Y-%m-%d %H:%M")
        return value

    @model_validator(mode="after")
    def require_unique_document_ids(self) -> EdinetDocumentList:
        """Reject ambiguous repeated approved filings."""
        document_ids = tuple(document.document_id for document in self.documents)
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("duplicate_edinet_document_id")
        return self


class _RawParameter(StrictContractModel):
    date: str
    type: Literal["2"]


class _RawResultSet(StrictContractModel):
    count: int = Field(ge=0)


class _RawMetadata(StrictContractModel):
    title: str
    parameter: _RawParameter
    resultset: _RawResultSet
    processDateTime: str  # noqa: N815 - exact provider field
    status: str
    message: str


class _RawDocument(StrictContractModel):
    seqNumber: int = Field(ge=1)  # noqa: N815 - exact provider field
    docID: Identifier  # noqa: N815 - exact provider field
    edinetCode: Identifier | None  # noqa: N815 - exact provider field
    secCode: Identifier | None  # noqa: N815 - exact provider field
    JCN: str | None  # noqa: N815 - exact provider field
    filerName: str  # noqa: N815 - exact provider field
    fundCode: str | None  # noqa: N815 - exact provider field
    ordinanceCode: str  # noqa: N815 - exact provider field
    formCode: str  # noqa: N815 - exact provider field
    docTypeCode: str  # noqa: N815 - exact provider field
    periodStart: str | None  # noqa: N815 - exact provider field
    periodEnd: str | None  # noqa: N815 - exact provider field
    submitDateTime: str  # noqa: N815 - exact provider field
    docDescription: str  # noqa: N815 - exact provider field
    issuerEdinetCode: str | None  # noqa: N815 - exact provider field
    subjectEdinetCode: str | None  # noqa: N815 - exact provider field
    subsidiaryEdinetCode: str | None  # noqa: N815 - exact provider field
    currentReportReason: str | None  # noqa: N815 - exact provider field
    parentDocID: Identifier | None  # noqa: N815 - exact provider field
    opeDateTime: str | None  # noqa: N815 - exact provider field
    withdrawalStatus: Literal["0", "1"]  # noqa: N815 - exact provider field
    docInfoEditStatus: str  # noqa: N815 - exact provider field
    disclosureStatus: str  # noqa: N815 - exact provider field
    xbrlFlag: Literal["0", "1"]  # noqa: N815 - exact provider field
    pdfFlag: Literal["0", "1"]  # noqa: N815 - exact provider field
    attachDocFlag: Literal["0", "1"]  # noqa: N815 - exact provider field
    englishDocFlag: Literal["0", "1"]  # noqa: N815 - exact provider field


class _RawDocumentList(StrictContractModel):
    metadata: _RawMetadata
    results: tuple[_RawDocument, ...] = Field(strict=False)

    @model_validator(mode="after")
    def require_provider_count_match(self) -> _RawDocumentList:
        """Reject truncated or internally inconsistent provider output."""
        if self.metadata.resultset.count != len(self.results):
            raise ValueError("edinet_result_count_mismatch")
        document_ids = tuple(document.docID for document in self.results)
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("duplicate_edinet_document_id")
        return self


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise EdinetDocumentListParseError("edinet_document_list_invalid")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise EdinetDocumentListParseError("edinet_document_list_invalid")


class EdinetDocumentListAdapter:
    """Build and parse EDINET document-list operations without performing I/O."""

    @property
    def source_id(self) -> str:
        """Return the immutable EDINET source identifier."""
        return "edinet"

    def build_intent(self, operation: str, parameters: tuple[SourceParameter, ...]) -> CredentialFreeSourceIntent:
        """Build one canonical EDINET list intent containing no subscription key."""
        if operation != _DOCUMENT_LIST_OPERATION:
            raise ValueError("unsupported_edinet_operation")
        values = {parameter.name: parameter.value for parameter in parameters}
        if tuple(parameter.name for parameter in parameters) != ("date", "type") or values.get("type") != "2":
            raise ValueError("invalid_edinet_document_list_parameters")
        try:
            date.fromisoformat(values["date"])
        except (KeyError, ValueError) as exc:
            raise ValueError("invalid_edinet_document_list_parameters") from exc
        return CredentialFreeSourceIntent(
            source_id=self.source_id,
            operation=operation,
            origin=_DOCUMENT_LIST_ORIGIN,
            resource_key=_DOCUMENT_LIST_RESOURCE,
            parameters=parameters,
        )

    def parse(self, response: BoundedSourceResponse) -> EdinetDocumentList:
        """Parse an exact bounded EDINET list and select approved filing types."""
        if response.media_type != _DOCUMENT_LIST_MEDIA_TYPE or response.encoding.lower() != _DOCUMENT_LIST_ENCODING:
            raise EdinetDocumentListParseError("edinet_document_list_invalid")
        try:
            text = response.body.decode(_DOCUMENT_LIST_ENCODING, errors="strict")
            value = json.loads(text, object_pairs_hook=_unique_json_object, parse_constant=_reject_json_constant)
            raw = _RawDocumentList.model_validate(value)
            documents = tuple(self._to_document(item) for item in raw.results if item.docTypeCode in EdinetDocumentType)
            return EdinetDocumentList(
                requested_date=raw.metadata.parameter.date,
                processed_at=raw.metadata.processDateTime,
                provider_status=raw.metadata.status,
                provider_message=raw.metadata.message,
                provider_result_count=raw.metadata.resultset.count,
                documents=documents,
            )
        except (UnicodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
            if isinstance(exc, EdinetDocumentListParseError):
                raise
            raise EdinetDocumentListParseError("edinet_document_list_invalid") from exc

    @staticmethod
    def _to_document(raw: _RawDocument) -> EdinetDocument:
        return EdinetDocument(
            sequence_number=raw.seqNumber,
            document_id=raw.docID,
            edinet_code=raw.edinetCode,
            security_code=raw.secCode,
            filer_name=raw.filerName,
            document_type=EdinetDocumentType(raw.docTypeCode),
            period_start=raw.periodStart,
            period_end=raw.periodEnd,
            submitted_at=raw.submitDateTime,
            description=raw.docDescription,
            parent_document_id=raw.parentDocID,
            withdrawal_status=raw.withdrawalStatus,
            xbrl_available=raw.xbrlFlag == "1",
        )
