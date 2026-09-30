"""Parse the fixed public EDINET code-list ZIP for one issuer binding."""

import csv
import io
import re
from datetime import date
from zipfile import BadZipFile, ZipFile

from stock_research_llm_orchestrator.contracts.base import StrictContractModel


CODE_LIST_URL = "https://disclosure2dl.edinet-fsa.go.jp/searchdocument/codelist/Edinetcode.zip"
MAX_ZIP_BYTES = 3_000_000
MAX_CSV_BYTES = 5_000_000


class CodeListObservation(StrictContractModel):
    """One uniquely matched issuer in a dated public list."""

    as_of: str
    count: int
    edinet_code: str
    security_code: str
    issuer_name: str


def parse_code_list(body: bytes, received_on: date) -> CodeListObservation:
    """Reject oversized, ambiguous, future-dated, or changed-format ZIPs."""
    if len(body) > MAX_ZIP_BYTES:
        raise ValueError("edinet_code_list_zip_too_large")
    try:
        with ZipFile(io.BytesIO(body)) as archive:
            members = archive.infolist()
            if len(members) != 1 or members[0].filename != "EdinetcodeDlInfo.csv":
                raise ValueError("edinet_code_list_inventory_invalid")
            if members[0].file_size > MAX_CSV_BYTES or members[0].is_dir():
                raise ValueError("edinet_code_list_csv_too_large")
            with archive.open(members[0]) as entry:
                content = entry.read(MAX_CSV_BYTES + 1)
            if len(content) > MAX_CSV_BYTES:
                raise ValueError("edinet_code_list_csv_too_large")
    except BadZipFile:
        raise ValueError("edinet_code_list_zip_invalid") from None
    try:
        rows = csv.reader(io.StringIO(content.decode("cp932")))
        first = next(rows)
        heading = next(rows)
        if len(first) != 4 or first[0] != "ダウンロード実行日" or first[2] != "件数":
            raise ValueError("edinet_code_list_header_invalid")
        match = re.fullmatch(r"(\d{4})年(\d{2})月(\d{2})日現在", first[1])
        count_match = re.fullmatch(r"(\d+)件", first[3])
        if match is None or count_match is None:
            raise ValueError("edinet_code_list_header_invalid")
        as_of = date(*map(int, match.groups()))
        if as_of > received_on:
            raise ValueError("edinet_code_list_from_future")
        if len(heading) != 13 or (heading[0], heading[6], heading[11]) != (
            "ＥＤＩＮＥＴコード",
            "提出者名",
            "証券コード",
        ):
            raise ValueError("edinet_code_list_columns_invalid")
        count = 0
        issuer_matches = []
        security_matches = []
        for row in rows:
            if len(row) != len(heading):
                raise ValueError("edinet_code_list_row_invalid")
            count += 1
            if row[0] == "E02144":
                issuer_matches.append(row)
            if row[11] == "72030":
                security_matches.append(row)
        if count != int(count_match.group(1)) or len(issuer_matches) != 1 or len(security_matches) != 1:
            raise ValueError("edinet_code_list_count_or_uniqueness_invalid")
        issuer = issuer_matches[0]
        if issuer != security_matches[0] or issuer[6] != "トヨタ自動車株式会社":
            raise ValueError("edinet_code_list_issuer_mismatch")
        return CodeListObservation(
            as_of=as_of.isoformat(),
            count=count,
            edinet_code=issuer[0],
            security_code=issuer[11],
            issuer_name=issuer[6],
        )
    except (UnicodeError, StopIteration, ValueError) as error:
        raise ValueError("edinet_code_list_parse_failed") from error
