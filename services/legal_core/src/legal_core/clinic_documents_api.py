"""Tenant-scoped API for clinic-owned documents with explicit review gates."""

import asyncio
from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
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
from legal_core.clinic_document_parser import MAX_UPLOAD_BYTES, parse_clinic_document_upload
from legal_core.clinic_document_store import (
    RawClinicDocumentStore,
    minio_store_from_environment,
)
from legal_core.clinic_documents import PreparedClinicDocumentText, prepare_clinic_document_text


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


async def _existing_version(
    session: AsyncSession,
    *,
    clinic_id: UUID,
    document_id: UUID,
    raw_sha256: str,
) -> Any:
    return (
        await session.execute(
            text(
                "SELECT id, document_id, version_no, source_filename, mime_type, raw_sha256, "
                "normalized_text_sha256, valid_from, valid_to, created_at "
                "FROM clinic_document_versions "
                "WHERE clinic_id=:clinic_id AND document_id=:document_id "
                "AND raw_sha256=:raw_sha256"
            ),
            {
                "clinic_id": clinic_id,
                "document_id": document_id,
                "raw_sha256": raw_sha256,
            },
        )
    ).mappings().first()


def _validate_existing_version_replay(
    existing: Any,
    *,
    source_filename: str,
    mime_type: str,
    normalized_text_sha256: str,
    valid_from: date | None,
    valid_to: date | None,
) -> None:
    if existing["normalized_text_sha256"] != normalized_text_sha256:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="CLINIC_DOCUMENT_REPROCESSING_CONFLICT",
            message="The same raw clinic document produced different normalized content",
        )
    metadata_matches = (
        existing["source_filename"] == source_filename
        and existing["mime_type"] == mime_type
        and existing["valid_from"] == valid_from
        and existing["valid_to"] == valid_to
    )
    if not metadata_matches:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="CLINIC_DOCUMENT_VERSION_METADATA_CONFLICT",
            message="The same raw clinic document already exists with different version metadata",
        )


async def _fragment_count(
    session: AsyncSession,
    *,
    clinic_id: UUID,
    version_id: UUID,
) -> int:
    return int(
        (
            await session.execute(
                text(
                    "SELECT count(*) FROM clinic_document_fragments "
                    "WHERE clinic_id=:clinic_id AND version_id=:version_id"
                ),
                {"clinic_id": clinic_id, "version_id": version_id},
            )
        ).scalar_one()
    )


async def _next_version_no(
    session: AsyncSession,
    *,
    clinic_id: UUID,
    document_id: UUID,
) -> int:
    return int(
        (
            await session.execute(
                text(
                    "SELECT COALESCE(max(version_no), 0) + 1 "
                    "FROM clinic_document_versions "
                    "WHERE clinic_id=:clinic_id AND document_id=:document_id"
                ),
                {"clinic_id": clinic_id, "document_id": document_id},
            )
        ).scalar_one()
    )


async def _insert_version_and_fragments(
    session: AsyncSession,
    *,
    clinic_id: UUID,
    document_id: UUID,
    membership_id: UUID,
    version_no: int,
    source_filename: str,
    mime_type: str,
    raw_object_key: str,
    raw_sha256: str,
    prepared: PreparedClinicDocumentText,
    valid_from: date | None,
    valid_to: date | None,
) -> Any:
    version = (
        await session.execute(
            text(
                "INSERT INTO clinic_document_versions "
                "(clinic_id, document_id, version_no, source_filename, mime_type, "
                "raw_object_key, raw_sha256, normalized_text, normalized_text_sha256, "
                "valid_from, valid_to, created_by_membership_id) VALUES "
                "(:clinic_id, :document_id, :version_no, :source_filename, :mime_type, "
                ":raw_object_key, :raw_sha256, :normalized_text, :normalized_text_sha256, "
                ":valid_from, :valid_to, :membership_id) "
                "RETURNING id, document_id, version_no, source_filename, mime_type, "
                "raw_sha256, normalized_text_sha256, valid_from, valid_to, created_at"
            ),
            {
                "clinic_id": clinic_id,
                "document_id": document_id,
                "version_no": version_no,
                "source_filename": source_filename,
                "mime_type": mime_type,
                "raw_object_key": raw_object_key,
                "raw_sha256": raw_sha256,
                "normalized_text": prepared.normalized_text,
                "normalized_text_sha256": prepared.content_sha256,
                "valid_from": valid_from,
                "valid_to": valid_to,
                "membership_id": membership_id,
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
                "clinic_id": clinic_id,
                "version_id": version_id,
                "ordinal": fragment.ordinal,
                "structural_path": fragment.structural_path,
                "fragment_text": fragment.fragment_text,
                "text_sha256": fragment.text_sha256,
            }
            for fragment in prepared.fragments
        ],
    )
    return version


async def _read_bounded_upload(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared:
        try:
            declared_size = int(declared)
        except ValueError as exc:
            raise ApiError(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="CLINIC_DOCUMENT_CONTENT_LENGTH_INVALID",
                message="Clinic document Content-Length is invalid",
            ) from exc
        if declared_size > MAX_UPLOAD_BYTES:
            raise ApiError(
                status_code=413,
                code="CLINIC_DOCUMENT_FILE_TOO_LARGE",
                message="Clinic document upload exceeds the supported size",
            )

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_UPLOAD_BYTES:
            raise ApiError(
                status_code=413,
                code="CLINIC_DOCUMENT_FILE_TOO_LARGE",
                message="Clinic document upload exceeds the supported size",
            )
        body.extend(chunk)
    return bytes(body)


def _validate_version_dates(valid_from: date | None, valid_to: date | None) -> None:
    if valid_from is not None and valid_to is not None and valid_to <= valid_from:
        raise ApiError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="CLINIC_DOCUMENT_DATES_INVALID",
            message="valid_to must be later than valid_from",
        )


def create_clinic_documents_router(
    session_factory: async_sessionmaker[AsyncSession],
    raw_store: RawClinicDocumentStore | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/v1/clinic-documents", tags=["clinic-documents"])
    resolved_raw_store = raw_store

    async def get_session() -> Any:
        async with session_factory() as session:
            yield session

    def get_raw_store() -> RawClinicDocumentStore:
        nonlocal resolved_raw_store
        if resolved_raw_store is None:
            try:
                resolved_raw_store = minio_store_from_environment()
            except (RuntimeError, ValueError) as exc:
                raise ApiError(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    code="CLINIC_DOCUMENT_STORAGE_NOT_CONFIGURED",
                    message="Clinic document file storage is not configured",
                ) from exc
        return resolved_raw_store

    Session = Annotated[AsyncSession, Depends(get_session)]
    SearchQuery = Annotated[str, Query(min_length=2, max_length=500)]
    SearchLimit = Annotated[int, Query(ge=1, le=50)]
    AsOfDate = Annotated[date | None, Query(alias="as_of_date")]
    SourceFilename = Annotated[
        str,
        Header(alias="X-Source-Filename", min_length=1, max_length=255),
    ]
    ValidFrom = Annotated[date | None, Query(alias="valid_from")]
    ValidTo = Annotated[date | None, Query(alias="valid_to")]

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

        existing = await _existing_version(
            session,
            clinic_id=actor.clinic_id,
            document_id=document_id,
            raw_sha256=prepared.content_sha256,
        )
        if existing is not None:
            _validate_existing_version_replay(
                existing,
                source_filename=payload.source_filename,
                mime_type="text/plain",
                normalized_text_sha256=prepared.content_sha256,
                valid_from=payload.valid_from,
                valid_to=payload.valid_to,
            )
            response.status_code = status.HTTP_200_OK
            return _version_response(
                existing,
                await _fragment_count(
                    session,
                    clinic_id=actor.clinic_id,
                    version_id=existing["id"],
                ),
            )

        version_no = await _next_version_no(
            session,
            clinic_id=actor.clinic_id,
            document_id=document_id,
        )
        raw_object_key = (
            f"inline-text/{actor.clinic_id}/{document_id}/v{version_no}/"
            f"{prepared.content_sha256}.txt"
        )
        version = await _insert_version_and_fragments(
            session,
            clinic_id=actor.clinic_id,
            document_id=document_id,
            membership_id=actor.membership_id,
            version_no=version_no,
            source_filename=payload.source_filename,
            mime_type="text/plain",
            raw_object_key=raw_object_key,
            raw_sha256=prepared.content_sha256,
            prepared=prepared,
            valid_from=payload.valid_from,
            valid_to=payload.valid_to,
        )
        await session.commit()
        return _version_response(version, len(prepared.fragments))

    @router.post(
        "/{document_id}/file-versions",
        response_model=ClinicDocumentVersionResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_file_version(
        document_id: UUID,
        request: Request,
        response: Response,
        source_filename: SourceFilename,
        telegram_user_id: TelegramUserId,
        session: Session,
        valid_from: ValidFrom = None,
        valid_to: ValidTo = None,
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
        _validate_version_dates(valid_from, valid_to)
        raw = await _read_bounded_upload(request)
        content_type = request.headers.get("content-type", "")
        try:
            parsed = await asyncio.to_thread(
                parse_clinic_document_upload,
                raw,
                source_filename=source_filename,
                content_type=content_type,
            )
            prepared = prepare_clinic_document_text(parsed.normalized_text)
        except ValueError as exc:
            raise ApiError(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="CLINIC_DOCUMENT_FILE_INVALID",
                message=str(exc),
            ) from exc
        except RuntimeError as exc:
            raise ApiError(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="CLINIC_DOCUMENT_PARSER_UNAVAILABLE",
                message="Clinic document parser is unavailable",
            ) from exc

        existing = await _existing_version(
            session,
            clinic_id=actor.clinic_id,
            document_id=document_id,
            raw_sha256=parsed.raw_sha256,
        )
        if existing is not None:
            _validate_existing_version_replay(
                existing,
                source_filename=parsed.source_filename,
                mime_type=parsed.mime_type,
                normalized_text_sha256=prepared.content_sha256,
                valid_from=valid_from,
                valid_to=valid_to,
            )
            response.status_code = status.HTTP_200_OK
            return _version_response(
                existing,
                await _fragment_count(
                    session,
                    clinic_id=actor.clinic_id,
                    version_id=existing["id"],
                ),
            )

        version_no = await _next_version_no(
            session,
            clinic_id=actor.clinic_id,
            document_id=document_id,
        )
        try:
            raw_object_key = await get_raw_store().put(
                clinic_id=actor.clinic_id,
                document_id=document_id,
                raw_sha256=parsed.raw_sha256,
                content=raw,
                content_type=parsed.mime_type,
            )
        except (RuntimeError, ValueError) as exc:
            raise ApiError(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="CLINIC_DOCUMENT_STORAGE_UNAVAILABLE",
                message="Clinic document file storage is unavailable",
            ) from exc

        version = await _insert_version_and_fragments(
            session,
            clinic_id=actor.clinic_id,
            document_id=document_id,
            membership_id=actor.membership_id,
            version_no=version_no,
            source_filename=parsed.source_filename,
            mime_type=parsed.mime_type,
            raw_object_key=raw_object_key,
            raw_sha256=parsed.raw_sha256,
            prepared=prepared,
            valid_from=valid_from,
            valid_to=valid_to,
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
                    "AND (CAST(:as_of_date AS date) IS NULL OR "
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
