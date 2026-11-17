"""Telegram presentation copy and navigation components."""

from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

WELCOME_IMAGE = Path(__file__).with_name("assets") / "welcome.png"
AVATAR_IMAGE = Path(__file__).with_name("assets") / "avatar.jpg"

BOT_NAME = "Dental Legal AI"
BOT_SHORT_DESCRIPTION = "Юридический навигатор для стоматологических клиник"
BOT_DESCRIPTION = (
    "Dental Legal AI — юридический помощник для стоматологических клиник. "
    "Опишите обезличенную ситуацию: бот уточнит недостающие факты и подготовит "
    "план действий с опорой на утверждённые нормы. Сложные кейсы проверяет юрист. "
    "Не отправляйте персональные данные и необезличенные медицинские документы."
)

START_MESSAGE = (
    "🦷⚖️  DENTAL LEGAL AI\n"
    "Юридический навигатор для стоматологических клиник\n\n"
    "Нажмите «Создать кейс» и опишите ситуацию. Бот задаст недостающие вопросы, "
    "предложит проверить факты и подготовит PDF с планом действий и нормами права.\n\n"
    "🛡 Не отправляйте персональные данные: ФИО, контакты и номера карт. "
    "Рекомендации предназначены для клиники; пациенту бот их не отправляет.\n\n"
    "Выберите раздел ниже ↓"
)

SCREENS = {
    "menu": START_MESSAGE,
    "features": (
        "⚖️  ВОЗМОЖНОСТИ\n\n"
        "Dental Legal AI помогает:\n\n"
        "• структурировать факты и хронологию;\n"
        "• выделять юридически значимые обстоятельства;\n"
        "• показывать пробелы в документах и доказательствах;\n"
        "• готовить черновики ответов и внутренних мемо;\n"
        "• ссылаться на утверждённые источники права.\n\n"
        "Если данных или применимых норм недостаточно, бот сообщит об этом."
    ),
    "workflow": (
        "🧭  КАК ЭТО РАБОТАЕТ\n\n"
        "1. Нажмите «Создать кейс» и опишите ситуацию одним сообщением.\n"
        "2. Ответьте на вопросы о недостающих фактах и проверьте резюме.\n"
        "3. Бот проверит применимые утверждённые нормы. При наличии подходящих "
        "эталонных кейсов учтёт их при анализе.\n"
        "4. Получите PDF с оценкой ситуации, рекомендациями и следующими действиями.\n"
        "5. Кейсы высокого риска поступают юристу для проверки.\n\n"
        "Прошлые кейсы и отчёты доступны в разделе «Мои кейсы».\n"
        "Бот не подменяет профессиональную юридическую оценку."
    ),
    "case:start": (
        "📝  СОЗДАТЬ КЕЙС\n\n"
        "Опишите ситуацию одним сообщением. Бот проверит доступ и уточнит "
        "недостающие факты. Не отправляйте ФИО, телефоны, адреса, номера медкарт "
        "и необезличенные файлы."
    ),
    "quick:start": (
        "🧩  БЫСТРОЕ ОПИСАНИЕ\n\n"
        "Отправьте одно обезличенное описание ситуации. Бот разберёт сведения, "
        "уточнит недостающее и предложит проверить краткое резюме перед анализом.\n\n"
        "Не отправляйте ФИО, контакты, адреса, номера медкарт или файлы пациента."
    ),
    "clinicdocs:open": (
        "📚 БАЗА ДОКУМЕНТОВ КЛИНИКИ\n\n"
        "Откройте этот раздел в основном интерфейсе бота. Загрузка документов необязательна, "
        "но одобренные шаблоны помогают точнее сформировать внутренний отчёт."
    ),
    "legalbase:open": (
        "📜 НОРМАТИВНАЯ БАЗА\n\n"
        "Этот раздел доступен в полном защищённом контуре только юристу и владельцу клиники. "
        "Он показывает одобренные официальные источники, а не внутренние документы клиники."
    ),
    "privacy": (
        "🛡  КОНФИДЕНЦИАЛЬНОСТЬ\n\n"
        "Медицинские сведения и данные пациента требуют особой защиты.\n\n"
        "• описывайте ситуацию без ФИО, контактов и идентификаторов пациента;\n"
        "• при запросе приложения можно добавить обезличенный TXT, PDF или DOCX до 15 МБ;\n"
        "• уберите из файлов идентификаторы, изображения и лишние медицинские сведения;\n"
        "• введённые факты сохраняются в карточке и повторяются в вашем PDF;\n"
        "• бот не ставит диагнозы; рекомендации требуют оценки с учётом фактов.\n\n"
        "Не отправляйте информацию, по которой можно определить пациента."
    ),
    "about": (
        "ℹ️  О ПРОЕКТЕ\n\n"
        "Dental Legal AI — специализированный помощник для юридически значимых "
        "ситуаций в стоматологии.\n\n"
        "В основе архитектуры:\n"
        "• отдельный Legal Core для проверенных норм права;\n"
        "• проверяемые источники и версионность;\n"
        "• разделение клиник и контроль доступа;\n"
        "• человек принимает окончательное решение."
    ),
}

HELP_MESSAGE = (
    "КОМАНДЫ\n\n"
    "/start — открыть приветствие\n"
    "/menu — показать главное меню\n"
    "/help — показать эту справку\n\n"
    "/whoami — показать Telegram ID\n"
    "/cancel — сохранить и выйти из заполнения\n"
    "/cases — мои кейсы и отчёты\n"
    "/describe_case — быстро описать обезличенную ситуацию\n"
    "/cancel_quick — отменить быстрое описание\n\n"
    "/admin — панель владельца сервиса\n\n"
    "/team — команда клиники (только владелец клиники)\n\n"
    "/legal_base — нормативная база (юрист и владелец клиники)\n\n"
    "Юридические рекомендации и черновики не формируются без проверенной правовой базы."
)

TEXT_INPUT_DISABLED_MESSAGE = (
    "🛡 Ввод текста пока отключён. Сообщение не анализируется.\n\n"
    "Не отправляйте данные пациента или документы. Откройте /menu, чтобы продолжить."
)

MAIN_MENU_CALLBACKS = {
    "features",
    "workflow",
    "case:start",
    "quick:start",
    "case:drafts",
    "history:list:0",
    "privacy",
    "about",
    "account:id",
    "case:escalations",
    "clinicdocs:open",
    "editor:open",
    "legalbase:open",
    "team:open",
    "help",
}


def main_menu_keyboard(
    role: str | None = None, *, is_legal_editor: bool = False
) -> InlineKeyboardMarkup:
    """Compose independent clinic and platform-editor privileges without conflating them."""

    shared_rows = [
        [
            InlineKeyboardButton("👤 Мой Telegram ID", callback_data="account:id"),
            InlineKeyboardButton("❓ Справка", callback_data="help"),
        ],
        [
            InlineKeyboardButton("🧭 Как это работает", callback_data="workflow"),
            InlineKeyboardButton("🛡 Конфиденциальность", callback_data="privacy"),
        ],
        [InlineKeyboardButton("ℹ️ О проекте", callback_data="about")],
    ]
    if role == "CLINIC_LAWYER":
        lawyer_rows: list[list[InlineKeyboardButton]] = [
            [InlineKeyboardButton("📝 Создать кейс", callback_data="quick:start")],
            [InlineKeyboardButton("📂 Мои кейсы", callback_data="history:list:0")],
            [InlineKeyboardButton("📂 Мои черновики", callback_data="case:drafts")],
            [
                InlineKeyboardButton(
                    "⚖️ Эскалации HIGH/CRITICAL", callback_data="case:escalations"
                )
            ],
            [
                InlineKeyboardButton(
                    "📜 Нормативная база", callback_data="legalbase:open"
                )
            ],
        ]
        return InlineKeyboardMarkup(
            [
                *lawyer_rows,
                *shared_rows,
            ]
        )

    case_rows = [
        [
            InlineKeyboardButton(
                "📝 Создать кейс",
                callback_data="quick:start",
            )
        ],
        [InlineKeyboardButton("📂 Мои кейсы", callback_data="history:list:0")],
        [InlineKeyboardButton("📂 Мои черновики", callback_data="case:drafts")],
    ]
    if role in {"CLINIC_OWNER", "CLINIC_ADMIN"}:
        case_rows.append(
            [
                InlineKeyboardButton(
                    "📚 База документов клиники",
                    callback_data="clinicdocs:open",
                )
            ]
        )
        case_rows.append(
            [
                InlineKeyboardButton(
                    "⚖️ Эскалации HIGH/CRITICAL", callback_data="case:escalations"
                )
            ]
        )
    if role == "CLINIC_OWNER" or is_legal_editor:
        case_rows.append(
            [
                InlineKeyboardButton(
                    "📜 Нормативная база", callback_data="legalbase:open"
                )
            ]
        )
    if role == "CLINIC_OWNER":
        case_rows.append([InlineKeyboardButton("👥 Команда клиники", callback_data="team:open")])

    return InlineKeyboardMarkup(
        [
            *case_rows,
            *shared_rows[:1],
            [InlineKeyboardButton("⚖️ Возможности", callback_data="features")],
            *shared_rows[1:],
        ]
    )


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("← Главное меню", callback_data="menu")]])


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Выдать полный доступ", callback_data="admin:grant")],
            [InlineKeyboardButton("🎁 Выдать pilot на 30 дней", callback_data="admin:pilot")],
            [InlineKeyboardButton("← Главное меню", callback_data="menu")],
        ]
    )


def clinic_team_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Добавить администратора", callback_data="team:add:admin")],
            [InlineKeyboardButton("➕ Добавить юриста", callback_data="team:add:lawyer")],
            [InlineKeyboardButton("← Главное меню", callback_data="menu")],
        ]
    )
