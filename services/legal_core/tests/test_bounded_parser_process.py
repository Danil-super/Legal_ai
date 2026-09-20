"""Synthetic child processes prove that native parser limits apply during execution."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from legal_core import bounded_parser_process as runner

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux") or shutil.which("prlimit") is None,
    reason="requires Linux resource limits",
)


def use_python_as_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    original = shutil.which

    def resolve(name: str, *, path: str) -> str | None:
        assert path == "/usr/bin:/bin"
        return sys.executable if name == "pdfinfo" else original(name, path=path)

    monkeypatch.setattr(runner.shutil, "which", resolve)


def test_kernel_limits_and_clean_environment_are_active(monkeypatch: pytest.MonkeyPatch) -> None:
    use_python_as_tool(monkeypatch)
    monkeypatch.setenv("SYNTHETIC_PROVIDER_CREDENTIAL", "never-inherited")
    script = (
        "import json, os, resource; "
        "print(json.dumps({'env':dict(os.environ), 'limits': [resource.getrlimit(x) for x in "
        "[resource.RLIMIT_AS, resource.RLIMIT_CPU, resource.RLIMIT_FSIZE, "
        "resource.RLIMIT_CORE, resource.RLIMIT_NOFILE]]}))"
    )
    result = runner.run_parser_tool(["pdfinfo", "-I", "-c", script], timeout_seconds=4)
    data = json.loads(result.stdout)
    assert "SYNTHETIC_PROVIDER_CREDENTIAL" not in data["env"]
    assert data["limits"] == [[512 * 1024 * 1024] * 2, [4, 4], [1_000_000] * 2, [0, 0], [64, 64]]
    assert result.stderr == ""


@pytest.mark.parametrize("descriptor", [1, 2])
def test_output_is_bounded_while_child_runs(
    monkeypatch: pytest.MonkeyPatch, descriptor: int,
) -> None:
    use_python_as_tool(monkeypatch)
    script = f"import os; [os.write({descriptor}, b'x' * 65536) for _ in range(64)]"
    with pytest.raises(ValueError, match="output exceeds"):
        runner.run_parser_tool(["pdfinfo", "-I", "-c", script], timeout_seconds=4)


def test_large_allocation_is_rejected_in_child(monkeypatch: pytest.MonkeyPatch) -> None:
    use_python_as_tool(monkeypatch)
    with pytest.raises(ValueError, match="rejected"):
        runner.run_parser_tool(
            ["pdfinfo", "-I", "-c", "bytearray(600 * 1024 * 1024)"], timeout_seconds=4,
        )


def test_wall_timeout_terminates_child(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    use_python_as_tool(monkeypatch)
    pid_file = tmp_path / "pid"
    script = (
        "import os,time; from pathlib import Path; "
        f"Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(10)"
    )
    with pytest.raises(ValueError, match="timed out"):
        runner.run_parser_tool(["pdfinfo", "-I", "-c", script], timeout_seconds=1)
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_native_stderr_and_invalid_bytes_are_not_exposed(monkeypatch: pytest.MonkeyPatch) -> None:
    use_python_as_tool(monkeypatch)
    for script in (
        "import sys; sys.stderr.write('SYNTHETIC_PRIVATE_TEXT'); sys.exit(1)",
        "import os; os.write(1, b'\\xff')",
    ):
        with pytest.raises(ValueError) as error:
            runner.run_parser_tool(["pdfinfo", "-I", "-c", script], timeout_seconds=4)
        assert "SYNTHETIC_PRIVATE_TEXT" not in str(error.value)
        assert error.value.__cause__ is None


@pytest.mark.parametrize("arguments", [[], ["sh", "-c", "echo x"], ["/tmp/pdfinfo", "x"]])
def test_only_fixed_server_tool_names_are_allowed(arguments: list[str]) -> None:
    with pytest.raises(ValueError, match="unsupported"):
        runner.run_parser_tool(arguments)


@pytest.mark.parametrize("timeout", [0, -1, 121, True])
def test_timeout_cannot_remove_the_limit(timeout: int) -> None:
    with pytest.raises(ValueError, match="timeout"):
        runner.run_parser_tool(["pdfinfo", "synthetic.pdf"], timeout_seconds=timeout)


def test_missing_limiter_never_falls_back_to_unbounded_process(monkeypatch) -> None:
    monkeypatch.setattr(runner.shutil, "which", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: pytest.fail("spawned"))
    with pytest.raises(RuntimeError, match="limiter"):
        runner.run_parser_tool(["pdfinfo", "synthetic.pdf"])


def test_process_error_is_sanitized(monkeypatch) -> None:
    use_python_as_tool(monkeypatch)

    def failed(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0], stderr="SYNTHETIC_PRIVATE_TEXT")

    monkeypatch.setattr(runner.subprocess, "run", failed)
    with pytest.raises(ValueError, match="rejected") as error:
        runner.run_parser_tool(["pdfinfo", "synthetic.pdf"])
    assert error.value.__cause__ is None


@pytest.mark.skipif(shutil.which("pdfinfo") is None, reason="requires Poppler")
def test_real_pdf_tools_keep_text_and_reject_encrypted_document() -> None:
    from io import BytesIO

    from reportlab.pdfgen import canvas
    from reportlab.lib.pdfencrypt import StandardEncryption
    from legal_core.clinic_document_parser import _parse_pdf

    for password in (None, "synthetic", ""):
        encrypted = password is not None
        output = BytesIO()
        pdf = canvas.Canvas(
            output, encrypt=StandardEncryption(password, ownerPassword="owner")
            if encrypted else None,
        )
        pdf.drawString(72, 720, "Synthetic parser regression")
        pdf.save()
        if encrypted:
            with pytest.raises(ValueError):
                _parse_pdf(output.getvalue())
        else:
            text, version = _parse_pdf(output.getvalue())
            assert "Synthetic parser regression" in text
            assert version == "pdftotext-clinic.v2"


@pytest.mark.parametrize("encryption", ["yes (print:yes copy:yes)", "unknown", ""])
def test_pdf_unknown_or_encrypted_metadata_never_reaches_extraction(
    monkeypatch, encryption,
) -> None:
    from legal_core import clinic_document_parser as parser

    calls = []

    def metadata(arguments, **kwargs):
        calls.append(arguments[0])
        info = "Pages: 1\n" + (f"Encrypted: {encryption}\n" if encryption else "")
        return subprocess.CompletedProcess(arguments, 0, stdout=info, stderr="")

    monkeypatch.setattr(parser, "_run_tool", metadata)
    with pytest.raises(ValueError):
        parser._parse_pdf(b"%PDF-1.7\nsynthetic")
    assert calls == ["pdfinfo"]
