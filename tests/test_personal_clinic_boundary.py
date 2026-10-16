"""Freeze the visible clinic surface while personal work remains hidden.

Changing this baseline requires a separately reviewed clinic-interface decision.
Hashes are Git blob identifiers, not cryptographic authorization or password checks.
"""

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fastapi.routing import APIRoute

ROOT = Path(__file__).resolve().parents[1]
CLINIC_BASELINE = {
    "services/gateway/telegram/src/telegram_gateway/__main__.py":
        "dc29188aba35fcd386a7b132f0c4a012026497ae",
    "services/gateway/telegram/src/telegram_gateway/ui.py":
        # Owner-requested clinic library unification; personal surface unchanged (ADR 0080).
        "da2a396c379d2cfef32c4d3b2c16160d534a83b2",
    "services/gateway/telegram/src/telegram_gateway/case_experience_runtime.py":
        "19f6371c3507b844c54fa8308a97cabfafe292cd",
    "services/legal_core/src/legal_core/main.py":
        # Reviewed owner-approved clinic preparation wiring; personal remains inert (ADR 0091).
        "a37582ab864227bdcccae17b6a21acb99b385c14",
    # Separately reviewed clinical automation pause; no personal surface change (ADR 0064).
    "docker-compose.yml": "c50d5c9ccd748eebed9bf2240489c898c1c0b5ef",
    "ops/deploy/docker-compose.production.yml": "9d07d50c67787a0da7a13fcfc49907d550298b83",
}


@pytest.mark.parametrize("path,expected", list(CLINIC_BASELINE.items()))
def test_existing_clinic_surface_and_startup_not_modified(path, expected):
    content = (ROOT / path).read_bytes()
    git_blob = b"blob " + str(len(content)).encode("ascii") + b"\0" + content
    assert hashlib.sha1(git_blob, usedforsecurity=False).hexdigest() == expected


def test_personal_flag_never_mounts_private_routes_in_clinic_app(monkeypatch):
    from legal_core.main import create_app

    monkeypatch.setenv("PERSONAL_PREVIEW_MODE", "synthetic")
    # Missing preview credentials must not even be read by the clinic entrypoint.
    monkeypatch.delenv("PERSONAL_PREVIEW_API_KEY", raising=False)
    app = create_app(enable_draft_retention=False, enable_analysis_worker=False)
    with TestClient(app) as client:
        for path in ["/v1/personal-preview/catalog", "/v1/personal-cases",
                     "/v1/personal-preview/scenarios/employee_pay"]:
            assert client.get(path).status_code == 404
            assert client.post(path, json={}).status_code == 404
        assert client.get("/health/live").status_code == 200


def test_understanding_composition_stays_clinical_even_when_personal_preview_is_requested(
    monkeypatch,
):
    from legal_core.main import create_app

    monkeypatch.setenv("PERSONAL_PREVIEW_MODE", "synthetic")
    monkeypatch.delenv("PERSONAL_PREVIEW_API_KEY", raising=False)
    app = create_app(enable_draft_retention=False, enable_analysis_worker=False)
    # Inspect actual registrations, not just OpenAPI: include_in_schema=False hides
    # documentation, but does not unmount a route (FastAPI reference via Context7).
    routes = [route for route in app.routes if isinstance(route, APIRoute)]
    assert {
        "/v1/telegram-intake-drafts/{draft_id}/understanding",
        "/v1/intake-preparations/{operation_id}",
        "/v1/intake-preparations/{operation_id}/context",
    } <= {route.path for route in routes}
    assert all("/personal" not in route.path for route in routes)
    assert all(not route.endpoint.__module__.startswith("legal_core.personal") for route in routes)
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        assert app.state.intake_preparation_runner is None


def test_clinic_composition_registers_no_personal_handlers(monkeypatch):
    from telegram.ext import CallbackQueryHandler, CommandHandler, ConversationHandler
    from telegram_gateway.case_experience_runtime import build_application_with_case_experience

    monkeypatch.setenv("PERSONAL_PREVIEW_MODE", "synthetic")
    app = build_application_with_case_experience("123456:" + "q" * 40)
    checked = 0

    def inspect_handler(handler):
        nonlocal checked
        if isinstance(handler, ConversationHandler):
            # Conversations are containers, not leaf callbacks. Inspect every nested
            # entry, state and fallback so the test also detects hidden registration.
            for child in [*handler.entry_points, *handler.fallbacks]:
                inspect_handler(child)
            for group in handler.states.values():
                for child in group:
                    inspect_handler(child)
            return
        checked += 1
        assert "personal_preview" not in handler.callback.__module__
        if isinstance(handler, CommandHandler):
            assert not any("personal" in command for command in handler.commands)
        if isinstance(handler, CallbackQueryHandler) and handler.pattern is not None:
            assert not handler.pattern.match("pp:audience:PATIENT")

    for group in app.handlers.values():
        for handler in group:
            inspect_handler(handler)
    assert checked > 20
