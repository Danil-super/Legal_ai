"""Metadata-only unified editor view; group membership never grants approval."""

from datetime import date

from sqlalchemy import Uuid, case, cast, exists, func, literal, null, or_, select, union_all
from sqlalchemy.sql.selectable import Subquery

from legal_core.models import LegalDocument, LegalReviewMaterial, LegalVersion
from legal_core.review_material_groups import GROUP_TITLES, review_group_expression

EDITOR_GROUP_TITLES = {key: title for key, title in GROUP_TITLES.items() if key != "other"}


def editor_group_items() -> Subquery:
    """Latest unexpired versions plus originals not already represented by exact bytes."""
    ranked = select(
        LegalVersion.id,
        func.row_number()
        .over(partition_by=LegalVersion.document_id, order_by=LegalVersion.version_no.desc())
        .label("rank"),
    ).subquery()
    versions = (
        select(
            LegalVersion.id,
            LegalVersion.raw_sha256,
            LegalVersion.approval_state,
            LegalDocument.title,
            LegalDocument.official_number,
        )
        .join(LegalDocument, LegalDocument.id == LegalVersion.document_id)
        .join(ranked, ranked.c.id == LegalVersion.id)
        .where(
            ranked.c.rank == 1,
            or_(LegalVersion.effective_to.is_(None), LegalVersion.effective_to > date.today()),
        )
        .cte("editor_current_versions")
    )
    material_group = review_group_expression(LegalReviewMaterial.title, LegalReviewMaterial.kind)
    version_group = review_group_expression(versions.c.title, literal("LEGAL_COPY"))
    # Existing canonical numbers supplement shortened historical document titles.
    version_group = case(
        (versions.c.official_number.in_(["323-ФЗ", "1051н", "2300-1", "659"]), "healthcare"),
        (versions.c.official_number == "152-ФЗ", "privacy"),
        else_=version_group,
    )
    originals = select(
        LegalReviewMaterial.id.label("material_id"),
        cast(null(), Uuid).label("version_id"),
        LegalReviewMaterial.title,
        LegalReviewMaterial.kind,
        LegalReviewMaterial.review_state.label("review_state"),
        case((material_group == "other", "general"), else_=material_group).label("group_key"),
        LegalReviewMaterial.raw_sha256,
    ).where(
        or_(
            LegalReviewMaterial.kind == "CLINICAL_REFERENCE",
            ~exists(
                select(versions.c.id).where(versions.c.raw_sha256 == LegalReviewMaterial.raw_sha256)
            ),
        )
    )
    prepared = select(
        cast(null(), Uuid).label("material_id"),
        versions.c.id.label("version_id"),
        versions.c.title,
        literal("LEGAL_COPY").label("kind"),
        versions.c.approval_state.label("review_state"),
        case((version_group == "other", "general"), else_=version_group).label("group_key"),
        versions.c.raw_sha256,
    )
    return union_all(originals, prepared).subquery("editor_group_items")
