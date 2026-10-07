# ruff: noqa: RUF001
"""Read-only guidance for existing preparation blockers, never an approval decision."""

from collections.abc import Sequence

PREPARATION_NEXT_STEP = (
    "Откройте карточку исходного файла: там указаны недостающие данные. "
    "Нужны подтверждённые реквизиты и даты с указанием места в источнике, "
    "полнота текста и проверенные фрагменты. "
    "Юрист сверяет данные и текст; администратору нужно подготовить нормативную версию "
    "и связать все части с версиями. После этого доступно отдельное утверждение группы."
)

PREPARATION_REASON_CODES = {"METADATA_REQUIRED", "PARTS_NOT_PREPARED", "PARTS_UNBOUND"}

_FIELD_LABELS = {
    "canonical_key": "идентификатор акта в реестре (готовит администратор)",
    "document_type": "вид нормативного акта",
    "issuer": "орган, издавший акт",
    "official_number": "номер акта",
    "adoption_date": "дата принятия акта",
    "publication_date": "дата официального опубликования",
    "version_date": "дата редакции",
    "effective_from": "дата начала действия редакции",
}


def preparation_requirements(
    missing_fields: Sequence[str], part_keys: Sequence[str], *, extraction_scope: object,
) -> list[str]:
    """Translate server-supplied missing fields while retaining exact part attribution."""
    lines = []
    if extraction_scope != "FULL_DOCUMENT":
        lines.append(
            "Статус: полнота текста не подтверждена. Нужна сверка полного текста "
            "и фрагментов с оригиналом; успешного извлечения текста недостаточно."
        )
    labels = {
        key: "Документ" if len(part_keys) == 1 else f"Часть {index}"
        for index, key in enumerate(part_keys, 1)
    }
    grouped: dict[str, list[str]] = {}
    for value in missing_fields:
        if value == "intended_parts":
            lines.append("Нужно определить все части исходного документа.")
            continue
        key, _, field = value.partition(":")
        label = labels.get(key, "Реквизиты")
        requirement = _FIELD_LABELS.get(
            field, "дополнительные реквизиты (уточнить у администратора)",
        )
        group = grouped.setdefault(label, [])
        if requirement not in group:
            group.append(requirement)
    if grouped:
        lines.append("Не хватает для подготовки версии:")
        lines.extend(f"• {label}: {', '.join(fields)}." for label, fields in grouped.items())
    if missing_fields or extraction_scope != "FULL_DOCUMENT":
        lines.append(
            "Юрист проверяет реквизиты и даты по источнику. Администратор сохраняет "
            "подтверждённые данные и доказательство полноты; эта карточка не редактирует их."
        )
    return lines
