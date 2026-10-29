# ADR 0106: Explicit consent and bounded case-file excerpts

29 October 2026. The owner requested both uploaded clinic templates and case
attachments in report preparation; SPEC-case-material-analysis.md defines the
approved increment. Existing attachment uploads promised private storage without
model use. They must remain storage-only until a new, explicit upload intent opts
the exact owned draft file into analysis.

The additive consent column defaults false. Old upload requests and receipts keep
their exact hashes/serialization. Only a true opt-in enters the new request hash;
reusing an old key with that new intent conflicts rather than changing consent.
An old storage-only receipt stays an old receipt even after a later opt-in.

The default-off context loader locally verifies private object size/hash, uses
the existing bounded format/privacy parser and selects at most twelve excerpts
of 1,200 characters, representing every eligible file before additional excerpts.
It sends neither raw files nor filenames/storage paths. File context is untrusted
factual background, never a statute, independent factual confirmation or a source
of permissions. Existing normative and risk gates remain authoritative.

Core context pairs optional caseMaterialContext with its exact SHA-256 trace.
The trace binds material/fragment identities, raw/text hashes and selected text.
Fresh submissions compare the expected optional trace exactly, including absence.
Preparation context and analysis snapshots retain that optional binding. Canonical
reports also carry an optional top-level trace so a blocked v1 result can bind its
input without falsely claiming a file was used. Ready reports have matching
snapshot/top-level traces and server-built caseMaterials source cards.

Absent optional fields are omitted: no-file and historical receipts keep their
existing canonical hashes. Completed stored records remain readable, while fresh
file-based reasoning is fenced against consent, expiry, removal or hash changes.
The early HIGH/CRITICAL routing receipt has no file-based recommendation and keeps
its fast context-free path; the separate lawyer-draft context loads and binds files
before optional model reasoning. No raw download right, model, risk rule, normative
approval or clinic-template approval changes.
