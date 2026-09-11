"""Render the one non-secret per-profile Hermes setting before startup.

The pinned Hermes gateway reads ``model.default`` before it expands config
environment references.  Only the model identifier is materialised into the
private profile volume; endpoint and credential references stay unresolved and
continue to come from the container environment.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

MODEL_PLACEHOLDER = "${HERMES_MODEL}"
MODEL_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,119}$")


def render_profile(template: str, model: str) -> str:
    """Substitute exactly one safe model identifier without materialising secrets."""
    normalized_model = model.strip()
    if not MODEL_IDENTIFIER.fullmatch(normalized_model):
        raise ValueError("model identifier is missing or invalid")
    if template.count(MODEL_PLACEHOLDER) != 1:
        raise ValueError("profile template must contain ${HERMES_MODEL} exactly once")
    return template.replace(MODEL_PLACEHOLDER, normalized_model)


def main(arguments: list[str]) -> None:
    if len(arguments) != 2:
        raise SystemExit("usage: render_profile.py TEMPLATE OUTPUT")

    template_path = Path(arguments[0])
    output_path = Path(arguments[1])
    try:
        rendered = render_profile(
            template_path.read_text(encoding="utf-8"),
            os.environ.get("HERMES_MODEL", ""),
        )
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Hermes profile rendering failed: {exc}") from exc

    temporary_path = output_path.with_suffix(".tmp")
    try:
        temporary_path.write_text(rendered, encoding="utf-8")
        os.chmod(temporary_path, 0o600)
        temporary_path.replace(output_path)
    except OSError as exc:
        raise SystemExit(f"Hermes profile write failed: {exc}") from exc


if __name__ == "__main__":
    main(sys.argv[1:])
