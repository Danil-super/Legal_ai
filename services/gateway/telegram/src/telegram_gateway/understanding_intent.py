"""Content-free durable Telegram selectors; selectors never grant authorization."""

from base64 import urlsafe_b64decode, urlsafe_b64encode
from uuid import UUID, uuid5

_NAMESPACE = UUID("27247423-e4db-54e2-80c5-c7adeb8b23d4")
_ACTIONS = frozenset({"refresh", "accept", "manual", "cancel", "edit"})
_CODES = {"refresh": "r1", "accept": "a1", "manual": "m1", "cancel": "c1", "edit": "e1"}


def callback(action: str, operation: UUID) -> str:
    if action not in _ACTIONS:
        raise ValueError("unsupported understanding action")
    return f"understanding:{action}1:{operation.hex}"


def parse_callback(value: str) -> tuple[str, UUID]:
    pieces = value.split(":")
    if len(pieces) != 3 or pieces[0] != "understanding":
        raise ValueError("invalid understanding callback")
    action = pieces[1].removesuffix("1")
    if action not in _ACTIONS or pieces[1] != action + "1":
        raise ValueError("invalid understanding action")
    operation = UUID(hex=pieces[2])
    if pieces[2] != operation.hex:
        raise ValueError("noncanonical understanding selector")
    return action, operation


def source_keys(actor: int, chat: int, message: int) -> tuple[UUID, UUID]:
    if any(type(value) is not int or value <= 0 for value in (actor, chat, message)):
        raise ValueError("original private source event required")
    event = f"telegram-understanding:v1:{actor}:{chat}:{message}"
    return uuid5(_NAMESPACE, event + ":create"), uuid5(_NAMESPACE, event + ":source")


def bound_callback(action: str, operation: UUID, draft_id: UUID) -> str:
    if action not in _CODES:
        raise ValueError("unsupported understanding action")
    encoded = urlsafe_b64encode(operation.bytes + draft_id.bytes).decode().rstrip("=")
    return f"understanding:{_CODES[action]}:{encoded}"


def parse_bound_callback(value: str) -> tuple[str, UUID, UUID]:
    pieces = value.split(":")
    if len(pieces) != 3 or pieces[0] != "understanding" or pieces[1] not in _CODES.values():
        raise ValueError("invalid bound understanding callback")
    encoded = pieces[2]
    if len(encoded) != 43 or any(c not in (
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    ) for c in encoded):
        raise ValueError("invalid bound understanding selector")
    raw = urlsafe_b64decode(encoded + "=")
    operation, draft_id = UUID(bytes=raw[:16]), UUID(bytes=raw[16:])
    action = next(key for key, code in _CODES.items() if code == pieces[1])
    if bound_callback(action, operation, draft_id) != value:
        raise ValueError("noncanonical bound understanding selector")
    return action, operation, draft_id
