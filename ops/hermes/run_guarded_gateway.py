"""Expose incomplete pinned SSE results; process-only, never mutate upstream files."""

from __future__ import annotations

import hashlib
import inspect
import runpy
import sys
import textwrap

EXPECTED_HANDLER_SHA256 = "55a06de31557760a11ad5e2576ddde96ae0ca7e21e3d1c991886616a85826abf"
OLD_CONDITION = "elif agent_error is not None or is_failed or (not completed and err_msg):"
NEW_CONDITION = "elif agent_error is not None or is_failed or is_partial or not completed:"
ERROR = "Hermes completion-status guard mismatch; refusing to start"


def patched_handler_source(source: str) -> str:
    if (
        hashlib.sha256(source.encode()).hexdigest() != EXPECTED_HANDLER_SHA256
        or source.count(OLD_CONDITION) != 1
    ):
        raise SystemExit(ERROR)
    return source.replace(OLD_CONDITION, NEW_CONDITION, 1)


def install_completion_guard() -> None:
    try:
        from gateway.platforms.api_server import APIServerAdapter

        original = APIServerAdapter._write_sse_chat_completion
        source = textwrap.dedent(inspect.getsource(original))
        patched = patched_handler_source(source)
        namespace: dict = {}
        exec(
            compile(patched, "<legal-hermes-completion-guard>", "exec", dont_inherit=True),
            original.__globals__,
            namespace,
        )
        APIServerAdapter._write_sse_chat_completion = namespace["_write_sse_chat_completion"]
    except Exception:
        raise SystemExit(ERROR) from None


def main() -> None:
    if len(sys.argv) != 1:
        raise SystemExit(ERROR)
    install_completion_guard()
    # Run the exact installed CLI in this same interpreter so the patch survives.
    # A subprocess/exec would discard the process-local method replacement.
    cli = "/opt/hermes/.venv/bin/hermes"
    sys.argv = [cli, "gateway", "run", "--no-supervise"]
    runpy.run_path(cli, run_name="__main__")


if __name__ == "__main__":
    main()
