# ruff: noqa: RUF001
"""Private clinic roster presentation; authorization remains in Legal Core."""


def clinic_team_text(items: object) -> str:
    """Bound the private roster and render only IDs and existing clinic roles."""
    if not isinstance(items, list):
        raise ValueError("Invalid clinic roster")
    roles = {
        "CLINIC_OWNER": "владелец",
        "CLINIC_ADMIN": "администратор",
        "CLINIC_LAWYER": "юрист",
    }
    lines = ["👥 КОМАНДА КЛИНИКИ", ""]
    for member in items[:50]:
        if not isinstance(member, dict):
            raise ValueError("Invalid clinic member")
        member_id, role = member.get("telegramUserId"), member.get("role")
        if (
            type(member_id) is not int or not 0 < member_id <= 9_223_372_036_854_775_807
            or not isinstance(role, str) or role not in roles
        ):
            raise ValueError("Invalid clinic member identity")
        lines.append(f"• {member_id} — {roles[role]}")
    if not items:
        lines.append("Сотрудников пока нет.")
    if len(items) > 50:
        lines.append("Показаны первые 50 сотрудников.")
    lines.extend([
        "", "Добавьте администратора или юриста по Telegram ID кнопкой ниже.",
        "Юристы работают с HIGH/CRITICAL. Для проверки норм нужна отдельная роль редактора.",
    ])
    return "\n".join(lines)
