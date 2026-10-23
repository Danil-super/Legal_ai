# ruff: noqa: RUF001
"""Owner-only choose/confirm UI; Core authorizes and serializes every transfer."""

from time import monotonic
from uuid import UUID, uuid4

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from telegram_gateway import bot
from telegram_gateway.case_wizard import LegalCoreApiError

PENDING_KEY = "escalation_assignment_pending"


async def handle_assignment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    actor = bot._actor_id(update)
    query = update.callback_query
    if actor is None or query is None or not isinstance(query.data, str):
        raise ValueError("invalid assignment action")
    client = bot._legal_core(context)
    if (await client.get_actor(actor)).get("role") != "CLINIC_OWNER":
        raise LegalCoreApiError(403, "CLINIC_OWNER_REQUIRED", "Owner required")
    data = bot._user_data(context)
    if query.data.startswith("esc:assign:"):
        data.pop(PENDING_KEY, None)
        escalation = UUID(query.data.removeprefix("esc:assign:"))
        detail = await client.get_escalation(escalation, actor)
        if detail.get("status") == "RESOLVED":
            raise LegalCoreApiError(409, "ESCALATION_RESOLVED", "Already resolved")
        roster = await client.list_clinic_members(actor)
        items = roster.get("items")
        if not isinstance(items, list):
            raise ValueError("invalid lawyer roster")
        choices = {}
        rows = []
        for member in items:
            if not isinstance(member, dict) or member.get("role") != "CLINIC_LAWYER":
                continue
            target = str(UUID(str(member.get("membershipId"))))
            telegram = member.get("telegramUserId")
            if type(telegram) is not int or telegram <= 0:
                raise ValueError("invalid lawyer identity")
            token = uuid4().hex
            choices[token] = {"target": target, "telegram": telegram}
            rows.append([InlineKeyboardButton(f"Юрист · {telegram}",
                                              callback_data=f"esc:pick:{token}")])
            if len(rows) == 50:
                break
        expected = detail.get("assignedMembershipId")
        data[PENDING_KEY] = {
            "escalation": str(escalation),
            "expected": str(UUID(str(expected))) if expected else None,
            "choices": choices, "expires": monotonic() + 600,
            "number": str(detail.get("publicNumber", "")), "selected": None,
        }
        rows.append([InlineKeyboardButton("← К кейсу",
                                          callback_data=f"case:escalation:{escalation}")])
        await bot._reply(
            update,
            "Выберите юриста. Передача произойдёт только после подтверждения."
            if choices else "Активных юристов пока нет. Добавьте юриста в «Команда клиники».",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return
    pending = data.get(PENDING_KEY)
    if not isinstance(pending, dict) or pending.get("expires", 0) <= monotonic():
        data.pop(PENDING_KEY, None)
        raise ValueError("expired assignment action")
    token = query.data.rsplit(":", 1)[-1]
    choice = pending["choices"].get(token)
    if not isinstance(choice, dict):
        raise ValueError("unknown assignment choice")
    escalation = UUID(pending["escalation"])
    if query.data.startswith("esc:pick:"):
        pending["selected"] = token
        await bot._reply(
            update, f"Передать кейс {pending['number']} юристу {choice['telegram']}?\n"
            "Он станет ответственным за проверку и завершение обращения. История сохранится.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Подтверждаю передачу",
                                      callback_data=f"esc:confirm-assign:{token}")],
                [InlineKeyboardButton("← Отмена, к кейсу",
                                      callback_data=f"case:escalation:{escalation}")],
            ]),
        )
        return
    if not query.data.startswith("esc:confirm-assign:") or pending.get("selected") != token:
        raise ValueError("assignment not confirmed")
    await client.assign_escalation(
        escalation, actor, assigned_membership_id=UUID(choice["target"]),
        expected_assigned_membership_id=UUID(pending["expected"]) if pending["expected"] else None,
    )
    data.pop(PENDING_KEY, None)
    from telegram_gateway.escalation_workspace import show_workspace

    await bot._reply(update, "✅ Кейс передан юристу. Он доступен ему в списке кейсов.")
    await show_workspace(update, context, escalation)
