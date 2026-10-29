"""Local, bounded excerpts from live case files with explicit analysis consent."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import signal
import sys
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from legal_core.bounded_parser_process import MAX_PARSER_OUTPUT_BYTES
from legal_core.case_material_context_contracts import (
    CaseMaterialContextFragment,
    case_material_context_trace_sha256,
)
from legal_core.case_material_contracts import CaseMaterialMimeType
from legal_core.clinic_document_parser import MAX_UPLOAD_BYTES
from legal_core.clinic_document_store import (
    RawCaseMaterialStore,
    case_material_object_key,
    minio_store_from_environment,
)
from legal_core.clinic_documents import prepare_clinic_document_text
from legal_core.contracts import FactKey
from legal_core.models import CaseMaterial

CASE_MATERIAL_CONTEXT_TIMEOUT_SECONDS = 15.0


def case_material_context_enabled() -> bool:
    return os.getenv("CASE_MATERIAL_CONTEXT_ENABLED", "0").strip().casefold() in {"1", "true"}


@dataclass(frozen=True)
class CaseMaterialSnapshot:
    material_id: UUID
    display_name: str
    mime_type: CaseMaterialMimeType
    raw_sha256: str
    raw_size_bytes: int
    raw_object_key: str
    expires_at: datetime


@dataclass(frozen=True)
class ParsedCaseMaterial:
    snapshot: CaseMaterialSnapshot
    normalized_text: str


@dataclass(frozen=True)
class CaseMaterialContextState:
    fragments: tuple[CaseMaterialContextFragment, ...] = ()
    materials: tuple[CaseMaterialSnapshot, ...] = ()
    deadline_monotonic: float | None = None

    @property
    def trace_sha256(self) -> str | None:
        return case_material_context_trace_sha256(self.fragments)


async def parse_case_material_locally(raw: bytes, *, mime_type: str) -> str:
    """Kill/reap the parser and its native PDF descendants on caller cancellation."""
    if not 0 < len(raw) <= MAX_UPLOAD_BYTES:
        raise ValueError("case material input is invalid")
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "legal_core.case_material_parser_worker", mime_type,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, start_new_session=True,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C",
             "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(Path(__file__).parents[1])},
    )
    assert process.stdin is not None and process.stdout is not None

    async def write_input() -> None:
        assert process.stdin is not None
        with suppress(BrokenPipeError, ConnectionResetError):
            process.stdin.write(raw)
            await process.stdin.drain()
            process.stdin.close()
            await process.stdin.wait_closed()

    writer = asyncio.create_task(write_input())
    output = bytearray()
    try:
        while chunk := await process.stdout.read(
            min(65536, MAX_PARSER_OUTPUT_BYTES + 1 - len(output))
        ):
            output.extend(chunk)
            if len(output) > MAX_PARSER_OUTPUT_BYTES:
                raise ValueError("case material parser output exceeds the supported bound")
        await writer
        await process.wait()
    finally:
        # A stalled native grandchild may outlive the Python worker. Kill the
        # entire private session even if the direct worker has already exited.
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        writer.cancel()
        await asyncio.gather(writer, return_exceptions=True)
        # Even after SIGKILL, a paused stdout transport can keep wait() blocked
        # until its buffered pipe reaches EOF. Discard bounded chunks rather
        # than accumulating rejected output with communicate().
        while await process.stdout.read(65536):
            pass
        await process.wait()
    if process.returncode != 0:
        raise ValueError("case material parsing is unavailable")
    invalid = False
    try:
        value = json.loads(output)
        if (not isinstance(value, dict)
                or set(value) != {"normalizedText", "rawSha256", "mimeType"}
                or not isinstance(value["normalizedText"], str)
                or not 0 < len(value["normalizedText"]) <= 200000
                or value["rawSha256"] != hashlib.sha256(raw).hexdigest()
                or value["mimeType"] != mime_type):
            invalid = True
    except (ValueError, TypeError):
        invalid = True
    if invalid:
        raise ValueError("case material parser binding is invalid")
    return str(value["normalizedText"])


def select_case_material_fragments(
    materials: Sequence[ParsedCaseMaterial], facts: Mapping[FactKey, object],
) -> tuple[CaseMaterialContextFragment, ...]:
    if len(materials) > 10:
        raise ValueError("too many eligible case materials")
    query = " ".join(str(facts.get(key, "")) for key in (
        FactKey.EVENT_SUMMARY, FactKey.AFFECTED_SERVICES, FactKey.SITUATION_AREAS,
    ))
    tokens = tuple(dict.fromkeys(re.findall(r"[^\W\d_]{3,}", query.casefold())))[:64]
    candidates = []
    for material in materials:
        prepared = prepare_clinic_document_text(material.normalized_text)
        ranked = sorted(prepared.fragments, key=lambda item: (
            -sum(token in item.fragment_text.casefold() for token in tokens), item.ordinal,
        ))
        candidates.append((material.snapshot, ranked))
    selected = [(index, ranked[0]) for index, (_snapshot, ranked) in enumerate(candidates)]
    extras = [(index, item) for index, (_snapshot, ranked) in enumerate(candidates)
              for item in ranked[1:]]
    extras.sort(key=lambda pair: (
        -sum(token in pair[1].fragment_text.casefold() for token in tokens),
        pair[0], pair[1].ordinal,
    ))
    selected.extend(extras[:12 - len(selected)])
    result = []
    for index, item in selected:
        snapshot, ranked = candidates[index]
        limitations = ["Использованы выбранные текстовые фрагменты; это не проверка всего файла."]
        if len(ranked) > sum(selected_index == index for selected_index, _ in selected):
            limitations.append(
                "Файл сокращён: для контекста выбраны наиболее подходящие фрагменты."
            )
        if snapshot.mime_type != "text/plain":
            limitations.append("Изображения, подписи и визуальное оформление не анализировались.")
        identifier = uuid5(NAMESPACE_URL, f"case-material:{snapshot.material_id}:"
                           f"{snapshot.raw_sha256}:{item.structural_path}:{item.text_sha256}")
        result.append(CaseMaterialContextFragment(
            materialId=snapshot.material_id, fragmentId=identifier,
            displayName=snapshot.display_name, mimeType=snapshot.mime_type,
            rawSha256=snapshot.raw_sha256, textSha256=item.text_sha256,
            structuralPath=item.structural_path, text=item.fragment_text,
            extractionLimitations=limitations,
        ))
    return tuple(result)


async def _current_materials(
    session: AsyncSession, *, clinic_id: UUID, case_id: UUID,
) -> tuple[CaseMaterialSnapshot, ...]:
    rows = list((await session.scalars(select(CaseMaterial).where(
        CaseMaterial.clinic_id == clinic_id, CaseMaterial.case_id == case_id,
    ).order_by(CaseMaterial.created_at, CaseMaterial.id).limit(11)
      .with_for_update(read=True).execution_options(populate_existing=True))).all())
    if len(rows) > 10:
        raise ValueError("case material set exceeds the supported bound")
    now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
    return tuple(CaseMaterialSnapshot(
        material_id=row.id, display_name=row.display_name,
        mime_type=TypeAdapter(CaseMaterialMimeType).validate_python(row.mime_type),
        raw_sha256=row.raw_sha256, raw_size_bytes=row.raw_size_bytes,
        raw_object_key=row.raw_object_key, expires_at=row.expires_at,
    ) for row in rows if row.analysis_use_allowed and row.expires_at > now)


async def load_case_material_context(
    session: AsyncSession, *, clinic_id: UUID, case_id: UUID,
    facts: Mapping[FactKey, object], raw_store: RawCaseMaterialStore | None = None,
) -> CaseMaterialContextState:
    if not case_material_context_enabled():
        return CaseMaterialContextState()
    result = None
    deadline = asyncio.get_running_loop().time() + CASE_MATERIAL_CONTEXT_TIMEOUT_SECONDS
    try:
        async with asyncio.timeout_at(deadline):
            materials = await _current_materials(session, clinic_id=clinic_id, case_id=case_id)
            if not materials:
                return CaseMaterialContextState(deadline_monotonic=deadline)
            store = raw_store or minio_store_from_environment()
            parsed = []
            for item in materials:
                expected_key = case_material_object_key(
                    clinic_id=clinic_id, material_id=item.material_id, raw_sha256=item.raw_sha256,
                )
                if (item.raw_object_key != expected_key
                        or not 0 < item.raw_size_bytes <= MAX_UPLOAD_BYTES):
                    raise ValueError("case material metadata binding is invalid")
                raw = await store.get_case_material(
                    stored_location=item.raw_object_key, max_bytes=item.raw_size_bytes,
                )
                if (len(raw) != item.raw_size_bytes
                        or hashlib.sha256(raw).hexdigest() != item.raw_sha256):
                    raise ValueError("case material object hash does not match")
                parsed.append(ParsedCaseMaterial(item, await parse_case_material_locally(
                    raw, mime_type=item.mime_type,
                )))
            candidate = CaseMaterialContextState(
                select_case_material_fragments(parsed, facts), materials, deadline,
            )
            # The same source may naturally expire while local extraction runs.
            # Never expose expired excerpts even before the later submission fence.
            await require_case_material_context_current(
                session, clinic_id=clinic_id, case_id=case_id, state=candidate,
            )
            result = candidate
    except (TimeoutError, ValueError, RuntimeError, KeyError, OSError):
        pass
    if result is None:
        from legal_core.case_api import ApiError

        raise ApiError(status_code=503, code="CASE_MATERIAL_CONTEXT_UNAVAILABLE",
                       message="Consented case material context is unavailable")
    return result


async def require_case_material_context_current(
    session: AsyncSession, *, clinic_id: UUID, case_id: UUID, state: CaseMaterialContextState,
) -> None:
    from legal_core.case_api import ApiError

    deadline = state.deadline_monotonic
    if deadline is None:
        deadline = asyncio.get_running_loop().time() + CASE_MATERIAL_CONTEXT_TIMEOUT_SECONDS
    try:
        async with asyncio.timeout_at(deadline):
            current = await _current_materials(session, clinic_id=clinic_id, case_id=case_id) \
                if case_material_context_enabled() else ()
    except (TimeoutError, ValueError):
        raise ApiError(status_code=503, code="CASE_MATERIAL_CONTEXT_UNAVAILABLE",
                       message="Case material source check is unavailable") from None
    if state.materials != current:
        raise ApiError(status_code=409, code="ANALYSIS_CONTEXT_STALE",
                       message="Case material consent or availability changed during analysis")
