"""Metadata-only unified editor view; group membership never grants approval."""

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from sqlalchemy import String, Uuid, case, cast, exists, func, literal, null, or_, select, union_all
from sqlalchemy.sql.selectable import Subquery

from legal_core.models import (
    LegalDocument,
    LegalMaterialPreparation,
    LegalPreparedPartVersion,
    LegalReferenceReviewEvent,
    LegalReviewMaterial,
    LegalVersion,
)
from legal_core.review_material_groups import GROUP_TITLES, review_group_expression

EDITOR_GROUP_TITLES = {key: title for key, title in GROUP_TITLES.items() if key != "other"}


def current_preparations() -> Subquery:
    ranked = select(
        LegalMaterialPreparation.id,
        LegalMaterialPreparation.material_id,
        LegalMaterialPreparation.title,
        LegalMaterialPreparation.kind,
        LegalMaterialPreparation.group_key,
        LegalMaterialPreparation.raw_sha256,
        LegalMaterialPreparation.preparation_sha256,
        LegalMaterialPreparation.metadata_json,
        func.row_number()
        .over(
            partition_by=LegalMaterialPreparation.material_id,
            order_by=LegalMaterialPreparation.revision.desc(),
        )
        .label("rank"),
    ).subquery()
    return select(ranked).where(ranked.c.rank == 1).subquery("current_preparations")


def editor_group_items() -> Subquery:
    """One current version per act, retaining a source card for every unbound part."""
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
    preparations = current_preparations()
    expected_parts = case(
        (preparations.c.kind == "NORMATIVE",
         func.coalesce(func.jsonb_array_length(preparations.c.metadata_json["parts"]), 0)),
        else_=0,
    )
    bindings = (
        select(
            LegalPreparedPartVersion.material_id,
            LegalPreparedPartVersion.preparation_id,
            LegalPreparedPartVersion.legal_version_id,
            LegalPreparedPartVersion.part_key,
        )
        .join(preparations, preparations.c.id == LegalPreparedPartVersion.preparation_id)
        .subquery("current_part_bindings")
    )
    current_bound_counts = (
        select(
            bindings.c.preparation_id,
            func.count(bindings.c.part_key).label("linked_parts"),
        )
        .join(versions, versions.c.id == bindings.c.legal_version_id)
        .group_by(bindings.c.preparation_id)
        .subquery("current_bound_counts")
    )
    linked_parts = func.coalesce(current_bound_counts.c.linked_parts, 0)
    originals = (
        select(
            LegalReviewMaterial.id.label("material_id"),
            cast(null(), Uuid).label("version_id"),
            func.coalesce(preparations.c.title, LegalReviewMaterial.title).label("title"),
            LegalReviewMaterial.kind,
            LegalReviewMaterial.review_state.label("review_state"),
            func.coalesce(
                preparations.c.group_key,
                case((material_group == "other", "general"), else_=material_group),
            ).label("group_key"),
            LegalReviewMaterial.raw_sha256,
            preparations.c.id.label("preparation_id"),
            preparations.c.kind.label("preparation_kind"),
            preparations.c.preparation_sha256,
            expected_parts.label("expected_parts"),
            linked_parts.label("linked_parts"),
            cast(null(), String(120)).label("part_key"),
            literal("ORIGINAL").label("link_state"),
            exists(select(LegalReferenceReviewEvent.id).where(
                LegalReferenceReviewEvent.preparation_id == preparations.c.id
            )).label("reference_reviewed"),
        )
        .outerjoin(preparations, preparations.c.material_id == LegalReviewMaterial.id)
        .outerjoin(current_bound_counts,
                   current_bound_counts.c.preparation_id == preparations.c.id)
        .where(
            or_(
                preparations.c.kind != "NORMATIVE",
                preparations.c.id.is_(None),
                expected_parts == 0,
                linked_parts < expected_parts,
            )
        )
    )
    prepared = (select(
        bindings.c.material_id.label("material_id"),
        versions.c.id.label("version_id"),
        versions.c.title,
        literal("LEGAL_COPY").label("kind"),
        versions.c.approval_state.label("review_state"),
        func.coalesce(
            preparations.c.group_key,
            case((version_group == "other", "general"), else_=version_group),
        ).label("group_key"),
        versions.c.raw_sha256,
        preparations.c.id.label("preparation_id"),
        preparations.c.kind.label("preparation_kind"),
        preparations.c.preparation_sha256,
        expected_parts.label("expected_parts"),
        linked_parts.label("linked_parts"),
        bindings.c.part_key,
        case((bindings.c.material_id.is_not(None), "EXACT_ORIGINAL"),
             else_="UNLINKED_VERSION").label("link_state"),
        literal(False).label("reference_reviewed"),
    )
        .outerjoin(bindings, bindings.c.legal_version_id == versions.c.id)
        .outerjoin(preparations, preparations.c.id == bindings.c.preparation_id)
        .outerjoin(current_bound_counts,
                   current_bound_counts.c.preparation_id == preparations.c.id)
    )
    return union_all(originals, prepared).subquery("editor_group_items")


def group_progress(rows: Sequence[Mapping[Any, Any]]) -> dict[str, int | bool]:
    """A group is complete only when every visible current obligation is resolved."""
    legal_versions: dict[Any, str] = {}
    references = reviewed_references = missing_parts = unprepared = unlinked = 0
    for row in rows:
        if row["version_id"] is not None:
            legal_versions[row["version_id"]] = row["review_state"]
            if row["link_state"] == "UNLINKED_VERSION":
                unlinked += 1
            continue
        if row["kind"] == "CLINICAL_REFERENCE" or row["preparation_kind"] == "REFERENCE_FORM":
            references += 1
            reviewed_references += bool(row["reference_reviewed"])
            continue
        expected = row["expected_parts"] or 0
        if expected:
            missing_parts += max(0, expected - (row["linked_parts"] or 0))
        else:
            unprepared += 1
    approved = sum(state == "APPROVED" for state in legal_versions.values())
    return {
        "normativeVersions": len(legal_versions),
        "approvedNormativeVersions": approved,
        "referenceMaterials": references,
        "reviewedReferenceMaterials": reviewed_references,
        "missingParts": missing_parts,
        "unpreparedOriginals": unprepared,
        "unlinkedVersions": unlinked,
        "complete": bool(rows) and not (missing_parts or unprepared or unlinked)
        and approved == len(legal_versions) and reviewed_references == references,
    }
