# ruff: noqa: RUF001
"""Bounded, retryable draft import. No review, legal approval or model calls."""

from datetime import date
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx2
from pydantic import Field, model_validator

from legal_core.contracts import ContractModel
from legal_core.pseudonymization import pseudonymize_text
from legal_core.reference_evaluation_contracts import (
    ReferenceEvaluationCreateRequest,
    ReferenceEvaluationGroup,
)

_GROUPS: dict[str, ReferenceEvaluationGroup] = {
    "Клинические справочные материалы": "clinical",
    "Труд и квалификация специалистов": "labour",
    "Суды, экспертиза и юридическая позиция": "courts",
    "Суды, экспертиза и юридическая помощь": "courts",
    "Персональные данные и информационная безопасность": "privacy",
    "Персональные данные и информация": "privacy",
    "Лицензирование и контроль": "licensing",
    "Медицинская деятельность и права пациентов": "healthcare",
    "Кодексы и общие правовые нормы": "general",
}


class ImportedReference(ContractModel):
    source_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    request: ReferenceEvaluationCreateRequest = Field(repr=False)
    full_text: str = Field(min_length=10, max_length=20_000, repr=False)

    @model_validator(mode="after")
    def require_deidentified_draft(self) -> "ImportedReference":
        if self.request.expected_route != "ABSTAIN":
            raise ValueError("IMPORT_REQUIRES_DRAFT_STARTING_ROUTE")
        if any(pseudonymize_text(text).changed for text in (
            self.request.scenario_text, self.full_text,
        )):
            raise ValueError("IMPORT_DIRECT_IDENTIFIER_DETECTED")
        return self


class ReferenceImportPlan(ContractModel):
    workbook_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cases: list[ImportedReference] = Field(min_length=1, max_length=1000, repr=False)

    @model_validator(mode="after")
    def require_unique_cases(self) -> "ReferenceImportPlan":
        if len({item.source_id for item in self.cases}) != len(self.cases):
            raise ValueError("IMPORT_DUPLICATE_CASE")
        return self


def build_plan(
    workbook_sha256: str, rows: list[dict[str, str]], *, evaluation_date: date
) -> ReferenceImportPlan:
    """Keep all workbook values in a private TXT; use only a recap in the card."""
    cases = []
    for row in rows:
        if not all(row.get(key, "").strip() for key in (
            "case_id", "title", "scenario", "user_question", "primary_group",
        )):
            raise ValueError("IMPORT_CASE_INCOMPLETE")
        if row["primary_group"] not in _GROUPS:
            raise ValueError("IMPORT_UNKNOWN_GROUP")
        title = " ".join(row["title"].split())
        summary = (
            f"Название: {title}\nКод исходника: {row['case_id']}\n\n"
            f"Фабула: {row['scenario']}\n\nВопрос: {row['user_question']}\n\n"
            f"Кандидат ответа из таблицы: {row.get('answer_short', '')}\n\n"
            "Это черновик, не одобренный эталон. Дата оценки — дата импорта, не дата события. "
            "Безопасный отказ — стартовый параметр: ожидаемый маршрут в исходнике не задан. "
            "Все исходные поля и полный ответ сохранены в приложенном материале."
        )
        full_text = (
            "Исходные поля эталонного кейса. Пометка готовности в таблице не подтверждает "
            "юридическую правильность или approval в боте.\n\n"
            + "\n\n".join(f"{key}:\n{value}" for key, value in sorted(row.items()))
        )
        cases.append(ImportedReference(
            source_id=row["case_id"],
            request=ReferenceEvaluationCreateRequest(
                groupKey=_GROUPS[row["primary_group"]],
                asOfDate=evaluation_date, expectedRoute="ABSTAIN", scenarioText=summary,
            ),
            full_text=full_text,
        ))
    return ReferenceImportPlan(workbook_sha256=workbook_sha256, cases=cases)


async def import_plan(
    plan: ReferenceImportPlan, *, actor: int, client: httpx2.AsyncClient
) -> int:
    """Use existing grant/tenant/audit/idempotency checks; never submit or approve."""
    if actor <= 0:
        raise ValueError("IMPORT_ACTOR_INVALID")
    for item in plan.cases:
        intent = f"reference-workbook-import.v1:{plan.workbook_sha256}:{item.source_id}"
        headers = {"X-Telegram-User-Id": str(actor)}
        response = await client.post(
            "/v1/reference-evaluations",
            headers={**headers, "Idempotency-Key": str(uuid5(NAMESPACE_URL, intent + ":create"))},
            json=item.request.model_dump(mode="json", by_alias=True),
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict) or not isinstance(result.get("case"), dict):
            raise ValueError("IMPORT_CREATE_RESPONSE_INVALID")
        if result["case"].get("status") != "DRAFT":
            raise ValueError("IMPORT_CREATE_STATUS_INVALID")
        case_id = UUID(result["case"]["id"])
        material = await client.post(
            f"/v1/reference-evaluations/{case_id}/material",
            headers={**headers, "Idempotency-Key": str(uuid5(NAMESPACE_URL, intent + ":material")),
                     "Content-Type": "text/plain; charset=utf-8",
                     "X-Source-Filename": f"reference-{item.source_id}.txt"},
            content=item.full_text.encode("utf-8"),
        )
        material.raise_for_status()
        uploaded = material.json()
        if not isinstance(uploaded, dict) or not isinstance(uploaded.get("case"), dict):
            raise ValueError("IMPORT_MATERIAL_RESPONSE_INVALID")
        if uploaded["case"].get("id") != str(case_id):
            raise ValueError("IMPORT_MATERIAL_CASE_MISMATCH")
    return len(plan.cases)
