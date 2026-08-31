"""Tenant-scoped API for clinic-owned documents with explicit review gates."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.case_api import ApiError, TelegramUserId, resolve_actor
from legal_core.clinic_document_contracts import (
    ClinicDocumentApprovalRequest,
    ClinicDocumentApprovalResponse,
    ClinicDocumentFragmentResponse,
    ClinicDocumentFragmentSearchResponse,
    ClinicDocumentListResponse,
    ClinicDocumentResponse,
    ClinicDocumentVersionResponse,
    CreateClinicDocumentRequest,
    CreateClinicDocumentTextVersionRequest,
)
from legal_core.clinic_documents import prepare_clinic_document_text


async def _tenant_document_row(
    session: AsyncSession,
    *,
    clinic_id: UUID,
    document_id: UUID,
    for_update: bool = False,
) -> Any:
    suffix = " FOR UPDATE" if for_update else ""
    result = await session.execute(
        text(
            "SELECT id, document_key, document_type, title, created_at "
            "FROM clinic_documents WHERE clinic_id=:clinic_id AND id=:document_id" + suffix
        ),
        {"clinic_id": clinic_id, "document_id": document_id},
    )
    return result.mappings().first()


def _document_response(row: Any) -> ClinicDocumentResponse:
    return ClinicDocumentResponse.model_validate(dict(row))


def _version_response(row: Any, fragment_count: int) -> ClinicDocumentVersionResponse:
    values = dict(row)
    values["fragment_count"] = fragment_count
    return ClinicDocumentVersionResponse.model_validate(values)


def create_clinic_documents_router(
    session_factory: async_sessionmaker[AsyncSession],
) -> APIRouter:
    router = APIRouter(prefix="/v1/clinic-documents", tags=["clinic-documents"])

    async def get_session() -> Any:
        async with session_factory() as session:
            yield session

    Session = Annotated[AsyncSession, Depends(get_session)]
    SearchQuery = Annotated[str, Query(min_length=2, max_length=500)]
    SearchLimit = Annotated[int, Query(ge=1, le=50)]
    AsOfDate = Annotated[date | None, Query(alias="as_of_date")]

    @router.get("", response_model=ClinicDocumentListResponse)
    async def list_documents(
        telegram_user_id: TelegramUserId,
        session: Session,
    ) -> ClinicDocumentListResponse:
        actor = await resolve_actor(session, telegram_user_id)
        rows = (
            await session.execute(
                text(
                    "SELECT id, document_key, document_type, title, created_at "
                    "FROM clinic_documents WHERE clinic_id=:clinic_id "
                    "ORDER BY document_type, document_key, id"
                ),
                {"clinic_id": actor.clinic_id},
            )
        ).mappings().all()
        return ClinicDocumentListResponse(items=[_document_response(row) for row in rows])

    @router.post(
        "",
        response_model=ClinicDocumentResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_document(
        payload: CreateClinicDocumentRequest,
        response: Response,
        telegram_user_id: TelegramUserId,
        session: Session,
    ) -> ClinicDocumentResponse:
        actor = await resolve_actor(session, telegram_user_id)
        inserted = (
            await session.execute(
                text(
                    "INSERT INTO clinic_documents "
                    "(clinic_id, document_key, document_type, title, created_by_membership_id) "
                    "VALUES (:clinic_id, :document_key, :document_type, :title, :membership_id) "
                    "ON CONFLICT (clinic_id, document_key) DO NOTHING "
                    "RETURNING id, document_key, document_type, title, created_at"
                ),
                {
                    "clinic_id": actor.clinic_id,
                    "document_key": payload.document_key,
                    "document_type": payload.document_type,
                    "title": payload.title,
                    "membership_id": actor.membership_id,
                },
            )
        ).mappings().first()
        if inserted is not None:
            await session.commit()
            return _document_response(inserted)

        existing = (
            await session.execute(
                text(
                    "SELECT id, document_key, document_type, title, created_at "
                    "FROM clinic_documents "
                    "WHERE clinic_id=:clinic_id AND document_key=:document_key"
                ),
                {"clinic_id": actor.clinic_id, "document_key": payload.document_key},
            )
        ).mappings().one()
        if (
            existing["document_type"] != payload.document_type
            or existing["title"] != payload.title
        ):
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="CLINIC_DOCUMENT_KEY_CONFLICT",
                message="Clinic document key already exists with different metadata",
            )
        response.status_code = status.HTTP_200_OK
        return _document_response(existing)

    @router.post(
        "/{document_id}/text-versions",
        response_model=ClinicDocumentVersionResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_text_version(
        document_id: UUID,
        payload: CreateClinicDocumentTextVersionRequest,
        response: Response,
        telegram_user_id: TelegramUserId,
        session: Session,
    ) -> ClinicDocumentVersionResponse:
        actor = await resolve_actor(session, telegram_user_id)
        document = await _tenant_document_row(
            session,
            clinic_id=actor.clinic_id,
            document_id=document_id,
            for_update=True,
        )
        if document is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="CLINIC_DOCUMENT_NOT_FOUND",
                message="Clinic document not found",
            )
        try:
            prepared = prepare_clinic_document_text(payload.normalized_text)
        except ValueError as exc:
            raise ApiError(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="CLINIC_DOCUMENT_TEXT_INVALID",
                message=str(exc),
            ) from exc

        existing = (
            await session.execute(
                text(
                    "SELECT id, document_id, version_no, source_filename, mime_type, raw_sha256, "
                    "normalized_text_sha256, valid_from, valid_to, created_at "
                    "FROM clinic_document_versions "
                    "WHERE clinic_id=:clinic_id AND document_id=:document_id "
                    "AND raw_sha256=:raw_sha256"
                ),
                {
                    "clinic_id": actor.clinic_id,
                    "document_id": document_id,
                    "raw_sha256": prepared.content_sha256,
                },
            )
        ).mappings().first()
        if existing is not None:
            fragment_count = int(
                (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM clinic_document_fragments "
                            "WHERE clinic_id=:clinic_id AND version_id=:version_id"
                        ),
                        {"clinic_id": actor.clinic_id, "version_id": existing["id"]},
                    )
                ).scalar_one()
            )
            response.status_code = status.HTTP_200_OK
            return _version_response(existing, fragment_count)

        version_no = int(
            (
                await session.execute(
                    text(
                        "SELECT COALESCE(max(version_no), 0) + 1 "
                        "FROM clinic_document_versions "
                        "WHERE clinic_id=:clinic_id AND document_id=:document_id"
                    ),
                    {"clinic_id": actor.clinic_id, "document_id": document_id},
                )
            ).scalar_one()
        )
        raw_object_key = (
            f"inline-text/{actor.clinic_id}/{document_id}/v{version_no}/"
            f"{prepared.content_sha256}.txt"
        )
        version = (
            await session.execute(
                text(
                    "INSERT INTO clinic_document_versions "
                    "(clinic_id, document_id, version_no, source_filename, mime_type, "
                    "raw_object_key, raw_sha256, normalized_text, normalized_text_sha256, "
                    "valid_from, valid_to, created_by_membership_id) VALUES "
                    "(:clinic_id, :document_id, :version_no, :source_filename, 'text/plain', "
                    ":raw_object_key, :raw_sha256, :normalized_text, :normalized_text_sha256, "
                    ":valid_from, :valid_to, :membership_id) "
                    "RETURNING id, document_id, version_no, source_filename, mime_type, "
                    "raw_sha256, normalized_text_sha256, valid_from, valid_to, created_at"
                ),
                {
                    "clinic_id": actor.clinic_id,
                    "document_id": document_id,
                    "version_no": version_no,
                    "source_filename": payload.source_filename,
                    "raw_object_key": raw_object_key,
                    "raw_sha256": prepared.content_sha256,
                    "normalized_text": prepared.normalized_text,
                    "normalized_text_sha256": prepared.content_sha256,
                    "valid_from": payload.valid_from,
                    "valid_to": payload.valid_to,
                    "membership_id": actor.membership_id,
                },
            )
        ).mappings().one()
        version_id = version["id"]
        await session.execute(
            text(
                "INSERT INTO clinic_document_fragments "
                "(clinic_id, version_id, ordinal, structural_path, fragment_text, text_sha256) "
                "VALUES (:clinic_id, :version_id, :ordinal, :structural_path, "
                ":fragment_text, :text_sha256)"
            ),
            [
                {
                    "clinic_id": actor.clinic_id,
                    "version_id": version_id,
                    "ordinal": fragment.ordinal,
                    "structural_path": fragment.structural_path,
                    "fragment_text": fragment.fragment_text,
                    "text_sha256": fragment.text_sha256,
                }
                for fragment in prepared.fragments
            ],
        )
        await session.commit()
        return _version_response(version, len(prepared.fragments))

    @router.post(
        "/versions/{version_id}/approval-events",
        response_model=ClinicDocumentApprovalResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def review_version(
        version_id: UUID,
        payload: ClinicDocumentApprovalRequest,
        response: Response,
        telegram_user_id: TelegramUserId,
        session: Session,
    ) -> ClinicDocumentApprovalResponse:
        actor = await resolve_actor(session, telegram_user_id)
        version = (
            await session.execute(
                text(
                    "SELECT id, raw_sha256, normalized_text_sha256 "
                    "FROM clinic_document_versions "
                    "WHERE clinic_id=:clinic_id AND id=:version_id"
                ),
                {"clinic_id": actor.clinic_id, "version_id": version_id},
            )
        ).mappings().first()
        if version is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="CLINIC_DOCUMENT_VERSION_NOT_FOUND",
                message="Clinic document version not found",
            )

        latest = (
            await session.execute(
                text(
                    "SELECT id, version_id, decision, reason_code, created_at "
                    "FROM clinic_document_approval_events "
                    "WHERE clinic_id=:clinic_id AND version_id=:version_id "
                    "ORDER BY created_at DESC, id DESC LIMIT 1"
                ),
                {"clinic_id": actor.clinic_id, "version_id": version_id},
            )
        ).mappings().first()
        if (
            latest is not None
            and latest["decision"] == payload.decision
            and latest["reason_code"] == payload.reason_code
        ):
            response.status_code = status.HTTP_200_OK
            return ClinicDocumentApprovalResponse.model_validate(dict(latest))

        event = (
            await session.execute(
                text(
                    "INSERT INTO clinic_document_approval_events "
                    "(clinic_id, version_id, actor_membership_id, decision, reason_code, "
                    "expected_raw_sha256, expected_normalized_text_sha256) VALUES "
                    "(:clinic_id, :version_id, :membership_id, :decision, :reason_code, "
                    ":raw_sha256, :text_sha256) "
                    "RETURNING id, version_id, decision, reason_code, created_at"
                ),
                {
                    "clinic_id": actor.clinic_id,
                    "version_id": version_id,
                    "membership_id": actor.membership_id,
                    "decision": payload.decision,
                    "reason_code": payload.reason_code,
                    "raw_sha256": version["raw_sha256"],
                    "text_sha256": version["normalized_text_sha256"],
                },
            )
        ).mappings().one()
        await session.commit()
        return ClinicDocumentApprovalResponse.model_validate(dict(event))

    @router.get("/fragments", response_model=ClinicDocumentFragmentSearchResponse)
    async def search_fragments(
        query: SearchQuery,
        telegram_user_id: TelegramUserId,
        session: Session,
        as_of_date: AsOfDate = None,
        limit: SearchLimit = 20,
    ) -> ClinicDocumentFragmentSearchResponse:
        actor = await resolve_actor(session, telegram_user_id)
        normalized_query = query.strip()
        if len(normalized_query) < 2:
            raise ApiError(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="CLINIC_DOCUMENT_QUERY_INVALID",
                message="Clinic document query must contain at least two non-space characters",
            )
        rows = (
            await session.execute(
                text(
                    "SELECT fragment_id, version_id, document_id, document_key, document_type, "
                    "document_title, version_no, valid_from, valid_to, ordinal, structural_path, "
                    "fragment_text, text_sha256 "
                    "FROM approved_clinic_document_fragments "
                    "WHERE clinic_id=:clinic_id "
                    "AND (position(lower(:query) in lower(fragment_text)) > 0 "
                    "OR position(lower(:query) in lower(document_title)) > 0 "
                    "OR position(lower(:query) in lower(document_key)) > 0) "
                    "AND (:as_of_date IS NULL OR "
                    "((valid_from IS NULL OR valid_from <= CAST(:as_of_date AS date)) "
                    "AND (valid_to IS NULL OR valid_to > CAST(:as_of_date AS date)))) "
                    "ORDER BY document_key, version_no DESC, ordinal, fragment_id "
                    "LIMIT :limit"
                ),
                {
                    "clinic_id": actor.clinic_id,
                    "query": normalized_query,
                    "as_of_date": as_of_date,
                    "limit": limit,
                },
            )
        ).mappings().all()
        return ClinicDocumentFragmentSearchResponse(
            items=[ClinicDocumentFragmentResponse.model_validate(dict(row)) for row in rows]
        )

    return router
