"""Synthetic calendar regression; provider output is not a live-model recall claim."""

import asyncio
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

from agent_orchestrator.intake_understanding import IntakeUnderstanding


SOURCE = (
    "Учебный кейс: установили коронку 10 сентября этого года, "
    "15 октября пациент обратился с жалобой на скол и просит вернуть оплату."
)


def test_initial_model_receives_authoritative_calendar_and_separate_event_service_roles():
    async def scenario():
        provider = SimpleNamespace(complete_json=AsyncMock(return_value={"sourceSpans": {}}))
        result = await IntakeUnderstanding(researcher=provider).understand(
            SOURCE, as_of_date=date(2026, 10, 23),
        )
        request = provider.complete_json.call_args.kwargs
        assert json.loads(request["user"]) == {"source": SOURCE, "asOfDate": "2026-10-23"}
        instructions = request["system"].split("JSON Schema:", 1)[0]
        assert "Omit ambiguous episode bindings and relative dates" not in instructions
        assert "этого года" in instructions and "asOfDate" in instructions
        assert "eventDate is the patient's communication/complaint date" in instructions
        assert "SERVICE_PROVIDED_DATE is the date of that particular service" in instructions
        assert result.event_date is None  # No fact invented when model abstains.

    asyncio.run(scenario())
