"""Pure EDINET XBRL ZIP retrieval intent and archive inventory parser."""

import io
import stat
import zipfile
from pathlib import PurePosixPath
from typing import Annotated

from pydantic import Field, StringConstraints, model_validator

from stock_research_llm_orchestrator.contracts.base import Sha256Hex, StrictContractModel
from stock_research_llm_orchestrator.sources.protocol import (
    BoundedSourceResponse,
    CredentialFreeSourceIntent,
    SourceParameter,
)


_DOCUMENT_RETRIEVAL_OPERATION = "document-retrieval"
_DOCUMENT_RETRIEVAL_ORIGIN = "api.edinet-fsa.go.jp"
_DOCUMENT_RETRIEVAL_MEDIA_TYPE = "application/zip"
_DOCUMENT_RETRIEVAL_ENCODING = "binary"
_MAX_ARCHIVE_MEMBERS = 10_000
_MAX_MEMBER_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
_MAX_TOTAL_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
_MAX_COMPRESSION_RATIO = 1_000
_ALLOWED_COMPRESSION_METHODS = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
_ArchivePath = Annotated[str, StringConstraints(min_length=1, max_length=1024)]


class EdinetDocumentRetrievalParseError(ValueError):
    """Sanitized failure raised for an unsafe or invalid EDINET archive."""


class EdinetArchiveMember(StrictContractModel):
    """Safe central-directory metadata for one non-directory archive member."""

    path: _ArchivePath
    compressed_bytes: int = Field(ge=0)
    uncompressed_bytes: int = Field(ge=0)
    crc32: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{8}$")]
    is_xbrl: bool


class EdinetDocumentArchive(StrictContractModel):
    """Validated inventory of one exact EDINET XBRL ZIP response."""

    archive_sha256: Sha256Hex
    total_compressed_bytes: int = Field(ge=0)
    total_uncompressed_bytes: int = Field(ge=0)
    members: tuple[EdinetArchiveMember, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_xbrl_and_unique_paths(self) -> EdinetDocumentArchive:
        """Require an XBRL document and an unambiguous member inventory."""
        paths = tuple(member.path for member in self.members)
        if len(paths) != len(set(paths)):
            raise ValueError("duplicate_edinet_archive_path")
        if not any(member.is_xbrl for member in self.members):
            raise ValueError("edinet_archive_has_no_xbrl")
        return self


class EdinetDocumentRetrievalAdapter:
    """Build and inspect EDINET document retrievals without I/O or extraction."""

    @property
    def source_id(self) -> str:
        """Return the immutable EDINET source identifier."""
        return "edinet"

    def build_intent(self, operation: str, parameters: tuple[SourceParameter, ...]) -> CredentialFreeSourceIntent:
        """Build one canonical XBRL ZIP retrieval intent without a subscription key."""
        if operation != _DOCUMENT_RETRIEVAL_OPERATION:
            raise ValueError("unsupported_edinet_operation")
        values = {parameter.name: parameter.value for parameter in parameters}
        if tuple(parameter.name for parameter in parameters) != ("document_id", "type") or values.get("type") != "1":
            raise ValueError("invalid_edinet_document_retrieval_parameters")
        try:
            return CredentialFreeSourceIntent(
                source_id=self.source_id,
                operation=operation,
                origin=_DOCUMENT_RETRIEVAL_ORIGIN,
                resource_key=values["document_id"],
                parameters=parameters,
            )
        except (KeyError, ValueError) as exc:
            raise ValueError("invalid_edinet_document_retrieval_parameters") from exc

    def parse(self, response: BoundedSourceResponse) -> EdinetDocumentArchive:
        """Inspect bounded ZIP metadata without extracting provider-controlled paths."""
        if response.media_type != _DOCUMENT_RETRIEVAL_MEDIA_TYPE or response.encoding != _DOCUMENT_RETRIEVAL_ENCODING:
            raise EdinetDocumentRetrievalParseError("edinet_document_retrieval_invalid")
        try:
            with zipfile.ZipFile(io.BytesIO(response.body), mode="r") as archive:
                infos = archive.infolist()
                if len(infos) > _MAX_ARCHIVE_MEMBERS:
                    raise ValueError("edinet_archive_member_limit_exceeded")
                for info in infos:
                    _validate_archive_path(info.filename, is_directory=info.is_dir())
                members = tuple(self._inspect_member(info) for info in infos if not info.is_dir())
            total_compressed = sum(member.compressed_bytes for member in members)
            total_uncompressed = sum(member.uncompressed_bytes for member in members)
            if total_uncompressed > _MAX_TOTAL_UNCOMPRESSED_BYTES:
                raise ValueError("edinet_archive_total_size_exceeded")
            return EdinetDocumentArchive(
                archive_sha256=response.sha256,
                total_compressed_bytes=total_compressed,
                total_uncompressed_bytes=total_uncompressed,
                members=members,
            )
        except (OSError, RuntimeError, ValueError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            if isinstance(exc, EdinetDocumentRetrievalParseError):
                raise
            raise EdinetDocumentRetrievalParseError("edinet_document_retrieval_invalid") from exc

    @staticmethod
    def _inspect_member(info: zipfile.ZipInfo) -> EdinetArchiveMember:
        path = info.filename
        _validate_archive_path(path, is_directory=False)
        if info.flag_bits & 0x1:
            raise ValueError("encrypted_edinet_archive_member")
        file_type = (info.external_attr >> 16) & 0o170000
        if file_type == stat.S_IFLNK:
            raise ValueError("symlink_edinet_archive_member")
        if info.compress_type not in _ALLOWED_COMPRESSION_METHODS:
            raise ValueError("unsupported_edinet_archive_compression")
        if info.file_size > _MAX_MEMBER_UNCOMPRESSED_BYTES:
            raise ValueError("edinet_archive_member_size_exceeded")
        denominator = max(info.compress_size, 1)
        if info.file_size > denominator * _MAX_COMPRESSION_RATIO:
            raise ValueError("edinet_archive_compression_ratio_exceeded")
        return EdinetArchiveMember(
            path=path,
            compressed_bytes=info.compress_size,
            uncompressed_bytes=info.file_size,
            crc32=f"{info.CRC:08x}",
            is_xbrl=path.lower().endswith(".xbrl"),
        )


def _validate_archive_path(value: str, *, is_directory: bool) -> None:
    if not value or "\\" in value or "\x00" in value:
        raise ValueError("unsafe_edinet_archive_path")
    candidate = value[:-1] if is_directory and value.endswith("/") else value
    path = PurePosixPath(candidate)
    if (
        not candidate
        or not path.parts
        or path.as_posix() != candidate
        or path.is_absolute()
        or ":" in path.parts[0]
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("unsafe_edinet_archive_path")
