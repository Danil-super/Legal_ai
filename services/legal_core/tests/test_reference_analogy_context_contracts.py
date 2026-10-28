"""Reference context/source binding; old absent-field serialization stays exact."""

import hashlib
import json

import pytest
from pydantic import ValidationError

from legal_core.analysis_contracts import AnalysisContextResponse, AnalysisSubmissionRequest
from legal_core.case_preparation_contracts import PreparationContext
from legal_core.lawyer_draft_contracts import LawyerDraftContextResponse
from legal_core.reference_analogy_contracts import build_reference_analogy_context
from test_lawyer_draft_contracts import _wire
from test_reference_analogy_contracts import example


def test_absent_reference_fields_preserve_exact_old_context_source_and_submission_wire():
    wire = _wire()
    parsed = LawyerDraftContextResponse.model_validate(wire)
    old = parsed.model_dump(mode="json", by_alias=True)
    source = parsed.source.context
    assert source is not None
    expected = dict(wire["source"]["context"])
    assert source.model_dump(mode="json", by_alias=True) == expected
    assert "referenceExample" not in json.dumps(old)
    original = hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest()
    assert hashlib.sha256(json.dumps(source.model_dump(mode="json", by_alias=True),
                                    sort_keys=True).encode()).hexdigest() == original
    request = {"asOfDate": "2026-10-08", "expectedFactSnapshotSha256": "a" * 64,
               "expectedEvidenceTraceSha256": "b" * 64,
               "expectedClinicDocumentContextTraceSha256": "c" * 64,
               "expectedRiskPolicyVersion": "test.v4", "claims": [], "semanticReviews": []}
    assert AnalysisSubmissionRequest.model_validate(request).model_dump(
        mode="json", by_alias=True) == request


def with_examples():
    wire = _wire()
    selected = build_reference_analogy_context([example()])
    wire["context"]["referenceExamples"] = [item.model_dump(mode="json", by_alias=True)
                                             for item in selected.examples]
    wire["context"]["referenceExampleTraceSha256"] = selected.trace_sha256
    wire["source"]["context"]["referenceExampleTraceSha256"] = selected.trace_sha256
    return wire


def test_exact_examples_and_trace_are_bound_to_the_private_source_without_storing_text_there():
    wire = with_examples()
    result = LawyerDraftContextResponse.model_validate(wire)
    assert len(result.context.reference_examples) == 1
    assert result.context.reference_example_trace_sha256 == (
        result.source.context.reference_example_trace_sha256)
    assert "scenarioText" not in result.source.model_dump_json(by_alias=True)


@pytest.mark.parametrize("fault", ["context-trace", "source-trace", "no-source-trace",
                                   "no-examples", "no-context-trace", "empty-examples"])
def test_incomplete_or_changed_reference_source_is_refused(fault):
    wire = with_examples()
    if fault == "context-trace":
        wire["context"]["referenceExampleTraceSha256"] = "f" * 64
    elif fault == "source-trace":
        wire["source"]["context"]["referenceExampleTraceSha256"] = "f" * 64
    elif fault == "no-source-trace":
        del wire["source"]["context"]["referenceExampleTraceSha256"]
    elif fault == "no-examples":
        del wire["context"]["referenceExamples"]
    elif fault == "empty-examples":
        wire["context"]["referenceExamples"] = []
    else:
        del wire["context"]["referenceExampleTraceSha256"]
    with pytest.raises(ValidationError):
        LawyerDraftContextResponse.model_validate(wire)


def test_explicit_none_keeps_legacy_omission_and_new_trace_changes_source_hash():
    wire = _wire()["source"]["context"]
    old = PreparationContext.model_validate(wire)
    absent = PreparationContext.model_validate({**wire, "referenceExampleTraceSha256": None})
    assert old.model_dump_json(by_alias=True) == absent.model_dump_json(by_alias=True)
    current = PreparationContext.model_validate({**wire, "referenceExampleTraceSha256": "d" * 64})
    assert old.model_dump_json(by_alias=True) != current.model_dump_json(by_alias=True)
    context = AnalysisContextResponse.model_validate(_wire()["context"])
    assert context.reference_examples is None and context.reference_example_trace_sha256 is None
