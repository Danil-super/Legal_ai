"""Contract tests for inspecting original DOCX bytes before material storage."""

import struct

import pytest

from legal_core.clinic_document_parser import (
    DOCX_MIME,
    TEXT_MIME,
    ParsedClinicDocumentUpload,
    parse_clinic_document_upload,
    sha256_bytes,
)
from material_privacy_fixtures import (
    CLEAN_TEXT,
    SYNTHETIC_EMAIL,
    WORD_NS,
    docx_bytes,
    word_part,
)


def _parse(raw: bytes) -> ParsedClinicDocumentUpload:
    return parse_clinic_document_upload(
        raw, source_filename="synthetic.docx", content_type=DOCX_MIME
    )


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("word/header1.xml", word_part("hdr", SYNTHETIC_EMAIL)),
        ("word/footer1.xml", word_part("ftr", SYNTHETIC_EMAIL)),
        ("word/comments.xml", word_part("comments", SYNTHETIC_EMAIL)),
        ("word/footnotes.xml", word_part("footnotes", SYNTHETIC_EMAIL)),
        ("word/endnotes.xml", word_part("endnotes", SYNTHETIC_EMAIL)),
        (
            "docProps/core.xml",
            '<cp:coreProperties '
            'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f"<dc:creator>{SYNTHETIC_EMAIL}</dc:creator></cp:coreProperties>",
        ),
        (
            "docProps/custom.xml",
            '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/'
            f'custom-properties"><property name="contact">{SYNTHETIC_EMAIL}'
            "</property></Properties>",
        ),
        (
            "word/comments.xml",
            f'<w:comments xmlns:w="{WORD_NS}"><w:comment w:author="{SYNTHETIC_EMAIL}" '
            'w:id="0"><w:p><w:r><w:t>Clean note.</w:t></w:r></w:p></w:comment></w:comments>',
        ),
        (
            "word/header1.xml",
            f'<w:hdr xmlns:w="{WORD_NS}"><!-- {SYNTHETIC_EMAIL} -->'
            "<w:p><w:r><w:t>Clean header.</w:t></w:r></w:p></w:hdr>",
        ),
        (
            "word/header1.xml",
            f'<w:hdr xmlns:w="{WORD_NS}"><?contact {SYNTHETIC_EMAIL}?>'
            "<w:p><w:r><w:t>Clean header.</w:t></w:r></w:p></w:hdr>",
        ),
        (
            "word/header1.xml",
            f'<w:hdr xmlns:w="{WORD_NS}"><w:p><w:r><w:t>synthetic.person</w:t></w:r>'
            "<w:r><w:t>@example.invalid</w:t></w:r></w:p></w:hdr>",
        ),
    ],
    ids=[
        "header", "footer", "comments", "footnotes", "endnotes", "core-properties",
        "custom-properties", "author-attribute", "xml-comment", "processing-instruction",
        "split-header-runs",
    ],
)
def test_identifiers_in_nonbody_xml_are_rejected(name: str, content: str) -> None:
    from legal_core.material_privacy import MaterialDirectIdentifier, validate_anonymized_material

    raw = docx_bytes(extra_entries={name: content})
    parsed = _parse(raw)
    assert parsed.normalized_text == CLEAN_TEXT
    with pytest.raises(MaterialDirectIdentifier) as error:
        validate_anonymized_material(raw, parsed)
    assert SYNTHETIC_EMAIL not in str(error.value)


@pytest.mark.parametrize(
    "hidden",
    [
        f'<w:del><w:r><w:delText>{SYNTHETIC_EMAIL}</w:delText></w:r></w:del>',
        f'<w:r><w:instrText>{SYNTHETIC_EMAIL}</w:instrText></w:r>',
        f'<w:r><w:drawing title="{SYNTHETIC_EMAIL}"/></w:r>',
    ],
    ids=["deleted-text", "field-instruction", "drawing-title"],
)
def test_identifiers_in_unextracted_body_representations_are_rejected(hidden: str) -> None:
    from legal_core.material_privacy import MaterialDirectIdentifier, validate_anonymized_material

    raw = docx_bytes(body=f"<w:p><w:r><w:t>{CLEAN_TEXT}</w:t></w:r>{hidden}</w:p>")
    parsed = _parse(raw)
    assert parsed.normalized_text == CLEAN_TEXT
    with pytest.raises(MaterialDirectIdentifier):
        validate_anonymized_material(raw, parsed)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-16-le", "utf-16-be"])
def test_hidden_identifier_detection_is_independent_of_xml_encoding(encoding: str) -> None:
    from legal_core.material_privacy import MaterialDirectIdentifier, validate_anonymized_material

    raw = docx_bytes(
        encoding=encoding,
        extra_entries={"word/header1.xml": word_part("hdr", SYNTHETIC_EMAIL)},
    )
    with pytest.raises(MaterialDirectIdentifier):
        validate_anonymized_material(raw, _parse(raw))


@pytest.mark.parametrize("location", ["archive", "entry"])
def test_identifiers_in_zip_comments_are_rejected(location: str) -> None:
    from legal_core.material_privacy import MaterialDirectIdentifier, validate_anonymized_material

    options = {f"{location}_comment": SYNTHETIC_EMAIL.encode()}
    raw = docx_bytes(**options)
    with pytest.raises(MaterialDirectIdentifier):
        validate_anonymized_material(raw, _parse(raw))


@pytest.mark.parametrize("location", ["entry_extra", "local_extra"])
def test_uninspectable_zip_extra_fields_are_rejected(location: str) -> None:
    from legal_core.material_privacy import validate_anonymized_material

    payload = b"SYNTHETIC_OPAQUE_ZIP_METADATA"
    extra = struct.pack("<HH", 0xCAFE, len(payload)) + payload
    raw = docx_bytes(**{location: extra})
    with pytest.raises(ValueError):
        validate_anonymized_material(raw, _parse(raw))


@pytest.mark.parametrize(
    "part",
    ["word/media/image1.png", "word/fonts/font1.odttf", "docProps/thumbnail.jpeg", "unknown.bin"],
)
def test_opaque_docx_parts_are_rejected_even_with_clean_body(part: str) -> None:
    from legal_core.material_privacy import validate_anonymized_material

    raw = docx_bytes(extra_entries={part: b"SYNTHETIC_OPAQUE_BYTES"})
    with pytest.raises(ValueError):
        validate_anonymized_material(raw, _parse(raw))


@pytest.mark.parametrize(
    "content",
    [
        b"not valid XML",
        (
            '<!DOCTYPE w:hdr [<!ENTITY private "SYNTHETIC_ENTITY">]>'
            f'<w:hdr xmlns:w="{WORD_NS}"><w:p><w:r><w:t>&private;</w:t></w:r></w:p></w:hdr>'
        ).encode(),
    ],
    ids=["malformed", "dtd"],
)
def test_uninspectable_secondary_xml_is_rejected(content: bytes) -> None:
    from legal_core.material_privacy import validate_anonymized_material

    raw = docx_bytes(extra_entries={"word/header1.xml": content})
    with pytest.raises(ValueError) as error:
        validate_anonymized_material(raw, _parse(raw))
    assert "SYNTHETIC_ENTITY" not in str(error.value)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_clean_docx_with_metadata_and_headers_is_accepted_without_rewriting(encoding: str) -> None:
    from legal_core.material_privacy import validate_anonymized_material

    raw = docx_bytes(
        encoding=encoding,
        extra_entries={
            "word/header1.xml": word_part("hdr", "Synthetic header."),
            "word/footer1.xml": word_part("ftr", "Synthetic footer."),
            "docProps/core.xml": '<properties><creator>Synthetic Editor</creator></properties>',
            "_rels/.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
            '2006/relationships"><Relationship Id="rId1" Target="word/document.xml" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'officeDocument"/></Relationships>',
        },
    )
    parsed = _parse(raw)
    assert validate_anonymized_material(raw, parsed) is None
    assert parsed.normalized_text == CLEAN_TEXT
    assert parsed.raw_sha256 == sha256_bytes(raw)
    assert parsed.raw_size_bytes == len(raw)


def test_clinic_library_parser_still_accepts_originals_with_media_and_identifiers() -> None:
    raw = docx_bytes(extra_entries={
        "word/header1.xml": word_part("hdr", SYNTHETIC_EMAIL),
        "word/media/image1.png": b"SYNTHETIC_OPAQUE_BYTES",
    })
    parsed = _parse(raw)
    assert parsed.normalized_text == CLEAN_TEXT
    assert parsed.raw_sha256 == sha256_bytes(raw)
    assert parsed.raw_size_bytes == len(raw)


def test_plain_text_material_identifier_control_is_preserved() -> None:
    from legal_core.material_privacy import MaterialDirectIdentifier, validate_anonymized_material

    raw = SYNTHETIC_EMAIL.encode()
    parsed = parse_clinic_document_upload(
        raw, source_filename="synthetic.txt", content_type=TEXT_MIME
    )
    with pytest.raises(MaterialDirectIdentifier):
        validate_anonymized_material(raw, parsed)
