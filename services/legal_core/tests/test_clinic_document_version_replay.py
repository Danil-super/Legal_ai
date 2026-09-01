from datetime import date

import pytest

from legal_core.case_api import ApiError
from legal_core.clinic_documents_api import _validate_existing_version_replay


def _existing() -> dict[str, object]:
    return {
        "source_filename": "contract.txt",
        "mime_type": "text/plain",
        "normalized_text_sha256": "a" * 64,
        "valid_from": date(2026, 1, 1),
        "valid_to": None,
    }


def test_exact_clinic_document_version_replay_is_allowed() -> None:
    _validate_existing_version_replay(
        _existing(),
        source_filename="contract.txt",
        mime_type="text/plain",
        normalized_text_sha256="a" * 64,
        valid_from=date(2026, 1, 1),
        valid_to=None,
    )


def test_same_raw_document_cannot_silently_change_version_metadata() -> None:
    with pytest.raises(ApiError) as raised:
        _validate_existing_version_replay(
            _existing(),
            source_filename="contract.txt",
            mime_type="text/plain",
            normalized_text_sha256="a" * 64,
            valid_from=date(2026, 2, 1),
            valid_to=None,
        )

    assert raised.value.status_code == 409
    assert raised.value.code == "CLINIC_DOCUMENT_VERSION_METADATA_CONFLICT"


def test_same_raw_document_cannot_silently_change_normalized_content() -> None:
    with pytest.raises(ApiError) as raised:
        _validate_existing_version_replay(
            _existing(),
            source_filename="contract.txt",
            mime_type="text/plain",
            normalized_text_sha256="b" * 64,
            valid_from=date(2026, 1, 1),
            valid_to=None,
        )

    assert raised.value.status_code == 409
    assert raised.value.code == "CLINIC_DOCUMENT_REPROCESSING_CONFLICT"
