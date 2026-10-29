"""A trusted operator can increase one clinic's quota without changing other tenants."""

from uuid import uuid4

import pytest

from legal_core.clinic_case_quota import monthly_case_limit

CLINIC_KEY = "CASE_MONTHLY_LIMIT_OVERRIDE_CLINIC_ID"
VALUE_KEY = "CASE_MONTHLY_LIMIT_OVERRIDE_VALUE"


def test_default_limit_is_unchanged_without_an_operator_override(monkeypatch):
    monkeypatch.delenv(CLINIC_KEY, raising=False)
    monkeypatch.delenv(VALUE_KEY, raising=False)
    assert monthly_case_limit(uuid4(), default_limit=30) == 30


def test_override_only_applies_to_the_exact_server_resolved_clinic(monkeypatch):
    clinic = uuid4()
    monkeypatch.setenv(CLINIC_KEY, str(clinic))
    monkeypatch.setenv(VALUE_KEY, "100")
    assert monthly_case_limit(clinic, default_limit=30) == 100
    assert monthly_case_limit(uuid4(), default_limit=30) == 30


@pytest.mark.parametrize("clinic,value", [
    ("", "100"), (str(uuid4()), ""), ("not-a-clinic", "100"),
    ("00000000-0000-0000-0000-000000000000", "100"), (uuid4().hex, "100"),
    (str(uuid4()), "0"), (str(uuid4()), "1001"), (str(uuid4()), "+100"),
    (str(uuid4()), "0100"), (str(uuid4()), "1e2"), (str(uuid4()), "true"),
])
def test_partial_or_malformed_override_never_opens_an_unlimited_quota(
    monkeypatch, clinic, value,
):
    monkeypatch.setenv(CLINIC_KEY, clinic)
    monkeypatch.setenv(VALUE_KEY, value)
    with pytest.raises(ValueError, match="Invalid clinic case quota override"):
        monthly_case_limit(uuid4(), default_limit=30)
