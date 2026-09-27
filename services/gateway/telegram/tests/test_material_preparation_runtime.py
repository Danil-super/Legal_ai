from uuid import uuid4

from telegram_gateway import legal_library_runtime as runtime


def test_preparation_card_preserves_original_download_and_group_back_button():
    material = str(uuid4())
    text, keyboard = runtime.render_material_preparation({
        "materialId": material, "title": "Synthetic clinical reference",
        "kind": "CLINICAL_REFERENCE", "groupKey": "clinical", "referenceYear": None,
        "extractionScope": "PARTIAL", "limitations": ["Page 1 has no text layer"],
        "missingFields": [], "parts": [],
    })
    assert "Synthetic clinical reference" in text
    assert "не указан" in text
    assert "Page 1 has no text layer" in text
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert f"editor:material:{material}" in callbacks
    assert "editor:group:clinical:1" in callbacks
    assert not any("confirm" in data for data in callbacks)
    assert all(len(data.encode()) <= 64 for data in callbacks)


def test_prepared_item_opens_its_card_without_adding_another_root_group():
    material = str(uuid4())
    payload = {"page": 1, "pageSize": 10, "totalItems": 1, "selectedGroup": "clinical",
               "groups": [{"key": "clinical", "title": "References", "totalItems": 1}],
               "items": [{"materialId": material, "versionId": None,
                          "preparationId": str(uuid4()), "preparationKind": "CLINICAL_REFERENCE",
                          "title": "Synthetic clinical reference", "kind": "CLINICAL_REFERENCE",
                          "reviewState": "METADATA_REQUIRED", "groupKey": "clinical"}]}
    _, keyboard = runtime.render_editor_review_materials(payload)
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert f"editor:preparation:{material}" in callbacks
    assert runtime._EDITOR_CALLBACK_RE.fullmatch(f"editor:preparation:{material}")
