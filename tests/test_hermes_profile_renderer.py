from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RENDERER_PATH = ROOT / "ops" / "hermes" / "render_profile.py"

spec = spec_from_file_location("hermes_profile_renderer", RENDERER_PATH)
assert spec is not None and spec.loader is not None
renderer = module_from_spec(spec)
spec.loader.exec_module(renderer)


def test_renderer_substitutes_only_a_valid_model_identifier() -> None:
    rendered = renderer.render_profile(
        "model:\n  default: ${HERMES_MODEL}\n  api_key: ${OPENAI_API_KEY}\n",
        "gpt-6-astra-1m",
    )

    assert "default: gpt-6-astra-1m" in rendered
    assert "${OPENAI_API_KEY}" in rendered
    assert "${HERMES_MODEL}" not in rendered


@pytest.mark.parametrize("model", ["", "two\nlines", "model: injected", "x" * 121])
def test_renderer_rejects_invalid_model_identifiers(model: str) -> None:
    with pytest.raises(ValueError, match="model"):
        renderer.render_profile("default: ${HERMES_MODEL}\n", model)


def test_renderer_rejects_an_ambiguous_template() -> None:
    with pytest.raises(ValueError, match="exactly once"):
        renderer.render_profile("${HERMES_MODEL} ${HERMES_MODEL}", "grok-4.6")
