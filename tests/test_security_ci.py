from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = ROOT / "ops" / "ci" / "security_summary.py"


def test_security_is_a_required_deployment_gate() -> None:
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    deploy = ci["jobs"]["deploy-production"]
    assert ci["jobs"]["security"]["uses"] == "./.github/workflows/security.yml"
    assert "security" in deploy["needs"]
    assert "needs.security.result == 'success'" in deploy["if"]


def test_security_workflow_has_pinned_actions_and_no_production_secrets() -> None:
    text = (ROOT / ".github/workflows/security.yml").read_text()
    workflow = yaml.safe_load(text)
    assert workflow["permissions"] == {"contents": "read"}
    assert "secrets." not in text
    assert "continue-on-error" not in text
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            if "uses" in step:
                assert re.fullmatch(r"[\w/-]+@[0-9a-f]{40}", step["uses"])
    assert "sha256sum --check --strict" in text
    assert "--redact=100" in text
    assert "exit \"$result\"" in text


def test_container_smoke_covers_real_images_without_network() -> None:
    text = (ROOT / ".github/workflows/security.yml").read_text()
    workflow = yaml.safe_load(text)
    cases = workflow["jobs"]["container-smoke"]["strategy"]["matrix"]["include"]
    assert {case["service"] for case in cases} == {"core", "telegram", "orchestrator", "watcher"}
    for case in cases:
        assert (ROOT / case["dockerfile"]).is_file()
    assert "--network none --read-only --cap-drop ALL" in text
    assert "assert os.getuid() != 0" in text


@pytest.mark.parametrize("workflow,job", [
    ("ci.yml", "deploy-production"), ("rollback.yml", "rollback-production"),
])
def test_deploy_and_rollback_share_non_cancelling_concurrency(workflow: str, job: str) -> None:
    config = yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text())
    assert config["jobs"][job]["concurrency"] == {
        "group": "dental-legal-ai-production", "cancel-in-progress": False,
    }


@pytest.mark.parametrize("kind", ["bandit", "gitleaks"])
def test_security_reports_do_not_echo_source_or_secrets(kind: str, tmp_path: Path) -> None:
    sentinel = "SYNTHETIC_PRIVATE_TEXT"
    payload = (
        {"metrics": {"_totals": {"loc": 10}}, "errors": [], "results": [{
            "filename": "services/example.py", "line_number": 1, "test_id": "B999",
            "issue_severity": "HIGH", "issue_confidence": "HIGH", "code": sentinel,
            "issue_text": sentinel,
        }]}
        if kind == "bandit" else [{
            "File": "example.txt", "StartLine": 1, "RuleID": "synthetic-rule",
            "Secret": sentinel, "Match": sentinel,
        }]
    )
    report = tmp_path / "report.json"
    report.write_text(json.dumps(payload))
    result = subprocess.run(
        [sys.executable, str(SUMMARY), kind, str(report)],
        capture_output=True, text=True, check=False, timeout=5,
    )
    assert result.returncode == 0
    assert sentinel not in result.stdout + result.stderr
    assert json.loads(result.stdout)["count"] == 1


def test_bandit_cannot_pass_with_unscanned_files() -> None:
    spec = importlib.util.spec_from_file_location("security_summary", SUMMARY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(ValueError):
        module.summary("bandit", {"results": [], "errors": [{"reason": "syntax error"}]})
