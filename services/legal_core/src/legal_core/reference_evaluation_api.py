"""Private API for reviewable, de-identified historical evaluation cases.

Nothing in this router is a legal source, a retrieval input, a model prompt, or a
patient-facing response.  It is a narrow human review workspace with explicit
membership grants and storage-first retention of optional source files.
"""

import asyncio
import hashlib
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.case_api import (
    ActorContext,
    ApiError,
    IdempotencyKey,
    TelegramUserId,
    _audit,
    _canonical_hash,
    _finish_idempotency,
    _idempotency_replay,
    _new_idempotency_record,
    resolve_actor,
)
from legal_core.case_materials_api import _read_bounded_material_upload
from legal_core.clinic_document_parser import parse_clinic_document_upload
from legal_core.clinic_document_store import (
    RawReferenceEvaluationStore,
    minio_store_from_environment,
)
from legal_core.models import (
    ReferenceEvaluationAccessGrant,
    ReferenceEvaluationCase,
    ReferenceEvaluationCaseVersion,
    ReferenceEvaluationReviewEvent,
)
from legal_core.pseudonymization import pseudonymize_text
from legal_core.reference_evaluation_contracts import (
    ReferenceEvaluationAccessResponse,
    ReferenceEvaluationCreateRequest,
    ReferenceEvaluationCreateResponse,
    ReferenceEvaluationDetail,
    ReferenceEvaluationGroup,
    ReferenceEvaluationListResponse,
    ReferenceEvaluationMaterialResponse,
    ReferenceEvaluationReviewRequest,
    ReferenceEvaluationReviewResponse,
    ReferenceEvaluationRevisionRequest,
    ReferenceEvaluationStatus,
    ReferenceEvaluationSummary,
    ReferenceExpectedRoute,
    ReferenceMaterialMimeType,
)

_DRAFT_RETENTION = timedelta(days=30)
_RETIRED_RETENTION = timedelta(days=90)
_MUTABLE_STATUSES = frozenset({"DRAFT", "CHANGES_REQUIRED"})
_REVIEWABLE_STATUSES = frozenset({"READY_FOR_REVIEW"})


def _display_name(case: ReferenceEvaluationCase, scenario_text: str | None = None) -> str:
    if scenario_text:
        first_line = scenario_text.split("\n", 1)[0]
        if first_line.startswith("Название: "):
            title = " ".join(first_line.removeprefix("Название: ").split())
            if title:
                return f"№{case.case_no} · {title}"[:120]
    return f"Эталонный кейс №{case.case_no}"


def _content_available(version: ReferenceEvaluationCaseVersion) -> bool:
    return version.content_purged_at is None and version.scenario_text is not None


def _summary(
    case: ReferenceEvaluationCase,
    version: ReferenceEvaluationCaseVersion,
) -> ReferenceEvaluationSummary:
    return ReferenceEvaluationSummary(
        id=case.id,
        displayName=_display_name(
            case, version.scenario_text if _content_available(version) else None
        ),
        groupKey=cast(ReferenceEvaluationGroup, case.group_key),
        status=cast(ReferenceEvaluationStatus, case.status),
        currentVersion=case.current_version,
        hasMaterial=version.raw_object_key is not None and version.content_purged_at is None,
        createdAt=case.created_at,
        updatedAt=case.updated_at,
    )


def _detail(
    case: ReferenceEvaluationCase,
    version: ReferenceEvaluationCaseVersion,
    *,
    actor: ActorContext,
) -> ReferenceEvaluationDetail:
    return ReferenceEvaluationDetail(
        **_summary(case, version).model_dump(mode="python", by_alias=False),
        asOfDate=version.as_of_date,
        expectedRoute=cast(ReferenceExpectedRoute, version.expected_route),
        scenarioText=version.scenario_text if _content_available(version) else None,
        contentPurgedAt=version.content_purged_at,
        createdBySelf=version.created_by_membership_id == actor.membership_id,
    )


async def _permissions(session: AsyncSession, actor: ActorContext) -> set[str]:
    values = await session.scalars(
        select(ReferenceEvaluationAccessGrant.permission).where(
            ReferenceEvaluationAccessGrant.clinic_id == actor.clinic_id,
            ReferenceEvaluationAccessGrant.membership_id == actor.membership_id,
        )
    )
    return set(values.all())


async def _require_workspace(
    session: AsyncSession,
    actor: ActorContext,
    *,
    contribute: bool = False,
    review: bool = False,
) -> set[str]:
    permissions = await _permissions(session, actor)
    if not permissions or (contribute and "CONTRIBUTOR" not in permissions) or (
        review and "REVIEWER" not in permissions
    ):
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="REFERENCE_EVALUATION_ACCESS_REQUIRED",
            message="Reference evaluation workspace is not available",
        )
    return permissions


async def _case(
    session: AsyncSession,
    actor: ActorContext,
    case_id: UUID,
    *,
    for_update: bool = False,
) -> ReferenceEvaluationCase:
    statement = select(ReferenceEvaluationCase).where(
        ReferenceEvaluationCase.id == case_id,
        ReferenceEvaluationCase.clinic_id == actor.clinic_id,
    )
    if for_update:
        statement = statement.with_for_update()
    value = await session.scalar(statement)
    if value is None:
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="REFERENCE_EVALUATION_CASE_NOT_FOUND",
            message="Reference evaluation case not found",
        )
    return value


async def _current_version(
    session: AsyncSession,
    case: ReferenceEvaluationCase,
    *,
    for_update: bool = False,
) -> ReferenceEvaluationCaseVersion:
    statement = select(ReferenceEvaluationCaseVersion).where(
        ReferenceEvaluationCaseVersion.clinic_id == case.clinic_id,
        ReferenceEvaluationCaseVersion.reference_case_id == case.id,
        ReferenceEvaluationCaseVersion.version == case.current_version,
    )
    if for_update:
        statement = statement.with_for_update()
    value = await session.scalar(statement)
    if value is None:
        raise RuntimeError("reference evaluation case current version is missing")
    return value


def _require_live_version(version: ReferenceEvaluationCaseVersion) -> None:
    if not _content_available(version):
        raise ApiError(
            status_code=status.HTTP_410_GONE,
            code="REFERENCE_EVALUATION_CONTENT_PURGED",
            message="Reference evaluation content is no longer available",
        )


def _reject_identifiers(value: str, *, code: str) -> str:
    checked = pseudonymize_text(value)
    if checked.changed:
        raise ApiError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code=code,
            message="Reference evaluation material must be de-identified",
        )
    return value.strip()


def _generic_download_name(version: ReferenceEvaluationCaseVersion) -> str:
    mime_type = version.raw_mime_type
    if mime_type is None:
        raise ApiError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="REFERENCE_EVALUATION_METADATA_INVALID",
            message="Reference evaluation metadata is unavailable",
        )
    extension = {
        "text/plain": "txt",
        "application/pdf": "pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    }.get(mime_type)
    if extension is None:
        raise ApiError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="REFERENCE_EVALUATION_METADATA_INVALID",
            message="Reference evaluation metadata is unavailable",
        )
    return f"reference-evaluation-{version.id}.{extension}"


def create_reference_evaluation_router(
    session_factory: async_sessionmaker[AsyncSession],
    raw_store: RawReferenceEvaluationStore | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/v1/reference-evaluations", tags=["reference-evaluations"])
    resolved_raw_store = raw_store

    async def get_session() -> Any:
        async with session_factory() as session:
            yield session

    def get_raw_store() -> RawReferenceEvaluationStore:
        nonlocal resolved_raw_store
        if resolved_raw_store is None:
            try:
                resolved_raw_store = minio_store_from_environment()
            except (RuntimeError, ValueError) as exc:
                raise ApiError(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    code="REFERENCE_EVALUATION_STORAGE_NOT_CONFIGURED",
                    message="Reference evaluation storage is not configured",
                ) from exc
        return resolved_raw_store

    Session = Annotated[AsyncSession, Depends(get_session)]
    SourceFilename = Annotated[
        str,
        Header(alias="X-Source-Filename", min_length=1, max_length=255),
    ]

    @router.get("/access", response_model=ReferenceEvaluationAccessResponse)
    async def reference_evaluation_access(
        telegram_user_id: TelegramUserId,
        session: Session,
    ) -> ReferenceEvaluationAccessResponse:
        actor = await resolve_actor(session, telegram_user_id)
        permissions = await _permissions(session, actor)
        return ReferenceEvaluationAccessResponse(
            canContribute="CONTRIBUTOR" in permissions,
            canReview="REVIEWER" in permissions,
        )

    @router.post(
        "",
        response_model=ReferenceEvaluationCreateResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_reference_evaluation(
        payload: ReferenceEvaluationCreateRequest,
        telegram_user_id: TelegramUserId,
        idempotency_key: IdempotencyKey,
        session: Session,
    ) -> ReferenceEvaluationCreateResponse:
        actor = await resolve_actor(session, telegram_user_id)
        await _require_workspace(session, actor, contribute=True)
        scenario_text = _reject_identifiers(
            payload.scenario_text, code="REFERENCE_EVALUATION_DIRECT_IDENTIFIER_NOT_ALLOWED"
        )
        request_hash = _canonical_hash(
            {
                "groupKey": payload.group_key,
                "asOfDate": payload.as_of_date.isoformat(),
                "expectedRoute": payload.expected_route,
                "scenarioSha256": hashlib.sha256(scenario_text.encode("utf-8")).hexdigest(),
            }
        )
        replay = await _idempotency_replay(
            session,
            actor=actor,
            scope="reference-evaluations:create",
            key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            return ReferenceEvaluationCreateResponse.model_validate(replay)
        now = datetime.now(UTC)
        case = ReferenceEvaluationCase(
            id=uuid4(),
            clinic_id=actor.clinic_id,
            created_by_membership_id=actor.membership_id,
            group_key=payload.group_key,
            status="DRAFT",
            current_version=1,
            created_at=now,
            updated_at=now,
        )
        version = ReferenceEvaluationCaseVersion(
            id=uuid4(),
            clinic_id=actor.clinic_id,
            reference_case_id=case.id,
            version=1,
            created_by_membership_id=actor.membership_id,
            as_of_date=payload.as_of_date,
            expected_route=payload.expected_route,
            scenario_text=scenario_text,
            scenario_sha256=hashlib.sha256(scenario_text.encode("utf-8")).hexdigest(),
            raw_mime_type=None,
            raw_size_bytes=None,
            raw_sha256=None,
            raw_object_key=None,
            content_expires_at=now + _DRAFT_RETENTION,
            content_purged_at=None,
            deletion_claimed_at=None,
            deletion_lease_token=None,
            deletion_attempts=0,
            created_at=now,
        )
        session.add_all([case, version])
        await session.flush()
        result = ReferenceEvaluationCreateResponse(case=_detail(case, version, actor=actor))
        record = _new_idempotency_record(
            actor=actor,
            scope="reference-evaluations:create",
            key=idempotency_key,
            request_hash=request_hash,
        )
        _finish_idempotency(
            record,
            resource_type="REFERENCE_EVALUATION_CASE",
            resource_id=case.id,
            response_json=result.model_dump(mode="json", by_alias=True),
        )
        session.add_all(
            [
                record,
                _audit(
                    actor=actor,
                    action="REFERENCE_EVALUATION_CASE_CREATED",
                    resource_type="REFERENCE_EVALUATION_CASE",
                    resource_id=case.id,
                    metadata={"groupKey": case.group_key, "version": version.version},
                ),
            ]
        )
        await session.commit()
        return result

    @router.get("", response_model=ReferenceEvaluationListResponse)
    async def list_reference_evaluations(
        telegram_user_id: TelegramUserId,
        session: Session,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        before: UUID | None = None,
    ) -> ReferenceEvaluationListResponse:
        actor = await resolve_actor(session, telegram_user_id)
        await _require_workspace(session, actor)
        statement = select(ReferenceEvaluationCase).where(
            ReferenceEvaluationCase.clinic_id == actor.clinic_id
        )
        if before is not None:
            cursor = await session.scalar(
                select(ReferenceEvaluationCase).where(
                    ReferenceEvaluationCase.id == before,
                    ReferenceEvaluationCase.clinic_id == actor.clinic_id,
                )
            )
            if cursor is None:
                raise ApiError(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    code="REFERENCE_EVALUATION_CURSOR_INVALID",
                    message="Reference evaluation cursor is invalid",
                )
            statement = statement.where(
                or_(
                    ReferenceEvaluationCase.updated_at < cursor.updated_at,
                    and_(
                        ReferenceEvaluationCase.updated_at == cursor.updated_at,
                        ReferenceEvaluationCase.id < cursor.id,
                    ),
                )
            )
        cases = list(
            (
                await session.scalars(
                    statement.order_by(
                        ReferenceEvaluationCase.updated_at.desc(), ReferenceEvaluationCase.id.desc()
                    ).limit(limit + 1)
                )
            ).all()
        )
        page, next_case = cases[:limit], cases[limit:]
        items: list[ReferenceEvaluationSummary] = []
        for case in page:
            version = await _current_version(session, case)
            items.append(_summary(case, version))
        return ReferenceEvaluationListResponse(
            items=items,
            # The cursor identifies the last item returned.  Using the first
            # non-returned row would make the strict "before" predicate skip it.
            nextBefore=page[-1].id if next_case else None,
        )

    @router.post("/{case_id}/revisions", response_model=ReferenceEvaluationCreateResponse)
    async def revise_reference_evaluation(
        case_id: UUID,
        payload: ReferenceEvaluationRevisionRequest,
        telegram_user_id: TelegramUserId,
        idempotency_key: IdempotencyKey,
        session: Session,
    ) -> ReferenceEvaluationCreateResponse:
        actor = await resolve_actor(session, telegram_user_id)
        await _require_workspace(session, actor, contribute=True)
        scenario_text = _reject_identifiers(
            payload.scenario_text, code="REFERENCE_EVALUATION_DIRECT_IDENTIFIER_NOT_ALLOWED"
        )
        request_hash = _canonical_hash(
            {
                "caseId": str(case_id),
                "asOfDate": payload.as_of_date.isoformat(),
                "expectedRoute": payload.expected_route,
                "scenarioSha256": hashlib.sha256(scenario_text.encode("utf-8")).hexdigest(),
            }
        )
        scope = f"reference-evaluations:revision:{case_id}"
        replay = await _idempotency_replay(
            session, actor=actor, scope=scope, key=idempotency_key, request_hash=request_hash
        )
        if replay is not None:
            return ReferenceEvaluationCreateResponse.model_validate(replay)
        case = await _case(session, actor, case_id, for_update=True)
        if (
            case.status != "CHANGES_REQUIRED"
            or case.created_by_membership_id != actor.membership_id
        ):
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="REFERENCE_EVALUATION_REVISION_NOT_ALLOWED",
                message="Only the contributor can revise a changes-requested reference case",
            )
        previous = await _current_version(session, case, for_update=True)
        _require_live_version(previous)
        now = datetime.now(UTC)
        case.current_version += 1
        case.status = "DRAFT"
        case.updated_at = now
        version = ReferenceEvaluationCaseVersion(
            id=uuid4(),
            clinic_id=actor.clinic_id,
            reference_case_id=case.id,
            version=case.current_version,
            created_by_membership_id=actor.membership_id,
            as_of_date=payload.as_of_date,
            expected_route=payload.expected_route,
            scenario_text=scenario_text,
            scenario_sha256=hashlib.sha256(scenario_text.encode("utf-8")).hexdigest(),
            raw_mime_type=None,
            raw_size_bytes=None,
            raw_sha256=None,
            raw_object_key=None,
            content_expires_at=now + _DRAFT_RETENTION,
            content_purged_at=None,
            deletion_claimed_at=None,
            deletion_lease_token=None,
            deletion_attempts=0,
            created_at=now,
        )
        result = ReferenceEvaluationCreateResponse(case=_detail(case, version, actor=actor))
        record = _new_idempotency_record(
            actor=actor, scope=scope, key=idempotency_key, request_hash=request_hash
        )
        _finish_idempotency(
            record,
            resource_type="REFERENCE_EVALUATION_CASE_VERSION",
            resource_id=version.id,
            response_json=result.model_dump(mode="json", by_alias=True),
        )
        session.add_all(
            [
                version,
                record,
                _audit(
                    actor=actor,
                    action="REFERENCE_EVALUATION_CASE_REVISED",
                    resource_type="REFERENCE_EVALUATION_CASE",
                    resource_id=case.id,
                    metadata={"version": version.version},
                ),
            ]
        )
        await session.commit()
        return result

    @router.get("/{case_id}", response_model=ReferenceEvaluationDetail)
    async def get_reference_evaluation(
        case_id: UUID,
        telegram_user_id: TelegramUserId,
        session: Session,
    ) -> ReferenceEvaluationDetail:
        actor = await resolve_actor(session, telegram_user_id)
        await _require_workspace(session, actor)
        case = await _case(session, actor, case_id)
        return _detail(case, await _current_version(session, case), actor=actor)

    @router.post(
        "/{case_id}/material",
        response_model=ReferenceEvaluationMaterialResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def upload_reference_evaluation_material(
        case_id: UUID,
        request: Request,
        response: Response,
        source_filename: SourceFilename,
        telegram_user_id: TelegramUserId,
        idempotency_key: IdempotencyKey,
        session: Session,
    ) -> ReferenceEvaluationMaterialResponse:
        actor = await resolve_actor(session, telegram_user_id)
        await _require_workspace(session, actor, contribute=True)
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
                code="REFERENCE_EVALUATION_FILE_INVALID",
                message="Reference evaluation file is not a supported safe document",
            ) from exc
        except RuntimeError as exc:
            raise ApiError(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="REFERENCE_EVALUATION_PARSER_UNAVAILABLE",
                message="Reference evaluation parser is unavailable",
            ) from exc
        _reject_identifiers(
            parsed.normalized_text, code="REFERENCE_EVALUATION_DIRECT_IDENTIFIER_NOT_ALLOWED"
        )
        request_hash = _canonical_hash(
            {"caseId": str(case_id), "rawSha256": parsed.raw_sha256, "mimeType": parsed.mime_type}
        )
        scope = f"reference-evaluations:material:{case_id}"
        replay = await _idempotency_replay(
            session, actor=actor, scope=scope, key=idempotency_key, request_hash=request_hash
        )
        if replay is not None:
            response.status_code = status.HTTP_200_OK
            return ReferenceEvaluationMaterialResponse.model_validate(replay)
        case = await _case(session, actor, case_id, for_update=True)
        if case.status not in _MUTABLE_STATUSES:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="REFERENCE_EVALUATION_CASE_NOT_MUTABLE",
                message="Reference evaluation case is not open for material changes",
            )
        version = await _current_version(session, case, for_update=True)
        _require_live_version(version)
        if version.raw_object_key is not None:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="REFERENCE_EVALUATION_MATERIAL_ALREADY_ATTACHED",
                message="Reference evaluation case already has a source file",
            )
        try:
            object_key = await get_raw_store().put_reference_evaluation(
                clinic_id=actor.clinic_id,
                version_id=version.id,
                raw_sha256=parsed.raw_sha256,
                content=raw,
                content_type=parsed.mime_type,
            )
        except (RuntimeError, ValueError) as exc:
            raise ApiError(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="REFERENCE_EVALUATION_STORAGE_UNAVAILABLE",
                message="Reference evaluation storage is unavailable",
            ) from exc
        version.raw_mime_type = parsed.mime_type
        version.raw_size_bytes = parsed.raw_size_bytes
        version.raw_sha256 = parsed.raw_sha256
        version.raw_object_key = object_key
        case.updated_at = datetime.now(UTC)
        result = ReferenceEvaluationMaterialResponse(
            case=_summary(case, version),
            mimeType=cast(ReferenceMaterialMimeType, parsed.mime_type),
            sizeBytes=parsed.raw_size_bytes,
        )
        record = _new_idempotency_record(
            actor=actor, scope=scope, key=idempotency_key, request_hash=request_hash
        )
        _finish_idempotency(
            record,
            resource_type="REFERENCE_EVALUATION_MATERIAL",
            resource_id=version.id,
            response_json=result.model_dump(mode="json", by_alias=True),
        )
        session.add_all(
            [
                record,
                _audit(
                    actor=actor,
                    action="REFERENCE_EVALUATION_MATERIAL_ATTACHED",
                    resource_type="REFERENCE_EVALUATION_CASE",
                    resource_id=case.id,
                    metadata={"mimeType": parsed.mime_type, "sizeBytes": parsed.raw_size_bytes},
                ),
            ]
        )
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            with suppress(RuntimeError, ValueError):
                await get_raw_store().delete_reference_evaluation(object_key=object_key)
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="REFERENCE_EVALUATION_WRITE_CONFLICT",
                message="Reference evaluation material could not be stored",
            ) from exc
        return result

    @router.get("/{case_id}/material")
    async def download_reference_evaluation_material(
        case_id: UUID,
        telegram_user_id: TelegramUserId,
        session: Session,
    ) -> StreamingResponse:
        actor = await resolve_actor(session, telegram_user_id)
        await _require_workspace(session, actor)
        case = await _case(session, actor, case_id)
        version = await _current_version(session, case)
        _require_live_version(version)
        if version.raw_object_key is None or version.raw_size_bytes is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="REFERENCE_EVALUATION_MATERIAL_NOT_FOUND",
                message="Reference evaluation source file not found",
            )
        try:
            raw = await get_raw_store().get_reference_evaluation(
                object_key=version.raw_object_key,
                max_bytes=version.raw_size_bytes,
            )
        except (RuntimeError, ValueError) as exc:
            raise ApiError(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="REFERENCE_EVALUATION_STORAGE_UNAVAILABLE",
                message="Reference evaluation storage is unavailable",
            ) from exc
        return StreamingResponse(
            iter([raw]),
            media_type=version.raw_mime_type,
            headers={
                "Content-Disposition": f'attachment; filename="{_generic_download_name(version)}"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.post("/{case_id}/submit", response_model=ReferenceEvaluationSummary)
    async def submit_reference_evaluation(
        case_id: UUID,
        telegram_user_id: TelegramUserId,
        idempotency_key: IdempotencyKey,
        session: Session,
    ) -> ReferenceEvaluationSummary:
        actor = await resolve_actor(session, telegram_user_id)
        await _require_workspace(session, actor, contribute=True)
        request_hash = _canonical_hash({"caseId": str(case_id), "action": "submit"})
        scope = f"reference-evaluations:submit:{case_id}"
        replay = await _idempotency_replay(
            session, actor=actor, scope=scope, key=idempotency_key, request_hash=request_hash
        )
        if replay is not None:
            return ReferenceEvaluationSummary.model_validate(replay)
        case = await _case(session, actor, case_id, for_update=True)
        if case.status not in _MUTABLE_STATUSES:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="REFERENCE_EVALUATION_CASE_NOT_MUTABLE",
                message="Reference evaluation case is not ready for review submission",
            )
        version = await _current_version(session, case, for_update=True)
        _require_live_version(version)
        case.status = "READY_FOR_REVIEW"
        case.updated_at = datetime.now(UTC)
        result = _summary(case, version)
        record = _new_idempotency_record(
            actor=actor, scope=scope, key=idempotency_key, request_hash=request_hash
        )
        _finish_idempotency(
            record,
            resource_type="REFERENCE_EVALUATION_CASE",
            resource_id=case.id,
            response_json=result.model_dump(mode="json", by_alias=True),
        )
        session.add_all(
            [
                record,
                _audit(
                    actor=actor,
                    action="REFERENCE_EVALUATION_REVIEW_REQUESTED",
                    resource_type="REFERENCE_EVALUATION_CASE",
                    resource_id=case.id,
                    metadata={"version": version.version},
                ),
            ]
        )
        await session.commit()
        return result

    @router.post("/{case_id}/review", response_model=ReferenceEvaluationReviewResponse)
    async def review_reference_evaluation(
        case_id: UUID,
        payload: ReferenceEvaluationReviewRequest,
        telegram_user_id: TelegramUserId,
        idempotency_key: IdempotencyKey,
        session: Session,
    ) -> ReferenceEvaluationReviewResponse:
        actor = await resolve_actor(session, telegram_user_id)
        await _require_workspace(session, actor, review=True)
        note = None
        if payload.note is not None:
            note = _reject_identifiers(
                payload.note, code="REFERENCE_EVALUATION_DIRECT_IDENTIFIER_NOT_ALLOWED"
            )
        request_hash = _canonical_hash(
            {"caseId": str(case_id), "decision": payload.decision, "note": note}
        )
        scope = f"reference-evaluations:review:{case_id}"
        replay = await _idempotency_replay(
            session, actor=actor, scope=scope, key=idempotency_key, request_hash=request_hash
        )
        if replay is not None:
            return ReferenceEvaluationReviewResponse.model_validate(replay)
        case = await _case(session, actor, case_id, for_update=True)
        version = await _current_version(session, case, for_update=True)
        _require_live_version(version)
        is_retire = payload.decision == "RETIRE"
        allowed = (
            case.status == "APPROVED_FOR_EVALUATION"
            if is_retire
            else case.status in _REVIEWABLE_STATUSES
        )
        if not allowed:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="REFERENCE_EVALUATION_REVIEW_STATE_INVALID",
                message="Reference evaluation case is not in the required review state",
            )
        if version.created_by_membership_id == actor.membership_id:
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="REFERENCE_EVALUATION_SELF_REVIEW_FORBIDDEN",
                message="A reviewer cannot review their own reference evaluation version",
            )
        now = datetime.now(UTC)
        new_status = {
            "APPROVE_FOR_EVALUATION": "APPROVED_FOR_EVALUATION",
            "CHANGES_REQUIRED": "CHANGES_REQUIRED",
            "REJECT": "REJECTED",
            "RETIRE": "RETIRED",
        }[payload.decision]
        case.status = new_status
        case.updated_at = now
        if new_status == "APPROVED_FOR_EVALUATION":
            version.content_expires_at = None
        elif new_status == "RETIRED":
            version.content_expires_at = now + _RETIRED_RETENTION
        else:
            version.content_expires_at = now + _DRAFT_RETENTION
        event = ReferenceEvaluationReviewEvent(
            id=uuid4(),
            clinic_id=actor.clinic_id,
            reference_case_id=case.id,
            reference_version_id=version.id,
            reviewer_membership_id=actor.membership_id,
            decision=payload.decision,
            note=note,
            created_at=now,
        )
        result = ReferenceEvaluationReviewResponse(
            case=_summary(case, version),
            decision=payload.decision,
            reviewedAt=now,
        )
        record = _new_idempotency_record(
            actor=actor, scope=scope, key=idempotency_key, request_hash=request_hash
        )
        _finish_idempotency(
            record,
            resource_type="REFERENCE_EVALUATION_REVIEW",
            resource_id=event.id,
            response_json=result.model_dump(mode="json", by_alias=True),
        )
        session.add_all(
            [
                event,
                record,
                _audit(
                    actor=actor,
                    action="REFERENCE_EVALUATION_REVIEWED",
                    resource_type="REFERENCE_EVALUATION_CASE",
                    resource_id=case.id,
                    metadata={"decision": payload.decision, "version": version.version},
                ),
            ]
        )
        await session.commit()
        return result

    return router
