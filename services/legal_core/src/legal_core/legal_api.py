"""Authenticated legal-evidence API and bounded platform editor workspace."""

import hashlib
import json
import os
import secrets
from datetime import UTC, date, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.api_contracts import (
    LegalEditorApprovalRequest,
    LegalEditorApprovalResponse,
    LegalEditorCandidatePage,
    LegalEditorCandidateSummary,
    LegalEditorFragment,
    LegalEditorFragmentPage,
    LegalEditorStatusResponse,
    LegalEditorVersionDetail,
    LegalFragmentResponse,
    LegalFragmentSearchResponse,
    LegalLibraryDocumentResponse,
    LegalLibraryResponse,
)
from legal_core.case_api import (
    ApiError,
    TelegramUserId,
    resolve_actor,
)
from legal_core.legal_approval import (
    ApprovalAttestation,
    LegalApprovalRejected,
    approve_legal_version_in_session,
    legal_approval_preflight_reason,
)
from legal_core.legal_retrieval import ApprovedLegalCorpusRepository
from legal_core.models import (
    LegalApprovalEvent,
    LegalDocument,
    LegalFragment,
    LegalSource,
    LegalVersion,
    User,
)

EDITOR_GATEWAY_KEY_ENV = "LEGAL_EDITOR_GATEWAY_KEY"
EDITOR_GATEWAY_HEADER = "X-Legal-Editor-Gateway-Key"
EDITOR_PAGE_SIZE = 10
EDITOR_FRAGMENT_PAGE_SIZE = 5
EDITOR_MAX_PAGE = 100
EDITOR_ARTIFACT_MAX_BYTES = 50_000_000
EDITOR_ARTIFACT_MIME_TYPES = frozenset({"application/pdf", "text/plain"})


def create_legal_router(
    session_factory: async_sessionmaker[AsyncSession],
) -> APIRouter:
    router = APIRouter(prefix="/v1/legal", tags=["legal-evidence"])

    async def get_session() -> Any:
        async with session_factory() as session:
            yield session

    Session = Annotated[AsyncSession, Depends(get_session)]
    SearchQuery = Annotated[str, Query(min_length=2, max_length=500)]
    AsOfDate = Annotated[date, Query(alias="as_of_date")]
    OptionalAsOfDate = Annotated[date | None, Query(alias="as_of_date")]
    SearchLimit = Annotated[int, Query(ge=1, le=20)]
    EditorPage = Annotated[int, Query(ge=1, le=EDITOR_MAX_PAGE)]
    EditorGatewayKey = Annotated[str | None, Header(alias=EDITOR_GATEWAY_HEADER)]
    EditorIdempotencyKey = Annotated[UUID, Header(alias="Idempotency-Key")]

    def require_lawyer_library_access(role: str) -> None:
        """Keep the administrator workspace minimal while preserving owner parity."""

        if role not in {"CLINIC_OWNER", "CLINIC_LAWYER"}:
            raise ApiError(
                status_code=403,
                code="LEGAL_LIBRARY_NOT_ALLOWED",
                message="Legal library access is limited to clinic lawyers and owners",
            )

    def _editor_gateway_key() -> str:
        value = os.getenv(EDITOR_GATEWAY_KEY_ENV, "").strip()
        if len(value) < 32:
            raise ApiError(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="LEGAL_EDITOR_WORKSPACE_UNAVAILABLE",
                message="Legal editor workspace is unavailable",
            )
        return value

    async def require_platform_legal_editor(
        session: AsyncSession,
        *,
        telegram_user_id: int,
        gateway_key: str | None,
    ) -> User:
        configured_key = _editor_gateway_key()
        if gateway_key is None or not secrets.compare_digest(gateway_key, configured_key):
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="LEGAL_EDITOR_NOT_ALLOWED",
                message="Legal editor access is not allowed",
            )
        editor = await session.scalar(
            select(User).where(
                User.telegram_user_id == telegram_user_id,
                User.status == "ACTIVE",
                User.system_role == "LEGAL_EDITOR",
            )
        )
        if editor is None:
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="LEGAL_EDITOR_NOT_ALLOWED",
                message="Legal editor access is not allowed",
            )
        return editor

    async def editor_approval_eligible(session: AsyncSession, version: LegalVersion) -> bool:
        """Evaluate the same immutable checks the approval endpoint will enforce under a lock."""

        if (
            version.approval_state != "REVIEW_REQUIRED"
            or version.artifact_kind != "OFFICIAL_RAW"
            or version.raw_mime_type not in EDITOR_ARTIFACT_MIME_TYPES
            or not 0 < version.raw_size_bytes <= EDITOR_ARTIFACT_MAX_BYTES
        ):
            return False
        source = await session.get(LegalSource, version.source_id)
        if source is None:  # pragma: no cover - foreign key protection
            return False
        attestation = ApprovalAttestation(
            reviewer_telegram_user_id=1,
            version_id=version.id,
            expected_sha256=version.raw_sha256,
            expected_normalized_sha256=version.normalized_sha256,
            expected_fragments_sha256=version.fragments_sha256,
            expected_effective_from=version.effective_from,
            expected_effective_to=version.effective_to,
            source_is_official=True,
            artifact_is_complete=True,
            effective_dates_verified=True,
            fragments_verified=True,
        )
        return await legal_approval_preflight_reason(session, version, source, attestation) is None

    def approval_response(version: LegalVersion) -> LegalEditorApprovalResponse:
        if version.approval_state != "APPROVED" or version.approved_at is None:
            raise RuntimeError("approved legal version is missing approval metadata")
        return LegalEditorApprovalResponse(
            versionId=version.id,
            approvalState="APPROVED",
            approvedAt=version.approved_at,
        )

    @router.get("/fragments", response_model=LegalFragmentSearchResponse)
    async def search_fragments(
        query: SearchQuery,
        as_of_date: AsOfDate,
        telegram_user_id: TelegramUserId,
        session: Session,
        limit: SearchLimit = 10,
    ) -> LegalFragmentSearchResponse:
        await resolve_actor(session, telegram_user_id)
        fragments = await ApprovedLegalCorpusRepository(session).search(
            query,
            as_of_date=as_of_date,
            limit=limit,
        )
        return LegalFragmentSearchResponse(
            items=[
                LegalFragmentResponse.model_validate(fragment, from_attributes=True)
                for fragment in fragments
            ]
        )

    @router.get("/library", response_model=LegalLibraryResponse)
    async def list_library_documents(
        telegram_user_id: TelegramUserId,
        session: Session,
        as_of_date: OptionalAsOfDate = None,
    ) -> LegalLibraryResponse:
        """List current approved source metadata; source drafts and raw files stay private."""

        actor = await resolve_actor(session, telegram_user_id)
        require_lawyer_library_access(actor.role)
        resolved_date = as_of_date or datetime.now(UTC).date()
        documents = await ApprovedLegalCorpusRepository(session).list_documents(
            as_of_date=resolved_date
        )
        return LegalLibraryResponse(
            asOfDate=resolved_date,
            items=[
                LegalLibraryDocumentResponse.model_validate(document, from_attributes=True)
                for document in documents
            ],
        )

    @router.get("/editor/status", response_model=LegalEditorStatusResponse)
    async def editor_status(
        telegram_user_id: TelegramUserId,
        session: Session,
        gateway_key: EditorGatewayKey = None,
    ) -> LegalEditorStatusResponse:
        await require_platform_legal_editor(
            session, telegram_user_id=telegram_user_id, gateway_key=gateway_key
        )
        return LegalEditorStatusResponse(isLegalEditor=True)

    @router.get("/review-queue", response_model=LegalEditorCandidatePage)
    async def list_platform_review_queue(
        telegram_user_id: TelegramUserId,
        session: Session,
        page: EditorPage = 1,
        gateway_key: EditorGatewayKey = None,
    ) -> LegalEditorCandidatePage:
        """List global candidates only for an authenticated platform legal editor."""

        await require_platform_legal_editor(
            session, telegram_user_id=telegram_user_id, gateway_key=gateway_key
        )
        # Legal versions are immutable audit records.  A changed fragment selection creates a
        # newer version of the same document; the review workspace must not present those
        # technical revisions as separate documents to an editor.
        current_queue_versions = (
            select(
                LegalVersion.id.label("version_id"),
                LegalVersion.approval_state.label("approval_state"),
                func.row_number()
                .over(
                    partition_by=LegalVersion.document_id,
                    order_by=LegalVersion.version_no.desc(),
                )
                .label("document_rank"),
            )
            .where(
                or_(
                    LegalVersion.effective_to.is_(None),
                    LegalVersion.effective_to > date.today(),
                )
            )
            .subquery()
        )
        queue_filter = (
            current_queue_versions.c.document_rank == 1,
            current_queue_versions.c.approval_state == "REVIEW_REQUIRED",
        )
        total_items = int(
            await session.scalar(
                select(func.count()).select_from(current_queue_versions).where(*queue_filter)
            )
            or 0
        )
        rows = (
            await session.execute(
                select(LegalVersion, LegalDocument.title, LegalDocument.official_number)
                .join(LegalDocument, LegalDocument.id == LegalVersion.document_id)
                .join(
                    current_queue_versions,
                    current_queue_versions.c.version_id == LegalVersion.id,
                )
                .where(*queue_filter)
                .order_by(LegalVersion.received_at.desc(), LegalVersion.id.desc())
                .offset((page - 1) * EDITOR_PAGE_SIZE)
                .limit(EDITOR_PAGE_SIZE)
            )
        ).all()
        items: list[LegalEditorCandidateSummary] = []
        for version, title, official_number in rows:
            items.append(
                LegalEditorCandidateSummary(
                    documentId=version.document_id,
                    versionId=version.id,
                    documentTitle=title,
                    officialNumber=official_number,
                    approvalState=version.approval_state,
                    artifactKind=version.artifact_kind,
                    approvalEligible=await editor_approval_eligible(session, version),
                )
            )
        return LegalEditorCandidatePage(
            page=page,
            pageSize=10,
            totalItems=total_items,
            items=items,
        )

    @router.get("/review-queue/{version_id}", response_model=LegalEditorVersionDetail)
    async def editor_version_detail(
        version_id: UUID,
        telegram_user_id: TelegramUserId,
        session: Session,
        gateway_key: EditorGatewayKey = None,
    ) -> LegalEditorVersionDetail:
        await require_platform_legal_editor(
            session, telegram_user_id=telegram_user_id, gateway_key=gateway_key
        )
        row = (
            await session.execute(
                select(
                    LegalVersion,
                    LegalDocument.title,
                    LegalDocument.issuer,
                    LegalDocument.official_number,
                    func.count(LegalFragment.id).label("fragment_count"),
                )
                .join(LegalDocument, LegalDocument.id == LegalVersion.document_id)
                .outerjoin(LegalFragment, LegalFragment.version_id == LegalVersion.id)
                .where(LegalVersion.id == version_id)
                .group_by(LegalVersion.id, LegalDocument.id)
            )
        ).one_or_none()
        if row is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="LEGAL_VERSION_NOT_FOUND",
                message="Legal version not found",
            )
        version, title, issuer, official_number, fragment_count = row
        return LegalEditorVersionDetail(
            documentId=version.document_id,
            versionId=version.id,
            documentTitle=title,
            issuer=issuer,
            officialNumber=official_number,
            sourceUrl=version.source_url,
            approvalState=version.approval_state,
            artifactKind=version.artifact_kind,
            rawMimeType=version.raw_mime_type,
            rawSizeBytes=version.raw_size_bytes,
            artifactPageCount=version.artifact_page_count,
            artifactRetrievedAt=version.artifact_retrieved_at,
            effectiveFrom=version.effective_from,
            effectiveTo=version.effective_to,
            rawSha256=version.raw_sha256,
            normalizedSha256=version.normalized_sha256,
            fragmentsSha256=version.fragments_sha256,
            fragmentCount=fragment_count,
            approvalEligible=await editor_approval_eligible(session, version),
        )

    @router.get("/review-queue/{version_id}/artifact")
    async def editor_version_artifact(
        version_id: UUID,
        telegram_user_id: TelegramUserId,
        session: Session,
        gateway_key: EditorGatewayKey = None,
    ) -> StreamingResponse:
        await require_platform_legal_editor(
            session, telegram_user_id=telegram_user_id, gateway_key=gateway_key
        )
        version = await session.get(LegalVersion, version_id)
        if version is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="LEGAL_VERSION_NOT_FOUND",
                message="Legal version not found",
            )
        raw_size = len(version.raw_bytes)
        if (
            raw_size != version.raw_size_bytes
            or hashlib.sha256(version.raw_bytes).hexdigest() != version.raw_sha256
            or raw_size > EDITOR_ARTIFACT_MAX_BYTES
            or version.raw_mime_type not in EDITOR_ARTIFACT_MIME_TYPES
        ):
            raise ApiError(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="LEGAL_ARTIFACT_NOT_DELIVERABLE",
                message="Legal artifact cannot be delivered",
            )
        suffix = ".pdf" if version.raw_mime_type == "application/pdf" else ".txt"
        return StreamingResponse(
            iter([version.raw_bytes]),
            media_type=version.raw_mime_type,
            headers={
                "Content-Length": str(raw_size),
                "Content-Disposition": f'attachment; filename="legal-{version.id}{suffix}"',
                "X-Content-Type-Options": "nosniff",
                "X-Legal-Artifact-Sha256": version.raw_sha256,
            },
        )

    @router.get("/review-queue/{version_id}/fragments", response_model=LegalEditorFragmentPage)
    async def editor_version_fragments(
        version_id: UUID,
        telegram_user_id: TelegramUserId,
        session: Session,
        page: EditorPage = 1,
        gateway_key: EditorGatewayKey = None,
    ) -> LegalEditorFragmentPage:
        await require_platform_legal_editor(
            session, telegram_user_id=telegram_user_id, gateway_key=gateway_key
        )
        version_exists = await session.scalar(
            select(LegalVersion.id).where(LegalVersion.id == version_id)
        )
        if version_exists is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="LEGAL_VERSION_NOT_FOUND",
                message="Legal version not found",
            )
        total_items = int(
            await session.scalar(
                select(func.count())
                .select_from(LegalFragment)
                .where(LegalFragment.version_id == version_id)
            )
            or 0
        )
        fragments = list(
            (
                await session.scalars(
                    select(LegalFragment)
                    .where(LegalFragment.version_id == version_id)
                    .order_by(LegalFragment.ordinal.asc(), LegalFragment.id.asc())
                    .offset((page - 1) * EDITOR_FRAGMENT_PAGE_SIZE)
                    .limit(EDITOR_FRAGMENT_PAGE_SIZE)
                )
            ).all()
        )
        return LegalEditorFragmentPage(
            page=page,
            pageSize=5,
            totalItems=total_items,
            items=[
                LegalEditorFragment(
                    ordinal=fragment.ordinal,
                    structuralPath=fragment.structural_path,
                    fragmentText=fragment.fragment_text[:1_200],
                    textSha256=fragment.text_sha256,
                    truncated=len(fragment.fragment_text) > 1_200,
                )
                for fragment in fragments
            ],
        )

    @router.post(
        "/review-queue/{version_id}/approval-events",
        response_model=LegalEditorApprovalResponse,
    )
    async def approve_editor_version(
        version_id: UUID,
        payload: LegalEditorApprovalRequest,
        telegram_user_id: TelegramUserId,
        idempotency_key: EditorIdempotencyKey,
        session: Session,
        gateway_key: EditorGatewayKey = None,
    ) -> LegalEditorApprovalResponse:
        editor = await require_platform_legal_editor(
            session, telegram_user_id=telegram_user_id, gateway_key=gateway_key
        )
        request_payload = payload.model_dump(mode="json", by_alias=True)
        request_payload["versionId"] = str(version_id)
        request_sha256 = hashlib.sha256(
            json.dumps(
                request_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        await session.execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtextextended(
                        f"legal-editor-approval:{editor.id}:{idempotency_key}", 736659
                    )
                )
            )
        )
        replay = await session.scalar(
            select(LegalApprovalEvent)
            .where(
                LegalApprovalEvent.actor_user_id == editor.id,
                LegalApprovalEvent.idempotency_key == idempotency_key,
            )
            .with_for_update()
        )
        if replay is not None:
            if replay.request_sha256 != request_sha256:
                raise ApiError(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    code="IDEMPOTENCY_KEY_REUSED",
                    message="Idempotency key was already used with another request",
                )
            replayed_version = await session.get(LegalVersion, replay.legal_version_id)
            if replayed_version is None:  # pragma: no cover - foreign key protection
                raise RuntimeError("approval event legal version is missing")
            return approval_response(replayed_version)

        attestation = ApprovalAttestation(
            reviewer_telegram_user_id=telegram_user_id,
            version_id=version_id,
            **payload.model_dump(mode="python", by_alias=False),
        )
        try:
            version = await approve_legal_version_in_session(
                session,
                attestation,
                reviewer=editor,
                require_review_required=True,
                record_rejected_attempt=False,
                idempotency_key=idempotency_key,
                request_sha256=request_sha256,
            )
        except LookupError as exc:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="LEGAL_VERSION_NOT_FOUND",
                message="Legal version not found",
            ) from exc
        except LegalApprovalRejected as exc:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="LEGAL_VERSION_NOT_APPROVABLE",
                message="Legal version is not approvable",
                details={"reasonCode": exc.reason_code},
            ) from exc
        await session.commit()
        return approval_response(version)

    return router
