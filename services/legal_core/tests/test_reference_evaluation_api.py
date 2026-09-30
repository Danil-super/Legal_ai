"""Integration coverage for de-identified reference-evaluation access and review."""

import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text

from legal_core.clinic_document_store import reference_evaluation_object_key
from legal_core.database import owner_database_url
from test_case_api import actor_headers, application_client, seed_admin

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="requires disposable PostgreSQL"
)


class FakeReferenceEvaluationStore:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.deleted: list[str] = []

    async def put_reference_evaluation(
        self,
        *,
        clinic_id: UUID,
        version_id: UUID,
        raw_sha256: str,
        content: bytes,
        content_type: str,
    ) -> str:
        key = reference_evaluation_object_key(
            clinic_id=clinic_id,
            version_id=version_id,
            raw_sha256=raw_sha256,
        )
        self.objects[key] = (content, content_type)
        return key

    async def get_reference_evaluation(self, *, object_key: str, max_bytes: int) -> bytes:
        content, _ = self.objects[object_key]
        assert len(content) <= max_bytes
        return content

    async def delete_reference_evaluation(self, *, object_key: str) -> None:
        self.deleted.append(object_key)
        self.objects.pop(object_key, None)


def _add_member(clinic_id: UUID, telegram_user_id: int) -> UUID:
    user_id = uuid4()
    membership_id = uuid4()
    engine = create_engine(owner_database_url().set(drivername="postgresql+psycopg"))
    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO users (id,telegram_user_id) VALUES (:id,:telegram_user_id)"),
                {"id": user_id, "telegram_user_id": telegram_user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO clinic_users (id,clinic_id,user_id,role) "
                    "VALUES (:id,:clinic_id,:user_id,'CLINIC_LAWYER')"
                ),
                {"id": membership_id, "clinic_id": clinic_id, "user_id": user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO subscription_entitlements "
                    "(clinic_id,user_id,status,plan_code,starts_at) "
                    "VALUES (:clinic_id,:user_id,'ACTIVE','MVP',timezone('utc', now()) "
                    "- interval '1 day')"
                ),
                {"clinic_id": clinic_id, "user_id": user_id},
            )
    finally:
        engine.dispose()
    return membership_id


def _grant(clinic_id: UUID, membership_id: UUID, *permissions: str) -> None:
    engine = create_engine(owner_database_url().set(drivername="postgresql+psycopg"))
    try:
        with engine.begin() as connection:
            for permission in permissions:
                connection.execute(
                    text(
                        "INSERT INTO reference_evaluation_access_grants "
                        "(clinic_id,membership_id,permission) "
                        "VALUES (:clinic_id,:membership_id,:permission)"
                    ),
                    {
                        "clinic_id": clinic_id,
                        "membership_id": membership_id,
                        "permission": permission,
                    },
                )
    finally:
        engine.dispose()


def _create_payload() -> dict[str, str]:
    return {
        "groupKey": "healthcare",
        "asOfDate": "2026-06-01",
        "expectedRoute": "HUMAN_ESCALATION",
        "scenarioText": "После лечения пациент сообщил о сохраняющейся чувствительности зуба.",
    }


def test_only_explicit_grants_can_upload_and_a_distinct_reviewer_can_approve() -> None:
    owner = 8_900_000_000 + uuid4().int % 100_000_000
    reviewer = 8_910_000_000 + uuid4().int % 100_000_000
    outsider = 8_920_000_000 + uuid4().int % 100_000_000
    clinic_id, owner_membership_id = seed_admin(owner, role="CLINIC_OWNER")
    reviewer_membership_id = _add_member(clinic_id, reviewer)
    seed_admin(outsider)
    _grant(clinic_id, owner_membership_id, "CONTRIBUTOR")
    _grant(clinic_id, reviewer_membership_id, "CONTRIBUTOR", "REVIEWER")
    store = FakeReferenceEvaluationStore()

    with application_client(reference_evaluation_store=store) as client:
        owner_access = client.get("/v1/reference-evaluations/access", headers=actor_headers(owner))
        outsider_access = client.get(
            "/v1/reference-evaluations/access", headers=actor_headers(outsider)
        )
        created = client.post(
            "/v1/reference-evaluations",
            json=_create_payload(),
            headers=actor_headers(owner, uuid4()),
        )
        assert created.status_code == 201, created.text
        case_id = created.json()["case"]["id"]
        uploaded = client.post(
            f"/v1/reference-evaluations/{case_id}/material",
            content=b"Synthetic de-identified historical scenario for review.",
            headers={
                **actor_headers(owner, uuid4()),
                "X-Source-Filename": "historical-patient-record.txt",
                "Content-Type": "text/plain",
            },
        )
        submitted = client.post(
            f"/v1/reference-evaluations/{case_id}/submit",
            headers=actor_headers(owner, uuid4()),
        )
        self_denied = client.post(
            f"/v1/reference-evaluations/{case_id}/review",
            json={"decision": "APPROVE_FOR_EVALUATION"},
            headers=actor_headers(owner, uuid4()),
        )
        approved = client.post(
            f"/v1/reference-evaluations/{case_id}/review",
            json={"decision": "APPROVE_FOR_EVALUATION"},
            headers=actor_headers(reviewer, uuid4()),
        )
        downloaded = client.get(
            f"/v1/reference-evaluations/{case_id}/material",
            headers=actor_headers(reviewer),
        )
        foreign = client.get(
            f"/v1/reference-evaluations/{case_id}", headers=actor_headers(outsider)
        )
        identifier_rejected = client.post(
            "/v1/reference-evaluations",
            json={**_create_payload(), "scenarioText": "Позвоните +7 999 123-45-67 для уточнения."},
            headers=actor_headers(owner, uuid4()),
        )

    assert owner_access.json() == {"canContribute": True, "canReview": False}
    assert outsider_access.json() == {"canContribute": False, "canReview": False}
    assert created.json()["case"]["displayName"].startswith("Эталонный кейс №")
    assert "scenarioSha256" not in created.text
    assert uploaded.status_code == 201, uploaded.text
    assert "historical-patient-record" not in uploaded.text
    assert submitted.json()["status"] == "READY_FOR_REVIEW"
    assert self_denied.status_code == 403
    assert approved.json()["case"]["status"] == "APPROVED_FOR_EVALUATION"
    assert downloaded.status_code == 200
    assert downloaded.content.startswith(b"Synthetic de-identified")
    assert "historical-patient-record" not in downloaded.headers["content-disposition"]
    assert foreign.status_code == 403
    assert identifier_rejected.status_code == 422
    assert identifier_rejected.json()["error"]["code"] == (
        "REFERENCE_EVALUATION_DIRECT_IDENTIFIER_NOT_ALLOWED"
    )


def test_reviewer_can_request_changes_and_only_the_contributor_can_submit_a_new_version() -> None:
    owner = 8_930_000_000 + uuid4().int % 100_000_000
    reviewer = 8_940_000_000 + uuid4().int % 100_000_000
    clinic_id, owner_membership_id = seed_admin(owner, role="CLINIC_OWNER")
    reviewer_membership_id = _add_member(clinic_id, reviewer)
    _grant(clinic_id, owner_membership_id, "CONTRIBUTOR")
    _grant(clinic_id, reviewer_membership_id, "CONTRIBUTOR", "REVIEWER")

    with application_client(reference_evaluation_store=FakeReferenceEvaluationStore()) as client:
        created = client.post(
            "/v1/reference-evaluations",
            json=_create_payload(),
            headers=actor_headers(owner, uuid4()),
        )
        assert created.status_code == 201, created.text
        case_id = created.json()["case"]["id"]
        assert client.post(
            f"/v1/reference-evaluations/{case_id}/submit",
            headers=actor_headers(owner, uuid4()),
        ).status_code == 200
        changes = client.post(
            f"/v1/reference-evaluations/{case_id}/review",
            json={
                "decision": "CHANGES_REQUIRED",
                "note": "Уточните фактическую последовательность.",
            },
            headers=actor_headers(reviewer, uuid4()),
        )
        revised = client.post(
            f"/v1/reference-evaluations/{case_id}/revisions",
            json={
                "asOfDate": "2026-06-02",
                "expectedRoute": "HUMAN_ESCALATION",
                "scenarioText": (
                    "После лечения сохранилась чувствительность; "
                    "клиника пригласила на осмотр."
                ),
            },
            headers=actor_headers(owner, uuid4()),
        )
        foreign_revision = client.post(
            f"/v1/reference-evaluations/{case_id}/revisions",
            json={
                "asOfDate": "2026-06-02",
                "expectedRoute": "HUMAN_ESCALATION",
                "scenarioText": "Новая обезличенная версия фактической ситуации для проверки.",
            },
            headers=actor_headers(reviewer, uuid4()),
        )

    assert changes.status_code == 200, changes.text
    assert changes.json()["case"]["status"] == "CHANGES_REQUIRED"
    assert revised.status_code == 200, revised.text
    assert revised.json()["case"]["currentVersion"] == 2
    assert revised.json()["case"]["status"] == "DRAFT"
    assert foreign_revision.status_code == 409
    assert foreign_revision.json()["error"]["code"] == "REFERENCE_EVALUATION_REVISION_NOT_ALLOWED"


def test_reference_evaluation_list_cursor_does_not_skip_the_next_case() -> None:
    owner = 8_960_000_000 + uuid4().int % 100_000_000
    clinic_id, membership_id = seed_admin(owner, role="CLINIC_OWNER")
    _grant(clinic_id, membership_id, "CONTRIBUTOR")

    with application_client(reference_evaluation_store=FakeReferenceEvaluationStore()) as client:
        created_ids = []
        for index in range(3):
            created = client.post(
                "/v1/reference-evaluations",
                json={
                    **_create_payload(),
                    "scenarioText": f"Обезличенная фактическая ситуация для проверки {index}.",
                },
                headers=actor_headers(owner, uuid4()),
            )
            assert created.status_code == 201, created.text
            created_ids.append(created.json()["case"]["id"])
        first = client.get(
            "/v1/reference-evaluations?limit=1", headers=actor_headers(owner)
        )
        second = client.get(
            "/v1/reference-evaluations",
            params={"limit": 1, "before": first.json()["nextBefore"]},
            headers=actor_headers(owner),
        )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_id = first.json()["items"][0]["id"]
    second_id = second.json()["items"][0]["id"]
    assert first_id != second_id
    assert {first_id, second_id} <= set(created_ids)
