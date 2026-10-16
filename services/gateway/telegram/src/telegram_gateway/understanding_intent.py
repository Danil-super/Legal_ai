"""Content-free durable Telegram selectors; selectors never grant authorization."""

from uuid import UUID, uuid5

_NAMESPACE = UUID("27247423-e4db-54e2-80c5-c7adeb8b23d4")
_ACTIONS = frozenset({"refresh", "accept", "manual", "cancel", "edit"})


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
