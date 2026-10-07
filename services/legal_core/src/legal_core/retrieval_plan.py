"""Deterministic query planning for the approved legal corpus.

The reasoning model never chooses arbitrary web/legal sources. This planner turns typed case facts
into a small bounded set of Russian lexical queries executed only by
``ApprovedLegalCorpusRepository``. Only fixed, product-reviewed query strings may cross the
optional external embedding boundary; free-text service wording remains local FTS-only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from itertools import zip_longest
from typing import Final
from uuid import UUID

from legal_core.contracts import FactKey
from legal_core.legal_retrieval import ApprovedLegalCorpusRepository, ApprovedLegalFragment


_BASE_QUERIES: Final = (
    "платные медицинские услуги",
    "права пациента медицинская помощь",
    "ответственность исполнитель медицинские услуги",
)
_REWORK_QUERIES: Final = (
    "безвозмездное устранение недостатков",
    "повторное выполнение работы",
)
_INCIDENT_QUERIES: Final = {
    "QUALITY_COMPLAINT": ("недостатки оказанной услуги", "гарантийный срок"),
    "INFORMED_CONSENT": ("информированное добровольное согласие",),
    "PERSONAL_DATA": ("врачебная тайна", "персональные данные"),
}

_SEMANTIC_SAFE_QUERIES: Final = frozenset(
    (
        *_BASE_QUERIES,
        *_REWORK_QUERIES,
        *(query for queries in _INCIDENT_QUERIES.values() for query in queries),
        "требования потребителя претензия медицинские услуги",
        "возмещение вреда здоровью медицинские услуги",
        "ответственность медицинская организация проверка",
        "защита прав потребителя медицинские услуги",
        "возврат денежных средств медицинские услуги",
        "возмещение убытков вреда медицинские услуги",
        "медицинская документация пациент копии",
    )
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

    if facts.get(FactKey.INTAKE_VERSION) == "GUIDED_V2":
        areas = _tokens(facts.get(FactKey.SITUATION_AREAS))
        if "PERSONAL_DATA" in areas:
            queries.extend(_INCIDENT_QUERIES["PERSONAL_DATA"])
        if "MEDICAL_RECORDS" in areas:
            queries.append("медицинская документация пациент копии")

        actions = _tokens(facts.get(FactKey.CLINIC_ACTIONS))
        if "OFFERED_REFUND" in actions:
            queries.append("возврат денежных средств медицинские услуги")
        if "OFFERED_CORRECTION" in actions:
            queries.extend(_REWORK_QUERIES)

        health_signals = _tokens(facts.get(FactKey.HEALTH_CONSEQUENCE_SIGNALS))
        if health_signals & {"COMPLICATION_OR_WORSENING", "HOSPITALIZATION"}:
            queries.append("возмещение вреда здоровью медицинские услуги")

        # V2 narratives and service labels are not legal classifications or search inputs.
        # These fixed phrases only select candidates, never establish a legal conclusion.
        return tuple(dict.fromkeys(queries))

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
    if "REWORK_DEMAND" in demands:
        queries.extend(_REWORK_QUERIES)

    # These are search candidates, not legal findings. A quality complaint does not
    # establish a defect or the existence/applicability of a contractual warranty.
    incidents = _tokens(facts.get(FactKey.INCIDENT_TYPES)) | _tokens(
        facts.get(FactKey.PRIMARY_INCIDENT_TYPE)
    )
    for incident, scenario_queries in _INCIDENT_QUERIES.items():
        if incident in incidents:
            queries.extend(scenario_queries)

    service_type = facts.get(FactKey.SERVICE_TYPE)
    if isinstance(service_type, str) and service_type.strip():
        # Free text may contain identifiers or prompt injection. It is useful for local ranking,
        # but intentionally never becomes an external semantic-embedding request.
        queries.append(f"медицинская услуга {service_type.strip()[:120]}")

    return tuple(dict.fromkeys(query.strip() for query in queries if query.strip()))


def is_semantic_safe_query(query: str) -> bool:
    """Return true only for fixed, product-reviewed legal retrieval phrases."""

    return query in _SEMANTIC_SAFE_QUERIES


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

    ranked_results: list[list[ApprovedLegalFragment]] = []
    for query in queries:
        ranked_results.append(
            await repository.search(
                query,
                as_of_date=as_of_date,
                limit=limit_per_query,
                semantic=is_semantic_safe_query(query),
            )
        )

    # Give each scenario query a place before filling the remaining budget with lower ranks.
    # Otherwise broad base queries can consume the entire context before harm/refund queries run.
    unique: dict[UUID, ApprovedLegalFragment] = {}
    for rank in zip_longest(*ranked_results):
        for fragment in rank:
            if fragment is None:
                continue
            unique.setdefault(fragment.fragment_id, fragment)
            if len(unique) >= max_fragments:
                return list(unique.values())
    return list(unique.values())
