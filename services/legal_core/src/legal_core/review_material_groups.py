"""Presentation-only classification; never a legal relevance or trust decision."""

from typing import Literal

from sqlalchemy import case, or_
from sqlalchemy.sql.elements import ColumnElement

from legal_core.models import LegalReviewMaterial

ReviewGroup = Literal[
    "clinical", "labour", "courts", "privacy", "licensing", "healthcare", "general", "other"
]
GROUP_TITLES: dict[str, str] = {
    "clinical": "Клинические справочные материалы",
    "labour": "Труд и квалификация специалистов",
    "courts": "Суды, экспертиза и юридическая помощь",
    "privacy": "Персональные данные и информация",
    "licensing": "Лицензирование и контроль",
    "healthcare": "Медицинская деятельность и права пациентов",
    "general": "Кодексы и общие правовые нормы",
    "other": "Прочие документы — уточнить раздел",
}
_RULES = (
    ("labour", ("трудовой кодекс%", "приказ министерства труда%")),
    ("courts", ("%верховного суда%", "гражданский процессуальный%", "%судебно эксперт%",
                "%адвокатской деятельности%", "%нотариате%")),
    ("privacy", ("%персональных данных%", "%электронной подписи%", "%средствах массовой%",
                 "приказ федеральной службы по надзору в сфере связи%")),
    ("licensing", ("%лицензировании%", "%государственном контроле%")),
    ("healthcare", ("приказ министерства здравоохранения%", "%санитарного врача%",
                     "%санитарно эпидемиологическом%", "%323 фз%", "%защите прав потребителей%",
                     "форма 043%", "постановление правительства российской федерации%659%")),
    ("general", ("%кодекс%", "федеральный закон%", "закон рф%")),
)


def material_group_expression() -> ColumnElement[str]:
    return case(
        (LegalReviewMaterial.kind == "CLINICAL_REFERENCE", "clinical"),
        *[
            (or_(*(LegalReviewMaterial.title.ilike(pattern) for pattern in patterns)), key)
            for key, patterns in _RULES
        ],
        else_="other",
    )
