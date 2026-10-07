"""Synthetic recovery regression; never dump a runtime/production database."""

import asyncio
import hashlib
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from legal_core.database import database_url, owner_database_url
from legal_core.material_preparation import MaterialPreparationInput, store_preparation
from legal_core.models import LegalReviewMaterial
from legal_core.runtime_db_role import provision_runtime_role

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="requires disposable PostgreSQL"
)
ROOT = Path(__file__).parents[3]
PAYLOAD = {"z": [True, None, {"я": "synthetic \"quote\"", "a": 3}], "a": {"b": []}}
CANONICAL = json.dumps(PAYLOAD, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def function_contract(connection):
    return connection.exec_driver_sql(
        "SELECT oid, proowner, proacl::text, provolatile, proparallel, proisstrict, "
        "proconfig::text FROM pg_proc WHERE oid IN "
        "('public.legal_canonical_jsonb(jsonb)'::regprocedure, "
        "'public.legal_regression_result_sha256(jsonb)'::regprocedure) ORDER BY proname"
    ).all()


@pytest.fixture
def recovery_databases(monkeypatch):
    assert os.environ["POSTGRES_DB"].startswith("dental_legal_test_")
    source = "dental_legal_test_dump_" + uuid4().hex[:16]
    target = "dental_legal_test_restore_" + uuid4().hex[:16]
    owner = create_engine(owner_database_url(), isolation_level="AUTOCOMMIT")
    try:
        with owner.connect() as connection:
            for name in (source, target):
                connection.exec_driver_sql(f'CREATE DATABASE "{name}" TEMPLATE template0')
        monkeypatch.setenv("POSTGRES_DB", source)
        provision_runtime_role()
        config = Config(str(ROOT / "alembic.ini"))
        command.upgrade(config, "a8d4e2f6b901")
        source_owner = create_engine(owner_database_url())
        try:
            with source_owner.connect() as connection:
                before = function_contract(connection)
        finally:
            source_owner.dispose()
        command.upgrade(config, "head")
        provision_runtime_role()
        yield source, target, before
    finally:
        with owner.connect() as connection:
            for name in (target, source):
                connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        owner.dispose()


def test_nested_canonical_and_hash_work_under_pg_restore_empty_search_path(recovery_databases):
    del recovery_databases
    owner = create_engine(owner_database_url())
    try:
        with owner.begin() as connection:
            connection.exec_driver_sql("SET LOCAL search_path=''")
            actual = connection.execute(text(
                "SELECT public.legal_canonical_jsonb(CAST(:p AS jsonb)), "
                "public.legal_regression_result_sha256(CAST(:p AS jsonb))"
            ), {"p": CANONICAL}).one()
            assert actual == (CANONICAL, hashlib.sha256(CANONICAL.encode()).hexdigest())
            assert connection.exec_driver_sql(
                "SELECT bool_and(provolatile='i' AND proparallel='s' AND proisstrict) "
                "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname='public' AND proname IN "
                "('legal_canonical_jsonb','legal_regression_result_sha256')"
            ).scalar() is True
    finally:
        owner.dispose()


def test_hash_migration_preserves_existing_oids_owners_acls_and_downgrade_compatibility(
    recovery_databases,
):
    before = recovery_databases[2]
    config = Config(str(ROOT / "alembic.ini"))
    owner = create_engine(owner_database_url())
    try:
        for revision in ("head", "a8d4e2f6b901", "head"):
            if revision == "head":
                command.upgrade(config, revision)
            else:
                command.downgrade(config, revision)
            with owner.begin() as connection:
                assert function_contract(connection) == before
                connection.exec_driver_sql("SET LOCAL search_path=''")
                assert connection.execute(text(
                    "SELECT public.legal_regression_result_sha256(CAST(:p AS jsonb))"
                ), {"p": CANONICAL}).scalar_one() == hashlib.sha256(CANONICAL.encode()).hexdigest()
    finally:
        owner.dispose()


def test_serial_custom_dump_restore_keeps_preparation_checks_and_hashes(recovery_databases):
    container = os.getenv("POSTGRES_TEST_CONTAINER", "")
    if not container:
        pytest.skip("set POSTGRES_TEST_CONTAINER to the disposable PostgreSQL container")
    assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", container)
    source, target, _ = recovery_databases

    async def seed():
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        raw = b"%PDF-1.7\nSynthetic recovery fixture\n%%EOF"
        sha = hashlib.sha256(raw).hexdigest()
        try:
            async with factory() as session, session.begin():
                material = LegalReviewMaterial(
                    package_key="synthetic-recovery", original_filename="synthetic.pdf",
                    title="Synthetic recovery", kind="CLINICAL_REFERENCE",
                    source_name="Synthetic", raw_mime_type="application/pdf",
                    raw_bytes=raw, raw_size_bytes=len(raw), raw_sha256=sha,
                    received_at=datetime.now(UTC),
                )
                session.add(material)
                await session.flush()
                preparation = await store_preparation(
                    session, material.id, MaterialPreparationInput(
                        raw_sha256=sha, title="Synthetic recovery", kind="CLINICAL_REFERENCE",
                        group_key="clinical", parser_version="synthetic-v1",
                        extraction_scope="NONE",
                        limitations=["Synthetic nested checksum fixture"],
                    ),
                )
                return preparation.preparation_sha256
        finally:
            await engine.dispose()

    expected_sha = asyncio.run(seed())
    command_base = ["docker", "exec", "-i", container]
    dumped = subprocess.run(
        [*command_base, "pg_dump", "-U", os.environ["POSTGRES_USER"], "-d", source, "-Fc"],
        check=False, capture_output=True, timeout=30,
    )
    if dumped.returncode:
        pytest.fail("synthetic pg_dump failed; stderr deliberately suppressed", pytrace=False)
    restored = subprocess.run(
        [*command_base, "pg_restore", "-U", os.environ["POSTGRES_USER"], "-d", target,
         "--single-transaction", "--exit-on-error", "--no-owner", "--no-privileges"],
        input=dumped.stdout, capture_output=True, check=False, timeout=30,
    )
    if restored.returncode:
        pytest.fail("synthetic standard pg_restore failed; stderr deliberately suppressed",
                    pytrace=False)
    owner = create_engine(owner_database_url().set(database=target))
    try:
        with owner.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT preparation_sha256 FROM public.legal_material_preparations"
            ).scalar_one() == expected_sha
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM public.legal_material_preparations"
            ).scalar_one() == 1
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM pg_constraint WHERE NOT convalidated"
            ).scalar_one() == 0
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM pg_index WHERE NOT indisvalid"
            ).scalar_one() == 0
    finally:
        owner.dispose()
