"""Bounded operator override for one server-resolved clinic's monthly quota."""

import os
from uuid import UUID


def monthly_case_limit(clinic_id: UUID, *, default_limit: int) -> int:
    configured_clinic = os.getenv("CASE_MONTHLY_LIMIT_OVERRIDE_CLINIC_ID", "").strip()
    configured_limit = os.getenv("CASE_MONTHLY_LIMIT_OVERRIDE_VALUE", "").strip()
    if not configured_clinic and not configured_limit:
        return default_limit
    invalid = ValueError("Invalid clinic case quota override")
    if (len(configured_clinic) != 36 or not 1 <= len(configured_limit) <= 4
            or not configured_limit.isascii() or not configured_limit.isdecimal()):
        raise invalid
    try:
        override_clinic = UUID(configured_clinic)
    except ValueError:
        raise invalid from None
    override_limit = int(configured_limit)
    if (str(override_clinic) != configured_clinic or override_clinic.int == 0
            or str(override_limit) != configured_limit or not 1 <= override_limit <= 1000):
        raise invalid
    return override_limit if clinic_id == override_clinic else default_limit
