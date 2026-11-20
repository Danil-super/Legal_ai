# ruff: noqa: RUF001
"""Exercise visible controls through the composed PTB dispatcher, not handler calls."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import test_case_experience as experience
from telegram_gateway import clinic_document_library_runtime as clinic_library
from telegram_gateway import legal_library_runtime as legal_library
from telegram_gateway.guided_case_intake_v2 import choice_items
from telegram_gateway.ui import BOT_DESCRIPTION, SCREENS, START_MESSAGE
from test_answer_runtime import AnswerCore
from test_answer_staged_recovery import amount_draft
from test_dialog_isolation import ACTOR, DRAFT
from test_intake_single_summary import await_state, simplified_harness


@pytest.fixture
def production_composition(monkeypatch):
    monkeypatch.setenv("AGENT_ORCHESTRATOR_URL", "http://synthetic-agent.invalid")
    monkeypatch.setenv("AGENT_INTERNAL_KEY", "synthetic-button-audit-" * 3)
    factory = experience.build_application_with_case_experience
    monkeypatch.setattr(experience, "build_application_with_case_experience", lambda token: factory(
        token, enable_understanding=True, enable_answer_interpretation=True,
        enable_simplified_dialogue=True, enable_lawyer_work_products=True,
    ))


@pytest.mark.parametrize("role", ["CLINIC_OWNER", "CLINIC_ADMIN", "CLINIC_LAWYER"])
@pytest.mark.parametrize("control,expected", [
    ("features", "ВОЗМОЖНОСТИ"), ("workflow", "КАК ЭТО"),
    ("privacy", "КОНФИДЕНЦИАЛЬНОСТЬ"), ("about", "О ПРОЕКТЕ"),
    ("account:id", str(ACTOR)), ("help", "/menu"),
    ("/start", "DENTAL LEGAL AI"), ("/menu", "DENTAL LEGAL AI"),
    ("/help", "/menu"), ("/whoami", str(ACTOR)),
])
def test_shared_menu_controls_and_return_work_for_each_role(
    production_composition, role, control, expected,
):
    async def scenario():
        async with experience.harness() as (_app, core, sent, dispatch):
            core.get_actor = AsyncMock(return_value={"role": role})
            await dispatch(control)
            assert expected in (sent[-1].get("text") or sent[-1].get("caption", ""))
            await dispatch("menu")
            buttons = [b.callback_data for row in sent[-1]["reply_markup"].inline_keyboard
                       for b in row]
            assert "quick:start" in buttons
            assert ("team:open" in buttons) == (role == "CLINIC_OWNER")
            assert "case:start" not in buttons
    asyncio.run(scenario())


def test_draft_delete_requires_confirmation_and_refreshed_list_excludes_it(production_composition):
    async def scenario():
        async with experience.harness() as (_app, core, sent, dispatch):
            item = {**core.draft, "updatedAt": "2026-11-17T12:00:00Z"}
            core.list_intake_drafts = AsyncMock(return_value={"items": [item]})
            core.archive_intake_draft = AsyncMock(return_value={"revision": 2})
            await dispatch("case:drafts")
            row = sent[-1]["reply_markup"].inline_keyboard[0]
            assert len(row) == 2
            assert row[0].callback_data == f"case:draft:{DRAFT}"
            await dispatch(row[1].callback_data)
            core.archive_intake_draft.assert_not_awaited()
            await dispatch("case:drafts")  # Cancelling the confirmation is read-only.
            core.archive_intake_draft.assert_not_awaited()
            await dispatch(row[1].callback_data)
            await dispatch(f"case:draft:delete:confirm:{DRAFT}")
            core.archive_intake_draft.assert_awaited_once_with(DRAFT, ACTOR, expected_revision=1)
            assert "удалён" in sent[-1]["text"]
            core.list_intake_drafts.return_value = {"items": []}
            await dispatch("case:drafts")
            assert not any(b.callback_data == f"case:draft:{DRAFT}"
                           for r in sent[-1]["reply_markup"].inline_keyboard for b in r)
    asyncio.run(scenario())


@pytest.mark.parametrize("control", ["clinicdocs:open", "cliniclib:open", "cliniclib:how"])
def test_clinic_library_menu_routes_and_returns_without_starting_case(
    production_composition, monkeypatch, control,
):
    client = SimpleNamespace(get_library=AsyncMock(return_value={"items": []}), aclose=AsyncMock())
    monkeypatch.setattr(clinic_library, "ClinicDocumentLibraryClient", lambda: client)

    async def scenario():
        async with experience.harness() as (app, _core, sent, dispatch):
            await dispatch(control)
            assert "документ" in sent[-1]["text"].lower()
            assert any(b.callback_data == "menu" for row in
                       sent[-1]["reply_markup"].inline_keyboard for b in row)
            assert not app.user_data[ACTOR].get("case_wizard")
            await dispatch("menu")
            assert "DENTAL LEGAL AI" in sent[-1].get("text", "")
    asyncio.run(scenario())


@pytest.mark.parametrize("group", [
    "clinical", "labour", "courts", "privacy", "licensing", "healthcare", "general",
])
def test_all_seven_approved_group_buttons_reach_editor_without_approval_controls(
    production_composition, monkeypatch, group,
):
    clinical = group == "clinical"
    item = {"title": "Synthetic reviewed material", "materialId": str(DRAFT),
            **({"preparationId": str(DRAFT), "kind": "CLINICAL_REFERENCE"} if clinical
               else {"versionId": str(DRAFT)})}
    payload = {"page": 1, "pageSize": 10, "totalItems": 1, "items": [item],
               "selectedGroup": group, "groups": [], "referenceReviewableCount": 0,
               "progress": {"complete": True, "normativeVersions": int(not clinical),
                            "approvedNormativeVersions": int(not clinical),
                            "referenceMaterials": int(clinical),
                            "reviewedReferenceMaterials": int(clinical), "missingParts": 0,
                            "unpreparedOriginals": 0, "unlinkedVersions": 0}}
    client = SimpleNamespace(get_editor_groups=AsyncMock(return_value=payload), aclose=AsyncMock())
    monkeypatch.setattr(legal_library, "LegalLibraryClient", lambda: client)

    async def scenario():
        async with experience.harness() as (_app, _core, sent, dispatch):
            await dispatch(f"editor:group:{group}:1")
            client.get_editor_groups.assert_awaited_once_with(ACTOR, page=1, group=group)
            buttons = [b.callback_data for row in sent[-1]["reply_markup"].inline_keyboard
                       for b in row]
            assert "menu" in buttons
            expected = f"editor:preparation:{DRAFT}" if clinical else f"editor:detail:{DRAFT}:1"
            assert expected in buttons
            assert not any(c.startswith(("editor:batch:", "editor:refbatch:")) for c in buttons)
            await dispatch("menu")
    asyncio.run(scenario())


def test_current_menu_copy_explains_live_flow_and_allowed_deidentified_files():
    text = " ".join([BOT_DESCRIPTION, START_MESSAGE, *SCREENS.values()]).lower()
    assert "демонстрацион" not in text
    assert "без llm" not in text
    assert "файлы, фио" not in SCREENS["privacy"].lower()
    assert "pdf" in SCREENS["privacy"].lower()
    assert "недостающ" in SCREENS["workflow"].lower()


def test_scoped_service_draft_resumes_at_summary_without_safety_questions():
    async def scenario():
        async with simplified_harness() as (_app, core, sent, dispatch):
            data = amount_draft()
            data.update(
                situationAreas=["SERVICE"], incomingKind="COMPLAINT",
                clinicActions=["REBOOKED_AFTER_WAIT"],
                safetyScreening={
                    "schemaVersion": "factual-safety-intake.v1", "moneyRequested": "NO",
                },
            )
            data.pop("healthSignals")
            data.pop("affectedServices", None)
            core.draft.update(
                wizardState="SAFETY", draftData=data, routineServiceScopeEnabled=True,
            )
            await dispatch(f"case:draft:{DRAFT}")
            assert not any("case:safety:" in str(message) for message in sent)
            assert any("case:v2:summary:confirm" in str(message) for message in sent)
            assert core.draft["draftData"]["safetyScreening"] == {
                "schemaVersion": "factual-safety-intake.v1", "moneyRequested": "NO",
            }
            core.submit_workflow = AsyncMock(return_value={
                "state": "SUCCEEDED", "workflowId": str(DRAFT), "receiptVersion": 2,
                "case": {
                    "id": str(DRAFT), "publicNumber": "DL-TEST-SERVICE",
                    "status": "ANALYSIS_BLOCKED", "intakeSchemaVersion": "dental-case-intake.v2",
                    "createdAt": "2026-11-20T10:00:00Z",
                },
            })
            core.archive_intake_draft = AsyncMock()
            with patch(
                "telegram_gateway.dialogue_submission.enqueue_analysis", new_callable=AsyncMock,
            ) as enqueue:
                await dispatch("case:v2:summary:confirm")
            enqueue.assert_awaited_once()
            core.submit_workflow.assert_awaited_once()
            assert core.submit_workflow.await_args.kwargs["expected_draft_revision"] == 1
            submitted = {fact["factKey"]: fact["value"]
                         for fact in core.submit_workflow.await_args.args[1]}
            assert submitted["FACTUAL_SAFETY_SCREENING"]["hospitalizationReported"] == "UNKNOWN"

    asyncio.run(scenario())


@pytest.mark.parametrize("typed", [False, True])
def test_scoped_service_without_money_facts_asks_only_about_patient_demand(typed):
    async def scenario():
        async with simplified_harness() as (app, core, sent, dispatch):
            data = amount_draft()
            data["clinicActions"] = ["REBOOKED_AFTER_WAIT"]
            data.pop("safetyScreening")
            data.pop("healthSignals")
            core.draft.update(
                wizardState="HEALTH", draftData=data, routineServiceScopeEnabled=True,
            )
            await dispatch(f"case:draft:{DRAFT}")
            assert "case:safety:moneyRequested:NO" in str(sent[-1])
            assert "case:safety:healthDeteriorationReported" not in str(sent[-1])
            if typed:
                await dispatch("text:Нет")
                question = core.operations[core.starts[-1]]["input"]["wizardState"]
                assert question == "SAFETY"
                core.draft["routineServiceScopeEnabled"] = True
                AnswerCore.propose(core, "NO")
                await await_state(app, "SUMMARY")
            else:
                await dispatch("case:safety:moneyRequested:NO")
            assert "case:v2:summary:confirm" in str(sent[-1])

    asyncio.run(scenario())


def test_short_safety_batch_only_saves_answers_to_the_questions_it_showed():
    async def scenario():
        async with simplified_harness() as (_app, core, sent, dispatch):
            data = amount_draft()
            data["situationAreas"] = ["FINANCE"]
            data["safetyScreening"] = {
                "schemaVersion": "factual-safety-intake.v1",
                "moneyRequested": "NO",
                "amount": "NOT_REQUESTED",
            }
            core.draft.update(
                wizardState="SAFETY", draftData=data, routineServiceScopeEnabled=True,
            )
            await dispatch(f"case:draft:{DRAFT}")
            assert any("case:safety:batch:none" in str(message) for message in sent)
            await dispatch("case:safety:batch:none")

            assert core.draft["draftData"]["safetyScreening"] == {
                "schemaVersion": "factual-safety-intake.v1",
                "moneyRequested": "NO",
                "amount": "NOT_REQUESTED",
                "healthDeteriorationReported": "NO",
                "hospitalizationReported": "NO",
                "representativeContact": "NO",
            }
            assert core.draft["wizardState"] == "SAFETY"

    asyncio.run(scenario())


@pytest.mark.parametrize("field,state,value", [
    (field, state, value)
    for field, state in (("situationAreas", "SITUATION"), ("clinicActions", "CLINIC_ACTIONS"),
                         ("healthSignals", "HEALTH"))
    for value, _label in choice_items(field)
])
def test_every_checklist_choice_can_be_selected_and_removed_without_leaving_step(
    field, state, value,
):
    async def scenario():
        async with simplified_harness() as (_app, core, sent, dispatch):
            data = amount_draft()
            data.pop(field)
            core.draft.update(wizardState=state, draftData=data)
            await dispatch(f"case:draft:{DRAFT}")
            count = len(sent)
            await dispatch(f"case:v2:multi:{field}:+{value}")
            assert core.draft["draftData"][field] == [value]
            assert core.draft["wizardState"] == state and len(sent) == count
            await dispatch(f"case:v2:multi:{field}:-{value}")
            assert core.draft["draftData"][field] == []
            assert core.draft["wizardState"] == state and len(sent) == count
    asyncio.run(scenario())
