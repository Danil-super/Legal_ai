"""Independent fail-closed CI controls; no databases, secrets or network."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops/ci/run_integration_shard.py"


@pytest.mark.parametrize("case,expected", [("pass", 0), ("failure", 1), ("collection", 2)])
def test_independent_selector_exec_preserves_real_pytest_exit_status(tmp_path, case, expected):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["cases"]\n',
    )
    (tmp_path / "cases").mkdir()
    source = {
        "pass": "def test_synthetic(): assert True\n",
        "failure": "def test_synthetic(): assert False\n",
        "collection": "raise RuntimeError('synthetic collection refusal')\n",
    }[case]
    (tmp_path / "cases/test_synthetic.py").write_text(source)
    bootstrap = (
        "import importlib.util; from pathlib import Path; "
        f"spec=importlib.util.spec_from_file_location('ci_selector', {str(SCRIPT)!r}); "
        "module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
        f"module.ROOT=Path({str(tmp_path)!r}); "
        "module.main(['--index','0','--count','1'])"
    )
    result = subprocess.run([sys.executable, "-c", bootstrap], cwd=tmp_path,
                            capture_output=True, text=True, check=False, timeout=15)
    assert result.returncode == expected, result.stdout + result.stderr


@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled", "skipped", "", "unknown"])
def test_independent_required_aggregate_shell_gate_accepts_only_success(outcome):
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    shard = workflow["jobs"]["postgres-integration-shard"]
    aggregate = workflow["jobs"]["postgres-integration"]
    assert shard["strategy"]["fail-fast"] is False
    assert not shard.get("continue-on-error", False)
    assert shard["runs-on"] == "ubuntu-latest"
    assert "postgres" in shard["services"]
    assert shard["env"]["POSTGRES_INTEGRATION"] == "1"
    assert shard["env"]["MINIO_INTEGRATION"] == "1"
    assert aggregate["if"] == "always()"
    assert aggregate["needs"] == ["postgres-integration-shard"]
    assert not aggregate.get("continue-on-error", False)
    step = aggregate["steps"][0]
    assert step["env"]["SHARD_RESULT"] == "${{ needs.postgres-integration-shard.result }}"
    assert not step.get("continue-on-error", False)
    result = subprocess.run(["/bin/bash", "-c", step["run"]],
                            env={"SHARD_RESULT": outcome}, capture_output=True,
                            text=True, check=False, timeout=5)
    assert (result.returncode == 0) is (outcome == "success")


def test_independent_pattern_overlap_does_not_repeat_files_and_cli_refuses_empty_selection(
    tmp_path,
):
    spec = importlib.util.spec_from_file_location("ci_selector", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["cases"]\n'
        'python_files = ["test_*.py", "*_test.py", "test_both_test.py"]\n',
    )
    (tmp_path / "cases").mkdir()
    for name in ("test_alpha.py", "omega_test.py", "test_both_test.py"):
        (tmp_path / "cases" / name).write_text("def test_synthetic(): pass\n")
    files = [name for index in range(3) for name in module.select_files(tmp_path, index, 3)]
    assert sorted(files) == [
        "cases/omega_test.py", "cases/test_alpha.py", "cases/test_both_test.py",
    ]
    bootstrap = (
        "import importlib.util; from pathlib import Path; "
        f"spec=importlib.util.spec_from_file_location('ci_selector', {str(SCRIPT)!r}); "
        "module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
        f"module.ROOT=Path({str(tmp_path)!r}); module.main(['--index','3','--count','4'])"
    )
    result = subprocess.run([sys.executable, "-c", bootstrap], cwd=tmp_path,
                            capture_output=True, text=True, check=False, timeout=5)
    assert result.returncode == 2
    assert "Cannot select a complete integration shard" in result.stderr
