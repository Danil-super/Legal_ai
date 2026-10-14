"""Additive ASCII-only HTTP transport for bounded Unicode upload filenames."""

import re
from urllib.parse import quote, unquote_to_bytes

MAX_FILENAME_HEADER_CHARS = 255 * 4 * 3
_INVALID_PERCENT = re.compile(r"%(?![0-9a-fA-F]{2})")


def _validate_filename(value: str) -> str:
    if not value.strip() or len(value) > 255:
        raise ValueError("Source filename length is invalid")
    if value.strip() in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError("Source filename must not contain a path")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("Source filename must not contain control characters")
    return value


def source_filename_headers(filename: str) -> dict[str, str]:
    filename = _validate_filename(filename)
    if filename.isascii():
        return {"X-Source-Filename": filename}
    return {
        "X-Source-Filename": quote(filename, safe=""),
        "X-Source-Filename-Encoding": "percent-utf8",
    }


def decode_source_filename(value: str, encoding: str | None = None) -> str:
    if len(value) > MAX_FILENAME_HEADER_CHARS:
        raise ValueError("Source filename header is too long")
    if encoding is None:
        return _validate_filename(value)
    if encoding != "percent-utf8" or not value.isascii() or _INVALID_PERCENT.search(value):
        raise ValueError("Source filename encoding is invalid")
    try:
        decoded = unquote_to_bytes(value).decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ValueError("Source filename encoding is invalid") from None
    return _validate_filename(decoded)
