"""Explicit offline preparation-package import; never performs human approval."""

import argparse
import asyncio
import json
import os
import stat
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.contracts import ContractModel
from legal_core.database import create_engine, create_session_factory
from legal_core.material_preparation import MaterialPreparationInput, store_preparation
from legal_core.models import LegalReviewMaterial


class PreparationPackageItem(ContractModel):
    original_filename: str = Field(min_length=1, max_length=240)
    preparation_path: str = Field(min_length=1, max_length=240)
    text_path: str | None = Field(default=None, min_length=1, max_length=240)

    @field_validator("original_filename", "preparation_path", "text_path")
    @classmethod
    def filename_only(cls, value: str | None) -> str | None:
        if value is not None and (
            value in {".", ".."} or not value.strip()
            or any(char in value for char in ("/", "\\", "\x00"))
        ):
            raise ValueError("only package-local filenames are allowed")
        return value


class PreparationPackage(ContractModel):
    schema_version: Literal[1]
    package_key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    items: list[PreparationPackageItem] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique_originals(self) -> "PreparationPackage":
        if len({item.original_filename for item in self.items}) != len(self.items):
            raise ValueError("duplicate original filename")
        return self


def _read_regular(root: Path, name: str, limit: int) -> bytes:
    PreparationPackageItem.filename_only(name)
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=directory)
        except OSError as exc:
            raise ValueError("package input must be a regular file") from exc
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise ValueError("package input must be a bounded regular file")
            content = stream.read(limit + 1)
            if len(content) > limit:
                raise ValueError("package input exceeds limit")
            return content
    finally:
        os.close(directory)


def read_preparation_package(
    path: Path,
) -> tuple[PreparationPackage, list[tuple[str, MaterialPreparationInput]]]:
    root = path.parent
    manifest = PreparationPackage.model_validate_json(_read_regular(root, path.name, 256_000))
    candidates = []
    seen = set()
    total = 0
    for item in manifest.items:
        metadata = json.loads(_read_regular(root, item.preparation_path, 256_000))
        if not isinstance(metadata, dict) or "normalized_text" in metadata:
            raise ValueError("preparation metadata must not contain inline document text")
        if item.text_path is not None:
            content = _read_regular(root, item.text_path, 50_000_000)
            total += len(content)
            if total > 80_000_000:
                raise ValueError("preparation package text exceeds limit")
            metadata["normalized_text"] = content.decode("utf-8")
        candidate = MaterialPreparationInput.model_validate(metadata)
        if candidate.raw_sha256 in seen:
            raise ValueError("duplicate original checksum")
        seen.add(candidate.raw_sha256)
        candidates.append((item.original_filename, candidate))
    return manifest, candidates


async def ingest_preparation_package(
    factory: async_sessionmaker[AsyncSession], path: Path,
) -> list[UUID]:
    manifest, candidates = read_preparation_package(path)
    async with factory() as session, session.begin():
        originals = (await session.execute(select(
            LegalReviewMaterial.id, LegalReviewMaterial.original_filename,
            LegalReviewMaterial.raw_sha256,
        ).where(LegalReviewMaterial.package_key == manifest.package_key)
          .order_by(LegalReviewMaterial.id).with_for_update())).all()
        by_name = {row.original_filename: row for row in originals}
        if len(by_name) != len(originals) or set(by_name) != {name for name, _ in candidates}:
            raise ValueError("preparation package must account for every original receipt")
        prepared = []
        for name, candidate in candidates:
            original = by_name[name]
            if original.raw_sha256 != candidate.raw_sha256:
                raise ValueError("preparation does not match original receipt")
            prepared.append((await store_preparation(session, original.id, candidate)).id)
        return prepared


async def _run(path: Path, validate_only: bool) -> None:
    if validate_only:
        _, candidates = read_preparation_package(path)
        print(f"validated {len(candidates)} unapproved preparations; no database writes")
        return
    engine = create_engine()
    try:
        ids = await ingest_preparation_package(create_session_factory(engine), path)
    finally:
        await engine.dispose()
    print(f"stored {len(ids)} unapproved preparations; no legal approval performed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    asyncio.run(_run(args.manifest, args.validate_only))


if __name__ == "__main__":
    main()
