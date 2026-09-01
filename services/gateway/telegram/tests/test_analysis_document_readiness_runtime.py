from telegram_gateway.analysis_runtime import telegram_analysis_summary


def _payload(readiness: list[dict[str, object]], *, allowed: bool = True) -> dict[str, object]:
    return {
        "analysisAllowed": allowed,
        "riskLevel": "LOW" if allowed else "UNAVAILABLE",
        "clinicDocumentReadiness": readiness,
        "report": {
            "reportJson": {
                "case": {"publicNumber": "DL-2026-000099"},
                "risk": {
                    "level": "LOW",
                    "reasonCodes": [],
                    "escalationRequired": False,
                },
                "recommendations": {"status": "AVAILABLE", "items": ["Зафиксировать обращение."]},
                "legalBasis": {"status": "AVAILABLE", "sources": []},
                "clinicDocuments": {"status": "NOT_USED", "sources": []},
                "draftResponse": {
                    "status": "BLOCKED",
                    "reasonCode": "HUMAN_LEGAL_REVIEW_REQUIRED",
                },
            }
        },
    }


def test_missing_clinic_documents_are_presented_as_non_blocking_internal_checklist() -> None:
    summary = telegram_analysis_summary(
        _payload(
            [
                {
                    "expectationCode": "CONTRACT",
                    "importance": "CORE",
                    "status": "NOT_AVAILABLE",
                    "analysisBlocking": False,
                },
                {
                    "expectationCode": "IMPLANT_CONSENT",
                    "importance": "SCENARIO",
                    "status": "NOT_AVAILABLE",
                    "analysisBlocking": False,
                },
                {
                    "expectationCode": "WARRANTY_POLICY",
                    "importance": "SCENARIO",
                    "status": "RETRIEVED",
                    "analysisBlocking": False,
                },
            ]
        )
    )

    assert "📎 Для разбора полезно добавить в базу клиники" in summary
    assert "⚠️ договор на платные стоматологические услуги" in summary
    assert "• ИДС на имплантацию / хирургическое вмешательство" in summary
    assert "гарантийное положение" not in summary
    assert "не перечень обязательных по закону документов" in summary


def test_available_not_retrieved_is_not_misreported_as_missing_from_clinic() -> None:
    summary = telegram_analysis_summary(
        _payload(
            [
                {
                    "expectationCode": "GENERAL_CONSENT",
                    "importance": "CORE",
                    "status": "AVAILABLE_NOT_RETRIEVED",
                    "matchedDocumentKeys": ["consent-general"],
                    "analysisBlocking": False,
                }
            ]
        )
    )

    assert "Для разбора полезно добавить" not in summary
    assert "общее информированное согласие" not in summary


def test_blocked_legal_analysis_can_still_show_non_blocking_clinic_document_gap() -> None:
    summary = telegram_analysis_summary(
        _payload(
            [
                {
                    "expectationCode": "MEDICAL_RECORD_ACCESS",
                    "importance": "SCENARIO",
                    "status": "NOT_AVAILABLE",
                    "analysisBlocking": False,
                }
            ],
            allowed=False,
        )
    )

    assert "не прошёл проверку доказательств" in summary
    assert "порядок предоставления медицинских документов" in summary
    assert "не перечень обязательных по закону документов" in summary


def test_missing_readiness_list_contract_is_fail_closed() -> None:
    payload = _payload([])
    payload["clinicDocumentReadiness"] = "not-a-list"

    try:
        telegram_analysis_summary(payload)
    except ValueError as exc:
        assert "clinic document readiness" in str(exc)
    else:
        raise AssertionError("malformed readiness must be rejected")
