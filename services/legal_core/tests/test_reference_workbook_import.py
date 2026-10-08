import asyncio
from datetime import date
from uuid import UUID

import httpx2
import pytest

from legal_core.reference_workbook_import import build_plan, import_plan


def row(**overrides):
    return {
        "case_id": "SYNTHETIC_1", "primary_group": "Медицинская деятельность и права пациентов",
        "title": "Synthetic service dispute", "scenario": "A fictional service dispute.",
        "user_question": "What should the fictional clinic check?",
        "answer_short": "Candidate: review fictional records.",
        "answer_full": "A longer fictional candidate answer, not verified law.",
        "review_status": "Assistant-ready", "version_date": "2026-10-04", **overrides,
    }


def test_plan_preserves_every_source_field_but_never_claims_human_approval():
    source = row()
    plan = build_plan("a" * 64, [source], evaluation_date=date(2026, 10, 8))
    item = plan.cases[0]
    assert item.request.group_key == "healthcare"
    assert item.request.expected_route == "ABSTAIN"
    assert item.request.as_of_date == date(2026, 10, 8)
    assert item.request.scenario_text.startswith("Название: Synthetic service dispute\n")
    assert "стартовый параметр" in item.request.scenario_text
    assert all(value in item.full_text for value in source.values())
    assert "не подтверждает" in item.full_text


@pytest.mark.parametrize("mutation", [
    {"scenario": "Call +7 999 123-45-67 about this fictional case."},
    {"primary_group": "Unknown unsupported group"},
    {"case_id": "../not-a-case"}, {"scenario": ""},
])
def test_plan_rejects_identifiers_invalid_shape_or_unknown_group(mutation):
    with pytest.raises(ValueError):
        build_plan("a" * 64, [row(**mutation)], evaluation_date=date(2026, 10, 8))


def test_plan_rejects_duplicate_rows_before_any_api_write():
    with pytest.raises(ValueError):
        build_plan("a" * 64, [row(), row()], evaluation_date=date(2026, 10, 8))


def test_import_uses_only_create_and_material_apis_with_stable_retry_keys():
    calls = []
    case_id = UUID(int=30)

    def respond(request):
        calls.append((request.url.path, request.headers["Idempotency-Key"]))
        assert request.headers["X-Telegram-User-Id"] == "7000000001"
        if request.url.path == "/v1/reference-evaluations":
            return httpx2.Response(201, json={"case": {"id": str(case_id), "status": "DRAFT"}})
        assert request.url.path == f"/v1/reference-evaluations/{case_id}/material"
        assert request.headers["Content-Type"].startswith("text/plain")
        return httpx2.Response(200, json={"case": {"id": str(case_id)}})

    async def run():
        plan = build_plan("a" * 64, [row()], evaluation_date=date(2026, 10, 8))
        async with httpx2.AsyncClient(
            base_url="http://example.test", transport=httpx2.MockTransport(respond)
        ) as client:
            assert await import_plan(plan, actor=7_000_000_001, client=client) == 1
            assert await import_plan(plan, actor=7_000_000_001, client=client) == 1

    asyncio.run(run())
    assert calls[:2] == calls[2:]
    assert calls[0][1] != calls[1][1]
