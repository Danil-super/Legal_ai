import asyncio

from telegram import InputFile, InputProfilePhotoStatic
from telegram_gateway.profile import BOT_COMMANDS, sync_avatar


class FakeBot:
    def __init__(self) -> None:
        self.profile_photo: InputProfilePhotoStatic | None = None

    async def set_my_profile_photo(self, photo: InputProfilePhotoStatic) -> None:
        self.profile_photo = photo


def test_avatar_sync_uploads_file_content_instead_of_a_local_path() -> None:
    bot = FakeBot()

    asyncio.run(sync_avatar(bot))  # type: ignore[arg-type]

    assert bot.profile_photo is not None
    assert isinstance(bot.profile_photo.photo, InputFile)


def test_telegram_command_menu_exposes_the_owner_panel_without_grant_shortcuts() -> None:
    names = {command.command for command in BOT_COMMANDS}

    assert {"start", "menu", "help", "whoami", "cancel", "admin"} <= names
    assert "grant_access" not in names
