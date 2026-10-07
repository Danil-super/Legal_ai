"""Synthetic editor cards explain preparation without weakening approval gates."""

from uuid import UUID

import pytest
from telegram_gateway import legal_library_runtime as runtime


def preparation(**changes):
    return {
        "materialId": str(UUID(int=1)), "title": "Synthetic normative original",
        "kind": "NORMATIVE", "groupKey": "general", "extractionScope": "PARTIAL",
        "limitations": [], "missingFields": [
            "document:canonical_key", "document:publication_date",
            "document:version_date", "document:effective_from",
        ],
        "parts": [{"part_key": "document", "title": "Synthetic normative part"}],
        "linkedPartKeys": [], **changes,
    }


def test_preparation_displays_each_missing_field_in_plain_language():
    text, keyboard = runtime.render_material_preparation(preparation())
    assert "идентификатор акта в реестре" in text
    assert "дата официального опубликования" in text
    assert "дата редакции" in text
    assert "дата начала действия редакции" in text
    assert "полнота текста не подтверждена" in text
    assert "canonical_key" not in text and "effective_from" not in text
    callbacks = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert f"editor:material:{UUID(int=1)}" in callbacks
    assert not any("confirm" in value for value in callbacks)


def test_missing_fields_remain_attributed_to_the_exact_bundle_part():
    text, _ = runtime.render_material_preparation(preparation(
        parts=[{"part_key": "part-1", "title": "Synthetic first part"},
               {"part_key": "part-2", "title": "Synthetic second part"}],
        missingFields=["part-1:official_number", "part-2:effective_from"],
    ))
    assert "Часть 1: номер акта" in text
    assert "Часть 2: дата начала действия редакции" in text


def test_full_preparation_is_not_reported_as_partial_or_auto_approved():
    text, keyboard = runtime.render_material_preparation(preparation(
        extractionScope="FULL_DOCUMENT", missingFields=[], linkedPartKeys=["document"],
    ))
    assert "полнота текста не подтверждена" not in text
    assert "Эта карточка не означает утверждение" in text
    assert not any("confirm" in b.callback_data for row in keyboard.inline_keyboard for b in row)


def test_reference_card_does_not_inherit_normative_metadata_requirements():
    text, _ = runtime.render_material_preparation(preparation(
        kind="CLINICAL_REFERENCE", groupKey="clinical", parts=[], missingFields=[],
    ))
    assert "дата начала действия редакции" not in text
    assert "подготовить нормативную версию" not in text
    assert "не нормативный акт" in text


def test_blocked_only_batch_explains_the_next_step_without_confirmation():
    text, keyboard = runtime.render_group_approval_preview({
        "group": "general", "ready": [], "alreadyApproved": 0,
        "blocked": [{"title": "Synthetic normative original", "reasonCode": "PARTS_UNBOUND"}],
    }, batch_id=str(UUID(int=2)), page=1)
    assert "Откройте карточку исходного файла" in text
    assert "подтверждённые реквизиты и даты" in text
    assert "полнота текста" in text
    assert "подготовить нормативную версию" in text
    assert not any("batchconfirm" in b.callback_data
                   for row in keyboard.inline_keyboard for b in row)


def test_unknown_field_does_not_echo_untrusted_metadata_codes():
    text, _ = runtime.render_material_preparation(preparation(
        missingFields=["document:SYNTHETIC_UNTRUSTED_DETAIL"],
    ))
    assert "SYNTHETIC_UNTRUSTED_DETAIL" not in text
    assert "дополнительные реквизиты" in text


@pytest.mark.parametrize(("field", "label"), [
    ("document_type", "вид нормативного акта"),
    ("issuer", "орган, издавший акт"),
    ("adoption_date", "дата принятия акта"),
])
def test_other_required_identity_fields_have_distinct_labels(field, label):
    text, _ = runtime.render_material_preparation(preparation(missingFields=[f"document:{field}"]))
    assert label in text


@pytest.mark.parametrize("scope", ["NONE", "SYNTHETIC_UNKNOWN_SCOPE", None])
def test_unknown_or_missing_scope_never_claims_full_text_completeness(scope):
    text, _ = runtime.render_material_preparation(preparation(extractionScope=scope))
    assert "полнота текста не подтверждена" in text


@pytest.mark.parametrize("changes", [
    {"missingFields": [None]}, {"missingFields": ["document:issuer"] * 162},
    {"parts": [None]}, {"parts": [{"part_key": None}]}, {"linkedPartKeys": None},
])
def test_invalid_preparation_metadata_fails_closed_without_echoing_values(changes):
    with pytest.raises(ValueError):
        runtime.render_material_preparation(preparation(**changes))


def test_unknown_non_string_batch_reason_remains_generic_without_confirming():
    text, keyboard = runtime.render_group_approval_preview({
        "group": "general", "ready": [], "alreadyApproved": 0,
        "blocked": [{"title": "Synthetic original", "reasonCode": ["SYNTHETIC_UNTRUSTED"]}],
    }, batch_id=str(UUID(int=2)), page=1)
    assert "SYNTHETIC_UNTRUSTED" not in text
    assert not any("batchconfirm" in b.callback_data
                   for row in keyboard.inline_keyboard for b in row)
