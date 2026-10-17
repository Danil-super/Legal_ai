"""Content-free, versioned answer selectors; selectors never authorize access."""

from base64 import urlsafe_b64decode, urlsafe_b64encode
from uuid import UUID

_CODES = {"refresh": "r1", "accept": "a1", "manual": "m1", "cancel": "c1", "edit": "e1",
          "menu": "h1"}


def bound_callback(action: str, operation: UUID, draft_id: UUID) -> str:
    if action not in _CODES:
        raise ValueError("unsupported answer action")
    encoded = urlsafe_b64encode(operation.bytes + draft_id.bytes).decode().rstrip("=")
    return f"answer:{_CODES[action]}:{encoded}"


def parse_bound_callback(value: str) -> tuple[str, UUID, UUID]:
    parts = value.split(":")
    if len(parts) != 3 or parts[0] != "answer" or parts[1] not in _CODES.values():
        raise ValueError("invalid answer callback")
    encoded = parts[2]
    if len(encoded) != 43 or any(character not in (
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    ) for character in encoded):
        raise ValueError("invalid answer selector")
    raw = urlsafe_b64decode(encoded + "=")
    operation, draft_id = UUID(bytes=raw[:16]), UUID(bytes=raw[16:])
    action = next(key for key, code in _CODES.items() if code == parts[1])
    if bound_callback(action, operation, draft_id) != value:
        raise ValueError("noncanonical answer selector")
    return action, operation, draft_id
