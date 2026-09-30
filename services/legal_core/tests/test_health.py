import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient
from legal_core import main
from legal_core.main import create_app

ReadinessProbe = Callable[[], Awaitable[dict[str, bool]]]


def client_with_probe(probe: ReadinessProbe) -> TestClient:
    return TestClient(create_app(readiness_probe=probe, enable_draft_retention=False))


def test_live_reports_service_identity() -> None:
    async def unused_probe() -> dict[str, bool]:
        raise AssertionError("liveness must not call dependency probes")

    with client_with_probe(unused_probe) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "legal-core",
        "version": "0.1.0",
    }


def test_ready_returns_200_when_all_dependencies_are_available() -> None:
    async def available_dependencies() -> dict[str, bool]:
        return {"postgres": True, "redis": True, "object_storage": True}

    with client_with_probe(available_dependencies) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"postgres": True, "redis": True, "object_storage": True},
    }


def test_ready_returns_503_and_failed_checks_when_a_dependency_is_unavailable() -> None:
    async def unavailable_dependency() -> dict[str, bool]:
        return {"postgres": True, "redis": False, "object_storage": True}

    with client_with_probe(unavailable_dependency) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {"postgres": True, "redis": False, "object_storage": True},
    }


def test_openapi_is_generated_but_not_exposed_without_authentication() -> None:
    async def available_dependencies() -> dict[str, bool]:
        return {"postgres": True, "redis": True, "object_storage": True}

    app = create_app(readiness_probe=available_dependencies, enable_draft_retention=False)

    assert {
        "/health/live",
        "/health/ready",
        "/v1/cases",
        "/v1/telegram-case-workflows/{workflow_id}",
        "/v1/telegram-case-workflows/{workflow_id}/submissions",
    } <= set(app.openapi()["paths"])
    with TestClient(app) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 404


def test_runtime_readiness_executes_a_database_query_under_the_application_role() -> None:
    executed: list[str] = []

    class Session:
        async def execute(self, statement: object) -> None:
            executed.append(str(statement))

    @asynccontextmanager
    async def sessions() -> AsyncIterator[Session]:
        yield Session()

    assert asyncio.run(main._probe_postgres(sessions))  # type: ignore[arg-type]
    assert executed == ["SELECT 1"]


def test_runtime_readiness_requires_a_redis_pong(monkeypatch) -> None:
    writes: list[bytes] = []

    class Reader:
        async def readuntil(self, delimiter: bytes) -> bytes:
            assert delimiter == b"\r\n"
            return b"+PONG\r\n"

    class Writer:
        def write(self, value: bytes) -> None:
            writes.append(value)

        async def drain(self) -> None:
            return None

        def close(self) -> None:
            return None

        async def wait_closed(self) -> None:
            return None

    async def connection(host: str, port: int) -> tuple[Reader, Writer]:
        assert (host, port) == ("redis", 6379)
        return Reader(), Writer()

    monkeypatch.setattr(main.asyncio, "open_connection", connection)

    assert asyncio.run(main._probe_redis())
    assert writes == [b"*1\r\n$4\r\nPING\r\n"]


def test_runtime_readiness_requires_authenticated_object_storage_access(monkeypatch) -> None:
    calls = 0

    class Storage:
        async def probe(self) -> bool:
            nonlocal calls
            calls += 1
            return True

    monkeypatch.setattr(main, "minio_store_from_environment", lambda: Storage())

    assert asyncio.run(main._probe_object_storage())
    assert calls == 1


def test_runtime_readiness_reports_individual_runtime_dependency_results(monkeypatch) -> None:
    async def postgres(session_factory: object) -> bool:
        assert session_factory is sentinel
        return True

    async def redis() -> bool:
        return False

    async def storage() -> bool:
        return True

    sentinel: Any = SimpleNamespace()
    monkeypatch.setattr(main, "_probe_postgres", postgres)
    monkeypatch.setattr(main, "_probe_redis", redis)
    monkeypatch.setattr(main, "_probe_object_storage", storage)

    assert asyncio.run(main.probe_dependencies(sentinel)) == {
        "postgres": True,
        "redis": False,
        "object_storage": True,
    }
