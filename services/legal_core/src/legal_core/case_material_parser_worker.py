"""One local private parser job, with no inherited database/provider credentials."""

import json
import sys

from legal_core.bounded_parser_process import MAX_PARSER_OUTPUT_BYTES
from legal_core.clinic_document_parser import (
    DOCX_MIME,
    MAX_UPLOAD_BYTES,
    PDF_MIME,
    TEXT_MIME,
    parse_clinic_document_upload,
)
from legal_core.material_privacy import validate_anonymized_material

EXTENSIONS = {TEXT_MIME: "txt", PDF_MIME: "pdf", DOCX_MIME: "docx"}


def main() -> int:
    try:
        mime = sys.argv[1]
        extension = EXTENSIONS[mime]
        raw = sys.stdin.buffer.read(MAX_UPLOAD_BYTES + 1)
        parsed = parse_clinic_document_upload(
            raw, source_filename=f"material.{extension}", content_type=mime,
        )
        validate_anonymized_material(raw, parsed)
        encoded = json.dumps({
            "normalizedText": parsed.normalized_text, "rawSha256": parsed.raw_sha256,
            "mimeType": parsed.mime_type,
        }, ensure_ascii=False).encode()
        if len(encoded) > MAX_PARSER_OUTPUT_BYTES:
            return 2
        sys.stdout.buffer.write(encoded)
        return 0
    except (IndexError, KeyError, ValueError, RuntimeError, OSError):
        # No diagnostics: parser errors can contain file text or private paths.
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
