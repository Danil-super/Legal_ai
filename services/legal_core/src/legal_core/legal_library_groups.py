"""Presentation groups over the existing approved, date-aware evidence view."""

from datetime import date

from sqlalchemy import Date, Integer, String, Uuid, case, func, literal, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from legal_core.legal_retrieval import (
    _LIST_APPROVED_LIBRARY_DOCUMENTS,
    ApprovedLegalLibraryDocument,
)
from legal_core.models import LegalMaterialPreparation, LegalPreparedPartVersion
from legal_core.review_material_groups import GROUP_TITLES, review_group_expression

LIBRARY_GROUP_TITLES = {key: title for key, title in GROUP_TITLES.items() if key != "other"}


async def list_approved_group_documents(
    session: AsyncSession, *, group: str, as_of_date: date, page: int
) -> tuple[list[ApprovedLegalLibraryDocument], bool]:
    if group not in LIBRARY_GROUP_TITLES or type(page) is not int or not 1 <= page <= 100:
        raise ValueError("legal library group page")
    # Preserve the production view and date predicates; only pagination/presentation change.
    approved = (
        text(_LIST_APPROVED_LIBRARY_DOCUMENTS.text.rsplit("ORDER BY", 1)[0])
        .columns(
            document_id=Uuid(),
            version_id=Uuid(),
            document_title=String(),
            issuer=String(),
            official_number=String(),
            effective_from=Date(),
            effective_to=Date(),
            source_url=String(),
            raw_sha256=String(),
            fragment_count=Integer(),
            date_basis=String(),
            extraction_limitations=JSONB(),
        )
        .subquery("approved_library_documents")
    )
    prepared_group = (
        select(LegalMaterialPreparation.group_key)
        .join(
            LegalPreparedPartVersion,
            LegalPreparedPartVersion.preparation_id == LegalMaterialPreparation.id,
        )
        .where(LegalPreparedPartVersion.legal_version_id == approved.c.version_id)
        .order_by(LegalMaterialPreparation.revision.desc(), LegalMaterialPreparation.id)
        .limit(1)
        .scalar_subquery()
    )
    classified = review_group_expression(approved.c.document_title, literal("LEGAL_COPY"))
    fallback = case(
        (approved.c.official_number.in_(["323-ФЗ", "1051н", "2300-1", "659"]), "healthcare"),
        (approved.c.official_number == "152-ФЗ", "privacy"),
        (classified == "other", "general"),
        else_=classified,
    )
    statement = (
        select(approved)
        .where(func.coalesce(prepared_group, fallback) == group)
        .order_by(
            approved.c.document_title,
            approved.c.official_number.nulls_last(),
            approved.c.document_id,
            approved.c.version_id,
        )
        .offset((page - 1) * 10)
        .limit(11)
    )
    rows = (await session.execute(statement, {"as_of_date": as_of_date})).mappings().all()
    return [ApprovedLegalLibraryDocument(**dict(row)) for row in rows[:10]], len(rows) > 10
