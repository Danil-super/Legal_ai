# ruff: noqa: RUF001
"""Save only direct narrative before optional model dispatch, with stable retry keys."""

import asyncio
from copy import deepcopy
from uuid import UUID

import pytest
from telegram_gateway.case_wizard import LegalCoreApiError
from telegram_gateway.understanding_source import save_source

DRAFT = UUID("00000000-0000-4000-8000-000000000039")
TEXT = "Синтетическая ситуация с длительным ожиданием приёма."


class SourceCore:
    def __init__(self, lost=None):
        self.draft = {"id": str(DRAFT), "revision": 1, "wizardState": "INCOMING",
                      "draftData": {"intakeVersion": 2}}
        self.requests, self.replays = [], {}
        self.lost = lost
        self.writes = 0

    async def get_intake_draft(self, draft_id, actor):
        return await self._json_request("GET", f"/v1/telegram-intake-drafts/{draft_id}",
                                        telegram_user_id=actor)

    async def _json_request(self, method, path, **kwargs):
        self.requests.append((method, path, deepcopy(kwargs)))
        if method == "GET":
            return deepcopy(self.draft)
        key = (method, path, kwargs["idempotency_key"])
        if key in self.replays:
            assert kwargs["payload"] == self.replays[key][0]
            return deepcopy(self.replays[key][1])
        if method == "PUT":
            body = kwargs["payload"]
            assert body["expectedRevision"] == self.draft["revision"]
            self.draft.update(revision=self.draft["revision"] + 1,
                              wizardState=body["wizardState"], draftData=body["draftData"])
            self.writes += 1
        result = deepcopy(self.draft)
        self.replays[key] = (deepcopy(kwargs["payload"]), deepcopy(result))
        if self.lost == method:
            self.lost = None
            raise LegalCoreApiError(503, "LEGAL_CORE_UNAVAILABLE", "unknown outcome")
        return result


@pytest.mark.parametrize("lost", [None, "POST", "PUT"])
def test_source_is_saved_once_across_unknown_response_and_replayed_original_event(lost):
    async def scenario():
        core = SourceCore(lost)
        first = await save_source(core, 777, 777, 99, TEXT)
        second = await save_source(core, 777, 777, 99, TEXT)
        assert first == second
        assert core.writes == 1
        assert first["draftData"] == {"intakeVersion": 2, "eventSummary": TEXT,
                                      "incomingSourceStatus": "NOT_ATTACHED"}
        assert first["wizardState"] == "INCOMING"
        assert not any("understanding" in path for _, path, _ in core.requests)
        creates = [kwargs["idempotency_key"] for method, _, kwargs in core.requests
                   if method == "POST"]
        assert len(set(creates)) == 1
    asyncio.run(scenario())


def test_original_event_cannot_replace_a_later_user_edit():
    async def scenario():
        core = SourceCore()
        await save_source(core, 777, 777, 99, TEXT)
        core.draft["draftData"]["incomingKind"] = "UNKNOWN"
        core.draft["revision"] += 1
        with pytest.raises(LegalCoreApiError):
            await save_source(core, 777, 777, 99, TEXT)
        assert core.writes == 1
        assert core.draft["draftData"]["incomingKind"] == "UNKNOWN"
    asyncio.run(scenario())


def test_edit_reuses_saved_draft_and_refuses_to_discard_confirmed_answers():
    async def scenario():
        core = SourceCore()
        await save_source(core, 777, 777, 99, TEXT)
        edited = TEXT + " Уточнение описания."
        await save_source(core, 777, 777, 100, edited, edit_draft_id=DRAFT)
        assert core.draft["draftData"]["eventSummary"] == edited
        assert len([1 for method, _, _ in core.requests if method == "POST"]) == 1
        core.draft["draftData"]["incomingKind"] = "UNKNOWN"
        with pytest.raises(LegalCoreApiError):
            await save_source(core, 777, 777, 101, TEXT, edit_draft_id=DRAFT)
        assert core.draft["draftData"]["incomingKind"] == "UNKNOWN"
    asyncio.run(scenario())
