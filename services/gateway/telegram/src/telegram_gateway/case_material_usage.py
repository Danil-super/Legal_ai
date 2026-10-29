"""Upload-purpose flag and ephemeral, delivered-prompt consent marker."""

import os

OFFER_KEY = "_v2_case_material_analysis_offer"


def enabled() -> bool:
    return os.getenv("CASE_MATERIAL_CONTEXT_ENABLED", "0").strip().casefold() in {"1", "true"}
