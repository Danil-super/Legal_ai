"""Small, entirely synthetic Office packages for material privacy regressions."""

import io
import struct
import zipfile

WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
SYNTHETIC_EMAIL = "synthetic.person@example.invalid"
CLEAN_TEXT = "Synthetic anonymised scenario for review."


def word_part(root: str, text: str) -> str:
    return f'<w:{root} xmlns:w="{WORD_NS}"><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:{root}>'


def docx_bytes(
    *,
    body: str | None = None,
    extra_entries: dict[str, str | bytes] | None = None,
    encoding: str = "utf-8",
    archive_comment: bytes = b"",
    entry_comment: bytes = b"",
    entry_extra: bytes = b"",
    local_extra: bytes = b"",
) -> bytes:
    body = body or f"<w:p><w:r><w:t>{CLEAN_TEXT}</w:t></w:r></w:p>"
    declaration_encoding = "UTF-16" if encoding.startswith("utf-16") else "UTF-8"
    document = (
        f'<?xml version="1.0" encoding="{declaration_encoding}"?>'
        f'<w:document xmlns:w="{WORD_NS}"><w:body>{body}</w:body></w:document>'
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/></Types>',
        )
        part = zipfile.ZipInfo("word/document.xml")
        part.comment = entry_comment
        part.extra = entry_extra
        archive.writestr(part, document.encode(encoding))
        for name, content in (extra_entries or {}).items():
            value = content.encode(encoding) if isinstance(content, str) else content
            archive.writestr(name, value)
        archive.comment = archive_comment
    raw = output.getvalue()
    if local_extra:
        # Append a local-header-only extra field without changing the central copy.
        # The document is the last local entry in these fixtures.
        assert not extra_entries
        local = raw.find(b"PK\x03\x04", 4)
        filename_size, extra_size = struct.unpack_from("<HH", raw, local + 26)
        insert_at = local + 30 + filename_size + extra_size
        central = raw.find(b"PK\x01\x02")
        changed = bytearray(raw[:insert_at] + local_extra + raw[insert_at:])
        struct.pack_into("<H", changed, local + 28, extra_size + len(local_extra))
        end = changed.rfind(b"PK\x05\x06")
        struct.pack_into("<I", changed, end + 16, central + len(local_extra))
        raw = bytes(changed)
    return raw


def directory_local_name_docx(*, mismatched: bool) -> bytes:
    local_name = (SYNTHETIC_EMAIL + "/").encode()
    central_name = "x" * (len(local_name) - 1) + "/"
    raw = docx_bytes(extra_entries={central_name: b""})
    if not mismatched:
        return raw
    changed = bytearray(raw)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        offset = archive.getinfo(central_name).header_offset + 30
    changed[offset:offset + len(local_name)] = local_name
    return bytes(changed)
