"""A deterministic unit correction preserves independent initial factual suggestions."""

from copy import deepcopy

import pytest

from test_intake_understanding import run_model


def payload(source, amount, anchor):
    start = source.index(anchor)
    return {'clinicActions': ['INVITED_FOR_EXAMINATION'],
            'safetyScreening': {'schemaVersion': 'factual-safety-intake.v1',
                                'moneyRequested': 'YES',
                                'amount': {'amountKopecks': amount, 'currency': 'RUB'}},
            'sourceSpans': {'clinicActions': [{'start': 0, 'end': 10}],
                            'safetyScreening': [{'start': start, 'end': start + len(anchor)}]}}


@pytest.mark.parametrize('rubles', [5000, 10000])
def test_initial_ruble_unit_error_is_corrected_without_an_extra_model_call(rubles):
    anchor = f'попросил возврат {rubles} рублей'
    source = f'20 октября оказали услугу, 27 октября {anchor}. Клиника пригласила на осмотр.'
    supplied = payload(source, rubles, anchor)
    original = deepcopy(supplied)
    calls = []
    result = run_model(source, supplied, calls)
    assert result.safety_screening['amount'] == {
        'amountKopecks': rubles * 100, 'currency': 'RUB',
    }
    assert result.clinic_actions == ['INVITED_FOR_EXAMINATION']
    assert len(calls) == 1 and supplied == original
    result.validate_source(source)


@pytest.mark.parametrize('anchor,amount', [
    ('попросил возврат 5000 рублей', 42),
    ('попросил возврат 5000 USD', 5000),
    ('попросил возврат 5000 или 10000 рублей', 5000),
    ('попросил возврат около 5000 рублей', 5000),
    ('попросил возврат 5000 баллов', 5000),
])
def test_unsupported_amount_does_not_discard_other_usable_suggestions(anchor, amount):
    source = f'Пациент {anchor}. Клиника пригласила на осмотр.'
    result = run_model(source, payload(source, amount, anchor), [])
    assert 'amount' not in result.safety_screening
    assert result.safety_screening['moneyRequested'] == 'YES'
    assert result.clinic_actions == ['INVITED_FOR_EXAMINATION']
    result.validate_source(source)


def test_correct_amount_remains_unchanged_with_separate_negative_health_anchors():
    money, health = 'попросил возврат 5000 рублей', 'Госпитализации не было'
    source = f'20 октября оказали услугу, 27 октября {money}. {health}.'
    supplied = payload(source, 500000, money)
    start = source.index(health)
    supplied['sourceSpans']['safetyScreening'].append({'start': start, 'end': start + len(health)})
    result = run_model(source, supplied, [])
    assert result.safety_screening['amount'] == {'amountKopecks': 500000, 'currency': 'RUB'}


@pytest.mark.parametrize('source,anchor,amount', [
    ('Пациент просит 15000 рублей.', '5000 рублей', 5000),
    ('Пациент не требует 5000 рублей.', '5000 рублей', 5000),
    ('Пациент просит около 5000 рублей.', '5000 рублей', 5000),
    ('Пациент просил 5000 рублей, затем 10000 рублей.', '5000 рублей', 5000),
])
def test_clipped_or_ambiguous_model_anchor_never_rewrites_the_whole_source(
    source, anchor, amount,
):
    result = run_model(source, payload(source, amount, anchor), [])
    assert 'amount' not in result.safety_screening
    assert result.clinic_actions == ['INVITED_FOR_EXAMINATION']


@pytest.mark.parametrize('rubles,expected', [(5000, 'MEDIUM'), (10000, 'HIGH')])
def test_initial_unit_normalization_preserves_the_unchanged_v4_risk_boundary(rubles, expected):
    from legal_core.contracts import FactKey
    from legal_core.intake_preparation_merge import merge_preparation_suggestions
    from legal_core.risk_engine import evaluate_risk
    from test_factual_safety_risk import facts
    from test_risk_policy_v4 import v4_policy

    anchor = f'попросил возврат {rubles} рублей'
    source = f'Пациент {anchor}. Клиника пригласила на осмотр.'
    result = run_model(source, payload(source, rubles, anchor), [])
    merged = merge_preparation_suggestions({'intakeVersion': 2, 'eventSummary': source},
                                          result, source)
    confirmed = facts(moneyRequested='YES', amount=merged['safetyScreening']['amount'])
    confirmed[FactKey.EVENT_SUMMARY] = source
    risk = evaluate_risk(confirmed, policy=v4_policy(), evidence_verified=True)
    assert risk.level.value == expected
    assert risk.external_draft_allowed is False


@pytest.mark.parametrize('separator', [' ', '\u00a0', '\u202f'])
def test_malformed_spaced_numeric_group_leaves_the_amount_question_unanswered(separator):
    source = f'Пациент просит 1{separator}5000 рублей.'
    result = run_model(source, payload(source, 5000, '5000 рублей'), [])
    assert 'amount' not in result.safety_screening
    assert result.clinic_actions == ['INVITED_FOR_EXAMINATION']
