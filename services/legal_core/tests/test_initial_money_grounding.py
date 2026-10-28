"""Initial narratives must bind numeric RUB proposals to the actual literal sum."""

import pytest

from legal_core.case_api import ApiError
from legal_core.intake_preparation_contracts import DraftSuggestions
from legal_core.intake_preparation_merge import merge_preparation_suggestions


def proposal(source, amount, anchors=None):
    parts = anchors or [source]
    return DraftSuggestions.model_validate({
        'safetyScreening': {'schemaVersion': 'factual-safety-intake.v1',
                            'moneyRequested': 'YES',
                            'amount': {'amountKopecks': amount, 'currency': 'RUB'}},
        'sourceSpans': {'safetyScreening': [
            {'start': source.index(part), 'end': source.index(part) + len(part)}
            for part in parts
        ]},
    })


@pytest.mark.parametrize('rubles', [5000, 10000])
def test_initial_model_cannot_store_rubles_as_kopecks(rubles):
    source = f'Пациент попросил возврат {rubles} рублей.'
    invalid = proposal(source, rubles)
    with pytest.raises(ValueError):
        invalid.validate_source(source)
    with pytest.raises(ApiError) as caught:
        merge_preparation_suggestions({'intakeVersion': 2, 'eventSummary': source},
                                     invalid, source)
    assert caught.value.code == 'PREPARATION_MERGE_CONFLICT'


@pytest.mark.parametrize('source,amount', [
    ('Просит вернуть 5000 рублей', 500000),
    ('Просит вернуть 10 000 ₽', 1000000),
    ('Просит 12\u202f000,50 руб.', 1200050),
    ('Просит вернуть 10 тысяч рублей', 1000000),
])
def test_literal_ruble_amount_keeps_exact_currency_and_minor_units(source, amount):
    proposal(source, amount).validate_source(source)


@pytest.mark.parametrize('source', [
    'Пациент попросил возврат 5000 USD',
    'Пациент попросил возврат 5000 евро',
    'Пациент попросил возврат 5000 или 10000 рублей',
    'Пациент попросил возврат около 5000 рублей',
    'Пациент не просит возврат 5000 рублей',
    'Пациент попросил возврат 5000 баллов',
])
def test_unambiguous_explicit_rubles_are_required_for_initial_numeric_amount(source):
    with pytest.raises(ValueError):
        proposal(source, 500000).validate_source(source)


def test_other_safety_anchors_do_not_negate_the_explicit_monetary_request():
    money = 'попросил возврат 5000 рублей'
    health = 'Госпитализации и ухудшения не было'
    source = f'20 октября оказали услугу, 27 октября {money}. {health}.'
    proposal(source, 500000, [money, health]).validate_source(source)


def test_two_conflicting_monetary_anchors_require_clarification():
    first, second = 'просил 5000 рублей', 'затем просил 10000 рублей'
    source = f'Пациент {first}, {second}.'
    with pytest.raises(ValueError):
        proposal(source, 500000, [first, second]).validate_source(source)


@pytest.mark.parametrize('source,anchor,amount', [
    ('Пациент просит 15000 рублей.', '5000 рублей', 500000),
    ('Пациент не требует 5000 рублей.', '5000 рублей', 500000),
    ('Пациент просит около 5000 рублей.', '5000 рублей', 500000),
    ('Пациент просил 5000 рублей, затем 10000 рублей.', '5000 рублей', 500000),
    ('Пациент просит 100 белорусских рублей.', 'рублей', 10000),
    ('Пациент просит A10000 рублей.', '10000 рублей', 1000000),
])
def test_model_selected_anchor_cannot_hide_larger_tokens_negation_or_other_amounts(
    source, anchor, amount,
):
    with pytest.raises(ValueError):
        proposal(source, amount, [anchor]).validate_source(source)
