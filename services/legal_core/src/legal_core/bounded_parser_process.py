"""Resource-bounded native PDF tools on Linux; not a filesystem/network sandbox."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Sequence

MAX_PARSER_OUTPUT_BYTES = 1_000_000
MAX_PARSER_ADDRESS_SPACE_BYTES = 512 * 1024 * 1024
TRUSTED_TOOL_PATH = "/usr/bin:/bin"
PARSER_TOOLS = frozenset({"pdfinfo", "pdftotext"})


def run_parser_tool(
    arguments: Sequence[str], *, timeout_seconds: int = 30,
) -> subprocess.CompletedProcess[str]:
    """Limit address space, CPU, core dumps and both output files before exec.

    Temporary files, unlike PIPE/capture_output, cannot accumulate unlimited output
    in the parent process. RLIMIT_FSIZE bounds them while the tool is still running.
    No preexec_fn is used: uploads are parsed in worker threads. All limits are hard
    child-process limits; the parent and stored documents are never modified.
    """
    if not arguments or arguments[0] not in PARSER_TOOLS:
        raise ValueError("unsupported document parser tool")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 120:
        raise ValueError("invalid document parser timeout")
    limiter = shutil.which("prlimit", path=TRUSTED_TOOL_PATH)
    if limiter is None:
        raise RuntimeError("document parser resource limiter is unavailable")
    # Tool names come from server code, never PATH or an uploaded filename.
    tool = shutil.which(arguments[0], path=TRUSTED_TOOL_PATH)
    if tool is None:
        raise RuntimeError("required document parser is not installed")
    command = [
        limiter,
        f"--as={MAX_PARSER_ADDRESS_SPACE_BYTES}:{MAX_PARSER_ADDRESS_SPACE_BYTES}",
        f"--cpu={timeout_seconds}:{timeout_seconds}",
        f"--fsize={MAX_PARSER_OUTPUT_BYTES}:{MAX_PARSER_OUTPUT_BYTES}",
        "--core=0:0", "--nofile=64:64", "--", tool, *arguments[1:],
    ]
    # Never inherit database/provider keys, proxy settings, HOME or dynamic-loader variables.
    environment = {"PATH": TRUSTED_TOOL_PATH, "LC_ALL": "C", "LANG": "C"}
    try:
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as diagnostics:
            result = subprocess.run(
                command, check=False, stdin=subprocess.DEVNULL, stdout=output,
                stderr=diagnostics, timeout=timeout_seconds, env=environment,
                close_fds=True,
            )
            if (
                os.fstat(output.fileno()).st_size >= MAX_PARSER_OUTPUT_BYTES
                or os.fstat(diagnostics.fileno()).st_size >= MAX_PARSER_OUTPUT_BYTES
            ):
                raise ValueError("document parser output exceeds the supported size")
            if result.returncode in {126, 127}:
                raise RuntimeError("required document parser could not be started")
            if result.returncode != 0:
                raise ValueError("document parser rejected the file")
            output.seek(0)
            raw_output = output.read(MAX_PARSER_OUTPUT_BYTES + 1)
            if len(raw_output) > MAX_PARSER_OUTPUT_BYTES:
                raise ValueError("document parser output exceeds the supported size")
            decoded = raw_output.decode("utf-8", errors="strict")
    except subprocess.TimeoutExpired:
        raise ValueError("document parser timed out") from None
    except subprocess.CalledProcessError:
        raise ValueError("document parser rejected the file") from None
    except UnicodeDecodeError:
        raise ValueError("document parser returned invalid text") from None
    except OSError:
        raise RuntimeError("document parser resources are unavailable") from None
    return subprocess.CompletedProcess(list(arguments), 0, stdout=decoded, stderr="")
