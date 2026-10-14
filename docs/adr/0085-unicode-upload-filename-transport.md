# ADR 0085: bounded Unicode filenames on the upload wire

## Context and decision

2026-10-14. Real httpx2 clients reject Cyrillic filenames in string HTTP headers
before sending the request. Case, reference-evaluation and clinic-library uploads
all reproduce UnicodeEncodeError. Retrying the same attachment cannot fix it.

Keep the existing X-Source-Filename contract for ASCII clients unchanged. For
Unicode names, gateway sends UTF-8 percent-encoding with the optional explicit
X-Source-Filename-Encoding: percent-utf8 header. Core decodes exactly once before
the existing parser/privacy/storage boundary. Without the marker, a literal
ASCII percent filename stays literal. Unknown markers are rejected, not guessed.

Wire size is bounded at 3060 characters (255 Unicode code points × maximum four
UTF-8 bytes × three percent-encoding characters). The decoded filename still has
the existing 255-character limit; paths, controls, malformed escapes and invalid
UTF-8 are rejected. This larger wire bound does not increase allowed filenames,
file size, MIME types, parser resources, privacy categories or upload permissions.

## Preservation and verification

Original bytes, checksums and the validated clinic filename are unchanged. Case
and evaluation metadata still hide the original filename. No new DB fields,
public storage links, model input, legal approvals or role grants are introduced.
The shared helper is explicitly copied into the gateway production image.

RED: all three actual httpx2 clients failed on a synthetic Cyrillic filename.
Unit checks cover ASCII compatibility, percent literals, Cyrillic/emoji roundtrip,
the maximum decoded name and malformed/path/control input. Real PostgreSQL API
checks cover unchanged bytes, clinic original metadata and rejection before raw
storage. A decoder-removal mutation caused four endpoint regressions, then was
restored. Combined regression, container import and CI remain release gates.
