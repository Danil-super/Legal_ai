# ruff: noqa: RUF001
"""Compact approved-only library navigation for actors without editor authority."""

from typing import Any
from urllib.parse import urlsplit

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from telegram_gateway.ui import back_keyboard

LIBRARY_GROUP_KEYS = frozenset(
    {
        "clinical",
        "labour",
        "courts",
        "privacy",
        "licensing",
        "healthcare",
        "general",
    }
)


def render_grouped_legal_library(payload: dict[str, Any]) -> tuple[str, InlineKeyboardMarkup]:
    items, groups = payload.get("items"), payload.get("groups")
    group, page, has_more = (
        payload.get("selectedGroup"),
        payload.get("page"),
        payload.get("hasMore"),
    )
    if (
        not isinstance(items, list)
        or len(items) > 10
        or not isinstance(groups, dict)
        or type(page) is not int
        or not 1 <= page <= 100
        or type(has_more) is not bool
        or any(
            key not in LIBRARY_GROUP_KEYS or not isinstance(title, str)
            for key, title in groups.items()
        )
    ):
        raise ValueError("legal library group page")
    rows: list[list[InlineKeyboardButton]] = []
    if group is None:
        if items or has_more:
            raise ValueError("legal library root")
        for key, title in groups.items():
            rows.append(
                [InlineKeyboardButton(f"📂 {title}"[:64], callback_data=f"legalbase:group:{key}:1")]
            )
        text = "📜 НОРМАТИВНАЯ БАЗА\n\nВыберите группу документов."
    else:
        if group not in LIBRARY_GROUP_KEYS:
            raise ValueError("legal library group")
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("legal library document")
            title, source = item.get("documentTitle"), item.get("sourceUrl")
            if not isinstance(title, str) or not isinstance(source, str):
                raise ValueError("legal library document")
            try:
                parsed = urlsplit(source)
                if (
                    len(source) > 2000
                    or parsed.scheme != "https"
                    or not parsed.hostname
                    or parsed.username is not None
                    or parsed.password is not None
                ):
                    raise ValueError("legal library source")
            except ValueError:
                raise ValueError("legal library source") from None
            rows.append([InlineKeyboardButton(f"📄 {' '.join(title.split())}"[:64], url=source)])
        navigation = []
        if page > 1:
            navigation.append(
                InlineKeyboardButton("◀️ Назад", callback_data=f"legalbase:group:{group}:{page - 1}")
            )
        if has_more and page < 100:
            navigation.append(
                InlineKeyboardButton(
                    "Вперёд ▶️", callback_data=f"legalbase:group:{group}:{page + 1}"
                )
            )
        if navigation:
            rows.append(navigation)
        rows.append([InlineKeyboardButton("← К группам", callback_data="legalbase:open")])
        text = "📜 НОРМАТИВНАЯ БАЗА\n\nУтверждённые нормы, применимые на выбранную дату."
        if not items:
            text += "\nВ этой группе нет применимых утверждённых норм."
        if group == "clinical":
            text += "\nКлинические справочники не являются нормативными актами."
    rows.extend([list(row) for row in back_keyboard().inline_keyboard])
    return text, InlineKeyboardMarkup(rows)
