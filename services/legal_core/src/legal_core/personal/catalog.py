# ruff: noqa: RUF001
"""Fixed synthetic scenarios, not a legal corpus or a live intake engine."""

from dataclasses import dataclass

from legal_core.personal.contracts import Audience, Topic, audience_for

PREVIEW_NOTICE = (
    "Закрытый прототип. Только заранее подготовленные вымышленные примеры. "
    "Юридический анализ и приём личных обращений ещё не включены. "
    "Не отправляйте персональные сведения, описание своей ситуации или файлы."
)


@dataclass(frozen=True)
class TopicCard:
    topic: Topic
    title: str
    source_candidates: tuple[str, ...]
    fact_questions: tuple[str, ...]
    synthetic_description: str

    @property
    def audience(self) -> Audience:
        return audience_for(self.topic)


CATALOG: tuple[TopicCard, ...] = (
    TopicCard(
        Topic.PATIENT_DOCUMENTS, "Получение медицинских документов",
        ("fz323", "order789n"),
        ("Кто обращается: пациент или представитель?", "Совершеннолетний ли пациент?",
         "Какие документы запрошены?", "Когда и каким способом направлен запрос?"),
        "Вымышленный совершеннолетний пациент хочет получить копии документов о лечении.",
    ),
    TopicCard(
        Topic.PATIENT_QUALITY, "Качество, возврат или переделка",
        ("fz323", "consumer2300", "pp736", "pp659"),
        ("Какая услуга и когда оказана?", "Платное лечение, ОМС или смешанная оплата?",
         "Что сообщил пациент, а что подтверждено документами?",
         "Какое требование заявлено и когда?"),
        "Вымышленный пациент сообщил о сколе коронки и рассматривает возврат или переделку.",
    ),
    TopicCard(
        Topic.PATIENT_CANCELLATION, "Отказ от дальнейших услуг",
        ("consumer2300", "pp736", "pp659"),
        ("Кто заказчик услуги и кто пациент?", "Когда заключён договор?",
         "Что выполнено и что оплачено?", "Когда сообщено об отказе от дальнейших услуг?"),
        "Вымышленный пациент оплатил план лечения, но хочет отказаться от оставшихся услуг.",
    ),
    TopicCard(
        Topic.EMPLOYEE_PAY, "Задержка выплат",
        ("labor197",),
        ("Как оформлены отношения?", "Какова должность и фактические обязанности?",
         "Какие суммы и даты выплат предусмотрены?", "Были ли частичные выплаты?"),
        "Вымышленный сотрудник сообщил, что получил только часть ожидаемой выплаты.",
    ),
    TopicCard(
        Topic.EMPLOYEE_DISMISSAL, "Увольнение и окончательный расчёт",
        ("labor197",),
        ("Как оформлены отношения?", "Кто инициировал прекращение работы?",
         "Когда получены документы об увольнении?", "Какие суммы начислены и выплачены?"),
        "Вымышленный работник получил документы о прекращении работы и проверяет расчёт.",
    ),
    TopicCard(
        Topic.EMPLOYEE_DEDUCTIONS, "Спорные удержания",
        ("labor197", "fz323"),
        ("Как оформлены отношения?", "Что удержано и на каком заявленном основании?",
         "Какие документы о проверке и решении представлены?",
         "Возврат пациенту и основание ответственности работника проверялись отдельно?"),
        "Вымышленная клиника вернула деньги пациенту и предложила удержать сумму у работника.",
    ),
)


def get_topic(value: str) -> TopicCard | None:
    return next((card for card in CATALOG if card.topic.value == value), None)


def render_demo(card: TopicCard) -> str:
    # No model-generated text, real facts, conclusion, deadline or computed risk.
    questions = "\n".join(f"{index}. {text}" for index, text in enumerate(card.fact_questions, 1))
    return (
        f"{card.title}\n\n{PREVIEW_NOTICE}\n\n"
        f"Пример: {card.synthetic_description}\n\n"
        f"Какие сведения потребуется уточнять в будущей версии:\n{questions}\n\n"
        "Результат: NOT_AVAILABLE. Это демонстрация сбора сведений, не правовой вывод."
    )
