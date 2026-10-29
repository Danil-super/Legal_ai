"""Bounded, hashed file excerpts are private context, never a legal source."""

import hashlib
from uuid import uuid4

import pytest

from legal_core.case_material_context_contracts import (
    CaseMaterialContextFragment,
    case_material_context_trace_sha256,
)


def fragment(**updates):
    text = "Синтетическая памятка: сохранить документ после приёма."
    values = {
        "materialId": uuid4(), "fragmentId": uuid4(), "displayName": "Материал 1",
        "mimeType": "text/plain", "rawSha256": "a" * 64,
        "textSha256": hashlib.sha256(text.encode()).hexdigest(),
        "structuralPath": "text/fragment/1", "text": text,
        "extractionLimitations": ["Выбран фрагмент; полный файл автоматически не проверялся."],
    }
    return CaseMaterialContextFragment.model_validate(values | updates)


def test_case_file_trace_is_absent_without_files_and_stable_for_exact_context():
    item = fragment()
    trace = case_material_context_trace_sha256([item])
    assert trace is not None and len(trace) == 64
    assert case_material_context_trace_sha256([]) is None
    assert case_material_context_trace_sha256([item.model_copy()]) == trace
    changed = fragment(materialId=item.material_id, fragmentId=item.fragment_id,
                       rawSha256="b" * 64)
    assert case_material_context_trace_sha256([changed]) != trace


@pytest.mark.parametrize("bad", [
    {"textSha256": "0" * 64}, {"displayName": "patient-record.txt"},
    {"text": "x" * 1201}, {"mimeType": "application/octet-stream"},
])
def test_context_rejects_tampered_hash_unbounded_text_and_original_names(bad):
    with pytest.raises(ValueError):
        fragment(**bad)


def test_trace_revalidates_mutated_fragments_and_rejects_duplicate_ids():
    item = fragment()
    with pytest.raises(ValueError):
        case_material_context_trace_sha256([item, item])
    item.text = "Изменённый текст без нового хеша"
    with pytest.raises(ValueError):
        case_material_context_trace_sha256([item])
