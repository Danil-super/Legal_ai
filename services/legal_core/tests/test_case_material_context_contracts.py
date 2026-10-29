"""Bounded, hashed file excerpts are private context, never a legal source."""

import hashlib
from uuid import uuid4

import pytest

from legal_core.case_material_context_contracts import (
    CaseMaterialContextFragment,
    case_material_context_trace_sha256,
)
from legal_core.analysis_contracts import AnalysisContextResponse
from legal_core.case_preparation_contracts import PreparationContext
from legal_core.contracts import CanonicalReport
from test_report_readiness_contract import _legacy_payload


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


def context(**updates):
    values = {
        "caseId": uuid4(), "asOfDate": "2026-10-29", "facts": {},
        "factSnapshotSha256": "a" * 64, "evidenceTraceSha256": "b" * 64,
        "clinicDocumentContextTraceSha256": "c" * 64, "riskPolicyVersion": "synthetic.v1",
        "highDemandThresholdKopecks": 1000000,
        "evidence": [{"fragmentId": uuid4(), "versionId": uuid4(), "documentId": uuid4(),
            "article": None, "part": None, "point": None, "structuralPath": "text/1",
            "fragmentText": "Синтетическая учебная норма.", "textSha256": "d" * 64,
            "effectiveFrom": "2026-01-01", "effectiveTo": None,
            "sourceUrl": "https://example.test/synthetic", "rawSha256": "e" * 64,
            "documentTitle": "Синтетическая норма", "issuer": None,
            "officialNumber": None, "versionDate": None, "publicationDate": None}],
    }
    return AnalysisContextResponse.model_validate(values | updates)


def test_context_has_exact_paired_trace_and_old_wire_omits_all_additions():
    old = context().model_dump(mode="json", by_alias=True)
    assert "caseMaterialContext" not in old and "caseMaterialContextTraceSha256" not in old
    item = fragment()
    trace = case_material_context_trace_sha256([item])
    assert context(caseMaterialContext=[item], caseMaterialContextTraceSha256=trace)
    for changes in (
        {"caseMaterialContext": [item]}, {"caseMaterialContextTraceSha256": trace},
        {"caseMaterialContext": [item], "caseMaterialContextTraceSha256": "0" * 64},
    ):
        with pytest.raises(ValueError):
            context(**changes)


def test_preparation_source_and_old_reports_omit_absent_file_binding():
    old = PreparationContext(asOfDate="2026-10-29", evidenceTraceSha256="a" * 64,
        clinicDocumentContextTraceSha256="b" * 64, riskPolicyVersion="synthetic.v1")
    assert "caseMaterialContextTraceSha256" not in old.model_dump(by_alias=True)
    report = CanonicalReport.model_validate(_legacy_payload())
    wire = report.model_dump(mode="json", by_alias=True)
    assert "caseMaterials" not in wire and "caseMaterialContextTraceSha256" not in wire
    bound = CanonicalReport.model_validate(_legacy_payload() | {
        "caseMaterialContextTraceSha256": "c" * 64,
    }).model_dump(mode="json", by_alias=True)
    assert bound["caseMaterialContextTraceSha256"] == "c" * 64
    assert "caseMaterials" not in bound  # A blocked result does not claim file use.
