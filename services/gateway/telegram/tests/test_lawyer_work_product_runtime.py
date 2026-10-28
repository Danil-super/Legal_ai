"""Readable, opt-in lawyer drafts; actual PTB dispatch with fake HTTP boundaries."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from unittest.mock import patch
from uuid import UUID

import pytest
from legal_core.case_preparation_contracts import InternalWorkProduct
from legal_core.lawyer_work_product_contracts import LawyerWorkProductView
from telegram_gateway import bot
from telegram_gateway.case_wizard import LegalCoreApiError
from telegram_gateway.lawyer_work_product_runtime import parse_steps, render_plan, review_request
from test_ai_first_contracts import work_product
from test_case_experience import ExperienceCore, harness
from test_dialog_isolation import ACTOR, DRAFT, ESCALATION

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


class PlanCore(ExperienceCore):
    def __init__(self):
        super().__init__()
        self.wire = plan_wire()
        self.role = "CLINIC_OWNER"
        self.assigned = True
        self.status = "IN_PROGRESS"
        self.reads, self.reviews, self.attempts = [], {}, []
        self.unavailable = False
        self.lose_reply = False

    async def get_actor(self, actor):
        return {"role": self.role}

    async def get_escalation(self, escalation, actor):
        return {**await super().get_escalation(escalation, actor), "caseId": str(CASE)}

    async def _json_request(self, method, path, *, telegram_user_id, **kwargs):
        if not path.endswith(("/work-product", "/work-product/reviews")):
            return await super()._json_request(method, path, telegram_user_id=telegram_user_id,
                                               **kwargs)
        assert telegram_user_id == ACTOR
        if self.unavailable:
            raise LegalCoreApiError(503, "UNAVAILABLE", "synthetic private diagnostic")
        if method == "GET":
            self.reads.append(path)
            if self.role == "CLINIC_LAWYER" and not self.assigned:
                raise LegalCoreApiError(403, "FORBIDDEN", "not assigned")
            if self.role == "CLINIC_ADMIN" and not (
                self.wire["reviewState"] == "HUMAN_APPROVED" and self.wire["isCurrent"]
            ):
                raise LegalCoreApiError(403, "FORBIDDEN", "not permitted")
            return deepcopy(self.wire)
        assert method == "POST"
        key, payload = kwargs["idempotency_key"], kwargs["payload"]
        self.attempts.append((key, deepcopy(payload)))
        if self.role not in {"CLINIC_OWNER", "CLINIC_LAWYER"} or not self.assigned:
            raise LegalCoreApiError(403, "ASSIGNEE_REQUIRED", "assignment changed")
        if key in self.reviews:
            assert self.reviews[key][0] == payload
            return deepcopy(self.reviews[key][1])
        if (not self.wire["isCurrent"] or payload["expectedVersionId"] != self.wire["versionId"]
                or payload["expectedContentSha256"] != self.wire["contentSha256"]):
            raise LegalCoreApiError(409, "WORK_PRODUCT_STALE", "stale version")
        updated = deepcopy(self.wire)
        updated["versionId"] = str(UUID(int=UUID(updated["versionId"]).int + 1))
        updated["origin"] = "HUMAN"
        updated["reviewState"] = "HUMAN_APPROVED" if payload["decision"] == "APPROVE" else (
            "HUMAN_REVISED"
        )
        updated["content"]["parentVersion"] = updated["content"]["version"]
        updated["content"]["version"] += 1
        if payload["decision"] == "REVISE":
            updated["content"]["actions"] = payload["actions"]
        updated["contentSha256"] = InternalWorkProduct.model_validate(
            updated["content"],
        ).content_sha256
        self.wire = updated
        self.reviews[key] = (deepcopy(payload), deepcopy(updated))
        if self.lose_reply:
            self.lose_reply = False
            raise LegalCoreApiError(503, "UNAVAILABLE", "reply lost after commit")
        return deepcopy(updated)


@asynccontextmanager
async def plan_harness(*, enabled=True):
    import test_case_experience as original
    factory = original.build_application_with_case_experience

    def build(token):
        return factory(token, enable_lawyer_work_products=enabled)

    with patch.object(original, "build_application_with_case_experience", build):
        async with harness() as (app, _core, sent, dispatch):
            core = PlanCore()
            app.bot_data[bot.LEGAL_CORE_CLIENT_KEY] = core
            yield app, core, sent, dispatch


def callbacks(sent):
    return [button.callback_data for item in sent if item.get("reply_markup")
            for row in item["reply_markup"].inline_keyboard for button in row]


def action(sent, name):
    return next(value for value in reversed(callbacks(sent)) if value.startswith("lawplan:" + name))


def test_disabled_extension_makes_no_plan_requests_or_controls():
    async def scenario():
        async with plan_harness(enabled=False) as (_app, core, sent, dispatch):
            await dispatch(f"case:escalation:{ESCALATION}")
            assert not core.reads and not any(value.startswith("lawplan:")
                                              for value in callbacks(sent))
    asyncio.run(scenario())


def test_plan_opens_readable_draft_and_approves_only_the_displayed_version_once():
    async def scenario():
        async with plan_harness() as (_app, core, sent, dispatch):
            await dispatch(f"case:escalation:{ESCALATION}")
            await dispatch(action(sent, "open"))
            confirm = action(sent, "approve")
            await dispatch(confirm)
            await dispatch(confirm)
            assert len(core.reviews) == len(core.attempts) == 1
            payload = core.attempts[0][1]
            assert payload == {"expectedVersionId": str(VERSION), "decision": "APPROVE",
                               "expectedContentSha256": plan_wire()["contentSha256"]}
            assert core.wire["reviewState"] == "HUMAN_APPROVED"
            assert not core.posts and not core.conclusions
            assert all(len(value.encode()) <= 64 for value in callbacks(sent))
            await dispatch(action(sent, "edit"))
            assert "Сначала покажу правки" in sent[-1]["text"]
    asyncio.run(scenario())


def test_edit_is_a_local_preview_then_explicit_revision_and_preserves_original_facts():
    async def scenario():
        async with plan_harness() as (_app, core, sent, dispatch):
            original = deepcopy(core.wire["content"])
            await dispatch(f"lawplan:open:{ESCALATION}")
            await dispatch(action(sent, "edit"))
            await dispatch("text:1. Проверить переписку.\n2. Запросить уточнение обстоятельств.")
            assert not core.reviews and not core.posts
            await dispatch(action(sent, "save"))
            assert core.wire["reviewState"] == "HUMAN_REVISED"
            assert core.wire["content"]["source"] == original["source"]
            assert core.wire["content"]["summary"] == original["summary"]
            assert core.wire["content"]["uncertainties"] == original["uncertainties"]
            assert core.attempts[0][1]["decision"] == "REVISE"
    asyncio.run(scenario())


@pytest.mark.parametrize("role,assigned,current,state", [
    ("CLINIC_ADMIN", False, True, "HUMAN_APPROVED"),
    ("CLINIC_OWNER", False, True, "UNAPPROVED"),
    ("CLINIC_LAWYER", True, False, "UNAPPROVED"),
])
def test_read_only_or_stale_plan_has_no_mutation_controls(role, assigned, current, state):
    async def scenario():
        async with plan_harness() as (_app, core, sent, dispatch):
            core.role, core.assigned = role, assigned
            core.wire = plan_wire(current=current, state=state)
            await dispatch(f"lawplan:open:{ESCALATION}")
            assert not any(value.startswith(("lawplan:approve:", "lawplan:edit:"))
                           for value in callbacks(sent))
            assert f"case:escalation:{ESCALATION}" in callbacks(sent)
            assert not core.reviews
    asyncio.run(scenario())


@pytest.mark.parametrize("role,assigned", [("CLINIC_LAWYER", False), ("CLINIC_ADMIN", True)])
def test_unapproved_plan_is_not_exposed_to_other_lawyer_or_case_creator(role, assigned):
    async def scenario():
        async with plan_harness() as (_app, core, sent, dispatch):
            core.role, core.assigned = role, assigned
            await dispatch(f"case:escalation:{ESCALATION}")
            assert not any(value.startswith("lawplan:open:") for value in callbacks(sent))
            await dispatch(f"lawplan:open:{ESCALATION}")
            assert core.wire["content"]["summary"] not in str(sent)
            assert not core.reviews
    asyncio.run(scenario())


def test_hidden_approval_action_cannot_approve_an_already_approved_view_again():
    async def scenario():
        async with plan_harness() as (_app, core, sent, dispatch):
            core.wire = plan_wire(state="HUMAN_APPROVED")
            await dispatch(f"lawplan:open:{ESCALATION}")
            await dispatch(action(sent, "edit").replace(":edit:", ":approve:"))
            assert not core.reviews
    asyncio.run(scenario())


def test_lost_review_response_can_retry_exact_same_payload_and_key():
    async def scenario():
        async with plan_harness() as (_app, core, sent, dispatch):
            await dispatch(f"lawplan:open:{ESCALATION}")
            confirm = action(sent, "approve")
            core.lose_reply = True
            await dispatch(confirm)
            await dispatch(confirm)
            assert len(core.attempts) == 2 and core.attempts[0] == core.attempts[1]
            assert len(core.reviews) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["menu", "cancel", "intake", "assignment", "stale", "version"])
def test_navigation_or_authorization_prevents_old_edit_from_becoming_discussion_or_review(change):
    async def scenario():
        async with plan_harness() as (app, core, sent, dispatch):
            await dispatch(f"lawplan:open:{ESCALATION}")
            await dispatch(action(sent, "edit"))
            await dispatch("text:1. Проверить переписку.")
            save = action(sent, "save")
            if change in {"menu", "cancel"}:
                await dispatch("/" + change)
            elif change == "intake":
                await dispatch(f"case:draft:{DRAFT}")
            elif change == "assignment":
                core.assigned = False
            elif change == "version":
                core.wire["versionId"] = str(UUID(int=999))
            else:
                core.wire["isCurrent"] = False
            await dispatch(save)
            assert not core.reviews and not core.posts
            if change == "intake":
                assert bot.WIZARD_DATA_KEY in app.user_data[ACTOR]
    asyncio.run(scenario())


def test_unavailable_plan_keeps_working_back_to_case():
    async def scenario():
        async with plan_harness() as (_app, core, sent, dispatch):
            core.unavailable = True
            await dispatch(f"lawplan:open:{ESCALATION}")
            assert "synthetic private diagnostic" not in str(sent)
            back = f"case:escalation:{ESCALATION}"
            assert back in callbacks(sent)
            await dispatch(back)
            assert "КЕЙС" in "\n".join(item.get("text", "") for item in sent)
    asyncio.run(scenario())


def test_malformed_open_button_is_handled_without_query_or_dispatch_error():
    async def scenario():
        async with plan_harness() as (_app, core, sent, dispatch):
            await dispatch("lawplan:open:" + "-" * 36)
            assert not core.reads and not core.reviews
            assert "menu" in callbacks(sent)
    asyncio.run(scenario())


def test_group_chat_cannot_read_or_mutate_an_internal_plan():
    async def scenario():
        async with plan_harness() as (_app, core, sent, dispatch):
            await dispatch(f"lawplan:open:{ESCALATION}", chat_type="group")
            assert not core.reads and not core.reviews
            assert core.wire["content"]["summary"] not in str(sent)
    asyncio.run(scenario())


def test_long_edit_preview_has_save_control_only_after_all_pages():
    async def scenario():
        async with plan_harness() as (_app, _core, sent, dispatch):
            await dispatch(f"lawplan:open:{ESCALATION}")
            await dispatch(action(sent, "edit"))
            before = len(sent)
            await dispatch("text:" + "\n".join(f"{i}. " + "x" * 590 for i in range(1, 9)))
            preview = sent[before:]
            assert len(preview) > 1
            assert preview[0].get("reply_markup") is None
            assert any(button.callback_data.startswith("lawplan:save:")
                       for row in preview[-1]["reply_markup"].inline_keyboard for button in row)
    asyncio.run(scenario())


@pytest.mark.parametrize("state,label", [
    ("UNAPPROVED", "Проект плана уже подготовлен"),
    ("HUMAN_REVISED", "План сохранён"),
])
def test_workspace_leads_to_ready_plan_review_instead_of_asking_for_a_new_answer(state, label):
    async def scenario():
        async with plan_harness() as (_app, core, sent, dispatch):
            core.wire = plan_wire(state=state)
            await dispatch(f"case:escalation:{ESCALATION}")
            text = "\n".join(item.get("text", "") for item in sent)
            assert label in text
            assert "проверит проект" in text and "исправит действия" in text
            assert "утвердит план" in text and "Дополнительно" in text
            assert "подготовит рекомендации" not in text
            assert "Отчёт ещё не сформирован" not in text
            assert "Напишите обезличенный вопрос или ответ" not in text
            assert action(sent, "open")
            assert len(core.reads) == 1  # The already authorized view supplies this copy.
    asyncio.run(scenario())


def test_approved_plan_workspace_offers_actions_without_asking_for_approval_again():
    async def scenario():
        async with plan_harness() as (_app, core, sent, dispatch):
            core.role = "CLINIC_ADMIN"
            core.wire = plan_wire(state="HUMAN_APPROVED")
            await dispatch(f"case:escalation:{ESCALATION}")
            text = "\n".join(item.get("text", "") for item in sent)
            assert "План решения утверждён юристом" in text
            assert "утвердит план" not in text and "подготовит рекомендации" not in text
            assert "действия и нормативные основания" in text
            assert action(sent, "open")
    asyncio.run(scenario())


@pytest.mark.parametrize("unavailable", [False, True])
def test_stale_or_unavailable_plan_does_not_promise_ready_recommendations(unavailable):
    async def scenario():
        async with plan_harness() as (_app, core, sent, dispatch):
            core.unavailable = unavailable
            core.wire = plan_wire(current=False)
            await dispatch(f"case:escalation:{ESCALATION}")
            text = "\n".join(item.get("text", "") for item in sent)
            assert ("План решения сейчас недоступен" if unavailable else
                    "Сохранённый план требует обновления") in text
            assert "подготовит рекомендации" not in text and "план уже подготовлен" not in text
            assert "утвердит план" not in text and "synthetic private" not in text
            assert ("lawplan:open:" in " ".join(callbacks(sent))) is not unavailable
    asyncio.run(scenario())


def test_flag_off_keeps_the_original_workspace_copy_without_any_plan_read():
    async def scenario():
        async with plan_harness(enabled=False) as (_app, core, sent, dispatch):
            await dispatch(f"case:escalation:{ESCALATION}")
            text = "\n".join(item.get("text", "") for item in sent)
            assert "Отчёт ещё не сформирован" in text
            assert "Напишите обезличенный вопрос или ответ" in text
            assert not core.reads
    asyncio.run(scenario())


@pytest.mark.parametrize("enabled", [True, False])
def test_downloaded_full_card_uses_the_same_actual_plan_status(enabled):
    async def scenario():
        async with plan_harness(enabled=enabled) as (_app, core, sent, dispatch):
            await dispatch(f"esc:card:{ESCALATION}")
            document = next(item["document"] for item in sent if item.get("document") is not None)
            text = document.input_file_content.decode()
            assert ("Проект плана уже подготовлен" in text) is enabled
            assert ("подготовит рекомендации" in text) is not enabled
            assert len(core.reads) == int(enabled)
    asyncio.run(scenario())
