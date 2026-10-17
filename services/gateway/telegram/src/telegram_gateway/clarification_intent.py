"""Distinct canonical report/preparation origins within Telegram's 64-byte limit."""

import re
from base64 import b64decode, urlsafe_b64encode
from binascii import Error
from uuid import UUID

_PREFIX = "case:clarify:"
_PREPARATION_PREFIX = "case:clarify2:"


def encode_clarification_callback(case_id: UUID, report_id: UUID) -> str:
    return _PREFIX + urlsafe_b64encode(case_id.bytes + report_id.bytes).decode("ascii").rstrip("=")


def decode_clarification_callback(callback: str) -> tuple[UUID, UUID]:
    if re.fullmatch(r"case:clarify:[A-Za-z0-9_-]{43}", callback) is None:
        raise ValueError("invalid clarification callback")
    try:
        raw = b64decode(callback[len(_PREFIX):] + "=", altchars=b"-_", validate=True)
    except Error as exc:
        raise ValueError("invalid clarification callback") from exc
    if len(raw) != 32:
        raise ValueError("invalid clarification callback")
    case_id, report_id = UUID(bytes=raw[:16]), UUID(bytes=raw[16:])
    if encode_clarification_callback(case_id, report_id) != callback:
        raise ValueError("noncanonical clarification callback")
    return case_id, report_id


def encode_preparation_callback(case_id: UUID, operation_id: UUID) -> str:
    pair = encode_clarification_callback(case_id, operation_id).removeprefix(_PREFIX)
    return _PREPARATION_PREFIX + pair


def decode_preparation_callback(callback: str) -> tuple[UUID, UUID]:
    if not callback.startswith(_PREPARATION_PREFIX):
        raise ValueError("invalid preparation callback")
    # Reuse the canonical UUID-pair parser without accepting its report namespace.
    return decode_clarification_callback(_PREFIX + callback[len(_PREPARATION_PREFIX):])
