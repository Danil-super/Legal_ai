import pytest
from legal_core.legal_updater import UpdateRunStatus, update_run_payload


def test_failed_update_run_payload_is_hash_only_and_requires_a_failure_code() -> None:
    payload = update_run_payload(
        status=UpdateRunStatus.FETCH_FAILED,
        idempotency_sha256="a" * 64,
        failure_code="HTTPS_TIMEOUT",
    )

    assert payload.status is UpdateRunStatus.FETCH_FAILED
    assert payload.failure_code == "HTTPS_TIMEOUT"
    assert len(payload.result_sha256) == 64
    assert not hasattr(payload, "error_text")

    with pytest.raises(ValueError, match="failure status requires a failure code"):
        update_run_payload(
            status=UpdateRunStatus.FETCH_FAILED,
            idempotency_sha256="a" * 64,
            failure_code=None,
        )


def test_review_queued_run_requires_its_review_item() -> None:
    with pytest.raises(ValueError, match="review-queued status requires a review item"):
        update_run_payload(
            status=UpdateRunStatus.REVIEW_QUEUED,
            idempotency_sha256="b" * 64,
            failure_code=None,
        )
