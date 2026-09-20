"""Plain-text presentation of canonical, server-verified legal conclusions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from legal_core.contracts import CanonicalReport


def legal_conclusion_lines(report_json: Mapping[str, Any]) -> list[str]:
    """Keep legacy reports readable; validate new conclusions before showing any text."""

    if "legalConclusions" not in report_json:
        return []
    raw = report_json["legalConclusions"]
    if not isinstance(raw, list):
        raise ValueError("canonical report has invalid legal conclusions")
    if not raw:
        return [
            "",
            "Юридическая оценка:",
            "Отдельные проверенные юридические выводы в этом отчёте не сохранены.",
        ]
    report = CanonicalReport.model_validate(report_json)
    source_numbers = {
        source.fragment_id: index
        for index, source in enumerate(report.legal_basis.sources, start=1)
    }
    lines = ["", "Юридическая оценка (проверенные выводы):"]
    for index, conclusion in enumerate(report.legal_conclusions, start=1):
        citations = ", ".join(
            f"[{source_numbers[fragment_id]}]"
            for fragment_id in conclusion.evidence_fragment_ids
        )
        # Do not truncate or regenerate legal wording: a qualifier may be at its end.
        # The existing analysis message splitter handles Telegram's UTF-16 limit.
        lines.extend([f"{index}. {conclusion.text}", f"Основание: {citations}."])
    lines.append("Это внутренняя оценка; окончательное решение принимает ответственный специалист.")
    return lines
