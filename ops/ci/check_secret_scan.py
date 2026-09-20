"""Exercise real Gitleaks sensitivity without using or printing real credentials."""

from __future__ import annotations

import json
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATHS = (
    "services/agent_orchestrator/src/agent_orchestrator/contracts.py",
    "services/legal_core/src/legal_core/analysis_contracts.py",
    "services/legal_core/src/legal_core/contracts.py",
    "ops/deploy/production.env.example",
)


def check(scanner: str) -> None:
    with tempfile.TemporaryDirectory(prefix="secret-scan-test-") as temporary:
        work = Path(temporary)
        target = work / "checkout"
        report = work / "report.json"
        alias = (
            "required_" + "fact_keys: list[FactKey] = Field(\n"
            + "    default_factory=list, alias=" + json.dumps("required" + "FactKeys")
            + ", max_length=20\n)\n"
        )
        placeholder = (
            "LEGAL_EDITOR_GATEWAY_" + "KEY=" + "REPLACE_WITH_A_"
            + "UNIQUE_32_PLUS_CHARACTER_SECRET\n"
        )
        for name in PATHS:
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(alias if name.endswith(".py") else placeholder, encoding="utf-8")

        def scan(expected_files: set[str]) -> None:
            result = subprocess.run(
                [str(Path(scanner).resolve()), "dir", ".", "--no-banner", "--redact=100",
                 "--config", str(ROOT / ".gitleaks.toml"), "--report-format=json",
                 "--report-path", str(report)],
                cwd=target, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False, timeout=30,
            )
            findings = json.loads(report.read_text(encoding="utf-8"))
            actual = {row["File"] for row in findings}
            if actual != expected_files or result.returncode != (1 if expected_files else 0):
                print(
                    f"Control failed: exit={result.returncode}, "
                    f"expected_files={len(expected_files)}, actual_files={len(actual)}",
                    file=sys.stderr,
                )
                raise RuntimeError("Gitleaks sensitivity regression")

        scan(set())
        # Generated only in a disposable directory; these values have never been issued.
        for name in PATHS:
            with (target / name).open("a", encoding="utf-8") as stream:
                stream.write('api_key = "' + secrets.token_urlsafe(32) + '"\n')
        scan(set(PATHS))
    print("Gitleaks controls passed: placeholders ignored; new keys detected in all four files")


if __name__ == "__main__":
    try:
        check(sys.argv[1])
    except (IndexError, KeyError, OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        print("Gitleaks sensitivity test failed; no matched content is printed", file=sys.stderr)
        raise SystemExit(1) from None
