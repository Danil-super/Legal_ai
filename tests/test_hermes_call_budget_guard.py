from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = spec_from_file_location("hermes_tool_free_guard", ROOT / "ops/hermes/assert_tool_free.py")
assert spec is not None and spec.loader is not None
guard = module_from_spec(spec)
spec.loader.exec_module(guard)


def config(model="synthetic-model"):
    return {"model": {"provider": "custom", "default": model}, "agent": {"api_max_retries": 1}}


def runtime_timeouts(monkeypatch, request=29, stale=29):
    module = ModuleType("hermes_cli.timeouts")

    def request_timeout(provider, model):
        assert provider == "custom" and model == "synthetic-model"
        return request

    def stale_timeout(provider, model):
        assert provider == "custom" and model == "synthetic-model"
        return stale

    module.get_provider_request_timeout = request_timeout
    module.get_provider_stale_timeout = stale_timeout
    monkeypatch.setitem(sys.modules, "hermes_cli.timeouts", module)


def test_guard_accepts_only_the_resolved_pinned_call_budget(monkeypatch):
    runtime_timeouts(monkeypatch)
    assert guard.assert_call_budget(config()) == {
        "apiAttemptsPerCycle": 1,
        "requestOperationTimeoutSeconds": 29,
        "configuredStaleTimeoutSeconds": 29,
    }


@pytest.mark.parametrize(("request_limit", "stale"), [
    (None, 29), (1800, 29), (29, None), (29, 300), (True, 29),
    (29, float("nan")), (29, 29.01),
])
def test_guard_rejects_missing_or_divergent_resolved_timeouts(monkeypatch, request_limit, stale):
    runtime_timeouts(monkeypatch, request=request_limit, stale=stale)
    with pytest.raises(SystemExit, match="call budget"):
        guard.assert_call_budget(config())


@pytest.mark.parametrize("attempts", [None, 0, 2, 3, True, "1"])
def test_guard_rejects_extra_attempt_cycles(monkeypatch, attempts):
    runtime_timeouts(monkeypatch)
    candidate = config()
    candidate["agent"]["api_max_retries"] = attempts
    with pytest.raises(SystemExit, match="call budget"):
        guard.assert_call_budget(candidate)


def test_guard_rejects_unreviewed_provider_without_printing_values(monkeypatch):
    runtime_timeouts(monkeypatch)
    candidate = config()
    candidate["model"]["provider"] = "SYNTHETIC_PRIVATE_CONFIG_DETAIL"
    with pytest.raises(SystemExit, match="call budget") as caught:
        guard.assert_call_budget(candidate)
    assert "SYNTHETIC_PRIVATE_CONFIG_DETAIL" not in str(caught.value)


def test_guard_loader_failure_is_closed_and_sanitized(monkeypatch):
    runtime_timeouts(monkeypatch)

    def failing_loader(provider, model):
        raise RuntimeError("SYNTHETIC_PRIVATE_CONFIG_DETAIL")

    module = sys.modules["hermes_cli.timeouts"]
    monkeypatch.setattr(module, "get_provider_request_timeout", failing_loader)
    with pytest.raises(SystemExit, match="call budget") as caught:
        guard.assert_call_budget(config())
    assert "SYNTHETIC_PRIVATE_CONFIG_DETAIL" not in str(caught.value)
    assert caught.value.__suppress_context__
