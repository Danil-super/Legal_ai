# ruff: noqa: RUF001
"""Opt-in internal plan display; Legal Core owns authorization and every review write."""

from __future__ import annotations

import base64
import json
import re
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from legal_core.lawyer_work_product_contracts import LawyerWorkProductReview, LawyerWorkProductView
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationHandlerStop,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from telegram_gateway import bot
from telegram_gateway.case_wizard import LegalCoreApiError
from telegram_gateway.quick_intake import contains_probable_person_name
from telegram_gateway.ui import back_keyboard

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


def _back(escalation: UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(
        "← К кейсу", callback_data=f"case:escalation:{escalation}",
    )]])


def _callback(action: str, escalation: UUID, nonce: str) -> str:
    encoded = base64.urlsafe_b64encode(escalation.bytes).decode().rstrip("=")
    return f"lawplan:{action}:{encoded}:{nonce}"


def _parse(callback: str) -> tuple[str, UUID, str | None]:
    parts = callback.split(":")
    if len(parts) == 3 and parts[:2] == ["lawplan", "open"]:
        return "open", UUID(parts[2]), None
    if len(parts) != 4 or parts[0] != "lawplan" or len(parts[2]) != 22:
        raise ValueError("invalid plan action")
    identity = UUID(bytes=base64.b64decode(parts[2] + "==", altchars=b"-_", validate=True))
    return parts[1], identity, parts[3]


def _bound_view(wire: object, detail: dict[str, Any]) -> LawyerWorkProductView:
    view = LawyerWorkProductView.model_validate(wire)
    if view.content.source.case_id != UUID(str(detail.get("caseId"))):
        raise ValueError("work product belongs to another case")
    return view


async def available(
    context: ContextTypes.DEFAULT_TYPE, escalation: UUID, detail: dict[str, Any],
    actor: int,
) -> bool:
    """Core authorizes the optional read; failures never disable the existing workspace."""
    try:
        _bound_view(await bot._legal_core(context)._json_request(
            "GET", f"/v1/case-escalations/{escalation}/work-product", telegram_user_id=actor,
        ), detail)
        return True
    except (LegalCoreApiError, ValueError, TypeError):
        return False


def _can_edit(
    detail: dict[str, Any], membership: dict[str, Any], view: LawyerWorkProductView,
) -> bool:
    return (view.is_current and detail.get("assignedToMe") is True
            and detail.get("status") == "IN_PROGRESS"
            and membership.get("role") in {"CLINIC_OWNER", "CLINIC_LAWYER"})


def _keyboard(pending: dict[str, Any], *, can_edit: bool, approved: bool = False,
              retry: bool = False) -> InlineKeyboardMarkup:
    escalation = UUID(pending["escalationId"])
    rows = []
    if can_edit:
        if pending["mode"] == "PREVIEW":
            rows.append([InlineKeyboardButton("✅ Сохранить правки", callback_data=_callback(
                "save", escalation, pending["nonce"],
            ))])
        elif not approved:
            rows.append([InlineKeyboardButton("✅ Утвердить план", callback_data=_callback(
                "approve", escalation, pending["nonce"],
            ))])
        if not retry:
            rows.append([InlineKeyboardButton("✏️ Изменить действия", callback_data=_callback(
                "edit", escalation, pending["nonce"],
            ))])
    return InlineKeyboardMarkup([*rows, *_back(escalation).inline_keyboard])


async def _show(
    update: Update, context: ContextTypes.DEFAULT_TYPE, escalation: UUID,
    detail: dict[str, Any], membership: dict[str, Any], view: LawyerWorkProductView,
) -> None:
    bot._clear_pending_inputs(context)
    pending = {"escalationId": str(escalation), "actorId": bot._actor_id(update),
               "nonce": uuid4().hex[:12], "mode": "VIEW",
               "view": view.model_dump(mode="json", by_alias=True)}
    bot._user_data(context)[PENDING] = pending
    pages = render_plan(view, str(detail.get("publicNumber", "")))
    for index, page in enumerate(pages):
        await bot._reply(update, page, reply_markup=_keyboard(
            pending, can_edit=_can_edit(detail, membership, view),
            approved=view.review_state == "HUMAN_APPROVED",
        ) if index == len(pages) - 1 else None)


def _other_input(data: dict[str, Any]) -> bool:
    return any(key in data for key in (
        bot.WIZARD_DATA_KEY, "quick_intake_pending", "quick_intake_candidate",
        "clinic_document_upload", "clinic_document_effective_date_pending",
        "reference_evaluation_pending", bot.ADMIN_GRANT_ACCESS_KEY, bot.ADMIN_GRANT_PILOT_KEY,
        bot.TEAM_MEMBER_ROLE_KEY,
    ))


async def fence(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = bot._user_data(context)
    callback = update.callback_query.data if update.callback_query is not None else None
    text = update.effective_message.text if update.effective_message is not None else None
    if (_other_input(data) or (isinstance(callback, str) and not callback.startswith("lawplan:"))
            or (update.callback_query is None and isinstance(text, str) and text.startswith("/"))):
        data.pop(PENDING, None)


def _checked_result(
    wire: object, detail: dict[str, Any], previous: LawyerWorkProductView,
    request: LawyerWorkProductReview,
) -> LawyerWorkProductView:
    result = _bound_view(wire, detail)
    content, old = result.content, previous.content
    if (result.version_id == previous.version_id or result.origin != "HUMAN"
            or result.review_state != ("HUMAN_APPROVED" if request.decision == "APPROVE"
                                       else "HUMAN_REVISED")
            or content.work_product_id != old.work_product_id or content.version != old.version + 1
            or content.parent_version != old.version or content.source != old.source
            or content.as_of_date != old.as_of_date
            or content.risk_policy_version != old.risk_policy_version
            or content.legal_fragment_ids != old.legal_fragment_ids
            or content.summary != old.summary
            or content.uncertainties != old.uncertainties
            or content.actions != (request.actions if request.actions is not None
                                   else old.actions)):
        raise ValueError("review receipt does not match the displayed plan and decision")
    return result


async def controls(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    callback, actor = await bot._answer_callback(update), bot._actor_id(update)
    if callback is None or actor is None:
        raise ApplicationHandlerStop
    try:
        action, escalation, nonce = _parse(callback)
    except (ValueError, TypeError):
        await bot._reply(update, "Кнопка плана недействительна. Откройте кейс заново.",
                         reply_markup=back_keyboard())
        raise ApplicationHandlerStop from None
    data = bot._user_data(context)
    if _other_input(data):
        data.pop(PENDING, None)
        await bot._reply(update, "Сначала завершите текущий ввод или откройте /menu.",
                         reply_markup=_back(escalation))
        raise ApplicationHandlerStop
    client = bot._legal_core(context)
    try:
        detail = await client.get_escalation(escalation, actor)
        membership = await client.get_actor(actor)
        if action == "open":
            view = _bound_view(await client._json_request(
                "GET", f"/v1/case-escalations/{escalation}/work-product", telegram_user_id=actor,
            ), detail)
            await _show(update, context, escalation, detail, membership, view)
            raise ApplicationHandlerStop
        pending = data.get(PENDING)
        if (not isinstance(pending, dict) or pending.get("nonce") != nonce
                or pending.get("actorId") != actor
                or pending.get("escalationId") != str(escalation)):
            # An old/double click must not destroy the controls of the new version.
            await bot._reply(update, "Эта кнопка уже заменена. Используйте последнюю карточку "
                             "плана или откройте её из кейса.", reply_markup=_back(escalation))
            raise ApplicationHandlerStop
        view = _bound_view(pending["view"], detail)
        if not _can_edit(detail, membership, view):
            raise ValueError("current assignment required")
        if action == "edit" and not pending.get("sentReview"):
            pending.update(mode="EDIT", nonce=uuid4().hex[:12])
            await bot._reply(update, "Пришлите новый список действий: от 1 до 8 пунктов, "
                             "каждый с новой строки, с нумерацией 1., 2. и далее. "
                             "До 600 символов в пункте, без персональных данных. "
                             "Сначала покажу правки; факты и основания сохраняются.",
                             reply_markup=_back(escalation))
            raise ApplicationHandlerStop
        if action not in {"approve", "save"} or pending["mode"] != (
            "VIEW" if action == "approve" else "PREVIEW"
        ):
            raise ValueError("explicit decision required")
        if action == "approve" and view.review_state == "HUMAN_APPROVED":
            raise ValueError("this displayed version is already approved")
        request = review_request(view, "APPROVE" if action == "approve" else "REVISE",
                                 actions=pending.get("actions") if action == "save" else None)
        payload = request.model_dump(mode="json", by_alias=True, exclude_none=True)
        pending["sentReview"] = payload
        key = uuid5(NAMESPACE_URL, f"lawyer-plan:{actor}:{escalation}:" + json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ))
        try:
            wire = await client._json_request(
                "POST", f"/v1/case-escalations/{escalation}/work-product/reviews",
                telegram_user_id=actor, payload=payload, idempotency_key=key,
            )
            result = _checked_result(wire, detail, view, request)
        except LegalCoreApiError as error:
            if error.status_code in {403, 404, 409, 422}:
                raise ValueError("review no longer authorized or current") from None
            await bot._reply(update, "Не удалось получить результат сохранения. "
                             "Повторите то же подтверждение: повторная версия не создаётся.",
                             reply_markup=_keyboard(pending, can_edit=True, retry=True))
            raise ApplicationHandlerStop from None
        await _show(update, context, escalation, detail, membership, result)
    except (LegalCoreApiError, ValueError, TypeError, KeyError):
        data.pop(PENDING, None)
        await bot._reply(update, "План недоступен, изменился или больше не разрешён для этого "
                         "действия. Вернитесь в кейс и откройте актуальную версию.",
                         reply_markup=_back(escalation))
    raise ApplicationHandlerStop


async def receive_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = bot._user_data(context)
    pending = data.get(PENDING)
    if (not isinstance(pending, dict) or _other_input(data)
            or pending.get("actorId") != bot._actor_id(update)):
        return
    if pending.get("mode") not in {"EDIT", "PREVIEW"}:
        return
    escalation = UUID(pending["escalationId"])
    if pending.get("sentReview"):
        await bot._reply(update, "Сначала восстановите результат тем же подтверждением "
                         "или откройте план из кейса.", reply_markup=_back(escalation))
        raise ApplicationHandlerStop
    try:
        actions = parse_steps(bot._message_text(update))
    except ValueError as error:
        await bot._reply(update, str(error), reply_markup=_back(escalation))
        raise ApplicationHandlerStop from None
    pending.update(mode="PREVIEW", actions=actions, nonce=uuid4().hex[:12])
    pages = _pages(["Предпросмотр правок — ещё не сохранены.", "\n".join(
        f"{number}. {step}" for number, step in enumerate(actions, 1)
    )])
    for index, page in enumerate(pages):
        await bot._reply(update, page, reply_markup=_keyboard(pending, can_edit=True)
                         if index == len(pages) - 1 else None)
    raise ApplicationHandlerStop


def install(application: bot.TelegramApplication) -> None:
    application.bot_data[ENABLED] = True
    application.add_handler(TypeHandler(Update, fence), group=-96)
    application.add_handler(CallbackQueryHandler(controls, pattern=(
        r"^lawplan:(?:open:[0-9a-f-]{36}|(?:approve|edit|save):[A-Za-z0-9_-]{22}:[0-9a-f]{12})$"
    )), group=-9)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_edit), group=-9)
