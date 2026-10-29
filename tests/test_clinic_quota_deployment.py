"""Quota overrides reach only Core in the resolved production configuration."""

import json
import os
from pathlib import Path
from subprocess import run

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLINIC_KEY = "CASE_MONTHLY_LIMIT_OVERRIDE_CLINIC_ID"
VALUE_KEY = "CASE_MONTHLY_LIMIT_OVERRIDE_VALUE"
SYNTHETIC_CLINIC = "11111111-1111-4111-8111-111111111111"


@pytest.mark.parametrize("analysis", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
def test_quota_pair_is_inert_by_default_and_reaches_only_core(analysis, enabled):
    environment = {
        "PATH": os.defpath,
        "POSTGRES_PASSWORD": "synthetic-password",
        "POSTGRES_APP_PASSWORD": "synthetic-app-password",
        "MINIO_ROOT_PASSWORD": "synthetic-storage-password",
        "TELEGRAM_BOT_TOKEN": "synthetic-token",
        "AGENT_INTERNAL_KEY": "synthetic-agent-key",
        "HERMES_LLM_BASE_URL": "https://synthetic.invalid/v1",
        "HERMES_LLM_API_KEY": "synthetic-provider-key",
        "HERMES_RESEARCHER_LLM_MODEL": "synthetic-researcher",
        "HERMES_REVIEWER_LLM_MODEL": "synthetic-reviewer",
        "HERMES_RESEARCHER_API_KEY": "synthetic-researcher-key",
        "HERMES_REVIEWER_API_KEY": "synthetic-reviewer-key",
    }
    if enabled:
        environment.update({CLINIC_KEY: SYNTHETIC_CLINIC, VALUE_KEY: "100"})
    arguments = ["docker", "compose", "--env-file", os.devnull,
                 "-f", str(ROOT / "docker-compose.yml"),
                 "-f", str(ROOT / "ops/deploy/docker-compose.production.yml")]
    if analysis:
        arguments.extend(["-f", str(ROOT / "ops/hermes/docker-compose.hermes.yml"),
                          "--profile", "analysis"])
    result = run([*arguments, "config", "--format", "json"], env=environment,
                 cwd=ROOT, check=False, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    core = services["legal-core"]["environment"]
    assert core[CLINIC_KEY] == (SYNTHETIC_CLINIC if enabled else "")
    assert core[VALUE_KEY] == ("100" if enabled else "")
    for name, service in services.items():
        if name != "legal-core":
            assert not {CLINIC_KEY, VALUE_KEY} & service.get("environment", {}).keys()
    assert not any("personal" in name for name in services)
