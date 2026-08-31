import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from legal_core.legal_watcher import (
    PORTAL_PAGE_SIZE,
    WatchManifest,
    load_watch_manifest,
    stage_official_publications,
)
from legal_core.pravo_source import PravoDocumentHit, PravoPdfArtifact


EO_NUMBER = "0001202606010083"
ROOT = Path(__file__).parents[3]


def _hit() -> PravoDocumentHit:
    return PravoDocumentHit(
        eo_number=EO_NUMBER,
        title="Постановление о правилах платных медицинских услуг",
        publication_date=date(2026, 8, 31),
        document_number="659",
        document_date=date(2026, 8, 30),
        pdf_length=len(b"%PDF-1.7\nreview candidate"),
    )


def _manifest() -> WatchManifest:
    return WatchManifest.model_validate(
        {
            "manifest_version": "dental-legal-watch.v1",
            "rules": [
                {
                    "rule_id": "paid-medical-text",
                    "search_mode": "DOCUMENT_TEXT",
                    "query": "платных медицинских услуг",
                    "required_title_terms": ["медицинских услуг"],
                },
                {
                    "rule_id": "paid-medical-name",
                    "search_mode": "NAME",
                    "query": "правила платных медицинских услуг",
                    "required_title_terms": ["платных"],
                },
            ],
        }
    )


class FakeSource:
    def __init__(self, *, content: bytes = b"%PDF-1.7\nreview candidate") -> None:
        self.content = content
        self.discover_calls: list[tuple[str | None, str | None]] = []
        self.fetch_calls: list[str] = []

    async def discover(
        self,
        *,
        publication_from: date,
        publication_to: date,
        page: int = 1,
        page_size: int = 100,
        name: str | None = None,
        document_text: str | None = None,
    ) -> tuple[PravoDocumentHit, ...]:
        assert publication_from == date(2026, 8, 31)
        assert publication_to == date(2026, 8, 31)
        assert page == 1
        assert page_size == PORTAL_PAGE_SIZE
        self.discover_calls.append((name, document_text))
        return (_hit(),)

    async def fetch_pdf(self, eo_number: str) -> PravoPdfArtifact:
        assert eo_number == EO_NUMBER
        self.fetch_calls.append(eo_number)
        import hashlib

        return PravoPdfArtifact(
            eo_number=eo_number,
            source_url=f"https://publication.pravo.gov.ru/File/Pdf?eoNumber={eo_number}",
            content=self.content,
            sha256=hashlib.sha256(self.content).hexdigest(),
        )


def test_watcher_deduplicates_rules_and_stages_immutable_review_candidate(tmp_path: Path) -> None:
    async def scenario() -> None:
        source = FakeSource()
        first = await stage_official_publications(
            source,
            manifest=_manifest(),
            publication_from=date(2026, 8, 31),
            publication_to=date(2026, 8, 31),
            inbox=tmp_path,
            staged_at=datetime(2026, 8, 31, 12, 0, tzinfo=UTC),
        )
        replay = await stage_official_publications(
            source,
            manifest=_manifest(),
            publication_from=date(2026, 8, 31),
            publication_to=date(2026, 8, 31),
            inbox=tmp_path,
            staged_at=datetime(2026, 8, 31, 13, 0, tzinfo=UTC),
        )

        assert len(first) == 1
        assert first[0].created is True
        assert replay[0].created is False
        assert source.fetch_calls == [EO_NUMBER, EO_NUMBER]
        assert source.discover_calls == [
            (None, "платных медицинских услуг"),
            ("правила платных медицинских услуг", None),
            (None, "платных медицинских услуг"),
            ("правила платных медицинских услуг", None),
        ]

        directory = tmp_path / EO_NUMBER
        assert (directory / "official.pdf").read_bytes().startswith(b"%PDF-")
        metadata = json.loads((directory / "candidate.json").read_text(encoding="utf-8"))
        assert metadata["status"] == "REVIEW_REQUIRED"
        assert metadata["autoPromotionAllowed"] is False
        assert metadata["matchedRuleIds"] == ["paid-medical-name", "paid-medical-text"]
        assert len(metadata["pdfSha256"]) == 64

    asyncio.run(scenario())


def test_watcher_refuses_changed_bytes_for_the_same_official_identity(tmp_path: Path) -> None:
    async def scenario() -> None:
        await stage_official_publications(
            FakeSource(),
            manifest=_manifest(),
            publication_from=date(2026, 8, 31),
            publication_to=date(2026, 8, 31),
            inbox=tmp_path,
            staged_at=datetime.now(UTC),
        )
        with pytest.raises(FileExistsError, match="refusing to overwrite different quarantine"):
            await stage_official_publications(
                FakeSource(content=b"%PDF-1.7\nreview candidatf"),
                manifest=_manifest(),
                publication_from=date(2026, 8, 31),
                publication_to=date(2026, 8, 31),
                inbox=tmp_path,
                staged_at=datetime.now(UTC) + timedelta(minutes=1),
            )

    asyncio.run(scenario())


def test_watcher_fails_closed_when_a_reviewed_query_saturates_a_portal_page(
    tmp_path: Path,
) -> None:
    class SaturatedSource(FakeSource):
        async def discover(self, **kwargs):
            return tuple(_hit() for _ in range(PORTAL_PAGE_SIZE))

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match="saturated one portal page"):
            await stage_official_publications(
                SaturatedSource(),
                manifest=_manifest(),
                publication_from=date(2026, 8, 31),
                publication_to=date(2026, 8, 31),
                inbox=tmp_path,
                staged_at=datetime.now(UTC),
            )

    asyncio.run(scenario())


def test_watch_manifest_rejects_duplicate_rule_ids() -> None:
    payload = {
        "manifest_version": "dental-legal-watch.v1",
        "rules": [
            {
                "rule_id": "duplicate-rule",
                "search_mode": "NAME",
                "query": "платные медицинские услуги",
            },
            {
                "rule_id": "duplicate-rule",
                "search_mode": "DOCUMENT_TEXT",
                "query": "медицинские услуги",
            },
        ],
    }
    with pytest.raises(ValueError, match="watch rule IDs must be unique"):
        WatchManifest.model_validate(payload)


def test_repository_watch_manifest_tracks_the_approved_scope_groups() -> None:
    manifest = load_watch_manifest(
        ROOT / "services/legal_core/corpus/legal_watch_rules.v1.json"
    )
    assert {rule.rule_id for rule in manifest.rules} == {
        "paid-medical-services",
        "health-protection-323-fz",
        "consumer-protection-2300-1",
        "civil-code-paid-services",
        "personal-data-152-fz",
    }
    assert all(rule.search_mode == "DOCUMENT_TEXT" for rule in manifest.rules)
    assert all(1 <= rule.max_hits <= 20 for rule in manifest.rules)
