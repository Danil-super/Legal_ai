"""Operator-controlled analysis fence; independent of policy approval and human workflows."""

from __future__ import annotations

import os
from collections.abc import Mapping

from legal_core.case_api import ApiError


def analysis_safe_stop_active(source: Mapping[str, str] | None = None) -> bool:
    source = os.environ if source is None else source
    # Absent preserves the existing deployment. A typo must not silently resume automation.
    return source.get("LEGAL_ANALYSIS_SAFE_STOP", "0") != "0"


def require_analysis_running() -> None:
    if analysis_safe_stop_active():
        raise ApiError(
            status_code=503,
            code="ANALYSIS_SAFE_STOP",
            message="Automated analysis is paused; retain the case for retry or lawyer review",
        )
