"""One-off Telegram profile synchronization.

Profile mutations are intentionally kept outside polling startup because Telegram
rate-limits them much more strictly than ordinary bot operations.
"""

import argparse
import asyncio

from telegram import Bot, BotCommand, InputProfilePhotoStatic

from telegram_gateway.bot import load_token
from telegram_gateway.ui import (
    AVATAR_IMAGE,
    BOT_DESCRIPTION,
    BOT_NAME,
    BOT_SHORT_DESCRIPTION,
)

BOT_COMMANDS = [
    BotCommand("start", "Открыть приветствие"),
    BotCommand("menu", "Показать главное меню"),
    BotCommand("help", "Показать справку"),
]


async def sync_text_profile(bot: Bot) -> None:
    await bot.set_my_commands(BOT_COMMANDS)
    await bot.set_my_name(BOT_NAME)
    await bot.set_my_short_description(BOT_SHORT_DESCRIPTION)
    await bot.set_my_description(BOT_DESCRIPTION)


async def sync_avatar(bot: Bot) -> None:
    # Passing an open JPG ensures multipart upload. Passing a local Path here is
    # interpreted as a remote identifier by the cloud Bot API.
    # Source: https://docs.python-telegram-bot.org/en/v22.8/telegram.inputprofilephotostatic.html
    with AVATAR_IMAGE.open("rb") as avatar:
        await bot.set_my_profile_photo(InputProfilePhotoStatic(avatar))


async def sync_profile(*, avatar_only: bool = False) -> None:
    async with Bot(load_token()) as bot:
        if not avatar_only:
            await sync_text_profile(bot)
        await sync_avatar(bot)


def main() -> None:
    parser = argparse.ArgumentParser(description="Synchronize the Telegram bot profile once")
    parser.add_argument(
        "--avatar-only",
        action="store_true",
        help="skip rate-limited name and description updates",
    )
    arguments = parser.parse_args()
    asyncio.run(sync_profile(avatar_only=arguments.avatar_only))


if __name__ == "__main__":
    main()
