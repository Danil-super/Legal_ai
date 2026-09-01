from agent_orchestrator.projection import build_projection_from_context
from legal_core.analysis_contracts import AnalysisContextResponse


def test_projection_preserves_non_blocking_readiness_without_adding_evidence_ids() -> None:
    context = AnalysisContextResponse.model_validate(
        {
            "caseId": "00000000-0000-0000-0000-000000000010",
            "asOfDate": "2026-08-31",
            "facts": {"SERVICE_TYPE": "Имплантация"},
            "factSnapshotSha256": "a" * 64,
            "evidenceTraceSha256": "b" * 64,
            "evidence": [
                {
                    "fragmentId": "00000000-0000-0000-0000-000000000001",
                    "versionId": "00000000-0000-0000-0000-000000000002",
                    "documentId": "00000000-0000-0000-0000-000000000003",
                    "article": None,
                    "part": None,
                    "point": "1",
                    "structuralPath": "point:1",
                    "fragmentText": "Синтетическая проверенная норма.",
                    "textSha256": "c" * 64,
                    "effectiveFrom": "2026-01-01",
                    "effectiveTo": None,
                    "sourceUrl": "https://publication.pravo.gov.ru/synthetic",
                    "rawSha256": "d" * 64,
                    "documentTitle": "Синтетический акт",
                    "issuer": "Synthetic authority",
                    "officialNumber": "1",
                    "versionDate": "2026-01-01",
                    "publicationDate": "2026-01-01",
                }
            ],
            "clinicDocumentContextTraceSha256": "e" * 64,
            "clinicDocumentContext": [],
            "clinicDocumentReadiness": [
                {
                    "expectationCode": "IMPLANT_CONSENT",
                    "importance": "SCENARIO",
                    "acceptedDocumentTypes": [
                        "INFORMED_CONSENT_IMPLANT",
                        "INFORMED_CONSENT_SURGERY",
                    ],
                    "reasonCode": "IMPLANT_CASE_SPECIALTY_CONSENT",
                    "status": "NOT_AVAILABLE",
                    "matchedDocumentKeys": [],
                    "analysisBlocking": False,
                }
            ],
            "riskPolicyVersion": "dental-risk.v1",
            "highDemandThresholdKopecks": 10_000_000,
        }
    )

    projection = build_projection_from_context(context)

    assert len(projection.clinic_document_readiness) == 1
    item = projection.clinic_document_readiness[0]
    assert item.expectation_code == "IMPLANT_CONSENT"
    assert item.status == "NOT_AVAILABLE"
    assert item.analysis_blocking is False
    payload = item.model_dump(mode="json", by_alias=True)
    assert set(payload) == {
        "expectationCode",
        "importance",
        "acceptedDocumentTypes",
        "reasonCode",
        "status",
        "matchedDocumentKeys",
        "analysisBlocking",
    }
    assert "fragmentId" not in payload
