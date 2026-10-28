"""Exact local money units; no model, currency conversion or legal qualification."""

from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import Decimal

_MONEY_NUMBER = re.compile(r"[0-9]+(?:[ \u00a0\u202f][0-9]{3})*(?:[.,][0-9]+)?")
_THOUSAND = re.compile(r"\s*(?:тыс\.?|тысяч(?:\u0430|и|\u0443|ей)?)(?!\w)", re.I)
_RUB = re.compile(
    r"₽|\b(?:rub|rubles?|\u0440\u0443\u0431(?:\.|л[\u0430-\u044f]*)?|\u0440\.)(?!\w)", re.I,
)
_FOREIGN_CURRENCY = re.compile(
    r"[$€£¥₴₸₺₾]|\b(?:usd|eur|gbp|jpy|cny|chf|uah|kzt|byn|rur|"
    r"доллар[\u0430-\u044f]*|евро|юан[\u0430-\u044f]*|фунт[\u0430-\u044f]*|"
    r"франк[\u0430-\u044f]*|йен[\u0430-\u044f]*|иен[\u0430-\u044f]*|"
    r"гривн[\u0430-\u044f]*|тенге|лир[\u0430-\u044f]*|лари|белорус[\u0430-\u044f]*)\b", re.I,
)
_UNCERTAIN_MONEY = re.compile(
    r"[-\u2212\u2013\u2014]\s*[0-9]|[<>≈~]|\b(?:не|нет|ни|без|минус|около|примерно|"
    r"приблизительно|порядка|от|до|между|или|либо|менее|более|"
    r"миллион[\u0430-\u044f]*|миллиард[\u0430-\u044f]*|млн|млрд)\b",
    re.I,
)


def literal_ruble_kopecks(source: str, *, require_explicit_currency: bool = False) -> int:
    """Parse one unambiguous literal using the established answer contract."""
    if _FOREIGN_CURRENCY.search(source) or _UNCERTAIN_MONEY.search(source):
        raise ValueError("monetary source is not an unambiguous ruble amount")
    matches = list(_MONEY_NUMBER.finditer(source))
    if len(matches) != 1:
        raise ValueError("monetary source requires one literal amount")
    match = matches[0]
    if match.start() and source[match.start() - 1].isalnum():
        raise ValueError("identifier is not an amount")
    number = match.group().replace(" ", "").replace("\u00a0", "").replace("\u202f", "")
    number = number.replace(",", ".")
    if "." in number and len(number.rsplit(".", 1)[1]) > 2:
        raise ValueError("unsupported or ambiguous decimal amount")
    scale = _THOUSAND.match(source, match.end())
    end = scale.end() if scale is not None else match.end()
    remainder = (source[:match.start()] + source[end:]).strip(" \t\r\n.,:;!")
    # The current question supplies RUB for a bare number. For a sentence, require
    # the currency literally; do not mistake counts, points or another unit for money.
    if (remainder or require_explicit_currency) and not _RUB.search(source):
        raise ValueError("sentence does not identify rubles")
    kopecks = Decimal(number) * (100000 if scale is not None else 100)
    return int(kopecks)


_EXPLICIT_RUB_AMOUNT = re.compile(
    r"(?<![\w.,+\-])" + _MONEY_NUMBER.pattern
    + r"(?:" + _THOUSAND.pattern + r")?\s*(?:" + _RUB.pattern + r")", re.I,
)
_SENTENCE_BREAK = re.compile(r"[!?;\n]|(?<![0-9])\.(?![0-9])")


def initial_money_kopecks(source: str, spans: Sequence[tuple[int, int]]) -> int:
    """Bind one explicit RUB amount to whole input and actual model source anchors.

    Dates are not money. Distinct monetary mentions, another currency or a negated/
    uncertain monetary sentence require clarification. Separate negative health
    sentences do not invalidate a positive monetary sentence. Offsets never prove
    semantic truth or human confirmation.
    """
    if any(not 0 <= start < end <= len(source) for start, end in spans):
        raise ValueError("monetary anchor is outside the input")
    if _FOREIGN_CURRENCY.search(source):
        raise ValueError("initial monetary input contains another currency")
    mentions = list(_EXPLICIT_RUB_AMOUNT.finditer(source))
    candidates: set[int] = set()
    anchored = False
    breaks = list(_SENTENCE_BREAK.finditer(source))
    for mention in mentions:
        left = max((item.end() for item in breaks if item.end() <= mention.start()), default=0)
        right = min((item.start() for item in breaks if item.start() >= mention.end()),
                    default=len(source))
        if _UNCERTAIN_MONEY.search(source[left:right]):
            raise ValueError("initial monetary sentence is negated or uncertain")
        candidates.add(literal_ruble_kopecks(mention.group(), require_explicit_currency=True))
        anchored |= any(start <= mention.start() and end >= mention.end() for start, end in spans)
    if len(candidates) != 1 or not anchored:
        raise ValueError("initial amount requires one complete consistent ruble anchor")
    return candidates.pop()
