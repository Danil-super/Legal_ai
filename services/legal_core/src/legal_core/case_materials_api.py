"""Protected, metadata-only access to short-lived anonymised case materials.

This API deliberately has no route that sends material text to retrieval, a model,
or the clinic document library.  It validates a bounded payload locally, stores
only raw bytes in private object storage, and exposes generic display labels.
"""

import asyncio
import hashlib
from contextlib import suppress
from datetime import UTC, datetime
from typing import Annotated, Any, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.case_api import (
    DRAFT_RETENTION,
    ActorContext,
    ApiError,
    IdempotencyKey,
    TelegramUserId,
    _actor_draft,
    _audit,
    _canonical_hash,
    _finish_idempotency,
    _idempotency_replay,
    _new_idempotency_record,
    _require_case_intake_actor,
    _tenant_case,
    resolve_actor,
)
from legal_core.case_material_contracts import (
    CaseMaterialDeleteResponse,
    CaseMaterialListResponse,
    CaseMaterialMimeType,
    CaseMaterialMutationResponse,
    CaseMaterialResponse,
)
from legal_core.clinic_document_parser import MAX_UPLOAD_BYTES, parse_clinic_document_upload
from legal_core.clinic_document_store import RawCaseMaterialStore, minio_store_from_environment
from legal_core.models import CaseMaterial, TelegramIntakeDraft
from legal_core.pseudonymization import pseudonymize_text

MAX_CASE_MATERIALS_PER_DRAFT = 10


def _material_response(material: CaseMaterial) -> CaseMaterialResponse:
    return CaseMaterialResponse(
        id=material.id,
        displayName=material.display_name,
        mimeType=cast(CaseMaterialMimeType, material.mime_type),
        sizeBytes=material.raw_size_bytes,
        createdAt=material.created_at,
    )


def _material_filename(material: CaseMaterial) -> str:
    extension = {
        "text/plain": "txt",
        "application/pdf": "pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    }.get(material.mime_type)
    if extension is None:  # Database data must never control response headers.
        raise ApiError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="CASE_MATERIAL_METADATA_INVALID",
            message="Case material metadata is unavailable",
        )
    return f"material-{material.id}.{extension}"


def _material_is_expired(material: CaseMaterial) -> bool:
    return material.expires_at <= datetime.now(UTC)


def _require_live_material(material: CaseMaterial) -> None:
    if _material_is_expired(material):
        raise ApiError(
            status_code=status.HTTP_410_GONE,
            code="CASE_MATERIAL_EXPIRED",
            message="Case material is no longer available under the retention policy",
        )


async def _read_bounded_material_upload(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared:
        try:
            declared_size = int(declared)
        except ValueError as exc:
            raise ApiError(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="CASE_MATERIAL_CONTENT_LENGTH_INVALID",
                message="Case material Content-Length is invalid",
            ) from exc
        if declared_size > MAX_UPLOAD_BYTES:
            raise ApiError(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                code="CASE_MATERIAL_FILE_TOO_LARGE",
                message="Case material exceeds the supported size",
            )

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_UPLOAD_BYTES:
            raise ApiError(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                code="CASE_MATERIAL_FILE_TOO_LARGE",
                message="Case material exceeds the supported size",
            )
        body.extend(chunk)
    return bytes(body)


async def _draft_material(
    session: AsyncSession,
    *,
    actor: ActorContext,
    draft_id: UUID,
    material_id: UUID,
    for_update: bool = False,
) -> CaseMaterial:
    statement = select(CaseMaterial).where(
        CaseMaterial.id == material_id,
        CaseMaterial.clinic_id == actor.clinic_id,
        CaseMaterial.draft_id == draft_id,
        CaseMaterial.uploader_membership_id == actor.membership_id,
    )
    if for_update:
        statement = statement.with_for_update()
    material = await session.scalar(statement)
    if material is None:
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="CASE_MATERIAL_NOT_FOUND",
            message="Case material not found",
        )
    _require_live_material(material)
    return material


async def _case_material(
    session: AsyncSession,
    *,
    actor: ActorContext,
    case_id: UUID,
    material_id: UUID,
) -> CaseMaterial:
    # Checking the case first gives a uniform tenant/content-retention boundary.
    await _tenant_case(session, actor, case_id)
    material = await session.scalar(
        select(CaseMaterial).where(
            CaseMaterial.id == material_id,
            CaseMaterial.clinic_id == actor.clinic_id,
            CaseMaterial.case_id == case_id,
            # A clinic role alone never reveals a case attachment.  A future
            # lawyer-review grant must be explicit and separately audited.
            CaseMaterial.uploader_membership_id == actor.membership_id,
        )
    )
    if material is None:
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="CASE_MATERIAL_NOT_FOUND",
            message="Case material not found",
        )
    _require_live_material(material)
    return material


async def _set_draft_material_state(
    session: AsyncSession,
    draft: TelegramIntakeDraft,
    *,
    attached: bool,
) -> None:
    draft_data = dict(draft.draft_json)
    if draft_data.get("intakeVersion") == 2:
        draft_data["caseMaterialsStatus"] = "ATTACHED" if attached else "NOT_ATTACHED"
        draft.draft_json = draft_data
    draft.revision += 1
    draft.updated_at = datetime.now(UTC)
    # An attachment is still part of the active draft and follows the same
    # approved 30-day lifecycle; it must not outlive a renewed draft.
    draft.purge_after = draft.updated_at + DRAFT_RETENTION
    await session.execute(
        update(CaseMaterial)
        .where(
            CaseMaterial.clinic_id == draft.clinic_id,
            CaseMaterial.draft_id == draft.id,
        )
        .values(expires_at=draft.purge_after)
    )


def create_case_materials_router(
    session_factory: async_sessionmaker[AsyncSession],
    raw_store: RawCaseMaterialStore | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["case-materials"])
    resolved_raw_store = raw_store

    async def get_session() -> Any:
        async with session_factory() as session:
            yield session

    def get_raw_store() -> RawCaseMaterialStore:
        nonlocal resolved_raw_store
        if resolved_raw_store is None:
            try:
                resolved_raw_store = minio_store_from_environment()
            except (RuntimeError, ValueError) as exc:
                raise ApiError(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    code="CASE_MATERIAL_STORAGE_NOT_CONFIGURED",
                    message="Case material storage is not configured",
                ) from exc
        return resolved_raw_store

    Session = Annotated[AsyncSession, Depends(get_session)]
    SourceFilename = Annotated[
        str,
        Header(alias="X-Source-Filename", min_length=1, max_length=255),
    ]

    @router.get(
        "/telegram-intake-drafts/{draft_id}/case-materials",
        response_model=CaseMaterialListResponse,
    )
    async def list_draft_case_materials(
        draft_id: UUID,
        telegram_user_id: TelegramUserId,
        session: Session,
    ) -> CaseMaterialListResponse:
        actor = await resolve_actor(session, telegram_user_id)
        _require_case_intake_actor(actor)
        await _actor_draft(session, actor, draft_id)
        materials = list(
            (
                await session.scalars(
                    select(CaseMaterial)
                    .where(
                        CaseMaterial.clinic_id == actor.clinic_id,
                        CaseMaterial.draft_id == draft_id,
                        CaseMaterial.uploader_membership_id == actor.membership_id,
                        CaseMaterial.expires_at > datetime.now(UTC),
                    )
                    .order_by(CaseMaterial.created_at, CaseMaterial.id)
                    .limit(MAX_CASE_MATERIALS_PER_DRAFT)
                )
            ).all()
        )
        return CaseMaterialListResponse(
            items=[_material_response(material) for material in materials]
        )

    @router.post(
        "/telegram-intake-drafts/{draft_id}/case-materials",
        response_model=CaseMaterialMutationResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def upload_draft_case_material(
        draft_id: UUID,
        request: Request,
        response: Response,
        source_filename: SourceFilename,
        telegram_user_id: TelegramUserId,
        idempotency_key: IdempotencyKey,
        session: Session,
    ) -> CaseMaterialMutationResponse:
        actor = await resolve_actor(session, telegram_user_id)
        _require_case_intake_actor(actor)
        raw = await _read_bounded_material_upload(request)
        try:
            parsed = await asyncio.to_thread(
                parse_clinic_document_upload,
                raw,
                source_filename=source_filename,
                content_type=request.headers.get("content-type", ""),
            )
        except ValueError as exc:
            raise ApiError(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="CASE_MATERIAL_FILE_INVALID",
                message="Case material is not a supported safe document",
            ) from exc
        except RuntimeError as exc:
            raise ApiError(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="CASE_MATERIAL_PARSER_UNAVAILABLE",
                message="Case material parser is unavailable",
            ) from exc
        if pseudonymize_text(parsed.normalized_text).changed:
            raise ApiError(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="CASE_MATERIAL_DIRECT_IDENTIFIER_NOT_ALLOWED",
                message="Case material must be anonymised before upload",
            )

        # `raw` and parsed text never enter SQL, audit data, exceptions, or logs.
        request_hash = _canonical_hash(
            {"rawSha256": parsed.raw_sha256, "mimeType": parsed.mime_type, "draftId": str(draft_id)}
        )
        scope = f"case-materials:create:{draft_id}"
        replay = await _idempotency_replay(
            session, actor=actor, scope=scope, key=idempotency_key, request_hash=request_hash
        )
        if replay is not None:
            response.status_code = status.HTTP_200_OK
            return CaseMaterialMutationResponse.model_validate(replay)

        draft = await _actor_draft(session, actor, draft_id)
        existing = await session.scalar(
            select(CaseMaterial).where(
                CaseMaterial.clinic_id == actor.clinic_id,
                CaseMaterial.draft_id == draft.id,
                CaseMaterial.uploader_membership_id == actor.membership_id,
                CaseMaterial.raw_sha256 == parsed.raw_sha256,
            )
        )
        if existing is not None:
            _require_live_material(existing)
            result = CaseMaterialMutationResponse(
                material=_material_response(existing), draftRevision=draft.revision
            )
            idempotency = _new_idempotency_record(
                actor=actor, scope=scope, key=idempotency_key, request_hash=request_hash
            )
            _finish_idempotency(
                idempotency,
                resource_type="CASE_MATERIAL",
                resource_id=existing.id,
                response_json=result.model_dump(mode="json", by_alias=True),
            )
            session.add(idempotency)
            await session.commit()
            response.status_code = status.HTTP_200_OK
            return result

        current_count = await session.scalar(
            select(func.count())
            .select_from(CaseMaterial)
            .where(
                CaseMaterial.clinic_id == actor.clinic_id,
                CaseMaterial.draft_id == draft.id,
                CaseMaterial.uploader_membership_id == actor.membership_id,
            )
        )
        if int(current_count or 0) >= MAX_CASE_MATERIALS_PER_DRAFT:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="CASE_MATERIAL_LIMIT_REACHED",
                message="The draft material limit was reached",
                details={"limit": MAX_CASE_MATERIALS_PER_DRAFT},
            )

        material = CaseMaterial(
            id=uuid4(),
            clinic_id=actor.clinic_id,
            draft_id=draft.id,
            case_id=None,
            uploader_membership_id=actor.membership_id,
            display_name=f"Материал {int(current_count or 0) + 1}",
            mime_type=parsed.mime_type,
            raw_size_bytes=parsed.raw_size_bytes,
            raw_sha256=parsed.raw_sha256,
            raw_object_key="pending",
            expires_at=draft.purge_after,
        )
        try:
            material.raw_object_key = await get_raw_store().put_case_material(
                clinic_id=actor.clinic_id,
                material_id=material.id,
                raw_sha256=parsed.raw_sha256,
                content=raw,
                content_type=parsed.mime_type,
            )
        except (RuntimeError, ValueError) as exc:
            raise ApiError(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="CASE_MATERIAL_STORAGE_UNAVAILABLE",
                message="Case material storage is unavailable",
            ) from exc

        await _set_draft_material_state(session, draft, attached=True)
        material.expires_at = draft.purge_after
        idempotency = _new_idempotency_record(
            actor=actor, scope=scope, key=idempotency_key, request_hash=request_hash
        )
        session.add_all([material, idempotency])
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            # The object is content-addressed by its newly allocated UUID.  It is
            # safe to delete best-effort if metadata cannot be committed.
            with suppress(RuntimeError, ValueError):
                await get_raw_store().delete_case_material(
                    stored_object_key=material.raw_object_key
                )
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="CASE_MATERIAL_WRITE_CONFLICT",
                message="Case material could not be stored",
            ) from exc
        result = CaseMaterialMutationResponse(
            material=_material_response(material), draftRevision=draft.revision
        )
        _finish_idempotency(
            idempotency,
            resource_type="CASE_MATERIAL",
            resource_id=material.id,
            response_json=result.model_dump(mode="json", by_alias=True),
        )
        session.add(
            _audit(
                actor=actor,
                action="CASE_MATERIAL_CREATED",
                resource_type="CASE_MATERIAL",
                resource_id=material.id,
                metadata={"mimeType": material.mime_type, "sizeBytes": material.raw_size_bytes},
            )
        )
        await session.commit()
        return result

    @router.get("/telegram-intake-drafts/{draft_id}/case-materials/{material_id}")
    async def download_draft_case_material(
        draft_id: UUID,
        material_id: UUID,
        telegram_user_id: TelegramUserId,
        session: Session,
    ) -> StreamingResponse:
        actor = await resolve_actor(session, telegram_user_id)
        _require_case_intake_actor(actor)
        await _actor_draft(session, actor, draft_id)
        material = await _draft_material(
            session, actor=actor, draft_id=draft_id, material_id=material_id
        )
        try:
            raw = await get_raw_store().get_case_material(
                stored_object_key=material.raw_object_key, max_bytes=material.raw_size_bytes
            )
        except (RuntimeError, ValueError) as exc:
            raise ApiError(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="CASE_MATERIAL_STORAGE_UNAVAILABLE",
                message="Case material storage is unavailable",
            ) from exc
        if (
            len(raw) != material.raw_size_bytes
            or hashlib.sha256(raw).hexdigest() != material.raw_sha256
        ):
            raise ApiError(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="CASE_MATERIAL_INTEGRITY_UNAVAILABLE",
                message="Case material integrity could not be verified",
            )
        return StreamingResponse(
            iter((raw,)),
            media_type=material.mime_type,
            headers={
                "Content-Disposition": f'attachment; filename="{_material_filename(material)}"',
                "Cache-Control": "no-store",
            },
        )

    @router.delete(
        "/telegram-intake-drafts/{draft_id}/case-materials/{material_id}",
        response_model=CaseMaterialDeleteResponse,
    )
    async def delete_draft_case_material(
        draft_id: UUID,
        material_id: UUID,
        telegram_user_id: TelegramUserId,
        session: Session,
    ) -> CaseMaterialDeleteResponse:
        actor = await resolve_actor(session, telegram_user_id)
        _require_case_intake_actor(actor)
        draft = await _actor_draft(session, actor, draft_id)
        material = await session.scalar(
            select(CaseMaterial)
            .where(
                CaseMaterial.id == material_id,
                CaseMaterial.clinic_id == actor.clinic_id,
                CaseMaterial.draft_id == draft_id,
                CaseMaterial.uploader_membership_id == actor.membership_id,
            )
            .with_for_update()
        )
        # Retrying a completed delete is deliberately successful. It does not
        # disclose anything to a different tenant because the draft was already
        # resolved through the exact owner membership above.
        if material is None:
            return CaseMaterialDeleteResponse(
                id=material_id, draftRevision=draft.revision
            )
        _require_live_material(material)
        deleted_response = _material_response(material)
        try:
            await get_raw_store().delete_case_material(stored_object_key=material.raw_object_key)
        except (RuntimeError, ValueError) as exc:
            raise ApiError(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="CASE_MATERIAL_STORAGE_UNAVAILABLE",
                message="Case material storage is unavailable",
            ) from exc
        await session.delete(material)
        await session.flush()
        remaining = await session.scalar(
            select(func.count())
            .select_from(CaseMaterial)
            .where(
                CaseMaterial.clinic_id == actor.clinic_id,
                CaseMaterial.draft_id == draft.id,
                CaseMaterial.id != material.id,
            )
        )
        await _set_draft_material_state(session, draft, attached=bool(remaining))
        session.add(
            _audit(
                actor=actor,
                action="CASE_MATERIAL_DELETED",
                resource_type="CASE_MATERIAL",
                resource_id=material.id,
                metadata={},
            )
        )
        await session.commit()
        return CaseMaterialDeleteResponse(id=deleted_response.id, draftRevision=draft.revision)

    @router.get("/cases/{case_id}/case-materials", response_model=CaseMaterialListResponse)
    async def list_case_materials(
        case_id: UUID,
        telegram_user_id: TelegramUserId,
        session: Session,
    ) -> CaseMaterialListResponse:
        actor = await resolve_actor(session, telegram_user_id)
        await _tenant_case(session, actor, case_id)
        materials = list(
            (
                await session.scalars(
                    select(CaseMaterial)
                    .where(
                        CaseMaterial.clinic_id == actor.clinic_id,
                        CaseMaterial.case_id == case_id,
                        CaseMaterial.uploader_membership_id == actor.membership_id,
                        CaseMaterial.expires_at > datetime.now(UTC),
                    )
                    .order_by(CaseMaterial.created_at, CaseMaterial.id)
                    .limit(MAX_CASE_MATERIALS_PER_DRAFT)
                )
            ).all()
        )
        return CaseMaterialListResponse(
            items=[_material_response(material) for material in materials]
        )

    @router.get("/cases/{case_id}/case-materials/{material_id}")
    async def download_case_material(
        case_id: UUID,
        material_id: UUID,
        telegram_user_id: TelegramUserId,
        session: Session,
    ) -> StreamingResponse:
        actor = await resolve_actor(session, telegram_user_id)
        material = await _case_material(
            session, actor=actor, case_id=case_id, material_id=material_id
        )
        try:
            raw = await get_raw_store().get_case_material(
                stored_object_key=material.raw_object_key, max_bytes=material.raw_size_bytes
            )
        except (RuntimeError, ValueError) as exc:
            raise ApiError(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="CASE_MATERIAL_STORAGE_UNAVAILABLE",
                message="Case material storage is unavailable",
            ) from exc
        if (
            len(raw) != material.raw_size_bytes
            or hashlib.sha256(raw).hexdigest() != material.raw_sha256
        ):
            raise ApiError(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="CASE_MATERIAL_INTEGRITY_UNAVAILABLE",
                message="Case material integrity could not be verified",
            )
        return StreamingResponse(
            iter((raw,)),
            media_type=material.mime_type,
            headers={
                "Content-Disposition": f'attachment; filename="{_material_filename(material)}"',
                "Cache-Control": "no-store",
            },
        )

    return router
