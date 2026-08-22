from collections.abc import Awaitable, Callable

from fastapi.testclient import TestClient
from legal_core.main import create_app

ReadinessProbe = Callable[[], Awaitable[dict[str, bool]]]


def client_with_probe(probe: ReadinessProbe) -> TestClient:
    return TestClient(create_app(readiness_probe=probe))


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

    app = create_app(readiness_probe=available_dependencies)

    assert {"/health/live", "/health/ready", "/v1/cases"} <= set(app.openapi()["paths"])
    with TestClient(app) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 404
