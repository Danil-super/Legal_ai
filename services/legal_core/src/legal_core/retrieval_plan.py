"""Deterministic query planning for the approved legal corpus.

The reasoning model never chooses arbitrary web/legal sources.  This planner turns typed case
facts into a small bounded set of Russian lexical queries executed only by
``ApprovedLegalCorpusRepository``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Final
from uuid import UUID

from legal_core.contracts import FactKey
from legal_core.legal_retrieval import ApprovedLegalCorpusRepository, ApprovedLegalFragment


_BASE_QUERIES: Final = (
    "платные медицинские услуги",
    "права пациента медицинская помощь",
    "ответственность исполнитель медицинские услуги",
)


def _is_yes(value: object) -> bool:
    return value is True or value == "YES"


def _tokens(value: object) -> set[str]:
    if isinstance(value, str):
        return {value.upper()}
    if isinstance(value, (list, tuple, set)):
        return {str(item).upper() for item in value}
    return set()


def plan_legal_queries(facts: Mapping[FactKey, object]) -> tuple[str, ...]:
    queries = list(_BASE_QUERIES)

    if _is_yes(facts.get(FactKey.FORMAL_CLAIM)):
        queries.append("требования потребителя претензия медицинские услуги")
    if _is_yes(facts.get(FactKey.HARM_CLAIMED)):
        queries.append("возмещение вреда здоровью медицинские услуги")
    if _is_yes(facts.get(FactKey.REGULATOR_OR_COURT)):
        queries.append("ответственность медицинская организация проверка")
    if _is_yes(facts.get(FactKey.REGULATOR_THREAT)):
        queries.append("защита прав потребителя медицинские услуги")

    demands = _tokens(facts.get(FactKey.PATIENT_DEMAND))
    if any("REFUND" in token or "RETURN" in token for token in demands):
        queries.append("возврат денежных средств медицинские услуги")
    if any("COMPENS" in token or "DAMAGE" in token for token in demands):
        queries.append("возмещение убытков вреда медицинские услуги")
    if any("DOCUMENT" in token or "RECORD" in token for token in demands):
        queries.append("медицинская документация пациент копии")

    service_type = facts.get(FactKey.SERVICE_TYPE)
    if isinstance(service_type, str) and service_type.strip():
        # Service wording helps rank relevant fragments without letting the model browse or invent
        # a source. Limit free-text contribution so a user message cannot turn into an unbounded
        # retrieval prompt.
        queries.append(f"медицинская услуга {service_type.strip()[:120]}")

    return tuple(dict.fromkeys(query.strip() for query in queries if query.strip()))


async def retrieve_planned_evidence(
    repository: ApprovedLegalCorpusRepository,
    *,
    queries: Sequence[str],
    as_of_date: date,
    limit_per_query: int = 5,
    max_fragments: int = 20,
) -> list[ApprovedLegalFragment]:
    if not 1 <= limit_per_query <= 10:
        raise ValueError("limit_per_query must be between 1 and 10")
    if not 1 <= max_fragments <= 30:
        raise ValueError("max_fragments must be between 1 and 30")

    unique: dict[UUID, ApprovedLegalFragment] = {}
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
