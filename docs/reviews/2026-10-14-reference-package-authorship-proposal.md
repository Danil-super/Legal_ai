# Reference package authorship: unresolved proposal, 2026-10-14

## Status and existing contract

Owner choice remains unresolved. This is a proposed stronger authorization rule,
not an approved baseline requirement or a confirmed violation of the existing
creator-only restriction. SPEC-reference-evaluation-cases.md and ADR 0050 require
a distinct reviewer: a reviewer cannot approve a version they created.
The existing `created_by_membership_id` restriction and its tests remain unchanged.

The isolated synthetic probe below observed that a user with both CONTRIBUTOR
and REVIEWER grants can upload material into another creator's draft, submit it,
then approve that creator's version. Responses were 201 / 200 / 200 and the final
state was APPROVED_FOR_EVALUATION. No production package was accessed or approved.

Possible owner choices are to retain the existing creator-only rule, restrict
upload/submission to the creator, or define package authorship with explicit
uploader/submission metadata and independent review. The latter two change
authorization or data contracts and have not been authorized. No such fix was made.

Case and version rows record their creator; review events record the reviewer.
They do not have dedicated uploader or submitter fields. Existing audit events
and idempotency records identify the actor of material/submission operations.

## Preserved exact proposal and reproduction

This function was newly proposed during the audit, never an approved baseline
test. Its final assertion expresses the stronger rule and failed on the observed
201 / 200 / 200 sequence. It is preserved here instead of being collected by pytest;
no existing approved-contract test was deleted, skipped, xfailed or weakened.

Run only in an isolated disposable PostgreSQL with the existing synthetic helper
imports from `test_case_api` and `test_reference_evaluation_api`. The function is
reproduced exactly below; it does not call a model, live API or production storage.

```python
def test_a_reviewer_cannot_supply_material_then_approve_the_same_package():
    actor = 116_000_000_000 + uuid4().int % 100_000_000
    reviewer = actor + 1_000_000_000
    clinic, member = seed_admin(actor, role="CLINIC_OWNER")
    reviewer_member = _add_member(clinic, reviewer)
    _grant(clinic, member, "CONTRIBUTOR")
    _grant(clinic, reviewer_member, "CONTRIBUTOR", "REVIEWER")
    with application_client(reference_evaluation_store=FakeReferenceEvaluationStore()) as client:
        created = client.post(
            "/v1/reference-evaluations",
            headers=actor_headers(actor, uuid4()),
            json=_create_payload(),
        )
        assert created.status_code == 201, created.text
        case = created.json()["case"]["id"]
        supplied = client.post(
            f"/v1/reference-evaluations/{case}/material",
            headers={
                **actor_headers(reviewer, uuid4()),
                "X-Source-Filename": "synthetic.txt",
                "Content-Type": "text/plain",
            },
            content=b"Synthetic independently unreviewed reference material supplied by reviewer.",
        )
        submitted = client.post(
            f"/v1/reference-evaluations/{case}/submit",
            headers=actor_headers(reviewer, uuid4()),
        )
        reviewed = client.post(
            f"/v1/reference-evaluations/{case}/review",
            headers=actor_headers(reviewer, uuid4()),
            json={"decision": "APPROVE_FOR_EVALUATION"},
        )
    assert supplied.status_code in (403, 404) or reviewed.status_code == 403, (
        supplied.status_code,
        submitted.status_code,
        reviewed.status_code,
        reviewed.text,
    )
```
