# ruff: noqa: RUF001
"""Opt-in internal plan display; Legal Core owns authorization and every review write."""

from __future__ import annotations

import re
from typing import Literal
from uuid import UUID

from legal_core.lawyer_work_product_contracts import LawyerWorkProductReview, LawyerWorkProductView

from telegram_gateway.quick_intake import contains_probable_person_name

ENABLED = "ai_first_lawyer_draft_enabled"
PENDING = "lawyer_work_product_pending"


def review_request(
    view: LawyerWorkProductView, decision: Literal["APPROVE", "REVISE"], *,
    actions: list[str] | None = None,
) -> LawyerWorkProductReview:
    return LawyerWorkProductReview(
        expectedVersionId=view.version_id, expectedContentSha256=view.content_sha256,
        decision=decision, actions=actions,
    )


def parse_steps(raw: str) -> list[str]:
    """Explicit numbered edits only; never redact or reinterpret a lawyer's decision."""
    try:
        if not isinstance(raw, str) or contains_probable_person_name(raw):
            raise ValueError("identifying input")
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        if not 1 <= len(lines) <= 8:
            raise ValueError("bounded step count")
        actions = []
        for index, line in enumerate(lines, 1):
            match = re.fullmatch(r"([1-8])[.)]\s+(.+)", line)
            if match is None or int(match[1]) != index:
                raise ValueError("numbered sequential steps required")
            actions.append(match[2].strip())
        validated = LawyerWorkProductReview(
            expectedVersionId=UUID(int=0), expectedContentSha256="0" * 64,
            decision="REVISE", actions=actions,
        )
        assert validated.actions is not None
        return validated.actions
    except (ValueError, TypeError):
        pass
    raise ValueError("Пришлите от 1 до 8 пунктов с нумерацией 1., 2. и далее: "
                     "каждый с новой строки, до 600 символов, без персональных данных.")


def _pages(blocks: list[str]) -> tuple[str, ...]:
    pages, current, units = [], "", 0
    for character in "\n\n".join(blocks):
        width = len(character.encode("utf-16-le")) // 2
        if units + width > 3900:
            pages.append(current)
            current, units = "", 0
        current += character
        units += width
    if current:
        pages.append(current)
    return tuple(pages)


def render_plan(view: LawyerWorkProductView, public_number: str) -> tuple[str, ...]:
    labels = {"UNAPPROVED": "Проект плана — требует проверки юриста.",
              "HUMAN_REVISED": "План отредактирован юристом — ожидает утверждения.",
              "HUMAN_APPROVED": "План утверждён юристом."}
    blocks = [f"📋 План решения · {public_number}", labels[view.review_state]]
    if not view.is_current:
        blocks.append("Обстоятельства или основания изменились. Эта версия требует обновления; "
                      "утверждать её нельзя.")
    content = view.content
    blocks.extend(["Ситуация\n" + content.summary, "Рекомендуемые действия\n" + "\n".join(
        f"{index}. {action}" for index, action in enumerate(content.actions, 1)
    )])
    if view.citations:
        citations = []
        for norm in view.citations:
            title = norm.document_title[:140]
            references = ", ".join(label + value for label, value in (
                ("ст. ", norm.article), ("ч. ", norm.part), ("п. ", norm.point),
            ) if value)
            citations.append("• " + title + (" — " + references if references else ""))
        blocks.append("Нормативные основания\n" + "\n".join(dict.fromkeys(citations)))
    if content.uncertainties:
        blocks.append("Что ещё уточнить\n" + "\n".join("• " + item
                                                     for item in content.uncertainties))
    return _pages(blocks)
