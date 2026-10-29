"""A retained plan remains readable when its current file context becomes stale."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from legal_core import lawyer_work_products as module
from legal_core.case_api import ApiError
from legal_core.case_preparation_contracts import InternalWorkProduct
from legal_core.risk_engine import fact_snapshot_sha256
from test_ai_first_contracts import work_product


@pytest.mark.parametrize("code", [
    "ANALYSIS_CONTEXT_STALE", "CASE_MATERIAL_CONTEXT_UNAVAILABLE", "UNRELATED_ERROR",
])
def test_historical_plan_handles_only_known_material_context_failures(monkeypatch, code):
    payload = work_product()
    payload["source"]["factSnapshotSha256"] = fact_snapshot_sha256({})
    content = InternalWorkProduct.model_validate(payload)
    row = SimpleNamespace(
        id=uuid4(), payload_json=payload, content_sha256=content.content_sha256,
        reference_example_trace_sha256=None, review_state="UNAPPROVED", origin="AI",
    )
    actor = SimpleNamespace(clinic_id=content.clinic_id)
    case = SimpleNamespace(id=content.source.case_id, status="ESCALATION_REQUIRED")
    monkeypatch.setattr(module, "_latest", AsyncMock(return_value=row))
    monkeypatch.setattr(module, "_current_fact_rows", AsyncMock(return_value=[]))
    monkeypatch.setattr(module, "_policy", AsyncMock(return_value=SimpleNamespace(
        domain=SimpleNamespace(version=content.risk_policy_version),
    )))
    monkeypatch.setattr(module, "_analysis_date", lambda facts: content.as_of_date)
    retrieval = AsyncMock(side_effect=ApiError(status_code=409, code=code,
                                              message="Synthetic context failure"))
    monkeypatch.setattr(module, "_retrieve_analysis_state", retrieval)

    async def scenario():
        if code == "UNRELATED_ERROR":
            with pytest.raises(ApiError) as error:
                await module._view(None, actor=actor, case=case, row=row)
            assert error.value.code == code
        else:
            result = await module._view(None, actor=actor, case=case, row=row)
            assert not result.is_current
            assert result.citations == []
            assert result.content.model_dump(mode="json", by_alias=True) == payload
        retrieval.assert_awaited_once()

    asyncio.run(scenario())
