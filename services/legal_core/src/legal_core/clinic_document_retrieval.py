"""Approved-only tenant clinic document context for bounded case analysis.

Clinic documents are contractual/internal context, never legal authority. Retrieval stays local to
PostgreSQL and never calls an embedding provider because clinic text can contain sensitive data.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from legal_core.contracts import FactKey


@dataclass(frozen=True, slots=True)
class ApprovedClinicDocumentFragment:
    fragment_id: UUID
    version_id: UUID
    document_id: UUID
    document_key: str
    document_type: str
    document_title: str
    version_no: int
    valid_from: date | None
    valid_to: date | None
    ordinal: int
    structural_path: str
    fragment_text: str
    text_sha256: str
    raw_sha256: str


_FRAGMENT_COLUMNS = """
    fragment_id, version_id, document_id, document_key, document_type,
    document_title, version_no, valid_from, valid_to, ordinal,
    structural_path, fragment_text, text_sha256, raw_sha256
"""

_SEARCH_APPROVED_CLINIC_CONTEXT = text(
    f"""
    SELECT {_FRAGMENT_COLUMNS}
      FROM approved_clinic_document_fragments
     WHERE clinic_id = :clinic_id
       AND (CAST(:as_of_date AS date) IS NULL
            OR ((valid_from IS NULL OR valid_from <= CAST(:as_of_date AS date))
                AND (valid_to IS NULL OR valid_to > CAST(:as_of_date AS date))))
       AND (
            strpos(lower(fragment_text), lower(:query)) > 0
            OR strpos(lower(document_title), lower(:query)) > 0
            OR strpos(lower(document_key), lower(:query)) > 0
            OR to_tsvector('russian', fragment_text || ' ' || document_title)
               @@ plainto_tsquery('russian', :query)
       )
     ORDER BY CASE
                  WHEN strpos(lower(fragment_text), lower(:query)) > 0 THEN 2
                  WHEN strpos(lower(document_title), lower(:query)) > 0 THEN 1
                  ELSE 0
              END DESC,
              document_key,
              version_no DESC,
              ordinal,
              fragment_id
     LIMIT :limit
    """
)

_MAX_CLINIC_QUERIES = 8


def _row_fragment(row: RowMapping) -> ApprovedClinicDocumentFragment:
    return ApprovedClinicDocumentFragment(**dict(row))


def _is_yes(value: object) -> bool:
    return value is True or value == "YES"


def _tokens(value: object) -> set[str]:
    if isinstance(value, str):
        return {value.upper()}
    if isinstance(value, dict):
        raw_values = value.get("values")
        if isinstance(raw_values, (list, tuple, set)):
            return {str(item).upper() for item in raw_values}
        raw_value = value.get("value")
        if isinstance(raw_value, str):
            return {raw_value.upper()}
    if isinstance(value, (list, tuple, set)):
        return {str(item).upper() for item in value}
    return set()


def _service_specific_queries(value: object) -> list[str]:
    if not isinstance(value, str) or not value.strip():
        return []
    service = value.strip()[:120]
    folded = service.casefold()
    queries = [service]
    if "имплант" in folded:
        queries.extend(["имплант", "после имплант"])
    elif any(token in folded for token in ("корон", "винир", "протез")):
        queries.extend(["протез", "гарант"])
    elif any(token in folded for token in ("удален", "хирург")):
        queries.extend(["хирург", "удален"])
    elif any(token in folded for token in ("ортодонт", "брекет")):
        queries.append("ортодонт")
    elif any(token in folded for token in ("эндодонт", "канал")):
        queries.append("эндодонт")
    return queries


def _warranty_sensitive_incident(facts: Mapping[FactKey, object]) -> bool:
    incident_tokens = _tokens(facts.get(FactKey.INCIDENT_TYPES))
    incident_tokens.update(_tokens(facts.get(FactKey.PRIMARY_INCIDENT_TYPE)))
    return any(
        marker in token
        for token in incident_tokens
        for marker in ("CROWN", "VENEER", "RESTORATION", "IMPLANT", "PROSTH")
    )


def plan_clinic_document_queries(facts: Mapping[FactKey, object]) -> tuple[str, ...]:
    """Plan bounded, local-only searches over clinic-approved documents.

    Case-specific documents are intentionally searched before generic contract/consent material so
    the bounded context budget is spent on the documents most likely to matter for this incident.
    These strings never cross an embedding/API boundary.
    """

    queries: list[str] = []
    queries.extend(_service_specific_queries(facts.get(FactKey.SERVICE_TYPE)))

    if _is_yes(facts.get(FactKey.FORMAL_CLAIM)):
        queries.append("претенз")

    demands = _tokens(facts.get(FactKey.PATIENT_DEMAND))
    if any("REFUND" in token or "RETURN" in token for token in demands):
        queries.append("возврат")
    if any("DOCUMENT" in token or "RECORD" in token for token in demands):
        queries.append("документ")

    if _warranty_sensitive_incident(facts):
        queries.append("гарант")

    queries.extend(["договор", "согласие", "гарант"])
    unique = tuple(dict.fromkeys(query for query in queries if query.strip()))
    return unique[:_MAX_CLINIC_QUERIES]


class ApprovedClinicDocumentContextRepository:
    """Read only the latest approved state exposed by the tenant-secured SQL view."""

    def __init__(self, session: AsyncSession, *, clinic_id: UUID) -> None:
        self._session = session
        self._clinic_id = clinic_id

    async def search(
        self,
        query: str,
        *,
        as_of_date: date | None,
        limit: int = 5,
    ) -> list[ApprovedClinicDocumentFragment]:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("clinic document query must not be blank")
        if len(normalized_query) > 500:
            raise ValueError("clinic document query must not exceed 500 characters")
        if not 1 <= limit <= 20:
            raise ValueError("clinic document search limit must be between 1 and 20")
        result = await self._session.execute(
            _SEARCH_APPROVED_CLINIC_CONTEXT,
            {
                "clinic_id": self._clinic_id,
                "query": normalized_query,
                "as_of_date": as_of_date,
                "limit": limit,
            },
        )
        return [_row_fragment(row) for row in result.mappings()]


async def retrieve_planned_clinic_context(
    repository: ApprovedClinicDocumentContextRepository,
    *,
    queries: Sequence[str],
    as_of_date: date,
    limit_per_query: int = 3,
    max_fragments: int = 12,
) -> list[ApprovedClinicDocumentFragment]:
    if not 1 <= limit_per_query <= 10:
        raise ValueError("limit_per_query must be between 1 and 10")
    if not 0 <= max_fragments <= 20:
        raise ValueError("max_fragments must be between 0 and 20")
    if max_fragments == 0:
        return []

    unique: dict[UUID, ApprovedClinicDocumentFragment] = {}
    for query in queries:
        for fragment in await repository.search(
            query,
            as_of_date=as_of_date,
            limit=limit_per_query,
        ):
            unique.setdefault(fragment.fragment_id, fragment)
            if len(unique) >= max_fragments:
                return list(unique.values())
    return list(unique.values())


def clinic_document_context_trace_sha256(
    fragments: Sequence[ApprovedClinicDocumentFragment],
    *,
    as_of_date: date,
) -> str:
    payload = {
        "asOfDate": as_of_date.isoformat(),
        "kind": "CLINIC_DOCUMENT_CONTEXT",
        "fragments": [
            {
                "fragmentId": str(fragment.fragment_id),
                "versionId": str(fragment.version_id),
                "documentId": str(fragment.document_id),
                "documentKey": fragment.document_key,
                "versionNo": fragment.version_no,
                "validFrom": (
                    None if fragment.valid_from is None else fragment.valid_from.isoformat()
                ),
                "validTo": None if fragment.valid_to is None else fragment.valid_to.isoformat(),
                "textSha256": fragment.text_sha256,
                "rawSha256": fragment.raw_sha256,
            }
            for fragment in fragments
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
