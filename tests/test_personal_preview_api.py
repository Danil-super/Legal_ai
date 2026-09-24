"""The hidden surface is absent by default and never accepts personal cases."""

import secrets

import pytest
from fastapi.testclient import TestClient

from legal_core.personal.catalog import CATALOG
from legal_core.personal.preview_api import create_app
from legal_core.personal.settings import PreviewMode, PreviewSettings


def enabled():
    return PreviewSettings(PreviewMode.SYNTHETIC, frozenset({101}), secrets.token_urlsafe(32))


def headers(config):
    return {"Authorization": f"Bearer {config.api_key}", "X-Personal-Preview-Tester": "101"}


@pytest.mark.parametrize("path", ["/v1/personal-preview/catalog",
                                  "/v1/personal-preview/scenarios/patient_documents",
                                  "/docs", "/openapi.json", "/redoc", "/v1/personal-cases"])
def test_default_has_no_registered_personal_routes_or_docs(path, monkeypatch):
    monkeypatch.delenv("PERSONAL_PREVIEW_MODE", raising=False)
    with TestClient(create_app()) as client:
        response = client.get(path)
        assert response.status_code == 404
        assert response.headers["Cache-Control"] == "no-store"
        assert "PERSONAL" not in response.text
        assert client.get("/health/live").json()["mode"] == "off"


@pytest.mark.parametrize("auth", [None, "Bearer wrong", "Basic abc", "Bearer " + "a" * 1000])
def test_user_header_alone_is_never_authority(auth):
    config = enabled()
    request_headers = {"X-Personal-Preview-Tester": "101"}
    if auth is not None:
        request_headers["Authorization"] = auth
    with TestClient(create_app(config)) as client:
        result = client.get("/v1/personal-preview/catalog", headers=request_headers)
        assert result.status_code == 404


@pytest.mark.parametrize("actor", ["", "102", "-101", "0", "101,102", "1" * 1000, "101.0"])
def test_allowlist_checked_after_gateway_credential(actor):
    config = enabled()
    with TestClient(create_app(config)) as client:
        result = client.get("/v1/personal-preview/catalog", headers={
            **headers(config), "X-Personal-Preview-Tester": actor,
        })
        assert result.status_code == 404


@pytest.mark.parametrize("name", ["Authorization", "X-Personal-Preview-Tester"])
def test_duplicate_authority_headers_rejected(name):
    config = enabled()
    values = list(headers(config).items())
    values.append((name, headers(config)[name]))
    with TestClient(create_app(config)) as client:
        assert client.get("/v1/personal-preview/catalog", headers=values).status_code == 404


def test_catalog_and_all_examples_are_read_only_fixed_and_unavailable_for_analysis():
    config = enabled()
    with TestClient(create_app(config)) as client:
        result = client.get("/v1/personal-preview/catalog", headers=headers(config))
        assert result.status_code == 200
        assert result.json()["live_analysis"] is False
        assert len(result.json()["topics"]) == 6
        assert config.api_key not in result.text
        assert "101" not in result.text
        for card in CATALOG:
            result = client.get(f"/v1/personal-preview/scenarios/{card.topic.value}",
                                headers=headers(config))
            assert result.status_code == 200
            assert result.json()["synthetic"] is True
            assert result.json()["analysis_status"] == "NOT_AVAILABLE"
            assert result.headers["Cache-Control"] == "no-store"
        missing = client.get("/v1/personal-preview/scenarios/not_a_case", headers=headers(config))
        assert missing.status_code == 404 and "not_a_case" not in missing.text


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("path", ["/v1/personal-preview/catalog", "/v1/personal-cases",
                                  "/v1/cases", "/v1/telegram-intake-drafts"])
def test_preview_has_no_personal_or_clinic_write_path(method, path):
    config = enabled()
    with TestClient(create_app(config)) as client:
        result = client.request(method, path, headers=headers(config), json={
            "patient_name": "SYNTHETIC_SECRET", "clinic_id": "foreign",
        })
        assert result.status_code in {404, 405}
        assert "SYNTHETIC_SECRET" not in result.text


def test_extra_query_parameters_do_not_select_clinic_tenant_or_change_static_result():
    config = enabled()
    with TestClient(create_app(config)) as client:
        base = client.get("/v1/personal-preview/catalog", headers=headers(config)).json()
        changed = client.get("/v1/personal-preview/catalog", headers={
            **headers(config), "X-Telegram-User-Id": "999", "X-Clinic-Id": "foreign",
        }, params={"clinic_id": "foreign", "raw_text": "SYNTHETIC_SECRET"}).json()
        assert changed == base
