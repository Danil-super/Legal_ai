"""Explicit development-only loader for the synthetic Clinic Documents fixture pack.

The loader always uses public Legal Core APIs instead of writing database rows directly. A write run
requires both ``--apply`` and ``ALLOW_SYNTHETIC_CLINIC_FIXTURES=1`` and is restricted to loopback or
the internal Compose hostname ``legal-core``. This prevents accidental seeding of a remote tenant.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx

_FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "clinic_documents"
_MANIFEST = _FIXTURE_DIR / "synthetic_pack.v1.json"
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1", "legal-core"}
_APPROVAL_REASON = "SYNTHETIC_FIXTURE_REVIEW_PASSED"


@dataclass(frozen=True, slots=True)
class SyntheticFixtureDocument:
    document_key: str
    document_type: str
    title: str
    filename: str
    valid_from: str
    text: str


@dataclass(frozen=True, slots=True)
class SyntheticLoadResult:
    document_key: str
    document_id: UUID
    version_id: UUID
    version_no: int
    approval_decision: str


class SyntheticFixtureLoadError(RuntimeError):
    pass


def _load_documents() -> tuple[SyntheticFixtureDocument, ...]:
    try:
        payload = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SyntheticFixtureLoadError("synthetic fixture manifest is unavailable") from exc
    if not isinstance(payload, dict):
        raise SyntheticFixtureLoadError("synthetic fixture manifest root is invalid")
    if payload.get("schema_version") != "synthetic-clinic-documents.v1":
        raise SyntheticFixtureLoadError("unsupported synthetic fixture schema")
    if payload.get("authority") != "NOT_A_LEGAL_SOURCE":
        raise SyntheticFixtureLoadError("synthetic fixture authority marker is missing")
    raw_documents = payload.get("documents")
    if not isinstance(raw_documents, list) or len(raw_documents) != 8:
        raise SyntheticFixtureLoadError("synthetic fixture pack must contain exactly eight documents")

    documents: list[SyntheticFixtureDocument] = []
    seen_keys: set[str] = set()
    for raw in raw_documents:
        if not isinstance(raw, dict):
            raise SyntheticFixtureLoadError("synthetic fixture document metadata is invalid")
        values = {
            "document_key": raw.get("document_key"),
            "document_type": raw.get("document_type"),
            "title": raw.get("title"),
            "filename": raw.get("filename"),
            "valid_from": raw.get("valid_from"),
        }
        if not all(isinstance(value, str) and value.strip() for value in values.values()):
            raise SyntheticFixtureLoadError("synthetic fixture document metadata is incomplete")
        document_key = str(values["document_key"])
        filename = str(values["filename"])
        if document_key in seen_keys:
            raise SyntheticFixtureLoadError("synthetic fixture document keys must be unique")
        seen_keys.add(document_key)
        path = (_FIXTURE_DIR / filename).resolve()
        if path.parent != _FIXTURE_DIR.resolve() or path.suffix != ".txt":
            raise SyntheticFixtureLoadError("synthetic fixture path escaped its directory")
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SyntheticFixtureLoadError("synthetic fixture text is unavailable") from exc
        if not text.startswith("SYNTHETIC DEVELOPMENT FIXTURE — NOT A LEGAL TEMPLATE"):
            raise SyntheticFixtureLoadError("synthetic fixture disclaimer is missing")
        documents.append(
            SyntheticFixtureDocument(
                document_key=document_key,
                document_type=str(values["document_type"]),
                title=str(values["title"]),
                filename=filename,
                valid_from=str(values["valid_from"]),
                text=text,
            )
        )
    return tuple(documents)


def _validate_target(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in _ALLOWED_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise SyntheticFixtureLoadError(
            "synthetic fixtures may target only local/internal Legal Core"
        )
    return normalized


def _writes_allowed(environment: Mapping[str, str] | None = None) -> bool:
    source = os.environ if environment is None else environment
    return source.get("ALLOW_SYNTHETIC_CLINIC_FIXTURES", "").strip() == "1"


def _json_object(response: httpx.Response, *, operation: str) -> dict[str, Any]:
    if response.status_code not in {200, 201}:
        raise SyntheticFixtureLoadError(
            f"Legal Core rejected synthetic fixture {operation} with HTTP {response.status_code}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise SyntheticFixtureLoadError(
            f"Legal Core returned invalid JSON during synthetic fixture {operation}"
        ) from exc
    if not isinstance(payload, dict):
        raise SyntheticFixtureLoadError(
            f"Legal Core returned an invalid envelope during synthetic fixture {operation}"
        )
    return payload


def load_synthetic_fixture_pack(
    *,
    telegram_user_id: int,
    base_url: str,
    client: httpx.Client | None = None,
    environment: Mapping[str, str] | None = None,
) -> tuple[SyntheticLoadResult, ...]:
    """Seed all fixtures through tenant-authenticated Legal Core APIs.

    Replays are safe because document creation, content-identical text versions and matching latest
    approval events are idempotent in Legal Core.
    """

    if telegram_user_id <= 0:
        raise ValueError("telegram_user_id must be positive")
    if not _writes_allowed(environment):
        raise SyntheticFixtureLoadError(
            "set ALLOW_SYNTHETIC_CLINIC_FIXTURES=1 before applying synthetic fixtures"
        )
    target = _validate_target(base_url)
    documents = _load_documents()
    headers = {"X-Telegram-User-Id": str(telegram_user_id)}
    if client is not None:
        injected_target = str(client.base_url).rstrip("/")
        if _validate_target(injected_target) != target:
            raise SyntheticFixtureLoadError("injected HTTP client does not match guarded target")
    owns_client = client is None
    http = client or httpx.Client(
        base_url=target,
        timeout=30.0,
        follow_redirects=False,
        trust_env=False,
    )
    try:
        actor = _json_object(
            http.get("/v1/actor", headers=headers),
            operation="actor check",
        )
        if not actor.get("clinicId"):
            raise SyntheticFixtureLoadError("Legal Core actor response has no clinic identity")

        results: list[SyntheticLoadResult] = []
        for document in documents:
            created = _json_object(
                http.post(
                    "/v1/clinic-documents",
                    headers=headers,
                    json={
                        "documentKey": document.document_key,
                        "documentType": document.document_type,
                        "title": document.title,
                    },
                ),
                operation=f"document create {document.document_key}",
            )
            try:
                document_id = UUID(str(created["id"]))
            except (KeyError, ValueError) as exc:
                raise SyntheticFixtureLoadError("Legal Core returned an invalid document id") from exc

            version = _json_object(
                http.post(
                    f"/v1/clinic-documents/{document_id}/text-versions",
                    headers=headers,
                    json={
                        "sourceFilename": document.filename,
                        "normalizedText": document.text,
                        "validFrom": document.valid_from,
                    },
                ),
                operation=f"version create {document.document_key}",
            )
            try:
                version_id = UUID(str(version["id"]))
                version_no = int(version["versionNo"])
            except (KeyError, TypeError, ValueError) as exc:
                raise SyntheticFixtureLoadError("Legal Core returned invalid version metadata") from exc

            approval = _json_object(
                http.post(
                    f"/v1/clinic-documents/versions/{version_id}/approval-events",
                    headers=headers,
                    json={"decision": "APPROVED", "reasonCode": _APPROVAL_REASON},
                ),
                operation=f"approval {document.document_key}",
            )
            decision = approval.get("decision")
            if decision != "APPROVED":
                raise SyntheticFixtureLoadError("synthetic fixture approval was not confirmed")
            results.append(
                SyntheticLoadResult(
                    document_key=document.document_key,
                    document_id=document_id,
                    version_id=version_id,
                    version_no=version_no,
                    approval_decision="APPROVED",
                )
            )
        return tuple(results)
    except httpx.HTTPError as exc:
        raise SyntheticFixtureLoadError("synthetic fixture Legal Core request failed") from exc
    finally:
        if owns_client:
            http.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Load synthetic Test-Dent Clinic Documents into a local development tenant"
    )
    parser.add_argument("--telegram-user-id", required=True, type=int)
    parser.add_argument(
        "--base-url",
        default=os.getenv("LEGAL_CORE_URL", "http://127.0.0.1:8000"),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform writes; without this flag the command is a dry-run manifest check",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    documents = _load_documents()
    target = _validate_target(args.base_url)
    if not args.apply:
        print(f"Dry run: {len(documents)} synthetic documents are valid for {target}")
        print("No writes performed. Add --apply and ALLOW_SYNTHETIC_CLINIC_FIXTURES=1 to seed them.")
        return
    results = load_synthetic_fixture_pack(
        telegram_user_id=args.telegram_user_id,
        base_url=target,
    )
    print(f"Synthetic fixture pack applied: {len(results)} documents")
    for result in results:
        print(f"- {result.document_key}: v{result.version_no} {result.approval_decision}")


if __name__ == "__main__":
    main()
