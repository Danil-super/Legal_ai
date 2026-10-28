"""Partition the configured suite without omissions, duplicates or test filters."""

import json
import sys
from pathlib import Path

import pytest

from ops.ci.run_integration_shard import main, select_files


def write_weights(root, files=None, default=1):
    directory = root / "ops/ci"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "integration_file_weights.json").write_text(json.dumps({
        "version": 1, "defaultSeconds": default, "files": files or {},
    }))


@pytest.fixture
def project(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["core/tests", "gateway/tests"]\n',
    )
    for directory in ("core/tests", "gateway/tests"):
        (tmp_path / directory).mkdir(parents=True)
    for name in ("test_first.py", "test_second.py", "third_test.py", "test_fourth.py"):
        (tmp_path / "core/tests" / name).write_text("def test_example(): pass\n")
    (tmp_path / "gateway/tests/test_ui.py").write_text("def test_example(): pass\n")
    (tmp_path / "gateway/tests/helpers.py").write_text("pass\n")
    write_weights(tmp_path)
    return tmp_path


def test_shards_cover_both_default_pytest_patterns_once_and_preserve_root_order(project):
    all_files = [name for index in range(3) for name in select_files(project, index, 3)]
    assert len(all_files) == len(set(all_files)) == 5
    assert set(all_files) == {
        "core/tests/test_first.py", "core/tests/test_second.py", "core/tests/third_test.py",
        "core/tests/test_fourth.py", "gateway/tests/test_ui.py",
    }
    assert select_files(project, 0, 3) == select_files(project, 0, 3)
    assert select_files(project, 0, 1)[-1] == "gateway/tests/test_ui.py"


@pytest.mark.parametrize("index,count", [(-1, 3), (3, 3), (0, 0), (0, 33), (True, 3), (0, True)])
def test_invalid_shard_never_silently_selects_less_work(project, index, count):
    with pytest.raises(ValueError):
        select_files(project, index, count)


def test_empty_shard_is_a_failure_not_a_passing_no_tests_result(project):
    with pytest.raises(ValueError):
        select_files(project, 5, 6)


@pytest.mark.parametrize("paths", ['["missing"]', '["../outside"]', '[]',
                                 '["core/tests", "core/tests"]'])
def test_invalid_or_overlapping_configured_roots_are_not_ignored(project, paths):
    (project / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\ntestpaths = " + paths + "\n",
    )
    with pytest.raises(ValueError):
        select_files(project, 0, 3)


def test_explicit_python_files_configuration_is_respected(project):
    (project / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["core/tests"]\n'
        'python_files = ["test_first.py"]\n',
    )
    assert select_files(project, 0, 1) == ["core/tests/test_first.py"]


@pytest.mark.parametrize("count", [3, 6])
def test_entry_executes_pytest_for_the_complete_shard_without_selective_filters(monkeypatch, count):
    calls = []
    monkeypatch.setattr("ops.ci.run_integration_shard.os.execv",
                        lambda executable, args: calls.append((executable, args)))
    main(["--index", "1", "--count", str(count)])
    assert len(calls) == 1 and calls[0][0] == sys.executable
    assert calls[0][1][:4] == [sys.executable, "-m", "pytest", "-vv"]
    assert calls[0][1][4:] == select_files(Path(__file__).resolve().parents[1], 1, count)


@pytest.mark.parametrize("count", [3, 6])
def test_real_suite_roots_are_all_in_the_combined_partition(count):
    root = Path(__file__).resolve().parents[1]
    files = [name for index in range(count) for name in select_files(root, index, count)]
    assert len(files) == len(set(files))
    assert sorted(files) == sorted(select_files(root, 0, 1))
    assert {name.split("/tests/")[0] for name in files if "/tests/" in name} == {
        "services/legal_core", "services/gateway/telegram", "services/agent_orchestrator",
    }
    assert any(name.startswith("tests/") for name in files)


def test_duration_balancing_separates_heavy_files_and_keeps_canonical_order(project):
    weights = {
        "core/tests/test_first.py": 100,
        "core/tests/third_test.py": 90,
        "core/tests/test_second.py": 80,
    }
    write_weights(project, weights)
    shards = [select_files(project, index, 3) for index in range(3)]
    assert all(sum(name in weights for name in shard) == 1 for shard in shards)
    all_files = select_files(project, 0, 1)
    assert all(shard == [name for name in all_files if name in shard] for shard in shards)
    assert sorted(name for shard in shards for name in shard) == sorted(all_files)
    assert shards == [select_files(project, index, 3) for index in range(3)]


def test_new_files_without_history_are_included_with_positive_default(project):
    write_weights(project, {"core/tests/test_first.py": 50})
    (project / "gateway/tests/test_new.py").write_text("def test_new(): pass\n")
    shards = [select_files(project, index, 3) for index in range(3)]
    assert sum("gateway/tests/test_new.py" in shard for shard in shards) == 1
    assert all(shards)


@pytest.mark.parametrize("replacement", [
    '{}', '{"version":2,"defaultSeconds":1,"files":{}}',
    '{"version":1,"defaultSeconds":0,"files":{}}',
    '{"version":1,"defaultSeconds":true,"files":{}}',
    '{"version":1,"defaultSeconds":NaN,"files":{}}',
    '{"version":1,"defaultSeconds":1,"files":{"core/tests/test_first.py":-1}}',
    '{"version":1,"defaultSeconds":1,"files":{"../escape.py":1}}',
    '{"version":1,"defaultSeconds":1,"files":{"/absolute.py":1}}',
    '{"version":1,"defaultSeconds":1,"files":{"x.py":1,"x.py":2}}',
    '{"version":1,"defaultSeconds":1,"files":{},"ignore":["test_new.py"]}',
])
def test_malformed_duration_history_cannot_silently_change_selection(project, replacement):
    (project / "ops/ci/integration_file_weights.json").write_text(replacement)
    with pytest.raises(ValueError):
        select_files(project, 0, 3)


def test_missing_duration_manifest_refuses_selection(project):
    (project / "ops/ci/integration_file_weights.json").unlink()
    with pytest.raises(OSError):
        select_files(project, 0, 3)
