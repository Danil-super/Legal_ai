"""Import the production gateway using only files actually copied by its Dockerfile."""

import json
import shlex
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "services/gateway/telegram/Dockerfile"


def _copy_runtime(destination: Path) -> None:
    # Deliberately support only the simple COPY grammar used by this image. A change to
    # flags/multi-stage copying must update this test instead of silently broadening it.
    dockerfile = DOCKERFILE.read_text().replace("\\\n", " ")
    for line in dockerfile.splitlines():
        if not line.startswith("COPY "):
            continue
        parts = shlex.split(line)
        assert len(parts) == 3 and not parts[1].startswith("--")
        source, target = parts[1:]
        if not source.startswith("services/"):
            continue
        relative_target = Path(target)
        assert not relative_target.is_absolute() and ".." not in relative_target.parts
        output = destination / relative_target
        output.parent.mkdir(parents=True, exist_ok=True)
        if (ROOT / source).is_dir():
            shutil.copytree(ROOT / source, output, dirs_exist_ok=True)
        else:
            shutil.copyfile(ROOT / source, output)


def _import_runtime(destination: Path) -> subprocess.CompletedProcess[str]:
    # -I -S disables PYTHONPATH and editable-install .pth hooks. Add installed third-party
    # dependencies as plain directories, without reintroducing the repository source tree.
    paths = [
        str(destination / "services/gateway/telegram/src"),
        str(destination / "services/legal_core/src"),
        sysconfig.get_path("purelib"),
        sysconfig.get_path("platlib"),
    ]
    script = """
import importlib.util
import json
import pathlib
import sys
sys.path[:0] = json.loads(sys.argv[1])
import telegram_gateway.__main__
import legal_core.contracts
assert pathlib.Path(legal_core.contracts.__file__).is_relative_to(sys.argv[2])
assert importlib.util.find_spec('legal_core.case_api') is None
"""
    return subprocess.run(
        [sys.executable, "-I", "-S", "-c", script, json.dumps(paths), str(destination)],
        cwd=destination,
        env={},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_gateway_entrypoint_imports_from_exact_container_copy_set(tmp_path: Path) -> None:
    _copy_runtime(tmp_path)
    result = _import_runtime(tmp_path)
    assert result.returncode == 0, result.stderr


def test_packaging_check_detects_missing_shared_contracts(tmp_path: Path) -> None:
    _copy_runtime(tmp_path)
    (tmp_path / "services/legal_core/src/legal_core/contracts.py").unlink()
    result = _import_runtime(tmp_path)
    assert result.returncode != 0
    assert "No module named 'legal_core.contracts'" in result.stderr


def test_container_checks_imports_before_polling() -> None:
    dockerfile = DOCKERFILE.read_text()
    assert 'RUN python -c "import telegram_gateway.__main__"' in dockerfile
    assert dockerfile.index('RUN python -c') > dockerfile.index('USER app')
