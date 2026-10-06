"""Freeze the visible clinic surface while personal work remains hidden.

Changing this baseline requires a separately reviewed clinic-interface decision.
Hashes are Git blob identifiers, not cryptographic authorization or password checks.
"""

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
CLINIC_BASELINE = {
    "services/gateway/telegram/src/telegram_gateway/__main__.py":
        "dc29188aba35fcd386a7b132f0c4a012026497ae",
    "services/gateway/telegram/src/telegram_gateway/ui.py":
        "484153ec54ecf8f34c34f199c70325db932e9751",
    "services/gateway/telegram/src/telegram_gateway/case_experience_runtime.py":
        "19f6371c3507b844c54fa8308a97cabfafe292cd",
    "services/legal_core/src/legal_core/main.py":
        "c14b7a37e8d65885cf009d947c8556d824c8f752",
    "docker-compose.yml": "83f8fa40abe8c98d9a5581dd446fa8f0245efd2b",
    "ops/deploy/docker-compose.production.yml": "7c2d4fd098b52f236096f06566204c6b51dfc29f",
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
