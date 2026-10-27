"""Readable, opt-in lawyer drafts; actual PTB dispatch with fake HTTP boundaries."""

from copy import deepcopy
from uuid import UUID

import pytest
from legal_core.case_preparation_contracts import InternalWorkProduct
from legal_core.lawyer_work_product_contracts import LawyerWorkProductView
from telegram_gateway.lawyer_work_product_runtime import parse_steps, render_plan, review_request
from test_ai_first_contracts import work_product

CASE = UUID(int=901)
VERSION = UUID(int=902)
FRAGMENT = UUID(int=903)


def plan_wire(*, state="UNAPPROVED", current=True):
    content = InternalWorkProduct.model_validate(work_product(source={
        "operationId": str(UUID(int=904)), "caseId": str(CASE),
        "factSnapshotSha256": "a" * 64, "context": {
            "asOfDate": "2026-10-26", "riskPolicyVersion": "dental-risk.v4",
            "evidenceTraceSha256": "b" * 64, "clinicDocumentContextTraceSha256": "c" * 64,
        },
    }, legalFragmentIds=[str(FRAGMENT)]))
    return {"versionId": str(VERSION), "reviewState": state,
            "origin": "AI" if state == "UNAPPROVED" else "HUMAN",
            "isCurrent": current, "contentSha256": content.content_sha256,
            "content": content.model_dump(mode="json", by_alias=True), "citations": [{
                "fragmentId": str(FRAGMENT), "versionId": str(UUID(int=905)),
                "documentId": str(UUID(int=906)), "article": "12", "part": None,
                "point": "1", "structuralPath": "статья 12, пункт 1",
                "fragmentText": "Синтетический фрагмент для проверки интерфейса.",
                "textSha256": "d" * 64, "rawSha256": "e" * 64,
                "effectiveFrom": "2020-01-01", "effectiveTo": None,
                "sourceUrl": "https://example.invalid/norm", "documentTitle": "Учебный закон",
                "issuer": None, "officialNumber": "123-ФЗ", "versionDate": None,
                "publicationDate": None,
            }]}


@pytest.mark.parametrize("state,label", [("UNAPPROVED", "требует проверки"),
                                         ("HUMAN_REVISED", "отредактирован"),
                                         ("HUMAN_APPROVED", "утверждён")])
def test_plan_presents_outer_review_state_and_useful_content_without_technical_fields(state, label):
    wire = plan_wire(state=state)
    pages = render_plan(LawyerWorkProductView.model_validate(wire), "DL-TEST-1")
    text = "\n".join(pages)
    assert label in text.lower()
    assert "DL-TEST-1" in text and "1. Подготовить" in text
    assert "Учебный закон" in text and "ст. 12" in text and "п. 1" in text
    for private in (str(VERSION), "a" * 64, "Hermes", "UNAPPROVED", "VERIFIED", "source"):
        assert private not in text
    assert all(len(page.encode("utf-16-le")) // 2 <= 3900 for page in pages)


def test_approval_has_no_edits_and_exact_displayed_immutable_binding():
    view = LawyerWorkProductView.model_validate(plan_wire())
    approved = review_request(view, "APPROVE")
    assert approved.model_dump(by_alias=True, exclude_none=True) == {
        "expectedVersionId": VERSION, "expectedContentSha256": view.content_sha256,
        "decision": "APPROVE",
    }
    with pytest.raises(ValueError):
        review_request(view, "APPROVE", actions=["Подменить содержание."])


def test_numbered_edits_are_bounded_and_leave_facts_source_and_approval_unchanged():
    wire = plan_wire()
    original = deepcopy(wire)
    view = LawyerWorkProductView.model_validate(wire)
    steps = parse_steps("1. Проверить переписку.\n2) Запросить уточнение обстоятельств.")
    reviewed = review_request(view, "REVISE", actions=steps)
    assert reviewed.actions == steps and reviewed.decision == "REVISE"
    assert reviewed.summary is None and reviewed.uncertainties is None
    assert wire == original


@pytest.mark.parametrize("text", ["", "Без нумерации", "2. Неверная нумерация.",
                                 "1. \n2. Пропущено действие.", "1. " + "x" * 601,
                                 "\n".join(f"{i}. Пункт" for i in range(1, 10)),
                                 "1. Написать patient@example.invalid"])
def test_invalid_or_identifying_edit_is_not_staged(text):
    with pytest.raises(ValueError):
        parse_steps(text)
