"""Synthetic current-copy semantics must survive approval preview and public library."""

from uuid import UUID

from telegram_gateway.legal_library_runtime import (
    render_group_approval_preview,
    render_legal_library,
)


def _preview(limitations):
    return dict(
        group="general",
        snapshot="a" * 64,
        alreadyApproved=0,
        blocked=[],
        ready=[
            dict(
                versionId=str(UUID(int=i + 1)),
                title=f"Synthetic document {i + 1}",
                effectiveFrom="2026-10-08",
                effectiveTo=None,
                dateBasis="LAWYER_CURRENT_COPY",
                extractionLimitations=[limitation],
            )
            for i, limitation in enumerate(limitations)
        ],
    )


def test_group_preview_discloses_every_distinct_limit_before_human_confirmation():
    preview = _preview([f"SYNTHETIC_LIMIT_{i}" for i in range(4)])
    text, keyboard = render_group_approval_preview(preview, batch_id=str(UUID(int=75)), page=1)
    assert all(f"SYNTHETIC_LIMIT_{i}" in text for i in range(4))
    assert any(
        "batchconfirm:" in (button.callback_data or "")
        for row in keyboard.inline_keyboard
        for button in row
    )


def test_truncated_limitations_never_enable_group_attestation():
    text, keyboard = render_group_approval_preview(
        _preview([f"SYNTHETIC_LIMIT_{i}: " + "sample " * 140 for i in range(10)]),
        batch_id=str(UUID(int=75)),
        page=1,
    )
    assert len(text) <= 3900
    assert not any(
        "batchconfirm:" in (button.callback_data or "")
        for row in keyboard.inline_keyboard
        for button in row
    )


def test_public_current_copy_does_not_claim_its_review_floor_as_statutory_date():
    text, _ = render_legal_library(
        dict(
            asOfDate="2026-10-08",
            items=[
                dict(
                    documentId=str(UUID(int=1)),
                    versionId=str(UUID(int=2)),
                    documentTitle="Synthetic current legal copy",
                    issuer=None,
                    officialNumber=None,
                    effectiveFrom="2026-10-08",
                    effectiveTo=None,
                    sourceUrl="https://internet.garant.ru/example",
                    rawSha256="a" * 64,
                    fragmentCount=1,
                    dateBasis="LAWYER_CURRENT_COPY",
                    extractionLimitations=["SYNTHETIC_LIMIT"],
                )
            ],
        )
    )
    assert "Проверенная применимость" in text
    assert "Действует: 2026-10-08" not in text
