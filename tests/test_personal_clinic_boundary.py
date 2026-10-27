"""Freeze the visible clinic surface while personal work remains hidden.

Changing this baseline requires a separately reviewed clinic-interface decision.
Hashes are Git blob identifiers, not cryptographic authorization or password checks.
"""

import hashlib
from pathlib import Path

import pytest
from fastapi import APIRouter
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
CLINIC_BASELINE = {
    "services/gateway/telegram/src/telegram_gateway/__main__.py":
        "dc29188aba35fcd386a7b132f0c4a012026497ae",
    "services/gateway/telegram/src/telegram_gateway/ui.py":
        # Reviewed owner-requested single clinical case entry; personal stays hidden (ADR 0099).
        "c9adedf6e5711e244be6f7a569e1b721c6e5068d",
    "services/gateway/telegram/src/telegram_gateway/case_experience_runtime.py":
        # Independently reviewed owner-requested single-summary clinical dialogue;
        # the existing validated flag stays off by default, personal remains inert.
        "a129dc9d49206572020b549418fa55dd3916f417",
    "services/legal_core/src/legal_core/main.py":
        # Reviewed owner-approved clinic preparation wiring; personal remains inert (ADR 0091).
        "a37582ab864227bdcccae17b6a21acb99b385c14",
    # Separately reviewed clinical automation pause; no personal surface change (ADR 0064).
    # Reviewed Gateway-only intake flag pass-through; defaults off (ADR 0091).
    # Owner-approved clinical multi-fact intake flag; defaults off (ADR0101).
    # Personal entry points, permissions and isolation checks remain frozen.
    "docker-compose.yml": "5624726f574fd1c2566f30e3b324fd294b3a55db",
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


def _assert_clinical_understanding_routes(app):
    # Inspect actual registrations, not just OpenAPI: include_in_schema=False hides
    # documentation, but does not unmount a route (FastAPI reference via Context7).
    # FastAPI >=0.137 retains live included-router containers, not a flat route list.
    pending = [("", route) for route in app.routes]
    paths = set()
    while pending:
        prefix, route = pending.pop()
        if isinstance(route, APIRoute):
            path = prefix + route.path
            assert "/personal" not in path, "Personal route registered in clinical composition"
            assert not route.endpoint.__module__.startswith("legal_core.personal")
            paths.add(path)
        else:
            included = getattr(route, "original_router", None)
            assert included is not None, f"Uninspected route container: {type(route).__name__}"
            context = getattr(route, "include_context", None)
            include_prefix = getattr(context, "prefix", None)
            assert isinstance(include_prefix, str), "Uninspected router inclusion prefix"
            effective_prefix = prefix + include_prefix
            assert "/personal" not in effective_prefix, "Personal router included in clinic"
            pending.extend((effective_prefix, child) for child in included.routes)
    assert {
        "/v1/telegram-intake-drafts/{draft_id}/understanding",
        "/v1/intake-preparations/{operation_id}",
        "/v1/intake-preparations/{operation_id}/context",
    } <= paths


def test_understanding_composition_stays_clinical_even_when_personal_preview_is_requested(
    monkeypatch,
):
    from legal_core.main import create_app

    monkeypatch.setenv("PERSONAL_PREVIEW_MODE", "synthetic")
    monkeypatch.delenv("PERSONAL_PREVIEW_API_KEY", raising=False)
    app = create_app(enable_draft_retention=False, enable_analysis_worker=False)
    _assert_clinical_understanding_routes(app)
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        assert app.state.intake_preparation_runner is None


@pytest.mark.parametrize("nested", [False, True])
def test_route_isolation_canary_rejects_hidden_personal_include_prefix(nested):
    from legal_core.main import create_app

    app = create_app(enable_draft_retention=False, enable_analysis_worker=False)
    hidden = APIRouter()

    @hidden.get("/hidden-review-only", include_in_schema=False)
    async def fictional_hidden_route():
        return {"synthetic": True}

    if nested:
        parent = APIRouter()
        parent.include_router(hidden, prefix="/nested")
        hidden = parent
    app.include_router(hidden, prefix="/v1/personal-preview")
    path = "/v1/personal-preview" + ("/nested" if nested else "") + "/hidden-review-only"
    assert path not in app.openapi()["paths"]
    with TestClient(app) as client:
        assert client.get(path).status_code == 200  # Prove a real hidden registration.
    with pytest.raises(AssertionError):
        _assert_clinical_understanding_routes(app)


@pytest.mark.parametrize("enable_understanding,enable_answers,enable_dialogue", [
    (False, False, False), (True, False, False), (True, True, False), (True, True, True),
])
def test_clinic_composition_registers_no_personal_handlers(
    monkeypatch, enable_understanding, enable_answers, enable_dialogue,
):
    from telegram.ext import CallbackQueryHandler, CommandHandler, ConversationHandler
    from telegram_gateway.case_experience_runtime import build_application_with_case_experience

    monkeypatch.setenv("PERSONAL_PREVIEW_MODE", "synthetic")
    app = build_application_with_case_experience(
        "123456:" + "q" * 40, enable_understanding=enable_understanding,
        enable_answer_interpretation=enable_answers,
        enable_simplified_dialogue=enable_dialogue,
    )
    assert app.bot_data["intake_simplified_dialogue"] is enable_dialogue
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


def test_clinic_default_factory_does_not_install_opt_in_understanding(monkeypatch):
    from unittest.mock import Mock

    from telegram_gateway import preparation_form_runtime, understanding_runtime
    from telegram_gateway.case_experience_runtime import build_application_with_case_experience

    fail = Mock(side_effect=AssertionError("ordinary factory must keep understanding disabled"))
    monkeypatch.setattr(preparation_form_runtime, "install_preparation_form", fail)
    monkeypatch.setattr(understanding_runtime, "install_understanding", fail)
    monkeypatch.setenv("PERSONAL_PREVIEW_MODE", "synthetic")
    build_application_with_case_experience("123456:" + "q" * 40)
    fail.assert_not_called()
