# ruff: noqa: RUF001
"""Activated real PTB composition; only Telegram and Core transports are synthetic."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from unittest.mock import patch
from uuid import UUID, uuid5

from telegram_gateway import bot
from telegram_gateway.case_wizard import LegalCoreApiError
from telegram_gateway.understanding_client import canonical_hash
from telegram_gateway.understanding_intent import bound_callback
from test_case_experience import ExperienceCore, harness
from test_dialog_isolation import ACTOR, DRAFT

TEXT = "Синтетический посетитель недоволен ожиданием приёма в клинике."


class UnderstandingCore(ExperienceCore):
    def __init__(self):
        super().__init__()
        self.operations, self.starts, self.accepts, self.cancels = {}, [], [], []
        self.lose_accept = self.lose_cancel = False

    async def get_intake_draft(self, draft, actor):
        if actor != ACTOR or self.deny or draft != DRAFT:
            raise LegalCoreApiError(403, "DENIED", "unavailable")
        return await super().get_intake_draft(draft, actor)

    async def _json_request(self, method, path, *, telegram_user_id, **kwargs):
        if telegram_user_id != ACTOR or self.deny:
            raise LegalCoreApiError(403, "DENIED", "unavailable")
        if path.endswith("/understanding"):
            if method == "GET":
                match = next((value for value in self.operations.values()
                              if value["revision"] == self.draft["revision"]), None)
                return {"operation": deepcopy(match["status"]) if match else None}
            assert method == "POST"
            assert kwargs["payload"] == {"expectedRevision": self.draft["revision"]}
            assert set(self.draft["draftData"]) == {
                "intakeVersion", "eventSummary", "incomingSourceStatus",
            }
            assert self.draft["draftData"]["incomingSourceStatus"] == "NOT_ATTACHED"
            operation = uuid5(DRAFT, str(self.draft["revision"]))
            if operation not in self.operations:
                self.operations[operation] = {
                    "revision": self.draft["revision"], "input": deepcopy(self.draft),
                    "status": {"operationId": str(operation), "state": "PENDING",
                               "suggestions": None, "source": None, "acceptance": None},
                }
                self.starts.append(operation)
            return deepcopy(self.operations[operation]["status"])
        if "/intake-preparations/" not in path:
            return await super()._json_request(method, path, telegram_user_id=telegram_user_id,
                                               **kwargs)
        operation = UUID(path.split("/")[3])
        record = self.operations[operation]
        status = record["status"]
        if method == "GET":
            return deepcopy(status)
        if method == "DELETE":
            if status["state"] == "ACCEPTED":
                raise LegalCoreApiError(409, "ALREADY_ACCEPTED", "unavailable")
            status.update(state="MANUAL_CONTINUATION", suggestions=None, source=None)
            self.cancels.append(operation)
            if self.lose_cancel:
                self.lose_cancel = False
                raise LegalCoreApiError(503, "UNAVAILABLE", "unknown")
            return deepcopy(status)
        assert method == "POST" and path.endswith("/acceptance")
        assert status["state"] == "PROPOSED"
        source = status["source"]
        assert kwargs["payload"] == {
            "expectedRevision": source["draftRevision"],
            "expectedInputSha256": source["inputSha256"],
            "expectedProposalSha256": source["proposalSha256"],
        }
        assert self.draft["revision"] == source["draftRevision"]
        self.draft["draftData"].update({key: value for key, value in status["suggestions"].items()
                                       if key != "sourceSpans"})
        self.draft["revision"] += 1
        self.accepts.append(operation)
        status.update(state="ACCEPTED", suggestions=None, source=None,
                      acceptance={"draftId": str(DRAFT),
                                  "acceptedRevision": self.draft["revision"]})
        if self.lose_accept:
            self.lose_accept = False
            raise LegalCoreApiError(503, "UNAVAILABLE", "unknown")
        return {"operationId": str(operation), "acceptedRevision": self.draft["revision"],
                "draft": deepcopy(self.draft)}

    def propose(self, **fields):
        operation = self.starts[-1]
        record = self.operations[operation]
        if record["status"]["state"] != "PENDING":
            return
        suggestions = {**fields, "sourceSpans": {
            field: [{"start": 0, "end": 4}] for field in fields}}
        if isinstance((fields.get("safetyScreening") or {}).get("amount"), dict):
            # Numeric money fixtures must include their real literal source, not
            # the unrelated first four characters used for nonmonetary fields.
            source = record["input"]["draftData"]["eventSummary"]
            suggestions["sourceSpans"]["safetyScreening"] = [
                {"start": 0, "end": len(source)},
            ]
        record["status"].update(
            state="PROPOSED", suggestions=suggestions,
            source={"draftId": str(DRAFT), "draftRevision": record["revision"],
                    "inputSha256": canonical_hash({key: record["input"][key]
                                                   for key in ("draftData", "wizardState")}),
                    "proposalSha256": canonical_hash(suggestions)},
        )


@asynccontextmanager
async def active_harness():
    import test_case_experience as original
    factory = original.build_application_with_case_experience
    with patch.object(original, "build_application_with_case_experience",
                      lambda token: factory(token, enable_understanding=True)):
        async with harness() as (app, _core, sent, dispatch):
            core = UnderstandingCore()
            app.bot_data[bot.LEGAL_CORE_CLIENT_KEY] = core
            yield app, core, sent, dispatch


def test_active_quick_entry_saves_only_narrative_before_fast_pending_response():
    async def scenario():
        async with active_harness() as (_app, core, sent, dispatch):
            await dispatch("quick:start")
            await dispatch("text:" + TEXT)
            assert core.draft["draftData"] == {"intakeVersion": 2, "eventSummary": TEXT,
                                              "incomingSourceStatus": "NOT_ATTACHED"}
            assert len(core.saved) == 1 and len(core.starts) == 1
            assert not core.accepts and not core.jobs
            assert "Hermes" not in "\n".join(item.get("text", "") for item in sent)
            assert any(button.text == "Проверить результат" for row in
                       sent[-1]["reply_markup"].inline_keyboard for button in row)
    asyncio.run(scenario())


def test_proposal_refresh_and_explicit_accept_resume_shared_missing_question():
    async def scenario():
        async with active_harness() as (app, core, sent, dispatch):
            await dispatch("quick:start")
            await dispatch("text:" + TEXT)
            operation = core.starts[-1]
            core.propose(incomingKind="COMPLAINT", eventDate={"date": None, "precision": "UNKNOWN"})
            await dispatch(bound_callback("refresh", operation, DRAFT))
            assert "incomingKind" not in core.draft["draftData"]
            assert any("обращение с недовольством" in item.get("text", "") for item in sent)
            await dispatch(bound_callback("accept", operation, DRAFT))
            assert core.draft["draftData"]["incomingKind"] == "COMPLAINT"
            assert core.draft["draftData"]["eventDate"] == {"date": None, "precision": "UNKNOWN"}
            await dispatch(f"case:draft:{DRAFT}")
            assert app.user_data[ACTOR][bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY] == "SITUATION"
            assert not core.jobs
    asyncio.run(scenario())


def test_pending_controls_recover_after_ram_loss_and_fence_before_manual():
    async def scenario():
        async with active_harness() as (app, core, _sent, dispatch):
            await dispatch("quick:start")
            await dispatch("text:" + TEXT)
            operation = core.starts[-1]
            app.user_data[ACTOR].clear()
            await dispatch(bound_callback("manual", operation, DRAFT))
            assert core.cancels == [operation]
            core.propose(incomingKind="COMPLAINT")  # genuinely late synthetic completion
            assert core.operations[operation]["status"]["state"] == "MANUAL_CONTINUATION"
            assert core.draft["draftData"] == {"intakeVersion": 2, "eventSummary": TEXT,
                                              "incomingSourceStatus": "NOT_ATTACHED"}
            await dispatch(f"case:draft:{DRAFT}")
            assert app.user_data[ACTOR][bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY] == "INCOMING"
    asyncio.run(scenario())
